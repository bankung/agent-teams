# DRAFT (Kanban #1269 AC4) — SEM team performance-dashboard audit hook.
# PostToolUse on Write for any SEM agent OR when file_path matches
# *sem-campaign-*.md, *sem-performance-*.md, *sem-report-*.md. Parses the
# written content for platform / budget / date-range and appends a TSV line
# to _scratch/sem-audit-trail.log. PostToolUse is informational — cannot block.
#
# DRAFT ONLY — do NOT install. Lead handles agent file + .claude/hooks/ placement
# per feedback_claude_dir_humans_only.md.
#
# Registration snippet (Lead writes into .claude/agents/<agent>.md frontmatter):
#   hooks:
#     PostToolUse:
#       - matcher: Write
#         hooks:
#           - type: command
#             command: powershell -NoProfile -ExecutionPolicy Bypass -File .claude/hooks/sem-performance-dashboard.ps1
#
# Audit log: _scratch/sem-audit-trail.log (future: POST /api/audit-events
# once that endpoint exists — same TODO as SEO ranking-report + data dashboard).
# Fail-soft on any parse error — emit allow + exit 0.

$ErrorActionPreference = 'Continue'

$AuditLog = '_scratch/sem-audit-trail.log'

function Emit-Allow {
    $out = @{
        hookSpecificOutput = @{
            hookEventName      = "PostToolUse"
            permissionDecision = "allow"
        }
    } | ConvertTo-Json -Compress -Depth 4
    Write-Output $out
}

function Fail-Soft {
    param([string]$WarnMsg)
    [Console]::Error.WriteLine("WARN: sem-performance-dashboard: $WarnMsg ; allowing (PostToolUse is informational)")
    Emit-Allow
    exit 0
}

try {
    $payloadRaw = [Console]::In.ReadToEnd()
    if (-not $payloadRaw) { Emit-Allow; exit 0 }
    $payload = $payloadRaw | ConvertFrom-Json
} catch {
    Fail-Soft -WarnMsg "payload not valid JSON: $($_.Exception.Message)"
}

$toolName = $payload.tool_name
if ($toolName -ne 'Write') { Emit-Allow; exit 0 }

# tool_response.success — treat null as success.
$success = $payload.tool_response.success
if ($success -eq $false) { Emit-Allow; exit 0 }

$filePath = $payload.tool_input.file_path
if (-not $filePath) { Emit-Allow; exit 0 }

# Agent-name (multi-key fallback). Either an SEM agent OR a path-pattern match
# triggers the audit; both checks are OR'd.
$agentName = $null
foreach ($key in @('agent_name', 'subagent_type', 'agent', 'agentName')) {
    if ($payload.PSObject.Properties.Name -contains $key -and $payload.$key) {
        $agentName = [string]$payload.$key
        break
    }
}
$semAgents = @('sem-campaign-lead', 'google-ads-specialist', 'meta-ads-specialist', 'platform-ads-coordinator')
$agentInScope = ($agentName -and ($semAgents -contains $agentName))

# Path-pattern match: *sem-campaign-*.md OR *sem-performance-*.md OR *sem-report-*.md
$pathInScope = ($filePath -match '(?i)sem-(campaign|performance|report)-[^/\\]*\.md$')

if (-not ($agentInScope -or $pathInScope)) {
    Emit-Allow
    exit 0
}

# Content extraction. Prefer tool_input.content; fallback to disk read.
$content = $payload.tool_input.content
if (-not $content) {
    try {
        if (Test-Path -LiteralPath $filePath) {
            $content = Get-Content -LiteralPath $filePath -Raw -ErrorAction Stop
        }
    } catch {
        Fail-Soft -WarnMsg "could not read file '$filePath': $($_.Exception.Message)"
    }
}

if (-not $content) {
    Fail-Soft -WarnMsg "no content to scan for '$filePath'"
}

# ---------------------------------------------------------------------------
# Extract platform / budget / date_range. All best-effort; missing fields go
# to '(unknown)'. The audit line is still emitted — operator can grep for
# unknowns later.
# ---------------------------------------------------------------------------
$platform = '(unknown)'
$rxPlatform = [regex]::Match($content, '(?im)^\s*(?:\*\*)?platform(?:\*\*)?\s*[:=]\s*(.+?)$')
if ($rxPlatform.Success) {
    $platform = $rxPlatform.Groups[1].Value.Trim() -replace '\*+$', ''
    $platform = $platform.Trim()
}

# Budget — reuse the same regex family as spend-cap-gate (scoped + bare).
$budgetUsd = 0.0
$rxBudget = [regex]::new('(?i)(?:daily[_\s]budget|monthly[_\s]budget|campaign[_\s]budget|daily_usd|monthly_usd|budget)\s*[:=]?\s*\$?\s*([0-9][0-9,]*(?:\.\d+)?)\s*(?:usd)?', 'IgnoreCase')
foreach ($m in $rxBudget.Matches($content)) {
    $raw = $m.Groups[1].Value -replace ',', ''
    [double]$parsed = 0
    if ([double]::TryParse($raw, [ref]$parsed)) { $budgetUsd += $parsed }
}
if ($budgetUsd -eq 0.0) {
    # Bare $-amount fallback.
    $rxBare = [regex]::new('\$\s*([0-9][0-9,]*(?:\.\d+)?)', 'IgnoreCase')
    foreach ($m in $rxBare.Matches($content)) {
        $raw = $m.Groups[1].Value -replace ',', ''
        [double]$parsed = 0
        if ([double]::TryParse($raw, [ref]$parsed)) { $budgetUsd += $parsed }
    }
}
$budgetStr = if ($budgetUsd -gt 0.0) { $budgetUsd.ToString() } else { '(unknown)' }

# Date range — accept "date_range:", "period:", or "from X to Y".
$dateRange = '(unknown)'
$rxDate = [regex]::Match($content, '(?im)^\s*(?:\*\*)?(?:date[_\s]range|period)(?:\*\*)?\s*[:=]\s*(.+?)$')
if ($rxDate.Success) {
    $dateRange = $rxDate.Groups[1].Value.Trim() -replace '\*+$', ''
    $dateRange = $dateRange.Trim()
} else {
    $rxFromTo = [regex]::Match($content, '(?i)\bfrom\s+(\S+)\s+to\s+(\S+)')
    if ($rxFromTo.Success) {
        $dateRange = "$($rxFromTo.Groups[1].Value.Trim()) to $($rxFromTo.Groups[2].Value.Trim())"
    }
}

# Sanity-check parse: if BOTH budget and platform are unknown, the file
# isn't really a useful SEM artifact — emit a stderr WARN.
if ($platform -eq '(unknown)' -and $budgetStr -eq '(unknown)') {
    [Console]::Error.WriteLine("WARN: sem-performance-dashboard: no parseable platform/budget fields in '$filePath'")
}

# Append audit TSV line. Best-effort; fail-soft.
$ts = Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ'
$line = "$ts`tagent=$($agentName -as [string])`tfile=$filePath`tplatform=$platform`tbudget_usd=$budgetStr`tdate_range=$dateRange"
try {
    $auditDir = Split-Path -Parent $AuditLog
    if ($auditDir -and -not (Test-Path -LiteralPath $auditDir)) {
        New-Item -ItemType Directory -Path $auditDir -Force | Out-Null
    }
    Add-Content -LiteralPath $AuditLog -Value $line -ErrorAction Stop
} catch {
    # TODO (Kanban #?): POST /api/audit-events when available.
    [Console]::Error.WriteLine("WARN: sem-performance-dashboard: could not append audit line: $($_.Exception.Message)")
}

Emit-Allow
exit 0
