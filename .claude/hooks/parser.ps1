# parser.ps1 — Mode-A usage-capture shared library + transcript parser.
#
# Task #2355 [mode-a-cost P2]. Option B (ledger-only): everything here feeds the
# POST /api/usage/events endpoint and NEVER touches tasks.estimated_cost_usd.
#
# This file is BOTH:
#   1. A standalone parser — dot-source it and call `Get-TranscriptUsage <path>`
#      to get per-model summed usage (deduped by message.id).
#   2. The shared helper library for the three hook scripts
#      (subagent-stop / precompact / sessionend). They dot-source this file so
#      the parse / POST / marker / log / watermark logic lives in ONE place.
#
# DESIGN NOTES (the load-bearing decisions):
#   * DEDUPE BY message.id. Streaming writes the same assistant message id across
#     several .jsonl lines; only the final line carries the populated
#     output_tokens. Per distinct id we keep the line whose usage has the MAX
#     output_tokens (ties -> max cache_creation as a tiebreak; the complete line
#     always wins because partial lines carry output_tokens=1). We then sum the
#     four token fields per (model).
#   * We sum the FLAT cache_creation_input_tokens and ignore the
#     usage.cache_creation.ephemeral_5m/1h breakdown (the endpoint wants the flat
#     figure; the breakdown is double-counting).
#   * Malformed / non-JSON / non-assistant / usage-less lines are skipped
#     silently (best-effort: a corrupt transcript must never throw).
#
# PowerShell 5.1 (Windows host). No Python. No external modules.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'   # callers wrap in try/catch and force exit 0.

# --- API config -------------------------------------------------------------
$script:UsageApiBase = 'http://localhost:8456'
$script:UsageEventsUrl = "$script:UsageApiBase/api/usage/events"


function Get-TranscriptUsage {
    <#
    .SYNOPSIS
      Parse a Claude Code transcript .jsonl into per-model summed token usage,
      deduped by message.id.
    .OUTPUTS
      An array of PSCustomObjects, one per distinct model:
        { model; input_tokens; output_tokens;
          cache_read_input_tokens; cache_creation_input_tokens; message_ids }
      message_ids = the list of distinct msg ids that contributed (used by the
      Lead-delta watermark logic). Empty array if the file is missing/unreadable.
    #>
    param(
        [Parameter(Mandatory = $true)][string] $TranscriptPath,
        # When supplied, msg ids already in this set are skipped (Lead-delta).
        [System.Collections.Generic.HashSet[string]] $ExcludeMsgIds = $null
    )

    # Per-msg-id best record: id -> { model; input; output; cread; ccreate }
    $best = @{}

    if (-not (Test-Path -LiteralPath $TranscriptPath)) {
        return @()
    }

    # Stream the file line-by-line; never slurp a multi-MB transcript into RAM.
    $reader = $null
    try {
        $reader = [System.IO.StreamReader]::new($TranscriptPath)
        while ($null -ne ($line = $reader.ReadLine())) {
            $line = $line.Trim()
            if ($line.Length -eq 0) { continue }

            $obj = $null
            try { $obj = $line | ConvertFrom-Json } catch { continue }  # malformed line

            # Only assistant message lines carry usage.
            if ($null -eq $obj -or $obj.type -ne 'assistant') { continue }
            $msg = $obj.message
            if ($null -eq $msg) { continue }

            $mid = $msg.id
            if ([string]::IsNullOrEmpty($mid)) { continue }
            if ($null -ne $ExcludeMsgIds -and $ExcludeMsgIds.Contains($mid)) { continue }

            $usage = $msg.usage
            if ($null -eq $usage) { continue }
            $model = $msg.model
            if ([string]::IsNullOrEmpty($model)) { continue }

            # Pull the four token fields with safe zero-defaults.
            $inTok  = [int](_Prop $usage 'input_tokens' 0)
            $outTok = [int](_Prop $usage 'output_tokens' 0)
            $crTok  = [int](_Prop $usage 'cache_read_input_tokens' 0)
            $ccTok  = [int](_Prop $usage 'cache_creation_input_tokens' 0)

            if ($best.ContainsKey($mid)) {
                $cur = $best[$mid]
                # Keep the more-complete line: max output, then max cache_creation.
                if ($outTok -gt $cur.output -or
                    ($outTok -eq $cur.output -and $ccTok -gt $cur.ccreate)) {
                    $best[$mid] = [pscustomobject]@{
                        model = $model; input = $inTok; output = $outTok
                        cread = $crTok; ccreate = $ccTok
                    }
                }
            }
            else {
                $best[$mid] = [pscustomobject]@{
                    model = $model; input = $inTok; output = $outTok
                    cread = $crTok; ccreate = $ccTok
                }
            }
        }
    }
    catch {
        # Unreadable file mid-stream: return whatever we accumulated.
    }
    finally {
        if ($null -ne $reader) { $reader.Dispose() }
    }

    # Sum per model.
    $perModel = @{}
    foreach ($mid in $best.Keys) {
        $r = $best[$mid]
        if (-not $perModel.ContainsKey($r.model)) {
            $perModel[$r.model] = [pscustomobject]@{
                model = $r.model
                input_tokens = 0; output_tokens = 0
                cache_read_input_tokens = 0; cache_creation_input_tokens = 0
                message_ids = [System.Collections.ArrayList]::new()
            }
        }
        $acc = $perModel[$r.model]
        $acc.input_tokens += $r.input
        $acc.output_tokens += $r.output
        $acc.cache_read_input_tokens += $r.cread
        $acc.cache_creation_input_tokens += $r.ccreate
        [void]$acc.message_ids.Add($mid)
    }

    return @($perModel.Values)
}


