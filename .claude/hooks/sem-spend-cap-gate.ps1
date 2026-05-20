# DRAFT (Kanban #1269 AC4) — SEM team spend-cap gate.
# PreToolUse on Edit + Write for sem-campaign-lead / google-ads-specialist /
# meta-ads-specialist / platform-ads-coordinator. Soft requires-attention nudge
# when a proposed budget edit exceeds a hardcoded daily/monthly threshold.
# Pre-flight tripwire only — authoritative cap lives in services/budget_gate.py
# (Kanban #1194 — Phase 1 cost cap). This hook never blocks via hard deny; it
# always either emits allow or requires-attention.
#
# DRAFT ONLY — do NOT install. Lead handles agent file + .claude/hooks/ placement
# per feedback_claude_dir_humans_only.md.
#
# Registration snippet (Lead writes into .claude/agents/<agent>.md frontmatter):
#   hooks:
#     PreToolUse:
#       - matcher: Edit|Write
#         hooks:
#           - type: command
#             command: powershell -NoProfile -ExecutionPolicy Bypass -File .claude/hooks/sem-spend-cap-gate.ps1
#
# Fail-open on any internal error (the gate is a nudge, not a halt).

$ErrorActionPreference = 'Continue'

# Constants: future per-project override via projects.budget_daily_usd GET API
# call (TODO Kanban #?). For now these are hardcoded sentinels.
$DailyCapUsd   = 5000
$MonthlyCapUsd = 50000

function Emit-Decision {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('allow', 'requires-attention')][string]$Decision,
        [string]$Reason = ''
    )
    $payload = @{
        hookSpecificOutput = @{
            hookEventName      = "PreToolUse"
            permissionDecision = $Decision
        }
    }
    if ($Reason) { $payload.hookSpecificOutput.permissionDecisionReason = $Reason }
    Write-Output ($payload | ConvertTo-Json -Compress -Depth 6)
}

function Fail-Open {
    param([string]$WarnMsg)
    [Console]::Error.WriteLine("WARN: sem-spend-cap-gate: $WarnMsg ; failing open (allow)")
    Emit-Decision -Decision 'allow' -Reason "sem-spend-cap-gate fail-open: $WarnMsg"
    exit 0
}

# Read stdin payload --------------------------------------------------------
try {
    $payloadRaw = [Console]::In.ReadToEnd()
    if (-not $payloadRaw) { Emit-Decision -Decision 'allow'; exit 0 }
    $payload = $payloadRaw | ConvertFrom-Json
} catch {
    Fail-Open -WarnMsg "payload not valid JSON: $($_.Exception.Message)"
}

$toolName = $payload.tool_name
if ($toolName -ne 'Edit' -and $toolName -ne 'Write') {
    Emit-Decision -Decision 'allow'
    exit 0
}

# Agent-name scope (multi-key fallback because payload shape varies).
$agentName = $null
foreach ($key in @('agent_name', 'subagent_type', 'agent', 'agentName')) {
    if ($payload.PSObject.Properties.Name -contains $key -and $payload.$key) {
        $agentName = [string]$payload.$key
        break
    }
}
$scopedAgents = @('sem-campaign-lead', 'google-ads-specialist', 'meta-ads-specialist', 'platform-ads-coordinator')
if ($agentName -and ($scopedAgents -notcontains $agentName)) {
    Emit-Decision -Decision 'allow' -Reason "sem-spend-cap-gate: agent '$agentName' out of scope"
    exit 0
}

# Extract proposed content from Write.content or Edit.new_string ------------
$content = ''
$toolInput = $payload.tool_input
if (-not $toolInput) { Emit-Decision -Decision 'allow'; exit 0 }

if ($toolName -eq 'Write' -and $toolInput.PSObject.Properties.Name -contains 'content') {
    $content = [string]$toolInput.content
} elseif ($toolName -eq 'Edit' -and $toolInput.PSObject.Properties.Name -contains 'new_string') {
    $content = [string]$toolInput.new_string
}

if (-not $content) { Emit-Decision -Decision 'allow'; exit 0 }

