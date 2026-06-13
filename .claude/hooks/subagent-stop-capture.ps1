# subagent-stop-capture.ps1 — SubagentStop hook (Mode-A cost capture, #2355 P2).
#
# Fires when a subagent finishes. Reads the hook stdin payload, locates that
# subagent's transcript, parses its per-model usage, and POSTs one
# usage_events row per model.
#
# CONTRACT / GUARANTEES:
#   * ALWAYS exit 0. SubagentStop treats exit 2 as BLOCK; this hook is
#     best-effort cost telemetry and must NEVER block or delay a turn. Every
#     error path -> log + exit 0 (drop the event).
#   * Whole body wrapped in try/catch. Nothing throws out.
#   * Option B: writes ONLY to /api/usage/events. Never touches tasks.
#
# Per-event fields (LOCKED design):
#   task_id        <cwd>/_runtime/lead_current_task.txt (trimmed; omit if missing)
#   agent_name     = agentType from the subagent .meta.json
#   session_ext_id = session_id from the payload
#   source         = 'mode_a'
#   is_estimate    = true
#   dedup_key      = "subagent-<agentId>"  (+ "-<model>" when >1 model)
#   provider       = 'anthropic' (default)
#   project id     <cwd>/_runtime/lead_project_id.txt  (missing -> log + exit 0)
#
# Cost is computed SERVER-SIDE — we never send a cost.

$ErrorActionPreference = 'Stop'

# Resolve our own directory so we can dot-source the shared lib regardless of CWD.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here 'parser.ps1')

# These two are overridable by the verification harness (env vars) so a sim run
# can target a test source without editing the script. In production they are
# the locked defaults.
$SOURCE = if ($env:USAGE_CAPTURE_SOURCE) { $env:USAGE_CAPTURE_SOURCE } else { 'mode_a' }
$DEDUP_PREFIX = if ($env:USAGE_CAPTURE_DEDUP_PREFIX) { $env:USAGE_CAPTURE_DEDUP_PREFIX } else { 'subagent' }

try {
    # --- Read + parse stdin payload ---------------------------------------
    $rawIn = [Console]::In.ReadToEnd()
    $payload = $null
    try { $payload = $rawIn | ConvertFrom-Json } catch { $payload = $null }

    if ($null -eq $payload) {
        # No usable payload; can't resolve cwd. Best-effort log to a fixed spot.
        Write-UsageLog "$here/usage_capture_fallback.log" "[SubagentStop] DROP: unparseable stdin payload: $rawIn"
        exit 0
    }

    $sessionId      = _Prop $payload 'session_id' $null
    $transcriptPath = _Prop $payload 'transcript_path' $null
    $cwd            = _Prop $payload 'cwd' $null
    $agentIdRaw     = _Prop $payload 'agent_id' $null   # may be absent / differently named

    # Log path lives under the (worktree) cwd's _runtime.
    $runtimeDir = if ($cwd) { Join-Path $cwd '_runtime' } else { $here }
    $logPath = Join-Path $runtimeDir 'usage_capture.log'

    # W2 (#2361): log only non-content diagnostic fields, never $rawIn (carries last_assistant_message).
    Write-UsageLog $logPath ("[SubagentStop] fields: session_id=$sessionId agent_id=$agentIdRaw cwd=$cwd transcript=$transcriptPath")

    if ([string]::IsNullOrEmpty($transcriptPath)) {
        Write-UsageLog $logPath "[SubagentStop] DROP: no transcript_path in payload"
        exit 0
    }

    # --- Resolve project id (REQUIRED) ------------------------------------
    $projIdPath = Join-Path $runtimeDir 'lead_project_id.txt'
    $projectId = Read-MarkerValue $projIdPath
    if ([string]::IsNullOrEmpty($projectId)) {
        Write-UsageLog $logPath "[SubagentStop] DROP: lead_project_id.txt missing/empty at $projIdPath"
        exit 0
    }

    # --- Resolve task id (OPTIONAL) ---------------------------------------
    $taskIdPath = Join-Path $runtimeDir 'lead_current_task.txt'
    $taskId = Read-MarkerValue $taskIdPath   # $null -> omit

    # --- Locate the finished subagent transcript --------------------------
    $subagentsDir = Resolve-SubagentsDir $transcriptPath
    $loc = Resolve-SubagentTranscript -SubagentsDir $subagentsDir -RawAgentId $agentIdRaw
    Write-UsageLog $logPath ("[SubagentStop] subagents_dir=$subagentsDir  strategy=$($loc.strategy)  agentId=$($loc.agentId)  path=$($loc.path)")

    if ([string]::IsNullOrEmpty($loc.path)) {
        Write-UsageLog $logPath "[SubagentStop] DROP: could not locate subagent transcript"
        exit 0
    }

    $agentName = Get-AgentTypeFromMeta $loc.metaPath
    Write-UsageLog $logPath ("[SubagentStop] agent_name(agentType)=$agentName  meta=$($loc.metaPath)")

    # --- Parse usage ------------------------------------------------------
    $rows = @(Get-TranscriptUsage -TranscriptPath $loc.path)
    if ($rows.Count -eq 0) {
        Write-UsageLog $logPath "[SubagentStop] DROP: no usage rows parsed from $($loc.path)"
        exit 0
    }

    $multiModel = ($rows.Count -gt 1)

    # --- POST one event per model -----------------------------------------
    foreach ($r in $rows) {
        $dedup = "$DEDUP_PREFIX-$($loc.agentId)"
        if ($multiModel) { $dedup = "$dedup-$($r.model)" }

        $body = @{
            model                       = $r.model
            input_tokens                = $r.input_tokens
            output_tokens               = $r.output_tokens
            cache_read_input_tokens     = $r.cache_read_input_tokens
            cache_creation_input_tokens = $r.cache_creation_input_tokens
            source                      = $SOURCE
            is_estimate                 = $true
            dedup_key                   = $dedup
        }
        if ($null -ne $sessionId) { $body.session_ext_id = $sessionId }
        if ($null -ne $agentName) { $body.agent_name = $agentName }
        if ($null -ne $taskId) {
            $asInt = 0
            if ([int]::TryParse($taskId, [ref]$asInt)) { $body.task_id = $asInt }
        }

        $res = Invoke-UsageEventPost -ProjectId $projectId -Body $body
        if ($res.ok) {
            Write-UsageLog $logPath ("[SubagentStop] POST OK model=$($r.model) dedup=$dedup HTTP=$($res.status) body=$($res.body)")
        }
        else {
            Write-UsageLog $logPath ("[SubagentStop] POST FAIL model=$($r.model) dedup=$dedup HTTP=$($res.status) err=$($res.error) body=$($res.body)")
        }
    }
}
catch {
    # Absolute backstop: never let anything escape.
    try {
        $fallback = "$here/usage_capture_fallback.log"
        Write-UsageLog $fallback ("[SubagentStop] EXCEPTION: " + $_.Exception.Message)
    }
    catch { }
}

exit 0
