# Block destructive raw SQL DML (DELETE / UPDATE / INSERT / TRUNCATE / DROP) at the harness layer.
# Both Lead's main session AND every subagent inherit this hook from .codex/hooks.json — the
# enforcement is harness-side, immune to context compaction or agent-definition skim.
#
# Inspects PreToolUse(Bash) calls whose command text invokes a SQL-execution shell
# (psql -c "...", python -c "..."). Allows everything else (alembic, pytest, curl, git, etc.)
# without inspection.
#
# Codified rule: .codex/docs/lessons.md "Raw SQL DML is human-only" — strike #1 incident
# was Kanban #483 (2026-05-09) where a subagent hard-deleted 45 soft-deleted project rows
# via raw SQL and reasoned around the golden rule.

$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
$cmd = $payload.tool_input.command

if (-not $cmd) { exit 0 }

# Skip wrapper commands that legitimately discuss SQL in their arguments without executing it
# (e.g., git commit messages, echo to file, grep/awk over SQL files). The first-word check is
# coarse but safe: these commands cannot reach the DB. docker / sh / bash / python / psql are
# NOT on the skip list — they CAN wrap a real DB call.
$firstWord = (($cmd -replace '^\s+', '') -split '\s+')[0]
$safeWrappers = @('git', 'echo', 'cat', 'head', 'tail', 'less', 'more',
                  'ls', 'pwd', 'cd', 'grep', 'awk', 'sed', 'find',
                  'diff', 'wc', 'sort', 'uniq', 'cut', 'tr')
if ($safeWrappers -contains $firstWord) { exit 0 }

# Only inspect commands that invoke a SQL-capable interactive interpreter with -c (script string).
# psql -c "..." executes the SQL string against the DB. python(3) -c "..." can run any Python,
# including DML via SQLAlchemy / asyncpg / psycopg.
$isPsqlExec   = $cmd -match '\bpsql\b[^\|;]*\s-c\b'
$isPythonExec = $cmd -match '\bpython3?\b[^\|;]*\s-c\b'

if (-not ($isPsqlExec -or $isPythonExec)) { exit 0 }

# Destructive keywords. Word-boundary anchored, case-insensitive. SELECT and \d (read-only)
# are intentionally not listed — they are the diagnostic tools subagents may use freely.
$patterns = @(
    '\bDELETE\s+FROM\b',
    '\bUPDATE\s+\w+\s+SET\b',
    '\bINSERT\s+INTO\b',
    '\bTRUNCATE\b',
    '\bDROP\s+(TABLE|DATABASE|SCHEMA|INDEX|CONSTRAINT|VIEW)\b',
    '\bALTER\s+TABLE\b'
)

foreach ($pattern in $patterns) {
    if ($cmd -match "(?i)$pattern") {
        $reason = @"
Raw SQL DML detected (pattern: $pattern).

Subagents must NEVER execute destructive SQL via psql -c or python -c — even for cleanup of
test-leaked rows, even on already-soft-deleted rows, even when the call looks "obviously safe".

Required behavior:
  1. Diagnose with SELECT / \d / EXPLAIN (read-only — these are not blocked).
  2. Propose the exact statement + row counts in your final report.
  3. Stop. Lead surfaces to user; user runs it.

If you are the user and want to run this manually, edit .codex/hooks.json to remove the
PreToolUse hook (or run the command in a separate terminal outside Codex). The friction
of disabling the hook IS the gate — see .codex/docs/lessons.md "Raw SQL DML is human-only".
"@

        $output = @{
            hookSpecificOutput = @{
                hookEventName            = "PreToolUse"
                permissionDecision       = "deny"
                permissionDecisionReason = $reason
            }
        } | ConvertTo-Json -Compress -Depth 4

        Write-Output $output
        exit 2
    }
}

exit 0
