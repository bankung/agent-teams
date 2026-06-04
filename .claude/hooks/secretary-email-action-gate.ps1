# PreToolUse hook — secretary email-action MCP-path backstop (Kanban #1585).
#
# CONTEXT (operator decision 2026-06-04, channel = "API + hook backstop"):
#   The PRIMARY enforcement for secretary email MUTATIONS is server-side: the
#   API tier gate on /api/tools/email/* — _enforce_tool_grant_or_403 (#1799,
#   role) THEN _enforce_operator_tier_or_403 (#1859, operator-proof). That path
#   is fully gated + audited.
#
#   This hook is DEFENSE-IN-DEPTH for the *other* channel: the Chrome MCP
#   (browser) path. The API gates do nothing about an agent operating Gmail /
#   Outlook directly in the browser (which would bypass them). This hook stops a
#   NON-secretary agent from doing that.
#
# SCOPE + KNOWN LIMIT (honest):
#   Wired on the `mcp__Claude_in_Chrome__.*` matcher. It detects an email-domain
#   target in the tool_input (navigation URL is the reliable chokepoint to REACH
#   webmail). It CANNOT classify an individual in-page click (mcp computer/click
#   carries screen coords, not a typed email action) — that fine granularity is
#   deliberately the API path's job, not this hook's. So this gate's real teeth
#   are: "a non-secretary agent cannot navigate to / drive Gmail-Outlook in the
#   browser." Reaching webmail requires a navigate, which is gated.
#
# DECISION (fail-OPEN to `ask`, mirroring approval-policies-gate.ps1):
#   - tool_input has no email-domain target           -> allow (not our surface)
#   - email-domain action, calling agent is a secretary* agent whose
#       .claude/agents/<agent_type>.md frontmatter declares email_actions: enabled
#                                                      -> allow
#   - email-domain action, any OTHER named subagent    -> deny
#   - email-domain action, no agent_type (main session / Lead-direct)
#                                                      -> ask (operator decides;
#         the Lead-direct send workaround is operator-HITL-confirmed anyway)
#   - any error / cannot determine                     -> ask (never silently
#         allow an email action, never brick on a transient glitch)
#
# agent_type / agent_id are present in the PreToolUse stdin payload only when the
# call originates from a subagent (Claude Code subagent hook contract). Absent =
# main session.

$ErrorActionPreference = 'Stop'

# Shared helpers ------------------------------------------------------------

function Emit-Decision {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('allow', 'deny', 'ask')][string]$Decision,
        [Parameter(Mandatory = $true)][string]$Reason
    )
    $out = @{
        hookSpecificOutput = @{
            hookEventName            = "PreToolUse"
            permissionDecision       = $Decision
            permissionDecisionReason = $Reason
        }
    } | ConvertTo-Json -Compress -Depth 6
    Write-Output $out
}

function Fail-Open-Ask {
    param([string]$WarnMsg)
    [Console]::Error.WriteLine("WARN: secretary-email-action-gate: $WarnMsg ; falling through to ask")
    Emit-Decision -Decision 'ask' -Reason "secretary-email-action-gate fallthrough: $WarnMsg"
    exit 0
}

# Email-domain matcher. Webmail surfaces secretary uses (Gmail + Outlook/Hotmail).
# Lists email-SPECIFIC hosts only (NOT bare google.com / live.com) to avoid
# over-matching non-mail traffic. Covers Gmail (mail.google.com, inbox.google.com,
# gmail.com, googlemail.com) + Microsoft webmail (outlook.com bare, outlook.live.com,
# outlook.office.com, outlook.office365.com, mail.live.com, hotmail.com).
$emailDomainPattern = '(?i)(mail\.google\.com|inbox\.google\.com|gmail\.com|googlemail\.com|outlook\.(com|live\.com|office\.com|office365\.com)|mail\.live\.com|hotmail\.com)'

# Read stdin payload --------------------------------------------------------

