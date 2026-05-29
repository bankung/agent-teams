# Strict read-only enforcement hook scoped to the project-auditor agent.
#
# Defense-in-depth: project-auditor's frontmatter `tools:` whitelist already restricts
# to [Read, Grep, Glob, Bash]. This hook adds a SECOND gate that narrows Bash to
# curl-only, denies Write / Edit / NotebookEdit outright, and rejects any other
# tool (Agent / WebFetch / MCP / etc.) — so an accidental whitelist drift or a
# tool-name confusion can't open a mutation path. The agent is supposed to
# audit projects and produce JSONB reports; it has no business mutating anything.
#
# Per-agent scope:
#   This hook only fires when .codex/agents/project-auditor.md wires it via
#   frontmatter `hooks: PreToolUse:` (per the per-agent-hooks pattern). It does
#   NOT live in .codex/settings.json — other agents (Lead, dev-*, etc.) are
#   unaffected. See feedback_per_agent_hooks memory for the per-agent semantics.
#
# Whitelist-not-blacklist philosophy for Bash:
#   The project-auditor uses Bash for one purpose — curl against the localhost API
#   to read project / task state. Any other Bash command is a code smell. Rather
#   than enumerate every dangerous command, we explicitly deny by default and
#   only allow `curl ...`. The pattern list below (DML, file-write redirects,
#   git-mutate, docker-mutate) is for diagnostic purposes: when something gets
#   denied, the reason names the matched pattern so the user understands WHY
#   the catch-all fallback fired. The catch-all is the actual safety net.
#
# Deny-always for Write / Edit / NotebookEdit:
#   Even though the agent's frontmatter `tools:` whitelist should already block
#   these, we deny here too — frontmatter whitelists have historically been the
#   first thing accidentally widened during agent edits. The hook is the durable
#   gate that survives a sloppy frontmatter PR.
#
# Kanban #1210 (GOV2 — project-auditor agent scaffolding).
# Patterns mirrored from:
#   - .codex/hooks/tester-curl-allow.ps1            (per-agent allow JSON shape)
#   - .codex/hooks/block-raw-sql-dml.ps1            (DML keyword detection)
#   - .codex/hooks/block-spawn-on-killed-project.ps1 (PS 5.1 + fail-open style)
#
# Lead handoff: the matching frontmatter block belongs in
#   .codex/agents/project-auditor.md
# Lead drafts that file in parallel; this hook does NOT touch agent files,
# settings.json, or any other hook.

$ErrorActionPreference = 'Stop'

# Read stdin payload. Malformed input -> fail-closed deny (auditor must never
# proceed on ambiguous state), unlike the killed-project hook which fails open
# because that one is a soft warning layer.
try {
    $payloadRaw = [Console]::In.ReadToEnd()
    if (-not $payloadRaw) {
        $out = @{
            hookSpecificOutput = @{
                hookEventName            = "PreToolUse"
                permissionDecision       = "deny"
                permissionDecisionReason = "project-auditor-readonly: empty PreToolUse payload, denying for safety"
            }
        } | ConvertTo-Json -Compress -Depth 4
        Write-Output $out
        exit 2
    }
    $payload = $payloadRaw | ConvertFrom-Json
} catch {
    $out = @{
        hookSpecificOutput = @{
            hookEventName            = "PreToolUse"
            permissionDecision       = "deny"
            permissionDecisionReason = "project-auditor-readonly: malformed PreToolUse payload, denying for safety"
        }
    } | ConvertTo-Json -Compress -Depth 4
    Write-Output $out
    exit 2
}

$toolName = $payload.tool_name

# Read-only tools that match the agent's intended capability surface.
# Neutral exit 0 = no JSON, harness falls through to normal allow/ask flow.
$readOnlyTools = @('Read', 'Grep', 'Glob')
if ($readOnlyTools -contains $toolName) { exit 0 }