# ---------------------------------------------------------------------------
# Budget extraction — heuristic, not authoritative.
# Patterns recognized (case-insensitive):
#   $N / $N.NN / $N,NNN(.NN)         -> dollar-prefixed
#   N USD / N.NN USD                  -> trailing-USD
#   daily_usd: N                      -> field-anchored
#   monthly_usd: N                    -> field-anchored
#   daily budget: $N                  -> keyword-anchored
#   monthly budget: $N                -> keyword-anchored
#   campaign budget: $N               -> keyword-anchored
#
# The "scope" of each match (daily vs monthly vs unscoped) is tracked so we
# can compare against the right cap. Unscoped matches default to daily.
# ---------------------------------------------------------------------------
$dailyTotal   = 0.0
$monthlyTotal = 0.0

function Parse-Amount {
    param([string]$Raw)
    # Strip $ , whitespace, USD suffix.
    $clean = $Raw -replace '[\$,\s]', '' -replace '(?i)usd', ''
    [double]$parsed = 0
    if ([double]::TryParse($clean, [ref]$parsed)) { return $parsed }
    return $null
}

# Scoped-monthly first (more specific): "monthly budget: $N" / "monthly_usd: N"
$rxMonthly = [regex]::new('(?i)(?:monthly[_\s]budget|monthly_usd)\s*[:=]?\s*\$?\s*([0-9][0-9,]*(?:\.\d+)?)\s*(?:usd)?', 'IgnoreCase')
foreach ($m in $rxMonthly.Matches($content)) {
    $v = Parse-Amount $m.Groups[1].Value
    if ($v -ne $null) { $monthlyTotal += $v }
}

# Scoped-daily: "daily budget: $N" / "daily_usd: N" / "campaign budget: $N"
$rxDaily = [regex]::new('(?i)(?:daily[_\s]budget|daily_usd|campaign[_\s]budget)\s*[:=]?\s*\$?\s*([0-9][0-9,]*(?:\.\d+)?)\s*(?:usd)?', 'IgnoreCase')
foreach ($m in $rxDaily.Matches($content)) {
    $v = Parse-Amount $m.Groups[1].Value
    if ($v -ne $null) { $dailyTotal += $v }
}

# Unscoped fallback: bare $N or N USD that wasn't already captured by the
# scoped rules above. We strip the scoped-match spans from a working copy
# then run the bare-amount regex on the remainder.
$residue = $content
foreach ($rx in @($rxMonthly, $rxDaily)) {
    foreach ($m in $rx.Matches($content)) {
        $residue = $residue.Replace($m.Value, ' ')
    }
}
$rxBare = [regex]::new('(?i)(?:\$\s*([0-9][0-9,]*(?:\.\d+)?)|([0-9][0-9,]*(?:\.\d+)?)\s*usd\b)', 'IgnoreCase')
foreach ($m in $rxBare.Matches($residue)) {
    $cap = if ($m.Groups[1].Success) { $m.Groups[1].Value } else { $m.Groups[2].Value }
    $v = Parse-Amount $cap
    if ($v -ne $null) { $dailyTotal += $v }
}

# Threshold check -----------------------------------------------------------
$dailyExceeded   = $dailyTotal   -gt $DailyCapUsd
$monthlyExceeded = $monthlyTotal -gt $MonthlyCapUsd

if (-not ($dailyExceeded -or $monthlyExceeded)) {
    Emit-Decision -Decision 'allow' -Reason "sem-spend-cap-gate: daily=$dailyTotal monthly=$monthlyTotal within caps"
    exit 0
}

$reason = @"
sem-spend-cap-gate: proposed budget exceeds soft threshold.

  detected daily total   = `$$dailyTotal   (cap `$$DailyCapUsd)
  detected monthly total = `$$monthlyTotal (cap `$$MonthlyCapUsd)

This is a pre-flight nudge, not the authoritative cap. The real budget gate
runs server-side in services/budget_gate.py (Kanban #1194). Confirm the
amounts are intentional before proceeding. If the values are correct and
have been approved by the operator, re-issue the Edit/Write — this hook is
a one-shot tripwire; subsequent invocations on the same content still emit
requires-attention until the source content drops below the threshold.

Future work: per-project override via GET /api/projects/<id> reading
budget_daily_usd / budget_monthly_usd fields (TODO Kanban #?).
"@

Emit-Decision -Decision 'requires-attention' -Reason $reason
exit 0
