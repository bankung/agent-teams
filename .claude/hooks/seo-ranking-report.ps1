# SEO ranking-report audit (PostToolUse) — audit-log SEO reports as they land.
#
# Trigger: PostToolUse on Write when invoked by seo-reporting-analyst agent
# and the written file looks like a ranking report.
#
# Logic: when a matching report file is written, parse it for date range +
# ranking-delta counts and append one line to _scratch/seo-audit-trail.log.
# PostToolUse is informational — it CANNOT block (the tool already ran);
# this hook is pure side-effect for downstream audit.
#
# Registration in .claude/settings.json (operator step — DO NOT auto-write):
#   "PostToolUse": [{"matcher": "Write",
#                    "hooks": [{"type":"command",
#                               "command":".claude/hooks/seo-ranking-report.ps1"}]}]
#
# Future: POST one row per report to /api/audit-events when that endpoint
# exists. For now, _scratch/seo-audit-trail.log is the local sink.
#
# Kanban #1266 AC1.

$ErrorActionPreference = 'Continue'

function Emit-Allow {
    param([string]$Reason)
    $out = @{
        hookSpecificOutput = @{
            hookEventName            = "PostToolUse"
            permissionDecision       = "allow"
            permissionDecisionReason = $Reason
        }
    } | ConvertTo-Json -Compress -Depth 6
    Write-Output $out
}

function Fail-Soft {
    param([string]$WarnMsg)
    [Console]::Error.WriteLine("WARN: seo-ranking-report: $WarnMsg ; allowing (PostToolUse is informational)")
    Emit-Allow -Reason "seo-ranking-report fail-soft: $WarnMsg"
    exit 0
}

# Read stdin payload --------------------------------------------------------
try {
    $payloadRaw = [Console]::In.ReadToEnd()
    if (-not $payloadRaw) { Fail-Soft -WarnMsg 'empty PostToolUse payload' }
    $payload = $payloadRaw | ConvertFrom-Json
} catch {
    Fail-Soft -WarnMsg "payload not valid JSON: $($_.Exception.Message)"
}

# Scope to invoking agent + tool --------------------------------------------
$agentName = $null
if ($payload.PSObject.Properties.Name -contains 'agent_name') {
    $agentName = [string]$payload.agent_name
} elseif ($payload.tool_input -and $payload.tool_input.PSObject.Properties.Name -contains 'subagent_type') {
    $agentName = [string]$payload.tool_input.subagent_type
}
# If the harness reports agent_name AND it's not the SEO reporting analyst,
# skip. (No agent_name -> we still inspect by filename pattern below, since
# operator may wire the hook at settings layer without agent scoping.)
if ($agentName -and $agentName -ne 'seo-reporting-analyst') {
    Emit-Allow -Reason "seo-ranking-report: agent '$agentName' out of scope"
    exit 0
}

$toolName = $payload.tool_name
if ($toolName -ne 'Write') {
    Emit-Allow -Reason "seo-ranking-report: tool '$toolName' not in scope"
    exit 0
}

$toolInput    = $payload.tool_input
$toolResponse = $payload.tool_response
$filePath     = $null
if ($toolInput -and $toolInput.PSObject.Properties.Name -contains 'file_path') {
    $filePath = [string]$toolInput.file_path
}
if (-not $filePath) {
    Emit-Allow -Reason 'seo-ranking-report: no file_path in payload'
    exit 0
}

# Only act on successful writes (tool_response.success may be absent on
# older hook payload shapes — default to true if missing).
$success = $true
if ($toolResponse -and $toolResponse.PSObject.Properties.Name -contains 'success') {
    $success = [bool]$toolResponse.success
}
if (-not $success) {
    Emit-Allow -Reason 'seo-ranking-report: tool reported failure ; nothing to log'
    exit 0
}

# Filename pattern: match seo-reporting-analyst report dirs OR ranking-brief
# files. Case-insensitive substring check.
$lower = $filePath.ToLowerInvariant()
$matchesReport  = ($lower -match 'seo-reporting-analyst') -and ($lower -match 'report') -and ($lower -match '\.md$')
$matchesBrief   = $lower -match 'ranking-brief.*\.md$'
if (-not ($matchesReport -or $matchesBrief)) {
    Emit-Allow -Reason "seo-ranking-report: '$filePath' not a ranking report ; not logged"
    exit 0
}

# Parse report file ---------------------------------------------------------
$dateRange  = '(unknown)'
$deltaCount = 0

if (Test-Path $filePath) {
    try {
        $body = Get-Content -Raw -Path $filePath -ErrorAction Stop

        # Date range — match "**Date range:**" or "Period:" or "Date range:" lines.
        $rxDate = [regex]::Match($body, '(?im)^\s*(?:\*\*)?(?:date range|period)(?:\*\*)?\s*:\s*(.+?)$')
        if ($rxDate.Success) {
            $dateRange = $rxDate.Groups[1].Value.Trim()
            # Strip trailing markdown emphasis.
            $dateRange = $dateRange -replace '\*+$', ''
            $dateRange = $dateRange.Trim()
        }

        # Ranking-delta count — match "+N position" or "-N positions" patterns.
        $deltaMatches = [regex]::Matches($body, '[+\-]\d+\s+position')
        $deltaCount = $deltaMatches.Count
    } catch {
        Fail-Soft -WarnMsg "could not read report file '$filePath': $($_.Exception.Message)"
    }
} else {
    # File doesn't exist post-write — possibly a race or sandbox quirk. Log
    # a stub line anyway so the audit trail captures the attempt.
    Fail-Soft -WarnMsg "report file not found post-write: $filePath"
}

# Resolve project_id (best-effort) from _runtime/lead_project_id.txt --------
$projectId = '?'
try {
    $repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..\..') -ErrorAction Stop
    $pidFile  = Join-Path $repoRoot '_runtime\lead_project_id.txt'
    if (Test-Path $pidFile) {
        $projectId = (Get-Content -Raw -Path $pidFile).Trim()
    }
} catch {
    # silent — projectId stays '?'
}

# Append audit line ---------------------------------------------------------
# Future: POST to /api/audit-events when that endpoint exists.
try {
    $repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..\..') -ErrorAction Stop
    $logPath  = Join-Path $repoRoot '_scratch\seo-audit-trail.log'
    $ts       = (Get-Date).ToUniversalTime().ToString('o')
    $line     = "$ts`tproject=$projectId`tfile=$filePath`tdate_range=$dateRange`tdelta_count=$deltaCount"
    Add-Content -Path $logPath -Value $line -ErrorAction Stop
} catch {
    Fail-Soft -WarnMsg "could not append audit line: $($_.Exception.Message)"
}

Emit-Allow -Reason "seo-ranking-report: logged audit line for '$filePath' (deltas=$deltaCount)"
exit 0
