# Pattern 5 PreToolUse hook — approval_policies harness enforcement.
#
# Reads the session-bound project's `approval_policies` JSONB (Kanban #953) and
# emits a Codex PreToolUse decision (allow / deny / ask) for tool calls
# that match the operator-codified rules.
#
# Kanban #1274 (impl from #1205 / mode-b-authorization-chain.md section 2.5).
#
# Design — fail-OPEN to `ask`:
#   The Pattern 5 design doc treats this hook as the FIRST layer in a decision
#   chain. If we can't reach the API, can't find the bound project, or hit any
#   transient error — we MUST NOT auto-approve (would defeat the gate) and
#   MUST NOT auto-deny (would brick Lead-direct on infra glitches). The right
#   fail-open mode is `ask` — the harness then falls through to its own
#   classifier / user-prompt path. Stderr WARN explains the cause for audit.
#
# Decision precedence within rule evaluation (first-match wins, in list order):
#   1. matched rule with action=auto_deny       → permissionDecision=deny
#   2. matched rule with action=requires_attention → permissionDecision=ask
#   3. matched rule with action=auto_approve    → permissionDecision=allow
#   4. no rule matched                          → permissionDecision=ask
#
# Rule shape (extension of the existing approval_policies JSONB; backward-compatible
# with the worker-side label-only consumer in services/approval_evaluator.py):
#
#   {
#     "rules": [
#       {
#         "name": "linkedin post pre-approved",
#         "match": {
#           "tool_name": "WebFetch",                              # exact match (optional)
#           "target_url_pattern": "^https://linkedin\\.com/.*",   # PowerShell regex on tool_input.url / .command (optional)
#           "content_predicate": "post"                            # regex on serialized tool_input (optional)
#         },
#         "action": "auto_approve",                                # one of auto_approve|auto_deny|requires_attention
#         "reason": "linkedin posts pre-approved by operator policy"
#       },
#       ...
#     ]
#   }
#
# A rule matches when EVERY present matcher key is satisfied. A rule with no
# matcher keys (empty `match` object) matches everything — operator should
# avoid that shape unless they explicitly want a catch-all.
#
# Hook tool-call surface:
#   - WebFetch                       → reads tool_input.url
#   - mcp__claude-in-chrome__*       → reads tool_input.url / tool_input.* (best-effort)
#   - Bash with curl command         → extracts URL from the curl command string
#
# Lead promotes from _scratch/ to .codex/hooks/approval-policies-gate.ps1
# (operator-only zone). Settings.json wiring is a separate operator step —
# see _scratch/draft-settings-additions.md.

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
    [Console]::Error.WriteLine("WARN: approval-policies-gate: $WarnMsg ; falling through to ask")
    Emit-Decision -Decision 'ask' -Reason "approval-policies-gate fallthrough: $WarnMsg"
    exit 0
}

# Read stdin payload --------------------------------------------------------

try {
    $payloadRaw = [Console]::In.ReadToEnd()
    if (-not $payloadRaw) { Fail-Open-Ask -WarnMsg 'empty PreToolUse payload' }
    $payload = $payloadRaw | ConvertFrom-Json
} catch {
    Fail-Open-Ask -WarnMsg "payload not valid JSON: $($_.Exception.Message)"
}

$toolName = $payload.tool_name
if (-not $toolName) { Fail-Open-Ask -WarnMsg 'tool_name missing from payload' }

# Locate bound project_id ---------------------------------------------------

# Test override — point hook at a fixture file instead of the runtime path
# (used exclusively by the smoke test; production paths never set this).
$projectIdFile = $env:APPROVAL_POLICIES_GATE_PROJECT_FILE
if (-not $projectIdFile) {
    $repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..\..')
    $projectIdFile = Join-Path $repoRoot '_runtime\lead_project_id.txt'
}

if (-not (Test-Path $projectIdFile)) {
    Fail-Open-Ask -WarnMsg "_runtime/lead_project_id.txt not found at $projectIdFile"
}

$raw = (Get-Content -Raw -Path $projectIdFile).Trim()
$projectId = 0
if (-not [int]::TryParse($raw, [ref]$projectId) -or $projectId -le 0) {
    Fail-Open-Ask -WarnMsg "_runtime/lead_project_id.txt invalid content '$raw'"
}

# Fetch approval_policies ---------------------------------------------------

# Test stub — APPROVAL_POLICIES_GATE_POLICY_FILE overrides the live HTTP call
# with a local fixture JSON file containing the projects-row shape. Lets the
# smoke test exercise every rule shape without a running API.
$policyFile = $env:APPROVAL_POLICIES_GATE_POLICY_FILE
$projectJson = $null