try {
    $payloadRaw = [Console]::In.ReadToEnd()
    if (-not $payloadRaw -or -not $payloadRaw.Trim()) { Fail-Open-Ask -WarnMsg 'empty PreToolUse payload' }
    $payload = $payloadRaw | ConvertFrom-Json
} catch {
    Fail-Open-Ask -WarnMsg "payload not valid JSON: $($_.Exception.Message)"
}
# ConvertFrom-Json can return $null on whitespace-only input WITHOUT throwing —
# guard so a null/empty payload fails OPEN to ask, never falls through to allow.
if ($null -eq $payload) { Fail-Open-Ask -WarnMsg 'payload parsed to null' }

# Serialize tool_input + best-effort email-domain detection -----------------

$toolInput = $payload.tool_input
$serialized = ''
if ($toolInput) {
    try {
        $serialized = $toolInput | ConvertTo-Json -Compress -Depth 6
    } catch {
        $serialized = [string]$toolInput
    }
}

# Not an email-domain target -> outside this hook's surface -> allow.
if (-not ($serialized -match $emailDomainPattern)) {
    Emit-Decision -Decision 'allow' -Reason 'secretary-email-action-gate: no email-domain target (not this hook surface)'
    exit 0
}

# It IS an email-domain action. Identify the caller. -------------------------

$agentType = $null
if ($payload.PSObject.Properties.Name -contains 'agent_type') {
    $agentType = [string]$payload.agent_type
}

# No agent_type = main session (Lead-direct). The Lead-direct email send
# workaround is operator-HITL-confirmed; surface to the operator rather than
# auto-allow or auto-deny.
if (-not $agentType) {
    Emit-Decision -Decision 'ask' -Reason 'secretary-email-action-gate: email-domain action from main session (no agent_type) — operator confirm'
    exit 0
}

# Named subagent. Authorize ONLY if it is a secretary* agent whose frontmatter
# declares the email_actions signal (the AC2 allow-list source of truth).
$agentsDir = $env:SECRETARY_EMAIL_GATE_AGENTS_DIR
if (-not $agentsDir) {
    try {
        $repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..\..')
        $agentsDir = Join-Path $repoRoot '.claude\agents'
    } catch {
        Fail-Open-Ask -WarnMsg "cannot resolve agents dir: $($_.Exception.Message)"
    }
}

# Guard the agent_type before using it as a filename (defense vs a crafted
# agent_type with path separators).
if ($agentType -notmatch '^[A-Za-z0-9_-]+$') {
    Emit-Decision -Decision 'deny' -Reason "secretary-email-action-gate: email-domain action from agent with unexpected agent_type '$agentType' — denied"
    exit 0
}

$agentFile = Join-Path $agentsDir ("{0}.md" -f $agentType)

$isSecretaryName = ($agentType -match '^secretary')
$hasSignal = $false
if (Test-Path $agentFile) {
    try {
        $agentText = Get-Content -Raw -Path $agentFile
        # Frontmatter signal: a top-level `email_actions: enabled` key, matched
        # ONLY inside the leading YAML frontmatter block (--- ... ---), never the
        # agent's prose body (defense vs a stray mention in the system prompt).
        $fm = ''
        $fmMatch = [regex]::Match($agentText, '(?s)\A---\r?\n(.*?)\r?\n---')
        if ($fmMatch.Success) { $fm = $fmMatch.Groups[1].Value }
        if ($fm -match '(?im)^\s*email_actions:\s*enabled\s*$') { $hasSignal = $true }
    } catch {
        Fail-Open-Ask -WarnMsg "cannot read agent file ${agentFile}: $($_.Exception.Message)"
    }
}

if ($isSecretaryName -and $hasSignal) {
    Emit-Decision -Decision 'allow' -Reason "secretary-email-action-gate: '$agentType' is a secretary agent with email_actions:enabled — allow"
    exit 0
}

# Any other named agent doing email in the browser -> deny (it has no business
# operating the operator's mailbox; the secretary owns that workflow).
$why = if (-not $isSecretaryName) { "non-secretary agent '$agentType'" } else { "agent '$agentType' lacks email_actions:enabled frontmatter" }
Emit-Decision -Decision 'deny' -Reason "secretary-email-action-gate: email-domain browser action by $why — denied (use the gated /api/tools/email path)"
exit 0
