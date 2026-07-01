# PostToolUse hook for the Bash tool — hygiene-discriminant anomaly detection.
#
# Goal (Kanban #1463 — option E hygiene discriminant approach):
#   Detect silent failures of harness-PS-wrapped Bash invocations and send a Telegram alert so
#   operator (and Lead) know to investigate / re-run. Uses the Telegram channel (#2757;
#   replaces the removed ntfy channel #2756).
#
# Primary trigger pattern: Bitdefender ATD's "Malicious command line" block (#1462) terminates
# the underlying powershell.exe child process, leaving Bash with:
#   - exit_code != 0
#   - near-empty stdout
#   - command involving docker / pytest / pip / pwsh / powershell child
# Same signature also catches non-AV silent failures (crashed subprocess, network drop, etc.).
#
# Fail-open semantics — observational only:
#   - Any error parsing payload     -> exit 0 (silent)
#   - .env not readable / no creds  -> exit 0
#   - Telegram POST fails           -> exit 0
#   - Tool not Bash                 -> exit 0
#   PostToolUse fires AFTER the tool completes, so a hook error never blocks user work.

$ErrorActionPreference = 'SilentlyContinue'

# --- Anomaly audit log (only writes when an anomaly is detected; gitignored _runtime/) ---
# Useful for tracking false-positive rate + push delivery success/failure over time.
$logPath = $null
try {
    $repoRootForLog = Resolve-Path (Join-Path $PSScriptRoot '..\..') -ErrorAction SilentlyContinue
    if ($repoRootForLog) {
        $runtimeDir = Join-Path $repoRootForLog '_runtime'
        if (-not (Test-Path $runtimeDir)) { New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null }
        $logPath = Join-Path $runtimeDir 'posttooluse-bash-hygiene.log'
    }
} catch {}

function Audit-Log([string]$tag, [string]$detail = "") {
    if (-not $logPath) { return }
    try {
        $ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss.fff")
        Add-Content -Path $logPath -Value "[$ts] $tag $detail" -ErrorAction SilentlyContinue
    } catch {}
}

# --- Parse payload ---
try {
    $payloadRaw = [Console]::In.ReadToEnd()
    if (-not $payloadRaw) { exit 0 }
    $payload = $payloadRaw | ConvertFrom-Json
} catch { exit 0 }

if ($payload.tool_name -ne 'Bash') { exit 0 }

$cmd = "$($payload.tool_input.command)"
if (-not $cmd) { exit 0 }

# Extract response — schema varies; access defensively.
$stdout = ""
$exitCode = 0
$resp = $payload.tool_response
if ($resp) {
    foreach ($field in 'output','stdout','content') {
        if ($resp.PSObject.Properties.Name -contains $field -and "$($resp.$field)") {
            $stdout = "$($resp.$field)"
            break
        }
    }
    foreach ($field in 'exit_code','exitCode','returncode') {
        if ($resp.PSObject.Properties.Name -contains $field) {
            try { $exitCode = [int]$resp.$field } catch { $exitCode = 0 }
            break
        }
    }
}

# --- Anomaly heuristic ---
# All three must hold:
#   1. Exit code is non-zero AND not -1 (interrupted) — interrupted is operator-caused, not silent fail
#   2. stdout (combined output) is near-empty after trim (<= 20 chars)
#   3. Command's first word matches a harness-PS-wrap-typical invocation (docker/pytest/pip/etc.)
#      AND command isn't a trivial filter (echo/pwd/ls/etc.) that legitimately produces no output

if ($exitCode -eq 0) { exit 0 }
if ($exitCode -eq -1) { exit 0 }

if ($stdout.Trim().Length -gt 20) { exit 0 }

$firstWord = (($cmd -replace '^\s+', '') -split '\s+')[0]
$trivialCmds = @('echo', 'pwd', 'ls', 'cat', 'cd', 'date', 'true', 'false', 'which', 'where', 'test')
if ($trivialCmds -contains $firstWord) { exit 0 }

# Match harness-PS-wrap pattern indicators. False-positive risk is low because we already
# gate on exit != 0 + empty stdout (both rare in legit flows). The pattern list is
# illustrative — catches the common cases, not exhaustive.
$avPatterns = @(
    '\bdocker\s+(run|compose\s+(exec|up|run|build))\b',
    '\bpytest\b',
    '\bpip\s+install\b',
    '\bpwsh\b',
    '\bpowershell\b',
    '\balembic\b',
    '\bgit\s+(push|fetch|pull|clone)\b'
)

$matched = $false
foreach ($p in $avPatterns) {
    if ($cmd -match "(?i)$p") { $matched = $true; break }
}
if (-not $matched) { exit 0 }
Audit-Log "ANOMALY" "exit=$exitCode stdout_len=$($stdout.Trim().Length) cmd_first=$firstWord"

# --- Read Telegram credentials from root .env (gitignored; #2757 — replaces ntfy #2756) ---
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$envFile  = Join-Path $repoRoot '.env'
if (-not (Test-Path $envFile)) { exit 0 }

$tgToken  = ""
$tgChatId = ""

foreach ($line in (Get-Content $envFile -ErrorAction SilentlyContinue)) {
    if ($line -match '^\s*TELEGRAM_BOT_TOKEN\s*=\s*([^#]+?)\s*$')           { $tgToken  = $matches[1].Trim().Trim('"') }
    elseif ($line -match '^\s*TELEGRAM_OPERATOR_CHAT_ID\s*=\s*([^#]+?)\s*$') { $tgChatId = $matches[1].Trim().Trim('"') }
}

# Fail-open: no creds (same posture as the Telegram HITL poller) -> silent exit.
if (-not $tgToken -or -not $tgChatId) { exit 0 }

# --- Compose + send Telegram message ---
# Telegram text is UTF-8 (no ASCII-header constraint the old ntfy X-Title had).
$shortCmd = if ($cmd.Length -gt 250) { $cmd.Substring(0, 250) + '...' } else { $cmd }
$text = "[Harness Bash anomaly] exit=$exitCode, <=20 chars output. Likely AV-block, partial run, or silent subprocess fail.`n`nCommand (truncated to 250):`n$shortCmd"

$url = "https://api.telegram.org/bot$tgToken/sendMessage"
$bodyObj = @{ chat_id = $tgChatId; text = $text }

try {
    Invoke-RestMethod -Uri $url -Method POST -Body $bodyObj -TimeoutSec 5 -ErrorAction Stop | Out-Null
    Audit-Log "TELEGRAM_OK" "chat=$tgChatId"
} catch {
    # Fail-open. Log only the exception TYPE — never the message/URL (the URL carries the
    # bot token; secret-leak hardening, cf. #2667/#2659). Never block (PostToolUse).
    Audit-Log "TELEGRAM_ERR" $_.Exception.GetType().Name
}

exit 0
