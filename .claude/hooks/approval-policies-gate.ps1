# approval-policies-gate.ps1 — standalone Layer-B policy gate (Lever B refactor).
#
# Used by:
#   - WebFetch PreToolUse matcher
#   - mcp__Claude_in_Chrome__.* PreToolUse matcher
#
# NOT used for Bash (replaced by pretooluse-bash-gate.ps1).
#
# ONLY CHANGE vs original: policy fetch uses the shared Invoke-CachedPolicyFetch
# (TTL-cached, no curl on cache hit) instead of an inline curl call.
# ALL other logic — rule evaluation, fail-open-to-ask, test overrides, Layer-B
# disjoint-namespace contract — is BYTE-FOR-BYTE identical to the original.
#
# Test overrides preserved (same env-var names):
#   APPROVAL_POLICIES_GATE_PROJECT_FILE  — fake lead_project_id.txt path
#   APPROVAL_POLICIES_GATE_POLICY_FILE   — fake project-row JSON (skips cache + HTTP)
#
# Promote path: _scratch/hooks-draft/approval-policies-gate.ps1
#            -> .claude/hooks/approval-policies-gate.ps1

$ErrorActionPreference = 'Stop'

# Dot-source shared helpers (same dir as this script when deployed to .claude/hooks/).
. (Join-Path $PSScriptRoot '_shared.ps1')

# ---------------------------------------------------------------------------
# Read stdin payload
# ---------------------------------------------------------------------------
$payloadRaw = $null
$payload    = $null
try {
    $payloadRaw = [Console]::In.ReadToEnd()
    if (-not $payloadRaw) { Fail-Open-Ask -WarnMsg 'empty PreToolUse payload' }
    $payload = $payloadRaw | ConvertFrom-Json
} catch {
    Fail-Open-Ask -WarnMsg "payload not valid JSON: $($_.Exception.Message)"
}

$toolName = $payload.tool_name
if (-not $toolName) { Fail-Open-Ask -WarnMsg 'tool_name missing from payload' }

# ---------------------------------------------------------------------------
# Resolve project_id
# ---------------------------------------------------------------------------
$projectId = Get-ProjectId
if ($null -eq $projectId) {
    Fail-Open-Ask -WarnMsg "_runtime/lead_project_id.txt not found or invalid"
}

# ---------------------------------------------------------------------------
# Fetch approval_policies (Lever B: cached)
# ---------------------------------------------------------------------------
$fetchResult = Invoke-CachedPolicyFetch -ProjectId $projectId
if ($fetchResult.failed) {
    Fail-Open-Ask -WarnMsg "API unreachable or non-2xx for project $projectId"
}

$policies = $fetchResult.policies

if ($null -eq $policies) {
    Emit-Decision -Decision 'allow' -Reason 'approval-policies-gate: project has no approval_policies set (#1614 default-allow)'
    exit 0
}

$rules = $policies.rules
if ($null -eq $rules -or $rules.Count -eq 0) {
    Emit-Decision -Decision 'allow' -Reason 'approval-policies-gate: approval_policies.rules empty (#1614 default-allow)'
    exit 0
}

# ---------------------------------------------------------------------------
# Extract URL + serialized content
# ---------------------------------------------------------------------------
$toolInput         = $payload.tool_input
$targetUrl         = $null
$serializedContent = ''

if ($toolInput) {
    if ($toolInput.PSObject.Properties.Name -contains 'url') {
        $targetUrl = [string]$toolInput.url
    } elseif ($toolName -eq 'Bash' -and $toolInput.PSObject.Properties.Name -contains 'command') {
        $urlMatch = [regex]::Match([string]$toolInput.command, '(?i)https?://[^\s"''<>]+')
        if ($urlMatch.Success) { $targetUrl = $urlMatch.Value }
    }
    try {
        $serializedContent = $toolInput | ConvertTo-Json -Compress -Depth 6
    } catch {
        $serializedContent = [string]$toolInput
    }
}

# ---------------------------------------------------------------------------
# Rule evaluation
# ---------------------------------------------------------------------------
$evalResult = Invoke-PolicyRuleEval `
    -Policies $policies `
    -ToolName $toolName `
    -TargetUrl $targetUrl `
    -SerializedContent $serializedContent

if ($evalResult.matched) {
    Emit-Decision -Decision $evalResult.decision -Reason $evalResult.reason
    exit 0
}

# No rule matched → default-allow (#1614).
Emit-Decision -Decision 'allow' -Reason 'approval-policies-gate: no rule matched (#1614 default-allow)'
exit 0