# Helper to emit a deny JSON + exit 2.
function Deny-Tool([string]$reason) {
    $out = @{
        hookSpecificOutput = @{
            hookEventName            = "PreToolUse"
            permissionDecision       = "deny"
            permissionDecisionReason = $reason
        }
    } | ConvertTo-Json -Compress -Depth 4
    Write-Output $out
    exit 2
}

# Write / Edit / NotebookEdit — defense-in-depth deny.
$writeTools = @('Write', 'Edit', 'NotebookEdit')
if ($writeTools -contains $toolName) {
    Deny-Tool @"
project-auditor is read-only. Tool '$toolName' is denied by .codex/hooks/project-auditor-readonly.ps1.

The project-auditor agent produces audit findings as its final text reply (and
optionally writes them to a JSONB report via the Lead-mediated API path).
It must not Write/Edit/NotebookEdit any file directly. If you need to record
findings, return them in your final reply and let Lead persist via API.

This denial is defense-in-depth — the agent's frontmatter tools whitelist
should already block this. If you are seeing this, the whitelist may have
drifted; flag it back to Lead.
"@
}

# Bash — whitelist-only: curl is allowed, everything else denied. The deny-pattern
# list below is for INFORMATIVE reasons; the actual safety net is the catch-all
# at the bottom of the Bash branch.
if ($toolName -eq 'Bash') {
    $cmd = $payload.tool_input.command
    if (-not $cmd) {
        Deny-Tool "project-auditor-readonly: empty Bash command, denying for safety"
    }

    $trimmed = $cmd -replace '^\s+', ''
    $firstWord = ($trimmed -split '\s+')[0]

    # ALLOW curl — auditor's only legitimate Bash use.
    if ($firstWord -eq 'curl') {
        $out = @{
            hookSpecificOutput = @{
                hookEventName            = "PreToolUse"
                permissionDecision       = "allow"
                permissionDecisionReason = "curl auto-approved for project-auditor read-only API probing (.codex/hooks/project-auditor-readonly.ps1)"
            }
        } | ConvertTo-Json -Compress -Depth 4
        Write-Output $out
        exit 0
    }

    # Informative deny patterns. Case-insensitive. If any of these fire, the
    # reason names the matched pattern so the user understands the smell.
    $denyPatterns = @(
        # DML SQL
        @{ pattern = '\bINSERT\s+INTO\b';                       label = 'INSERT INTO (DML)' },
        @{ pattern = '\bUPDATE\s+\w+\s+SET\b';                  label = 'UPDATE ... SET (DML)' },
        @{ pattern = '\bDELETE\s+FROM\b';                       label = 'DELETE FROM (DML)' },
        @{ pattern = '\bTRUNCATE\b';                            label = 'TRUNCATE (DML)' },
        @{ pattern = '\bDROP\s+(TABLE|DATABASE|SCHEMA|INDEX)\b'; label = 'DROP TABLE/DATABASE/SCHEMA/INDEX (DDL)' },
        @{ pattern = '\bALTER\s+TABLE\b';                       label = 'ALTER TABLE (DDL)' },
        @{ pattern = '\bCREATE\s+(TABLE|DATABASE|INDEX)\b';     label = 'CREATE TABLE/DATABASE/INDEX (DDL)' },
        # File-write redirects / file mutations
        @{ pattern = '(?<![<>])>(?![>&])';                      label = 'shell redirect > (file write)' },
        @{ pattern = '>>';                                       label = 'shell redirect >> (file append)' },
        @{ pattern = '\btee\b';                                  label = 'tee (file write)' },
        @{ pattern = '\bcp\b';                                   label = 'cp (file copy)' },
        @{ pattern = '\bmv\b';                                   label = 'mv (file move)' },
        @{ pattern = '\brm\b';                                   label = 'rm (file delete)' },
        @{ pattern = '\bmkdir\b';                                label = 'mkdir (filesystem mutation)' },
        @{ pattern = '\btouch\b';                                label = 'touch (file create)' },
        @{ pattern = '\bchmod\b';                                label = 'chmod (permission mutation)' },
        @{ pattern = '\bchown\b';                                label = 'chown (ownership mutation)' },
        # Git-mutate
        @{ pattern = '\bgit\s+push\b';                           label = 'git push (remote mutation)' },
        @{ pattern = '\bgit\s+commit\b';                         label = 'git commit (history mutation)' },
        @{ pattern = '\bgit\s+merge\b';                          label = 'git merge (history mutation)' },
        @{ pattern = '\bgit\s+rebase\b';                         label = 'git rebase (history mutation)' },
        @{ pattern = '\bgit\s+reset\b';                          label = 'git reset (history mutation)' },
        @{ pattern = '\bgit\s+checkout\b';                       label = 'git checkout (working-tree mutation)' },
        @{ pattern = '\bgit\s+add\b';                            label = 'git add (index mutation)' },
        @{ pattern = '\bgit\s+stash\b';                          label = 'git stash (index/working-tree mutation)' },
        @{ pattern = '\bgit\s+tag\b';                            label = 'git tag (ref mutation)' },
        @{ pattern = '\bgit\s+revert\b';                         label = 'git revert (history mutation)' },
        # Docker-mutate
        @{ pattern = '\bdocker\s+compose\s+up\b';                label = 'docker compose up (container lifecycle)' },
        @{ pattern = '\bdocker\s+compose\s+down\b';              label = 'docker compose down (container lifecycle)' },
        @{ pattern = '\bdocker\s+compose\s+restart\b';           label = 'docker compose restart (container lifecycle)' },
        @{ pattern = '\bdocker\s+compose\s+exec\b';              label = 'docker compose exec (potential psql/mutation path)' },
        @{ pattern = '\bdocker\s+rm\b';                          label = 'docker rm (container delete)' },
        @{ pattern = '\bdocker\s+stop\b';                        label = 'docker stop (container lifecycle)' },
        @{ pattern = '\bdocker\s+volume\s+rm\b';                 label = 'docker volume rm (data delete)' },
        @{ pattern = '\bdocker\s+build\b';                       label = 'docker build (image mutation)' }
    )

    foreach ($p in $denyPatterns) {
        if ($cmd -match "(?i)$($p.pattern)") {
            Deny-Tool @"
project-auditor Bash blocked — matched pattern: $($p.label)

project-auditor is restricted to curl-only Bash invocations. The matched pattern
indicates a mutating operation that has no place in a read-only audit. If the
audit truly needs this data, propose the exact command in your final reply
and let Lead surface to user for manual execution.

See: .codex/hooks/project-auditor-readonly.ps1
"@
        }
    }

    # Catch-all whitelist fallback — any non-curl Bash that didn't match a known
    # mutation pattern is still denied. This is the real safety net.
    Deny-Tool @"
project-auditor Bash blocked — '$firstWord' is not on the curl-only whitelist.

The project-auditor agent may only invoke `curl ...` from Bash (typically against
http://localhost:8456/api/... to read project/task state). Any other command —
even apparently read-only ones like ls / cat / grep — should be done via the
Read / Grep / Glob tools, not Bash. If you genuinely need a Bash subprocess
beyond curl, propose it in your final reply and let Lead surface to user.

See: .codex/hooks/project-auditor-readonly.ps1
"@
}

# Any other tool (Agent, WebFetch, MCP tools, etc.) — deny. The auditor must
# not spawn subagents, fetch external resources, or invoke MCP servers.
Deny-Tool @"
project-auditor tool '$toolName' is denied — not on the read-only whitelist.

Allowed tools: Read, Grep, Glob, Bash (curl-only).
Denied: Write, Edit, NotebookEdit, Agent, WebFetch, and all MCP tools.

The project-auditor audits projects read-only and returns findings as its final
reply (or via a Lead-mediated API write). It does not spawn other agents,
reach the web, or call MCP servers. If the audit requires one of these, the
scope has changed and Lead must redesign the agent surface.

See: .codex/hooks/project-auditor-readonly.ps1
"@
