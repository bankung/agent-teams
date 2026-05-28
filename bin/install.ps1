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
      5  schema migration failed
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

# ---- 1b. .env + CREDENTIALS_MASTER_KEY ------------------------------------
# Ensure .env exists. If not, copy from .env.example so docker compose can start.
$EnvFile     = Join-Path $RepoRoot '.env'
$EnvExample  = Join-Path $RepoRoot '.env.example'
if (-not (Test-Path -LiteralPath $EnvFile)) {
    if (Test-Path -LiteralPath $EnvExample) {
        Copy-Item -LiteralPath $EnvExample -Destination $EnvFile
        Write-Log ".env not found — copied from .env.example."
    } else {
        Write-Warn ".env.example not found. You may need to create .env manually."
    }
}

# Generate CREDENTIALS_MASTER_KEY if missing or empty in .env.
# Fernet key = URL-safe base64 of 32 random bytes (44 chars ending in '=').
# Does NOT touch an existing non-empty value (idempotent).
if (Test-Path -LiteralPath $EnvFile) {
    $envContent = Get-Content -LiteralPath $EnvFile -Raw
    # Match the key line — value is empty or the placeholder text.
    $keyMissing = $envContent -notmatch '(?m)^CREDENTIALS_MASTER_KEY=\S+'
    if ($keyMissing) {
        Write-Log "CREDENTIALS_MASTER_KEY is missing/empty — generating a Fernet key..."
        # Generate 32 cryptographically random bytes, then URL-safe base64-encode them.
        $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        $keyBytes = New-Object byte[] 32
        $rng.GetBytes($keyBytes)
        $rng.Dispose()
        $fernetKey = [Convert]::ToBase64String($keyBytes).Replace('+', '-').Replace('/', '_')

        # Replace the line in .env (handles both empty-value and placeholder-value lines).
        $envContent = $envContent -replace '(?m)^CREDENTIALS_MASTER_KEY=.*', "CREDENTIALS_MASTER_KEY=$fernetKey"
        # If the line was absent entirely, -replace was a no-op — append it.
        if ($envContent -notmatch '(?m)^CREDENTIALS_MASTER_KEY=\S+') {
            $envContent = $envContent.TrimEnd("`r","`n") + "`nCREDENTIALS_MASTER_KEY=$fernetKey`n"
        }
        [IO.File]::WriteAllText($EnvFile, $envContent, [Text.Encoding]::UTF8)
        Write-Host ""
        Write-Host "NOTICE: A new CREDENTIALS_MASTER_KEY has been generated and written to .env." -ForegroundColor Cyan
        Write-Host "        Back it up securely (password manager / offline storage). Losing this" -ForegroundColor Cyan
        Write-Host "        key makes ALL stored vault credentials permanently unrecoverable." -ForegroundColor Cyan
        Write-Host ""
    } else {
        Write-Log "CREDENTIALS_MASTER_KEY already set — leaving untouched."
    }
}

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

# ---- 2b. Schema migration (live-DB guard bypass) ----------------------------
# The L10 guard in api/alembic/env.py refuses non-_test DBs without
# MIGRATION_TARGET=live. Same for L11 in scripts/seed.py / SEED_TARGET=production.
# Both are SAFE to bypass on a fresh install — there's no data to lose. Subsequent
# re-runs of this installer are no-ops (alembic reports 'no new revisions';
# seed is idempotent). The guards remain in force for any other code path.
Write-Log "First-time install: bypassing live-DB guards (MIGRATION_TARGET=live + SEED_TARGET=production) for the initial schema + seed."
Write-Log "  This is safe on a fresh DB. Subsequent re-runs are no-ops (alembic no-op + seed idempotent)."
Write-Log "Running schema migration (docker compose exec -T -e MIGRATION_TARGET=live api alembic upgrade head)..."
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    & docker compose exec -T -e MIGRATION_TARGET=live api alembic upgrade head
    $alembicExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prevEAP
}
if ($alembicExit -ne 0) {
    Write-Err "Schema migration failed. Check logs: docker compose logs api"
    exit 5
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
Write-Log "Running seed (docker compose exec -T -e SEED_TARGET=production api python -m scripts.seed)..."
# Seed emits sqlalchemy INFO on stderr — same NativeCommandError trap as above.
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    & docker compose exec -T -e SEED_TARGET=production api python -m scripts.seed
    $seedExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prevEAP
}
if ($seedExit -ne 0) {
    Write-Err "Seed failed. Check logs: docker compose logs api"
    exit 4
}

# ---- 5. Claude Code plan → tier preset -------------------------------------
# Non-interactive safe: if NON_INTERACTIVE env var is set, or if running
# without a host UI (e.g. CI / redirected stdin), default to max silently.
$TierChoice = 'max'
$isInteractive = [Environment]::UserInteractive -and (-not $env:NON_INTERACTIVE)
if ($isInteractive) {
    Write-Host ""
    $planInput = Read-Host "Claude Code plan? [m]ax / [p]ro  (default: max, Enter to skip)"
    if ($planInput -match '^(p|pro)$') {
        $TierChoice = 'l2'
    }
} else {
    Write-Log "Non-interactive mode — defaulting to TIER MAX."
}

if ($TierChoice -eq 'l2') {
    Write-Log "Pro plan selected — applying TIER L2 preset..."
    $tierScript = Join-Path $RepoRoot 'bin\agent-teams-tier-set.ps1'
    if (Test-Path $tierScript) {
        & $tierScript l2
    } else {
        Write-Warn "bin\agent-teams-tier-set.ps1 not found — skipping tier apply. Run it manually."
    }
    Write-Log "TIER L2 active. Restart your Claude Code session to pick up new model defaults."
} else {
    Write-Log "TIER MAX active (operator default — no agent file changes)."
}

# ---- 6. Next steps + friendly banner ----------------------------------------
$help = @"

=========================================================================
✓ agent-teams is installed and running.

Next steps:
  1. Open http://localhost:5431 in your browser.
  2. Click the 'demo-tour' project. Try a task. (5 min walkthrough.)
  3. Read QUICKSTART.md (at the repo root) for the full intro.

Need help? See README.md or run `.\bin\agent-teams-tier-set.ps1 --help`
to switch Claude Code Pro/Max tier presets.
=========================================================================

"@
Write-Host $help

try {
    Start-Process $ProjectUrl | Out-Null
} catch {
    # Browser-open is best-effort — never gate the script on this.
}

exit 0
