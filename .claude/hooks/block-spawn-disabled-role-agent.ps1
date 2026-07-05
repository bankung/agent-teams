<#
PreToolUse hook: block an Agent spawn when the session-bound project has disabled the
target ROLE (config.enabled_roles) or the target AGENT (config.agent_settings).

CONTEXT (Kanban #2769, 2026-07-05):
Two per-project spawn gates were Lead-DISCIPLINE only (documented in .claude/teams/dev.md):
  - config.enabled_roles: int[]        (#7)    — TaskRole-code whitelist.
  - config.agent_settings[name].enabled: bool  (#1018) — per-agent on/off toggle.
A non-compliant or headless Lead could still spawn a disabled role/agent. This hook
enforces BOTH at runtime by delegating the decision to the server authority:
  GET /api/projects/{id}/spawn-check?agent=<subagent_type>
which resolves agent -> TaskRole code (AGENT_ROLE_CODE in api/src/constants.py — the ONE
source of that map, so it can't drift into PowerShell) and applies both gates, backfill-safe.

WHY a thin hook + server authority: keep the role map next to TaskRole (Python, tested); the
hook only forwards subagent_type and enforces the verdict. Mirrors block-spawn-on-killed-project.ps1.

FAILURE MODES (fail-open — this is a discipline gate, not the security-critical kill gate;
bricking Lead on hook misbehavior is worse than allowing a spawn the server would also serve):
  - payload malformed / not an Agent call / no subagent_type -> exit 0 (neutral allow)
  - no per-session project binding (session needs re-bind)   -> stderr WARN + exit 0
  - endpoint unreachable / non-2xx / non-JSON                -> stderr WARN + exit 0
  - response.allowed != false (true / absent / anything)     -> exit 0 (neutral allow)
  - response.allowed == false                                -> deny JSON + exit 2
#>

$ErrorActionPreference = 'Stop'

# Read stdin payload — neutral exit if anything malformed.
try {
    $payloadRaw = [Console]::In.ReadToEnd()
    if (-not $payloadRaw) { exit 0 }
    $payload = $payloadRaw | ConvertFrom-Json
} catch {
    exit 0
}

# Only act on Agent tool calls. Other tool surfaces are covered by other hooks.
if ($payload.tool_name -ne 'Agent') { exit 0 }

# The agent being spawned. No subagent_type -> nothing to gate (neutral allow).
$agent = $payload.tool_input.subagent_type
if (-not $agent) { exit 0 }

# Resolve project PER SESSION (#2692): a miss -> gate inactive (fail-open), never another
# session's project. _shared.ps1 gives Get-ProjectId + Emit-Decision.
. (Join-Path $PSScriptRoot '_shared.ps1')
$projectId = Get-ProjectId -SessionId $payload.session_id
if ($null -eq $projectId) {
    [Console]::Error.WriteLine("WARN: no per-session project binding (may need re-bind); spawn role/agent gate inactive")
    exit 0
}

# Delegate the allow/deny decision to the server authority. Fail-open on any infra error —
# the endpoint (and the Lead-discipline documented in dev.md) remain the backstops.
$agentEnc = [uri]::EscapeDataString([string]$agent)
$apiUrl = "http://localhost:8456/api/projects/$projectId/spawn-check?agent=$agentEnc"
$body = $null
try {
    $body = & curl.exe --silent --max-time 3 --fail -H "X-Project-Id: $projectId" $apiUrl 2>$null
} catch {
    [Console]::Error.WriteLine("WARN: spawn-check unreachable (id=$projectId agent=$agent); gate inactive")
    exit 0
}
if ($LASTEXITCODE -ne 0 -or -not $body) {
    [Console]::Error.WriteLine("WARN: spawn-check non-2xx/empty (id=$projectId agent=$agent); gate inactive")
    exit 0
}

$resp = $null
try {
    $resp = $body | ConvertFrom-Json
} catch {
    [Console]::Error.WriteLine("WARN: spawn-check non-JSON (id=$projectId agent=$agent); gate inactive")
    exit 0
}

# ONLY an explicit allowed=false denies. true / absent / anything-else -> neutral allow
# ($null -ne $false and $true -ne $false both fall through to exit 0).
if ($resp.allowed -ne $false) { exit 0 }

$reasonText = if ($resp.reason) { [string]$resp.reason } else { "agent '$agent' is not permitted for project id=$projectId" }

$msg = @"
Agent spawn blocked: '$agent' is not permitted for session-bound project id=$projectId.

  reason : $reasonText

This is a per-project spawn gate (Kanban #7 config.enabled_roles / #1018 config.agent_settings),
now runtime-enforced (#2769). To allow it: enable the agent in Settings -> Agents, add its role
code to the project's config.enabled_roles, or spawn an allowed agent instead.

If you are working on a DIFFERENT project this session, the binding is stale -> re-run /zb-bind.

See: .claude/hooks/block-spawn-disabled-role-agent.ps1
"@

Emit-Decision -Decision 'deny' -Reason $msg
exit 2
