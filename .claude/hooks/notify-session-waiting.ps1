# notify-session-waiting.ps1 — Claude Code Notification hook (Kanban #1937).
# Fires when the Claude Code session emits a Notification event (idle / waiting
# for user input at a permission prompt). Reads the active project context from
# the per-session _runtime/lead_project_id_<session_id>.txt (#2692), pulls the
# current IN_PROGRESS task, and POSTs a
# push notification via POST /api/notifications/deliver so the web_push fan-out
# (and/or local-file fallback) reaches the operator even when the terminal is not
# in focus.
#
# WHY: Kanban #1937 — Live-session waiting bridge. Connects the Claude Code
# Notification hook event to the existing notification fabric so blocked/idle
# sessions surface as real push notifications rather than silent terminal waits.
#
# Design constraints:
#   - MUST be best-effort: exit 0 on any error; NEVER block or fail the session.
#   - Runs under PowerShell 5.1 (Windows host); no third-party modules.
#   - Short curl timeouts to avoid adding latency to the session halt UI.
#   - Reads STDIN JSON (hook payload) for the optional `message` field.

$ErrorActionPreference = 'SilentlyContinue'   # never throw; we exit 0 on any error

# ---------------------------------------------------------------------------
# Step 1 — Read the Notification event from STDIN (once) — session_id + message
# ---------------------------------------------------------------------------

