# sessionend-capture.ps1 — SessionEnd hook (Mode-A cost capture, #2355 P2).
#
# Fires when the session ends. FINAL flush of the Lead's (main-transcript) usage
# delta since the watermark — same logic as PreCompact, just the last call.
# Thin wrapper over Invoke-LeadDeltaFlush in parser.ps1.
#
# CONTRACT: ALWAYS exit 0. Best-effort. Whole body in try/catch.
# Option B: POSTs to /api/usage/events only.

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here 'parser.ps1')

$SOURCE = if ($env:USAGE_CAPTURE_SOURCE) { $env:USAGE_CAPTURE_SOURCE } else { 'mode_a' }

try {
    $rawIn = [Console]::In.ReadToEnd()
    $payload = $null
    try { $payload = $rawIn | ConvertFrom-Json } catch { $payload = $null }
    if ($null -eq $payload) {
        Write-UsageLog "$here/usage_capture_fallback.log" "[SessionEnd] DROP: unparseable stdin: $rawIn"
        exit 0
    }

    $sessionId      = _Prop $payload 'session_id' $null
    $transcriptPath = _Prop $payload 'transcript_path' $null
    $cwd            = _Prop $payload 'cwd' $null
    $reason         = _Prop $payload 'reason' 'unknown'

    $runtimeDir = if ($cwd) { Join-Path $cwd '_runtime' } else { $here }
    $logPath = Join-Path $runtimeDir 'usage_capture.log'
    # W2 (#2361): log only non-content diagnostic fields, never $rawIn (carries last_assistant_message).
    Write-UsageLog $logPath ("[SessionEnd] fields (reason=$reason): session_id=$sessionId cwd=$cwd transcript=$transcriptPath")

    if ([string]::IsNullOrEmpty($transcriptPath) -or [string]::IsNullOrEmpty($sessionId)) {
        Write-UsageLog $logPath "[SessionEnd] DROP: missing transcript_path or session_id"
        exit 0
    }

    $projectId = Resolve-LeadProjectId -RuntimeDir $runtimeDir -SessionId $sessionId -LogPath $logPath
    if ([string]::IsNullOrEmpty($projectId)) {
        Write-UsageLog $logPath "[SessionEnd] DROP: no per-session project binding for $sessionId"
        exit 0
    }
    $taskId = Resolve-ActiveTaskId -RuntimeDir $runtimeDir -ProjectId $projectId -LogPath $logPath

    $watermark = Join-Path $runtimeDir "usage_watermark_$sessionId.json"

    $summary = Invoke-LeadDeltaFlush `
        -EventLabel 'SessionEnd' `
        -TranscriptPath $transcriptPath `
        -SessionId $sessionId `
        -ProjectId $projectId `
        -WatermarkPath $watermark `
        -LogPath $logPath `
        -TaskId $taskId `
        -Source $SOURCE
    Write-UsageLog $logPath "[SessionEnd] DONE: $summary"
}
catch {
    try { Write-UsageLog "$here/usage_capture_fallback.log" ("[SessionEnd] EXCEPTION: " + $_.Exception.Message) } catch { }
}

exit 0
