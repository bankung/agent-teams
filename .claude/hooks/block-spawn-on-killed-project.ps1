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
Read `_runtime/lead_project_id.txt`, parse the integer id, call FastAPI
`GET /api/projects/<id>`, check `is_killed` flag, deny if true.
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

# Resolve repo root from this script's location: .claude/hooks/<script>.ps1 -> ..\..
$repoRoot   = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$projectIdFile = Join-Path $repoRoot '_runtime\lead_project_id.txt'

if (-not (Test-Path $projectIdFile)) {
    [Console]::Error.WriteLine("WARN: _runtime/lead_project_id.txt not found; spawn block hook inactive - Lead bootstrap may be incomplete")
    exit 0
}

# Parse the file. Must be a positive integer; anything else = fail-open.
$raw = (Get-Content -Raw -Path $projectIdFile).Trim()
$projectId = 0
if (-not [int]::TryParse($raw, [ref]$projectId)) {
    [Console]::Error.WriteLine("WARN: _runtime/lead_project_id.txt contains non-integer content '$raw'; spawn block hook inactive")
    exit 0
}
if ($projectId -le 0) {
    [Console]::Error.WriteLine("WARN: _runtime/lead_project_id.txt project_id '$projectId' is not positive; spawn block hook inactive")
    exit 0
}

# GET /api/projects/{id}. curl.exe is used (not Invoke-WebRequest) because it matches
# every other hook in this repo and avoids PowerShell 5.1's stderr-wrapping quirk on
# native commands. --silent suppresses progress; --max-time bounds the wait so a hung
# API doesn't stall the Agent spawn for the full 5s timeout.
$apiUrl = "http://localhost:8456/api/projects/$projectId"
try {
    $body = & curl.exe --silent --max-time 3 --fail -H "X-Project-Id: $projectId" $apiUrl 2>$null
} catch {
    [Console]::Error.WriteLine("WARN: curl invocation failed for $apiUrl ; spawn block hook inactive")
    exit 0
}

if ($LASTEXITCODE -ne 0 -or -not $body) {
    [Console]::Error.WriteLine("WARN: API unreachable or non-2xx at $apiUrl (curl exit $LASTEXITCODE); spawn block hook inactive")
    exit 0
}

try {
    $project = $body | ConvertFrom-Json
} catch {
    [Console]::Error.WriteLine("WARN: API response not valid JSON; spawn block hook inactive")
    exit 0
}

# is_killed missing OR false -> neutral allow.
if (-not $project.PSObject.Properties.Name -contains 'is_killed') {
    [Console]::Error.WriteLine("WARN: API response missing is_killed field; spawn block hook inactive")
    exit 0
}
if (-not $project.is_killed) { exit 0 }

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
stale. Re-bind by re-running the bootstrap flow ('Which project are we working on?') and
the new project_id will be written to _runtime/lead_project_id.txt.

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