function _Prop {
    # Safe property read off a PSCustomObject (ConvertFrom-Json) with default.
    param($Object, [string]$Name, $Default)
    if ($null -eq $Object) { return $Default }
    $p = $Object.PSObject.Properties[$Name]
    if ($null -eq $p -or $null -eq $p.Value) { return $Default }
    return $p.Value
}


function Read-MarkerValue {
    <# Read a single-line marker file (e.g. a per-session lead_project_id_<sid>.txt).
       Returns the trimmed content, or $null if missing/empty/unreadable. #>
    param([Parameter(Mandatory = $true)][string] $Path)
    try {
        if (-not (Test-Path -LiteralPath $Path)) { return $null }
        $v = (Get-Content -LiteralPath $Path -Raw -ErrorAction Stop).Trim()
        if ([string]::IsNullOrEmpty($v)) { return $null }
        return $v
    }
    catch { return $null }
}


function Resolve-LeadProjectId {
    <#
    .SYNOPSIS
      Resolve the bound project id for THIS session — session-scoped (#2679).
    .DESCRIPTION
      Reads _runtime/lead_project_id_<SessionId>.txt. Trusts ONLY this session's
      file; a missing file -> $null. There is deliberately NO fallback to the
      global lead_project_id.txt — that global value belongs to whichever session
      bound LAST (possibly a different project), so trusting it is exactly the
      cross-session mis-attribution bug this replaces. Session UUIDs never collide,
      so a stale per-session file from a dead session can never be mis-read.
    .OUTPUTS
      A project id STRING (matching Read-MarkerValue's contract), or $null.
    #>
    param(
        [Parameter(Mandatory = $true)][string] $RuntimeDir,
        [string] $SessionId = $null,
        [string] $LogPath = $null
    )

    if ([string]::IsNullOrEmpty($SessionId)) {
        if ($LogPath) { Write-UsageLog $LogPath "[ResolveProj] no session_id -> NULL" }
        return $null
    }
    # Defense-in-depth (#2692 review MINOR-1): session_id is a Claude-generated UUID;
    # reject any non-UUID-shaped value so a crafted id can't traverse out of _runtime.
    if ($SessionId -notmatch '^[a-zA-Z0-9\-]{8,64}$') {
        if ($LogPath) { Write-UsageLog $LogPath "[ResolveProj] non-UUID session_id -> NULL" }
        return $null
    }

    $path = Join-Path $RuntimeDir "lead_project_id_$SessionId.txt"
    $val = Read-MarkerValue $path
    if (-not [string]::IsNullOrEmpty($val)) {
        if ($LogPath) { Write-UsageLog $LogPath "[ResolveProj] per-session project_id=$val (lead_project_id_$SessionId.txt)" }
        return $val
    }

    if ($LogPath) { Write-UsageLog $LogPath "[ResolveProj] no per-session binding for $SessionId -> NULL (no global fallback)" }
    return $null
}


function Resolve-ActiveTaskId {
    <#
    .SYNOPSIS
      Resolve which task a usage event belongs to — PULL, not PUSH (#2662).
    .DESCRIPTION
      Primary: ask the API which task is IN_PROGRESS (process_status=2) for this
      project and pick the most-recently-started one (tiebreak: max id). The
      in-progress status is the Kanban source of truth, maintained by normal
      discipline. Returns the in-progress task id, else $null (#2679 dropped the
      legacy lead_current_task.txt marker fallback). NEVER throws; any failure
      (API down, timeout, non-JSON) falls through to $null.
    .OUTPUTS
      A task id STRING (matching Read-MarkerValue's contract), or $null.
    #>
    param(
        [Parameter(Mandatory = $true)][string] $RuntimeDir,
        [Parameter(Mandatory = $true)][string] $ProjectId,
        [string] $LogPath = $null
    )

    # --- Primary: PULL the in-progress task from the API ------------------
    try {
        $url = "$script:UsageApiBase/api/tasks?process_status=2"
        $raw = & curl.exe --silent --show-error --max-time 5 `
            -H "X-Project-Id: $ProjectId" `
            $url 2>&1 | Out-String

        $parsed = $null
        try { $parsed = $raw | ConvertFrom-Json } catch { $parsed = $null }

        $rows = @($parsed | Where-Object { $null -ne $_ -and $null -ne $_.id })
        if ($rows.Count -gt 0) {
            $pick = $rows | Sort-Object `
                @{ Expression = { if ($_.started_at) { [datetime]$_.started_at } else { [datetime]::MinValue } }; Descending = $true }, `
                @{ Expression = { [int]$_.id }; Descending = $true } |
                Select-Object -First 1
            if ($null -ne $pick) {
                if ($LogPath) { Write-UsageLog $LogPath "[ResolveTask] PULL in-progress task_id=$($pick.id) (started=$($pick.started_at))" }
                return [string]$pick.id
            }
        }
        if ($LogPath) { Write-UsageLog $LogPath "[ResolveTask] PULL no in-progress task -> marker fallback" }
    }
    catch {
        if ($LogPath) { Write-UsageLog $LogPath ("[ResolveTask] PULL error -> marker fallback: " + $_.Exception.Message) }
    }

    if ($LogPath) { Write-UsageLog $LogPath "[ResolveTask] no in-progress task -> NULL" }
    return $null
}


function Write-UsageLog {
    <# Best-effort append to the capture log. Never throws. #>
    param(
        [Parameter(Mandatory = $true)][string] $LogPath,
        [Parameter(Mandatory = $true)][string] $Message
    )
    try {
        $dir = Split-Path -Parent $LogPath
        if ($dir -and -not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
        $ts = (Get-Date).ToString('o')
        Add-Content -LiteralPath $LogPath -Value "$ts  $Message" -Encoding utf8
    }
    catch { }   # logging must never break a hook
}


function Invoke-UsageEventPost {
    <#
    .SYNOPSIS
      POST one usage event. Returns a result object; NEVER throws.
    .OUTPUTS
      { ok = $true/$false; status = <int|null>; body = <string>; error = <string|null> }
      ok is $true on HTTP 200 (idempotent hit) or 201 (fresh insert).
    #>
    param(
        [Parameter(Mandatory = $true)][string] $ProjectId,
        [Parameter(Mandatory = $true)][hashtable] $Body
    )

    # Build JSON via ConvertTo-Json (handles escaping/UTF-8 correctly), write to
    # a temp file, and POST with curl.exe --data-binary @file. We avoid passing
    # JSON inline on the PowerShell command line (it mangles quotes/non-ASCII).
    $tmp = $null
    try {
        $json = $Body | ConvertTo-Json -Depth 6 -Compress
        $tmp = [System.IO.Path]::GetTempFileName()
        # -Encoding utf8 in PS5.1 emits a BOM; curl tolerates a leading BOM on a
        # JSON body, but to be safe we write raw UTF-8 (no BOM) via .NET.
        [System.IO.File]::WriteAllText($tmp, $json, (New-Object System.Text.UTF8Encoding($false)))

        # curl.exe: capture body + trailing HTTP code via -w. --silent suppresses
        # the progress meter; --max-time bounds the call so a hung API can't stall
        # the turn.
        $raw = & curl.exe --silent --show-error --max-time 8 `
            -X POST `
            -H "X-Project-Id: $ProjectId" `
            -H "Content-Type: application/json" `
            --data-binary "@$tmp" `
            -w "`n__HTTP_STATUS__:%{http_code}" `
            $script:UsageEventsUrl 2>&1 | Out-String

        # Split the body from the appended status marker.
        $status = $null
        $bodyText = $raw
        $m = [regex]::Match($raw, '__HTTP_STATUS__:(\d+)\s*$')
        if ($m.Success) {
            $status = [int]$m.Groups[1].Value
            $bodyText = $raw.Substring(0, $m.Index).TrimEnd()
        }

        $ok = ($status -eq 200 -or $status -eq 201)
        return [pscustomobject]@{
            ok = $ok; status = $status; body = $bodyText.Trim(); error = $null
        }
    }
    catch {
        return [pscustomobject]@{
            ok = $false; status = $null; body = $null; error = $_.Exception.Message
        }
    }
    finally {
        if ($null -ne $tmp -and (Test-Path -LiteralPath $tmp)) {
            Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        }
    }
}


function Resolve-SubagentsDir {
    <# subagents_dir = (transcript_path minus trailing ".jsonl") + "/subagents".
       Derives the subagent transcript directory from the MAIN transcript path. #>
    param([Parameter(Mandatory = $true)][string] $TranscriptPath)
    $base = $TranscriptPath
    if ($base.EndsWith('.jsonl')) {
        $base = $base.Substring(0, $base.Length - '.jsonl'.Length)
    }
    return (Join-Path $base 'subagents')
}


function Resolve-SubagentTranscript {
    <#
    .SYNOPSIS
      Locate the finished subagent transcript. Returns a result object.
    .DESCRIPTION
      Strategy (logged so we can audit which path hit):
        1. If an agent id was supplied, strip any "subagent-"/"agent-" prefix and
           try <subagents_dir>/agent-<id>.jsonl.
        2. If that file doesn't exist, FALL BACK to the newest *.jsonl by
           LastWriteTime in <subagents_dir>.
    .OUTPUTS
      { path = <full path|null>; agentId = <derived id|null>;
        strategy = 'agent_id'|'mtime'|'none'; metaPath = <full path|null> }
    #>
    param(
        [Parameter(Mandatory = $true)][string] $SubagentsDir,
        [string] $RawAgentId = $null
    )

    $result = [pscustomobject]@{
        path = $null; agentId = $null; strategy = 'none'; metaPath = $null
    }

    if (-not (Test-Path -LiteralPath $SubagentsDir)) { return $result }

    # Strategy 1: agent id direct hit.
    if (-not [string]::IsNullOrEmpty($RawAgentId)) {
        $id = $RawAgentId
        foreach ($prefix in @('subagent-', 'agent-')) {
            if ($id.StartsWith($prefix)) { $id = $id.Substring($prefix.Length); break }
        }
        $candidate = Join-Path $SubagentsDir "agent-$id.jsonl"
        if (Test-Path -LiteralPath $candidate) {
            $result.path = $candidate
            $result.agentId = $id
            $result.strategy = 'agent_id'
            $meta = Join-Path $SubagentsDir "agent-$id.meta.json"
            if (Test-Path -LiteralPath $meta) { $result.metaPath = $meta }
            return $result
        }
    }

    # Strategy 2: newest *.jsonl by LastWriteTime.
    $newest = Get-ChildItem -LiteralPath $SubagentsDir -Filter 'agent-*.jsonl' -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($null -ne $newest) {
        $result.path = $newest.FullName
        $result.strategy = 'mtime'
        # Derive agentId from the filename: agent-<id>.jsonl
        $fn = $newest.BaseName   # agent-<id>
        if ($fn.StartsWith('agent-')) { $result.agentId = $fn.Substring('agent-'.Length) }
        $meta = Join-Path $SubagentsDir ($newest.BaseName + '.meta.json')
        if (Test-Path -LiteralPath $meta) { $result.metaPath = $meta }
    }

    return $result
}


function Get-AgentTypeFromMeta {
    <# Read agentType from a subagent .meta.json. Returns $null on any failure. #>
    param([string] $MetaPath)
    if ([string]::IsNullOrEmpty($MetaPath)) { return $null }
    try {
        if (-not (Test-Path -LiteralPath $MetaPath)) { return $null }
        $meta = (Get-Content -LiteralPath $MetaPath -Raw -ErrorAction Stop) | ConvertFrom-Json
        $v = _Prop $meta 'agentType' $null
        if ([string]::IsNullOrEmpty($v)) { return $null }
        return $v
    }
    catch { return $null }
}


# --- Watermark (Lead-delta) -------------------------------------------------
# Format: a JSON file at <runtime>/usage_watermark_<session_id>.json holding
#   { "captured_ids": ["msg_...", ...] }
# = the set of Lead message ids already POSTed. Simplest CORRECT scheme: an
# explicit id set. PreCompact / SessionEnd parse the MAIN transcript, exclude
# these ids, POST only the new ones, then ADD the freshly-POSTed ids and rewrite
# the file. A re-run finds nothing new -> no double POST. Per-message dedup_key
# ("lead-<session_id>-<msg_id>") makes the endpoint idempotent even if the
# watermark write is lost, so the two layers are belt-and-suspenders.

function Read-Watermark {
    param([Parameter(Mandatory = $true)][string] $Path)
    # NOTE: a HashSet is enumerable, so a bare `return $set` lets PowerShell
    # UNROLL an empty set to $null (and a 1-element set to its scalar). The
    # comma operator `,$set` wraps it so the collection itself is returned intact.
    $set = New-Object 'System.Collections.Generic.HashSet[string]'
    try {
        if (Test-Path -LiteralPath $Path) {
            $data = (Get-Content -LiteralPath $Path -Raw -ErrorAction Stop) | ConvertFrom-Json
            $ids = _Prop $data 'captured_ids' @()
            foreach ($id in $ids) { if (-not [string]::IsNullOrEmpty($id)) { [void]$set.Add($id) } }
        }
    }
    catch { }   # corrupt watermark -> treat as empty (we'll re-POST; endpoint dedups)
    return ,$set
}

function Write-Watermark {
    param(
        [Parameter(Mandatory = $true)][string] $Path,
        [Parameter(Mandatory = $true)][System.Collections.Generic.HashSet[string]] $Ids
    )
    try {
        $dir = Split-Path -Parent $Path
        if ($dir -and -not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
        $payload = @{ captured_ids = @($Ids) } | ConvertTo-Json -Depth 4
        [System.IO.File]::WriteAllText($Path, $payload, (New-Object System.Text.UTF8Encoding($false)))
        return $true
    }
    catch { return $false }
}


function Invoke-LeadDeltaFlush {
    <#
    .SYNOPSIS
      Shared Lead-delta capture for PreCompact + SessionEnd. Parses the MAIN
      transcript, POSTs only Lead messages not already in the watermark, then
      advances the watermark with the ids that POSTed OK. NEVER throws.
    .DESCRIPTION
      The two hooks differ ONLY in their event label; everything else is here.
      Dedup is two-layered:
        * watermark id-set (skips already-captured msg ids before POSTing)
        * per-message dedup_key "lead-<session_id>-<msg_id>" (endpoint idempotent,
          so even a lost watermark write can't double-count)
      The watermark advances only for ids whose POST returned ok ($true). A
      transient API failure leaves that id out of the watermark, so the NEXT
      flush retries it — and the endpoint dedups if it actually landed.
    .OUTPUTS
      A summary string for the caller to log.
    #>
    param(
        [Parameter(Mandatory = $true)][string] $EventLabel,        # 'PreCompact' | 'SessionEnd'
        [Parameter(Mandatory = $true)][string] $TranscriptPath,
        [Parameter(Mandatory = $true)][string] $SessionId,
        [Parameter(Mandatory = $true)][string] $ProjectId,
        [Parameter(Mandatory = $true)][string] $WatermarkPath,
        [Parameter(Mandatory = $true)][string] $LogPath,
        [string] $TaskId = $null,
        [string] $Source = 'mode_a'
    )

    if (-not (Test-Path -LiteralPath $TranscriptPath)) {
        Write-UsageLog $LogPath "[$EventLabel] DROP: main transcript missing at $TranscriptPath"
        return 'no-transcript'
    }

    $captured = Read-Watermark $WatermarkPath
    $beforeCount = $captured.Count

    # Parse the MAIN transcript, EXCLUDING already-captured ids. Per-model rows
    # come back, each carrying the NEW msg ids that contributed.
    $rows = @(Get-TranscriptUsage -TranscriptPath $TranscriptPath -ExcludeMsgIds $captured)
    if ($rows.Count -eq 0) {
        Write-UsageLog $LogPath "[$EventLabel] no new Lead messages since watermark (captured=$beforeCount)"
        return "nothing-new (captured=$beforeCount)"
    }

    $taskInt = $null
    if (-not [string]::IsNullOrEmpty($TaskId)) {
        $tmp = 0
        if ([int]::TryParse($TaskId, [ref]$tmp)) { $taskInt = $tmp }
    }

    $postedOk = 0
    $postedFail = 0

    foreach ($r in $rows) {
        # DEDUP-KEY SCHEME (documented in INSTALL.md): emit ONE event per (model)
        # per flush carrying the model aggregate of the NEW (not-yet-captured)
        # msg ids. dedup_key = "lead-<session>-<firstNewId>-n<count>" — uniquely
        # identifies this flush-delta. The watermark prevents re-emitting the
        # same delta on a later flush; the dedup_key makes the endpoint idempotent
        # if a watermark write is lost. firstNewId is the lexicographically
        # smallest new id so the key is stable regardless of parse order.
        $idsSorted = @($r.message_ids | Sort-Object)
        $dedup = "lead-$SessionId-$($idsSorted[0])-n$($idsSorted.Count)"

        $body = @{
            model                       = $r.model
            input_tokens                = $r.input_tokens
            output_tokens               = $r.output_tokens
            cache_read_input_tokens     = $r.cache_read_input_tokens
            cache_creation_input_tokens = $r.cache_creation_input_tokens
            source                      = $Source
            is_estimate                 = $true
            dedup_key                   = $dedup
            session_ext_id              = $SessionId
            # agent_name omitted -> NULL = Lead/main.
        }
        if ($null -ne $taskInt) { $body.task_id = $taskInt }

        $res = Invoke-UsageEventPost -ProjectId $ProjectId -Body $body
        if ($res.ok) {
            $postedOk++
            foreach ($mid in $r.message_ids) { [void]$captured.Add($mid) }
            Write-UsageLog $LogPath ("[$EventLabel] POST OK model=$($r.model) ids=$($idsSorted.Count) dedup=$dedup HTTP=$($res.status) body=$($res.body)")
        }
        else {
            $postedFail++
            Write-UsageLog $LogPath ("[$EventLabel] POST FAIL model=$($r.model) dedup=$dedup HTTP=$($res.status) err=$($res.error) body=$($res.body)")
        }
    }

    # Advance the watermark ONLY with successfully-POSTed ids.
    if ($postedOk -gt 0) {
        $wrote = Write-Watermark -Path $WatermarkPath -Ids $captured
        Write-UsageLog $LogPath ("[$EventLabel] watermark advanced: before=$beforeCount after=$($captured.Count) written=$wrote")
    }

    return "ok=$postedOk fail=$postedFail captured_before=$beforeCount captured_after=$($captured.Count)"
}
