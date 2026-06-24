<#
PreToolUse hook: block Agent spawn when project is marked killed.

CONTEXT (Kanban #1209, 2026-05-18):
Lead bootstrap step 5 persists the active project's id to `_runtime/lead_project_id.txt`
(single integer, no surrounding whitespace). This file is the contract read by this hook.
On every Agent spawn, we verify the project's `is_killed` state via `GET /api/projects/<id>`
and deny the spawn if the project is killed.

WHY:
During context compaction or multi-session environments, session-scoped active-project
binding can drift. The hook fails open (with stderr WARN) if the file is missing, so
Lead-direct work continues, but the spawn-block layer is INACTIVE until the file is written.
This prevents accidental agent spawns against killed projects.

FAILURE MODES:
- File missing / corrupted → fails open with WARN; spawn proceeds (Lead-direct work unaffected).
- API connection error → fails open with WARN; spawn proceeds (safety > blockade).
- Project is_killed=true → deny spawn; return error message to user.

IMPLEMENTATION:
Read `_runtime/lead_project_id.txt`, parse the integer id, read `is_killed` from the
shared Lever B cache (Invoke-CachedPolicyFetch in _shared.ps1; live GET only on cache
miss / >60s), deny if true. On is_killed=true a supplementary GET fetches the kill
metadata for the message. Sharing the cache drops the dedicated per-spawn GET (R2/#2541).
#>


# Block PreToolUse(Agent) spawns when the session-bound project is killed (is_killed=true).
#
# Kanban #1209 AC#2(d) — layer-2 defense complementing the API gate. The primary defense
# is POST /api/tasks returning 423 Locked when the bound project is killed; this hook
# adds an earlier checkpoint so Lead can't even start a specialist subagent under a
# killed project.
#
# Design — LOCKED as D6 = Option (i): file-path contract.
#   Lead writes the session's bound project_id at bootstrap to:
#     _runtime/lead_project_id.txt   (single integer, no surrounding whitespace required)
#   This hook reads that file, GETs /api/projects/{id}, checks is_killed, denies if true.
#
# Fail-open semantics (intentional — bricking Lead on hook misbehavior is worse than
# allowing a spawn under killed state, since the API gate still catches downstream writes):
#   - File missing                  -> stderr WARN + exit 0 (neutral allow)
#   - File contains junk            -> stderr WARN + exit 0
#   - API unreachable / non-2xx     -> stderr WARN + exit 0
#   - Response not JSON / no field  -> stderr WARN + exit 0
#   - is_killed absent or false     -> exit 0 (neutral allow, no output)
#   - is_killed = true              -> deny JSON + exit 2
#
# Lead-side responsibility: bootstrap section of CLAUDE.md must write the project_id
# to _runtime/lead_project_id.txt right after step 4 ("Announce the binding"). The
# file is per-machine runtime state — gitignored except for the .gitkeep marker.

$ErrorActionPreference = 'Stop'

# Read stdin payload — neutral exit if anything malformed.
try {
    $payloadRaw = [Console]::In.ReadToEnd()
    if (-not $payloadRaw) { exit 0 }
    $payload = $payloadRaw | ConvertFrom-Json
} catch {
    exit 0
}

# Only act on Agent tool calls. The Bash / Edit / Write surface is covered by other hooks.
$toolName = $payload.tool_name
if ($toolName -ne 'Agent') { exit 0 }

# Resolve project PER SESSION (#2692): the binding belongs to THIS session only;
# a miss -> hook inactive (fail-open), never another session's project. No global
# fallback (a global value could be a different concurrent session's project).
. (Join-Path $PSScriptRoot '_shared.ps1')
$projectId = Get-ProjectId -SessionId $payload.session_id
if ($null -eq $projectId) {
    [Console]::Error.WriteLine("WARN: no per-session project binding (session may need re-bind); spawn block hook inactive")
    exit 0
}

# R2 (#2541): consult the SHARED Lever B cache (the same _runtime cache the Bash gate
# warms) for the kill flag instead of a dedicated per-spawn GET. The common path (project
# not killed) is a cache hit -> no curl. Fail-open on any infra error — the PRIMARY defense
# is POST /api/tasks returning 423 on a killed project; this hook is layer-2.
#
# Staleness note: the cache TTL is 60s, so a fresh kill can take up to 60s to gate spawns
# here; the live 423 API gate still blocks the spawned agent's first write immediately.
# (_shared.ps1 already dot-sourced above for Get-ProjectId.)

$fetch = Invoke-CachedPolicyFetch -ProjectId $projectId
if ($fetch.failed) {
    [Console]::Error.WriteLine("WARN: project fetch failed for id=$projectId ; spawn block hook inactive")
    exit 0
}
# is_killed false OR absent -> neutral allow (no curl on the common path).
if (-not $fetch.is_killed) { exit 0 }

# is_killed = true (rare) -> one live GET for the full kill metadata to build the message.
# If this supplementary fetch fails, we STILL deny (the cached kill flag is authoritative);
# the message just degrades to generic placeholders via the fallbacks below.
$apiUrl = "http://localhost:8456/api/projects/$projectId"
$project = $null
try {
    $body = & curl.exe --silent --max-time 3 --fail -H "X-Project-Id: $projectId" $apiUrl 2>$null
    if ($LASTEXITCODE -eq 0 -and $body) { $project = $body | ConvertFrom-Json }
} catch { $project = $null }

# is_killed = true -> deny with informative reason.
$killedAt     = if ($project.killed_at)     { $project.killed_at }     else { '(unknown)' }
$killedReason = if ($project.killed_reason) { $project.killed_reason } else { '(no reason recorded)' }
if ($killedReason.Length -gt 200) {
    $killedReason = $killedReason.Substring(0, 200) + '...'
}
$projectName = if ($project.name) { $project.name } else { "id=$projectId" }

$reason = @"
Agent spawn blocked: session-bound project '$projectName' (id=$projectId) is in KILLED state.

  killed_at     : $killedAt
  killed_reason : $killedReason

Killed projects reject new work to preserve incident state and prevent further mutations.
If you need to spawn a specialist under this project, the user must revive it first via
POST /api/projects/$projectId/revive (Kanban #1209 — kill switch).

If you are working on a DIFFERENT project this session, the bootstrap project binding is
stale or missing. Re-bind by re-running the bootstrap flow ('Which project are we working
on?'); the per-session binding _runtime/lead_project_id_<session_id>.txt will be written.

See: .claude/hooks/block-spawn-on-killed-project.ps1
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