if ($policyFile) {
    if (-not (Test-Path $policyFile)) {
        Fail-Open-Ask -WarnMsg "policy fixture file not found: $policyFile"
    }
    try {
        $projectJson = (Get-Content -Raw -Path $policyFile) | ConvertFrom-Json
    } catch {
        Fail-Open-Ask -WarnMsg "policy fixture not valid JSON: $($_.Exception.Message)"
    }
} else {
    $apiUrl = "http://localhost:8456/api/projects/$projectId"
    try {
        $body = & curl.exe --silent --max-time 3 --fail -H "X-Project-Id: $projectId" $apiUrl 2>$null
    } catch {
        Fail-Open-Ask -WarnMsg "curl invocation failed for $apiUrl"
    }
    if ($LASTEXITCODE -ne 0 -or -not $body) {
        Fail-Open-Ask -WarnMsg "API unreachable or non-2xx at $apiUrl (curl exit $LASTEXITCODE)"
    }
    try {
        $projectJson = $body | ConvertFrom-Json
    } catch {
        Fail-Open-Ask -WarnMsg "API response not valid JSON"
    }
}

# Pull rules. Null / missing approval_policies / missing rules array = no
# matching policies → fall through to ask (same effect as zero rules).
$policies = $projectJson.approval_policies
if ($null -eq $policies) {
    Emit-Decision -Decision 'ask' -Reason 'approval-policies-gate: project has no approval_policies set'
    exit 0
}
$rules = $policies.rules
if ($null -eq $rules -or $rules.Count -eq 0) {
    Emit-Decision -Decision 'ask' -Reason 'approval-policies-gate: approval_policies.rules empty'
    exit 0
}

# Extract URL + serialized-content from tool_input ---------------------------

$toolInput = $payload.tool_input
$targetUrl = $null
$serializedContent = ''

if ($toolInput) {
    # Best-effort URL extraction: WebFetch + most MCP tools use `url`.
    if ($toolInput.PSObject.Properties.Name -contains 'url') {
        $targetUrl = [string]$toolInput.url
    }
    # Bash: pull the first http(s) URL out of the command string. We don't
    # try to be clever about quoting — a substring regex is enough for the
    # matcher's purposes (the rule itself supplies the precise regex).
    elseif ($toolName -eq 'Bash' -and $toolInput.PSObject.Properties.Name -contains 'command') {
        $cmd = [string]$toolInput.command
        $urlMatch = [regex]::Match($cmd, '(?i)https?://[^\s"''<>]+')
        if ($urlMatch.Success) { $targetUrl = $urlMatch.Value }
    }

    # Serialize the entire tool_input for content_predicate matching.
    try {
        $serializedContent = $toolInput | ConvertTo-Json -Compress -Depth 6
    } catch {
        $serializedContent = [string]$toolInput
    }
}

# Rule evaluation -----------------------------------------------------------

function Test-Rule {
    param($Rule, [string]$ToolName, [string]$Url, [string]$Content)
    $match = $Rule.match
    if ($null -eq $match) { return $false }    # malformed rule → never match

    # tool_name (exact string equality).
    if ($match.PSObject.Properties.Name -contains 'tool_name') {
        $want = [string]$match.tool_name
        if ($want -and $want -ne $ToolName) { return $false }
    }
    # target_url_pattern (regex on extracted URL).
    if ($match.PSObject.Properties.Name -contains 'target_url_pattern') {
        $pat = [string]$match.target_url_pattern
        if ($pat) {
            if (-not $Url) { return $false }
            try {
                if (-not [regex]::IsMatch($Url, $pat)) { return $false }
            } catch {
                # bad regex → treat as non-match (don't crash the hook)
                return $false
            }
        }
    }
    # content_predicate (regex on serialized tool_input).
    if ($match.PSObject.Properties.Name -contains 'content_predicate') {
        $pat = [string]$match.content_predicate
        if ($pat) {
            try {
                if (-not [regex]::IsMatch($Content, $pat)) { return $false }
            } catch {
                return $false
            }
        }
    }
    return $true
}

foreach ($rule in $rules) {
    if (Test-Rule -Rule $rule -ToolName $toolName -Url $targetUrl -Content $serializedContent) {
        $action = [string]$rule.action
        $ruleName = if ($rule.name) { [string]$rule.name } else { '(unnamed rule)' }
        $ruleReason = if ($rule.reason) { [string]$rule.reason } else { "matched rule '$ruleName'" }

        switch ($action) {
            'auto_approve' {
                Emit-Decision -Decision 'allow' -Reason "approval-policies-gate: $ruleName — $ruleReason"
                exit 0
            }
            'auto_deny' {
                Emit-Decision -Decision 'deny' -Reason "approval-policies-gate: $ruleName — $ruleReason"
                exit 0
            }
            'requires_attention' {
                Emit-Decision -Decision 'ask' -Reason "approval-policies-gate: $ruleName — $ruleReason"
                exit 0
            }
            default {
                # unknown action → don't allow on ambiguous semantics; treat as ask + warn
                [Console]::Error.WriteLine("WARN: approval-policies-gate: rule '$ruleName' has unknown action '$action' ; treating as ask")
                Emit-Decision -Decision 'ask' -Reason "approval-policies-gate: $ruleName has unknown action '$action'"
                exit 0
            }
        }
    }
}

# No rule matched. Fall through to harness's own decision path.
Emit-Decision -Decision 'ask' -Reason 'approval-policies-gate: no rule matched'
exit 0
