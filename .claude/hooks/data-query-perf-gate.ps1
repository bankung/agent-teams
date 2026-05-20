# DRAFT (Kanban #1271 AC3) — Data Analytics team query-performance gate.
# PreToolUse on Bash for sql-optimizer / bi-analyst / analytics-platform-integrator.
# Soft requires-attention nudge when a SQL execution looks unbounded (no LIMIT,
# no EXPLAIN ANALYZE prefix). Threshold N=10s is informational only — the hook
# never blocks; it surfaces a smell for operator review.
#
# DRAFT ONLY — do NOT install. Lead handles agent file + .claude/hooks/ placement
# per feedback_claude_dir_humans_only.md.
#
# Registration snippet (Lead writes into .claude/agents/<agent>.md frontmatter):
#   hooks:
#     PreToolUse:
#       - matcher: Bash
#         hooks:
#           - type: command
#             command: powershell -NoProfile -ExecutionPolicy Bypass -File .claude/hooks/data-query-perf-gate.ps1
#
# Fail-open on any internal error (we never want a gate hook to halt analysts
# on parser glitches; the actual safety net is operator review of the warning).

$ErrorActionPreference = 'Continue'

# Constant: future per-project override via projects.config JSONB (TODO Kanban #?).
$QueryTimeoutSeconds = 10

try {
    $payloadRaw = [Console]::In.ReadToEnd()
    if (-not $payloadRaw) { exit 0 }
    $payload = $payloadRaw | ConvertFrom-Json
} catch {
    # Fail-open: malformed payload should not block analyst work.
    exit 0
}

$toolName = $payload.tool_name
if ($toolName -ne 'Bash') { exit 0 }

$cmd = $payload.tool_input.command
if (-not $cmd) { exit 0 }

# Agent-name scope: this hook is meant to run only for data analytics agents,
# but we double-check the payload in case it's wired at settings.json scope.
# Multi-key fallback because the payload shape across hook versions varies.
$agentName = $null
foreach ($key in @('agent_name', 'subagent_type', 'agent', 'agentName')) {
    if ($payload.PSObject.Properties.Name -contains $key -and $payload.$key) {
        $agentName = $payload.$key
        break
    }
}
$scopedAgents = @('sql-optimizer', 'bi-analyst', 'analytics-platform-integrator')
# If agent_name is present AND not in scope -> exit 0 (let other hooks handle).
# If agent_name is null (per-agent-frontmatter wiring) -> proceed (the wiring IS the scope).
if ($agentName -and ($scopedAgents -notcontains $agentName)) { exit 0 }

# ---------------------------------------------------------------------------
# SQL-execution surface detection
# ---------------------------------------------------------------------------
# Surfaces in scope (in rough order of common-ness):
#   psql -c "<query>"           (or single-quoted)
#   bigquery query "<query>"    (gcloud subcommand)
#   snowsql -q "<query>"
#   python -c "...execute(...)" (generic SQLAlchemy / psycopg fallback)
#
# We extract the embedded query string then run the heuristic on it.
$queryBody = $null

# psql -c "..." or psql -c '...'
$psqlMatch = [regex]::Match($cmd, '(?i)\bpsql\b[^"'']*-c\s+(["''])(?<q>(?:(?!\1).)*)\1')
if ($psqlMatch.Success) { $queryBody = $psqlMatch.Groups['q'].Value }

# bigquery query "..." (bq command + alias)
if (-not $queryBody) {
    $bqMatch = [regex]::Match($cmd, '(?i)\b(?:bigquery|bq)\s+query\s+(["''])(?<q>(?:(?!\1).)*)\1')
    if ($bqMatch.Success) { $queryBody = $bqMatch.Groups['q'].Value }
}

# snowsql -q "..."
if (-not $queryBody) {
    $snowMatch = [regex]::Match($cmd, '(?i)\bsnowsql\b[^"'']*-q\s+(["''])(?<q>(?:(?!\1).)*)\1')
    if ($snowMatch.Success) { $queryBody = $snowMatch.Groups['q'].Value }
}

# python -c "...execute(...)" — generic fallback. Extract entire -c body and
# look for a SELECT inside.
if (-not $queryBody) {
    $pyMatch = [regex]::Match($cmd, '(?i)\bpython\b[^"'']*-c\s+(["''])(?<q>(?:(?!\1).)*)\1')
    if ($pyMatch.Success) {
        $pyBody = $pyMatch.Groups['q'].Value
        if ($pyBody -match '(?i)\bexecute\s*\(' -and $pyBody -match '(?i)\bSELECT\b') {
            $queryBody = $pyBody
        }
    }
}

# No SQL surface detected -> not our concern, exit neutrally.
if (-not $queryBody) { exit 0 }

# ---------------------------------------------------------------------------
# Heuristic: is this a potentially-expensive unbounded SELECT?
# ---------------------------------------------------------------------------
# Skip if the operator already prefixed EXPLAIN / EXPLAIN ANALYZE — they're
# intentionally probing the plan.
if ($queryBody -match '(?i)^\s*EXPLAIN\b') { exit 0 }

# Must contain SELECT ... FROM ... to be a real query.
if ($queryBody -notmatch '(?is)\bSELECT\b.*\bFROM\b') { exit 0 }

# DDL/DML carve-out — we do NOT police INSERT/UPDATE/DELETE here; that's the
# job of block-raw-sql-dml.ps1. This gate only nudges SELECTs.
if ($queryBody -match '(?i)\b(INSERT|UPDATE|DELETE|TRUNCATE|DROP|ALTER|CREATE)\b') { exit 0 }

# LIMIT analysis: extract the numeric limit if present.
$limitMatch = [regex]::Match($queryBody, '(?i)\bLIMIT\s+(\d+)')
$hasSmallLimit = $false
if ($limitMatch.Success) {
    $limitN = [int]$limitMatch.Groups[1].Value
    if ($limitN -le 1000) { $hasSmallLimit = $true }
}

if ($hasSmallLimit) { exit 0 }

# ---------------------------------------------------------------------------
# Smell detected: SELECT ... FROM ... with no LIMIT (or LIMIT > 1000) and no
# EXPLAIN prefix. Emit requires-attention.
# ---------------------------------------------------------------------------
$reason = @"
data-query-perf-gate: SELECT looks unbounded (no LIMIT or LIMIT > 1000, no
EXPLAIN ANALYZE prefix). Default soft threshold N=$QueryTimeoutSeconds seconds.

Before running this query, run EXPLAIN ANALYZE first to confirm the plan
fits the threshold:

    psql -c "EXPLAIN ANALYZE $($queryBody.Substring(0, [Math]::Min(80, $queryBody.Length)))..."

If the planner shows total cost / time within budget, re-run the original
query; this gate will allow it on the second invocation because the prior
turn established the EXPLAIN evidence (operator judgement).

See: data-analytics team standards for query budget conventions.
"@

$output = @{
    hookSpecificOutput = @{
        hookEventName            = "PreToolUse"
        permissionDecision       = "requires-attention"
        permissionDecisionReason = $reason
    }
} | ConvertTo-Json -Compress -Depth 4

Write-Output $output
exit 0