$sessionId   = $null
$hookMessage = ''
try {
    $stdinRaw = [Console]::In.ReadToEnd()
    if ($stdinRaw -and $stdinRaw.Length -gt 4096) { $stdinRaw = $stdinRaw.Substring(0, 4096) }
    if ($stdinRaw) {
        $hookPayload = $stdinRaw | ConvertFrom-Json -ErrorAction Stop
        if ($hookPayload.PSObject.Properties.Name -contains 'session_id') {
            $sessionId = [string]$hookPayload.session_id
            # Defense-in-depth (#2692 review MINOR-1/NIT-1): only UUID-shaped session
            # ids; \z (not $) so a trailing newline can't slip past the anchor in PS.
            if ($sessionId -notmatch '^[a-zA-Z0-9\-]{8,64}\z') { $sessionId = $null }
        }
        if ($hookPayload.PSObject.Properties.Name -contains 'message') {
            $hookMessage = [string]$hookPayload.message
            if ($hookMessage.Length -gt 200) { $hookMessage = $hookMessage.Substring(0, 200) }
        }
    }
} catch { <# best-effort; proceed with empty message #> }

# ---------------------------------------------------------------------------
# Step 2 — Resolve the bound project id PER SESSION (#2692). Miss -> exit 0.
# (No global fallback: a global value could be another concurrent session's project.)
# ---------------------------------------------------------------------------

if (-not $sessionId) { exit 0 }
try {
    $repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..\..')
    $projectIdFile = Join-Path $repoRoot ("_runtime\lead_project_id_$sessionId.txt")
    if (-not (Test-Path $projectIdFile)) { exit 0 }
    $raw = (Get-Content -Raw -Path $projectIdFile -ErrorAction Stop).Trim()
    $projectId = 0
    if (-not [int]::TryParse($raw, [ref]$projectId) -or $projectId -le 0) { exit 0 }
} catch { exit 0 }

# ---------------------------------------------------------------------------
# Step 3 — resolve the IN_PROGRESS task that anchors the notification
# S3 (#2541): cache the picked task (id/title/priority) with a short TTL so rapid
# successive idle notifications skip the tasks-list GET. Any cache miss / stale entry /
# parse error falls through to the full GET + write-through (full fallback preserved).
# ---------------------------------------------------------------------------

$taskId = $null
$taskTitle = ''
$taskPriority = $null

$notifyCacheFile = Join-Path $repoRoot "_runtime\notify_last_task_${projectId}.json"
$notifyCacheTtl  = 30   # seconds — bounds how stale the anchored task can be

# --- Try the cache first (skip the tasks-list GET when fresh) ---
try {
    if (Test-Path $notifyCacheFile) {
        $cachedTask = (Get-Content -Raw -Path $notifyCacheFile) | ConvertFrom-Json -ErrorAction Stop
        $cacheAge = [int][DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [int]$cachedTask.fetched_at_unix
        if ($cacheAge -ge 0 -and $cacheAge -lt $notifyCacheTtl -and $cachedTask.task_id) {
            $taskId       = $cachedTask.task_id
            $taskTitle    = [string]$cachedTask.title
            $taskPriority = $cachedTask.priority
        }
    }
} catch { $taskId = $null }   # corrupt/unreadable cache -> full GET below

# --- Cache miss / stale -> full GET + write-through ---
if (-not $taskId) {
    try {
        $tasksJson = & curl.exe --silent --max-time 4 `
            -H "X-Project-Id: $projectId" `
            "http://localhost:8456/api/tasks?process_status=2&limit=50" 2>$null

        if ($LASTEXITCODE -eq 0 -and $tasksJson) {
            $tasks = $tasksJson | ConvertFrom-Json -ErrorAction Stop
            if ($tasks -and $tasks.Count -gt 0) {
                # Pick the highest priority (HIGHEST numeric code; LOW=1..URGENT=4) task;
                # break ties by most recently started (latest started_at).
                $best = $tasks |
                    Sort-Object -Property @{Expression='priority';Descending=$true},
                                          @{Expression='started_at';Descending=$true} |
                    Select-Object -First 1
                $taskId    = $best.id
                $taskTitle = [string]$best.title
                $taskPriority = $best.priority

                # Write-through cache (best-effort; failure is non-fatal).
                try {
                    $cacheObj = @{
                        fetched_at_unix = [int][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
                        task_id  = $taskId
                        title    = $taskTitle
                        priority = $taskPriority
                    } | ConvertTo-Json -Compress
                    [System.IO.File]::WriteAllText($notifyCacheFile, $cacheObj, (New-Object System.Text.UTF8Encoding($false)))
                } catch { <# cache write failure is non-fatal #> }
            }
        }
    } catch { <# best-effort; proceed without task context #> }
}

# ---------------------------------------------------------------------------
# Step 4 — Compute downstream_block_count
# ---------------------------------------------------------------------------

$downstreamBlockCount = 0

if ($taskId) {
    try {
        # Tasks that list THIS task as their blocked_by blocker and are not DONE/CANCELLED.
        # Reverse-lookup endpoint (index ix_tasks_blocked_by) — already returns only
        # tasks blocked by $taskId, so no all-tasks fetch + client filter (#2046).
        $blockedJson = & curl.exe --silent --max-time 4 `
            -H "X-Project-Id: $projectId" `
            "http://localhost:8456/api/tasks/$taskId/blocks" 2>$null

        if ($LASTEXITCODE -eq 0 -and $blockedJson) {
            $blockers = $blockedJson | ConvertFrom-Json -ErrorAction Stop
            if ($blockers) {
                $downstreamBlockCount = @(
                    $blockers | Where-Object {
                        $_.process_status -notin @(5, 6)   # not DONE or CANCELLED
                    }
                ).Count
            }
        }
    } catch { <# best-effort; proceed with 0 #> }
}

# ---------------------------------------------------------------------------
# Step 5 — Build notification payload and POST to /api/notifications/deliver
# ---------------------------------------------------------------------------

# We need a task_id for the deliver endpoint (required field, ge=1).
# If no IN_PROGRESS task was found, we cannot deliver via the endpoint
# (no task_id anchor). Fall through silently — the local-file fallback
# won't fire here, but that's acceptable; the session is not blocked.
if (-not $taskId) { exit 0 }

# Compose a human-readable message.
$summaryMsg = if ($hookMessage) { $hookMessage } else { 'Claude Code session is waiting for your attention.' }
$contextMsg = "Task #${taskId}: $taskTitle"
if ($downstreamBlockCount -gt 0) {
    $contextMsg += " (blocks $downstreamBlockCount downstream task$(if($downstreamBlockCount -ne 1){'s'}))"
}

# Build the JSON body. Use ConvertTo-Json to ensure proper escaping.
$payloadObj = @{
    task_id    = [int]$taskId
    kind       = 'web_push'
    event_kind = 'session_waiting'
    payload    = @{
        title              = 'Claude Code — session waiting'
        message            = $summaryMsg
        task_context       = $contextMsg
        task_priority      = $taskPriority
        downstream_blocked = $downstreamBlockCount
        project_id         = $projectId
    }
}

try {
    $bodyJson = $payloadObj | ConvertTo-Json -Compress -Depth 5 -ErrorAction Stop
} catch { exit 0 }

# Write body to a temp file so curl doesn't choke on special characters in the
# JSON string on Windows (avoids the PowerShell -d "@-" stdin pipe complexity).
try {
    $tmpFile = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmpFile, $bodyJson, [System.Text.Encoding]::UTF8)
} catch { exit 0 }

try {
    & curl.exe --silent --max-time 6 `
        -X POST `
        -H "Content-Type: application/json" `
        -H "X-Project-Id: $projectId" `
        --data "@$tmpFile" `
        "http://localhost:8456/api/notifications/deliver" 2>$null | Out-Null
} catch { <# best-effort; outcome irrelevant #> } finally {
    try { Remove-Item $tmpFile -Force -ErrorAction SilentlyContinue } catch {}
}

exit 0
