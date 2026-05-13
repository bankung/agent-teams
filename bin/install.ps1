<#
.SYNOPSIS
    agent-teams — one-shot installer for native Windows (PowerShell 5.1+).

.DESCRIPTION
    Mirrors bin/install.sh:
      1. Verify Docker is installed AND the daemon is responsive.
      2. docker compose up -d --build  (builds on first run, cache after).
      3. Wait for the API to answer 200 on http://localhost:8456/api/projects.
      4. Run the seed (docker compose exec — no host Python required).
         (Per project memory: 'python'/'python3' on Windows are Store stubs.)
      5. Print the Kanban URL and optionally open it.

    Companion: bin/install.sh (macOS / Linux / WSL). Reset: bin/reset.ps1.

.EXAMPLE
    PS> .\bin\install.ps1

.NOTES
    PowerShell 5.1 compatible. Uses Invoke-WebRequest -UseBasicParsing for portability
    (no curl.exe dependency). Exit codes mirror install.sh:
      0  success
      1  docker missing OR daemon unreachable
      2  docker compose up failed
      3  API healthy-wait timed out
      4  seed failed
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

# Resolve repo root from this script's location so the script works from any cwd.
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Resolve-Path (Join-Path $ScriptDir '..')
Set-Location -LiteralPath $RepoRoot

$ApiPort         = if ($env:API_PORT) { $env:API_PORT } else { '8456' }
$WebPort         = if ($env:WEB_PORT) { $env:WEB_PORT } else { '5431' }
$ProjectUrl      = "http://localhost:$WebPort/p/agent-teams"
$HealthUrl       = "http://localhost:$ApiPort/api/projects"
$WaitTimeoutSec  = 60
$WaitIntervalSec = 5

function Write-Log  { param([string]$Msg) Write-Host "==> $Msg" }
function Write-Warn { param([string]$Msg) Write-Host "WARN: $Msg" -ForegroundColor Yellow }
function Write-Err  { param([string]$Msg) Write-Host "ERROR: $Msg" -ForegroundColor Red }

# ---- 1. Docker check --------------------------------------------------------
$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerCmd) {
    Write-Err "Docker is not installed (or not on PATH)."
    Write-Err "Install Docker Desktop: https://docs.docker.com/get-docker/"
    exit 1
}

# `docker info` exits non-zero when the daemon isn't reachable. PS 5.1 with
# $ErrorActionPreference='Stop' will halt on native stderr (NativeCommandError),
# so we briefly relax it for this single call and route stderr to a temp file
# we discard. Only $LASTEXITCODE matters for the decision.
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$dockerStderr = [IO.Path]::GetTempFileName()
try {
    & docker info 1>$null 2>$dockerStderr
    $dockerInfoExit = $LASTEXITCODE
} finally {
    Remove-Item -LiteralPath $dockerStderr -ErrorAction SilentlyContinue
    $ErrorActionPreference = $prevEAP
}
if ($dockerInfoExit -ne 0) {
    Write-Err "Docker is installed but the daemon is not responding."
    Write-Err "Start Docker Desktop and retry."
    Write-Err "Install / troubleshooting: https://docs.docker.com/get-docker/"
    exit 1
}
Write-Log "Docker daemon OK."

# ---- 2. docker compose up ---------------------------------------------------
# `docker compose` writes build progress to stderr. With $ErrorActionPreference='Stop'
# PS 5.1 wraps each line in a NativeCommandError and halts. Run with 'Continue'
# for the duration of the call; rely on $LASTEXITCODE for the success check.
Write-Log "Building and starting services (docker compose up -d --build)..."
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    & docker compose up -d --build
    $composeExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prevEAP
}
if ($composeExit -ne 0) {
    Write-Err "docker compose up failed. Inspect the output above."
    exit 2
}

# ---- 3. Wait for API healthy ------------------------------------------------
Write-Log "Waiting for API at $HealthUrl (cap ${WaitTimeoutSec}s)..."
$elapsed = 0
$healthy = $false
while ($elapsed -lt $WaitTimeoutSec) {
    try {
        # -UseBasicParsing avoids the IE engine dep on old PS / Server Core.
        $resp = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
        if ($resp.StatusCode -eq 200) {
            $healthy = $true
            break
        }
    } catch {
        # connection refused / 5xx / timeout — keep polling.
    }
    Write-Host "    ...still waiting (${elapsed}s elapsed)"
    Start-Sleep -Seconds $WaitIntervalSec
    $elapsed += $WaitIntervalSec
}

if (-not $healthy) {
    Write-Err "API did not become healthy within ${WaitTimeoutSec}s."
    Write-Err "Check logs: docker compose logs api"
    exit 3
}
Write-Log "API healthy."

# ---- 4. Seed ----------------------------------------------------------------
# Seed is idempotent — re-runs print 'already seeded' and exit 0.
# -T disables pseudo-TTY (required when stdin is not a terminal).
Write-Log "Running seed (docker compose exec -T api python -m scripts.seed)..."
# Seed emits sqlalchemy INFO on stderr — same NativeCommandError trap as above.
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    & docker compose exec -T api python -m scripts.seed
    $seedExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prevEAP
}
if ($seedExit -ne 0) {
    Write-Err "Seed failed. Check logs: docker compose logs api"
    exit 4
}

# ---- 5. URL + help ----------------------------------------------------------
$help = @"

================================================================================
agent-teams is ready.

  Kanban UI : $ProjectUrl
  API base  : http://localhost:$ApiPort

Helpful commands:
  Stop      : docker compose down
  Restart   : docker compose up -d            (or rerun .\bin\install.ps1)
  Reset DB  : .\bin\reset.ps1                 (or 'docker compose down -v')
  Tail logs : docker compose logs -f api web

"@
Write-Host $help

try {
    Start-Process $ProjectUrl | Out-Null
} catch {
    # Browser-open is best-effort — never gate the script on this.
}

exit 0
