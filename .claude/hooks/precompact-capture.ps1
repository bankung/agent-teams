# precompact-capture.ps1 — PreCompact hook (Mode-A cost capture, #2355 P2).
#
# Fires right before the conversation is compacted (manual or auto). Flushes the
# Lead's (main-transcript) usage delta since the watermark so cost isn't lost
# across a compaction. Thin wrapper over Invoke-LeadDeltaFlush in parser.ps1.
#
# CONTRACT: ALWAYS exit 0 (PreCompact treats exit 2 as BLOCK). Best-effort.
# Whole body in try/catch. Option B: POSTs to /api/usage/events only.

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here 'parser.ps1')

$SOURCE = if ($env:USAGE_CAPTURE_SOURCE) { $env:USAGE_CAPTURE_SOURCE } else { 'mode_a' }

try {
    $rawIn = [Console]::In.ReadToEnd()
    $payload = $null
    try { $payload = $rawIn | ConvertFrom-Json } catch { $payload = $null }
    if ($null -eq $payload) {
        Write-UsageLog "$here/usage_capture_fallback.log" "[PreCompact] DROP: unparseable stdin: $rawIn"
        exit 0
    }

    $sessionId      = _Prop $payload 'session_id' $null
    $transcriptPath = _Prop $payload 'transcript_path' $null
    $cwd            = _Prop $payload 'cwd' $null
    $trigger        = _Prop $payload 'trigger' 'unknown'

    $runtimeDir = if ($cwd) { Join-Path $cwd '_runtime' } else { $here }
    $logPath = Join-Path $runtimeDir 'usage_capture.log'
    Write-UsageLog $logPath ("[PreCompact] RAW PAYLOAD (trigger=$trigger): " + $rawIn.Trim())

    if ([string]::IsNullOrEmpty($transcriptPath) -or [string]::IsNullOrEmpty($sessionId)) {
        Write-UsageLog $logPath "[PreCompact] DROP: missing transcript_path or session_id"
        exit 0
    }

    $projectId = Read-MarkerValue (Join-Path $runtimeDir 'lead_project_id.txt')
    if ([string]::IsNullOrEmpty($projectId)) {
        Write-UsageLog $logPath "[PreCompact] DROP: lead_project_id.txt missing/empty"
        exit 0
    }
    $taskId = Read-MarkerValue (Join-Path $runtimeDir 'lead_current_task.txt')

    $watermark = Join-Path $runtimeDir "usage_watermark_$sessionId.json"

    $summary = Invoke-LeadDeltaFlush `
        -EventLabel 'PreCompact' `
        -TranscriptPath $transcriptPath `
        -SessionId $sessionId `
        -ProjectId $projectId `
        -WatermarkPath $watermark `
        -LogPath $logPath `
        -TaskId $taskId `
        -Source $SOURCE
    Write-UsageLog $logPath "[PreCompact] DONE: $summary"
}
catch {
    try { Write-UsageLog "$here/usage_capture_fallback.log" ("[PreCompact] EXCEPTION: " + $_.Exception.Message) } catch { }
}

exit 0
