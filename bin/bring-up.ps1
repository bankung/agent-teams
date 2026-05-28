<#
.SYNOPSIS
    agent-teams — after-pull / fresh-machine bring-up for Windows (PowerShell 5.1+).

.DESCRIPTION
    Mirrors bin/bring-up.sh:
      1. Refuse if the working tree is dirty (uncommitted/untracked changes).
         Pass -Force to skip this check.
      2. git pull --ff-only  (fast-forward only; aborts on diverged history).
      3. Print the resulting short HEAD SHA.
      4. Delegate to bin/install.ps1 which handles:
           - docker compose up -d --build
           - alembic upgrade head  (with MIGRATION_TARGET=live — L10 guard bypass)
           - scripts/seed           (with SEED_TARGET=production — L11 guard bypass)
           - wait-for-healthy + friendly banner

    Companion: bin/bring-up.sh (macOS / Linux / WSL). Launcher: bin/bring-up.cmd.
    To wipe and rebuild from scratch: bin/reset.ps1 (destructive).

.EXAMPLE
    PS> .\bin\bring-up.ps1

.EXAMPLE
    PS> .\bin\bring-up.ps1 -Force   # skip dirty-tree check

.NOTES
    PowerShell 5.1 compatible. No &&/||/ternary operators.
    Exit codes mirror install.ps1 (0=success, 1=git failure, 2+=forwarded from install.ps1).

    On a fresh Windows machine the default ExecutionPolicy (Restricted) blocks .ps1.
    Use the provided launcher instead:
        bin\bring-up.cmd      (no policy change required — uses -ExecutionPolicy Bypass)
#>
[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

# Resolve repo root from this script's location so the script works from any cwd.
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Resolve-Path (Join-Path $ScriptDir '..')
Set-Location -LiteralPath $RepoRoot

function Write-Log  { param([string]$Msg) Write-Host "==> $Msg" }
function Write-Err  { param([string]$Msg) Write-Host "ERROR: $Msg" -ForegroundColor Red }

# ---- dirty-tree check -------------------------------------------------------
# git status writes only to stdout; use $LASTEXITCODE rather than relying on
# stderr (avoids the PS 5.1 NativeCommandError trap).
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$dirtyOutput = & git status --porcelain 2>$null
$gitStatusExit = $LASTEXITCODE
$ErrorActionPreference = $prevEAP

if ($gitStatusExit -ne 0) {
    Write-Err "git status failed (exit $gitStatusExit). Is this a git repository?"
    exit 1
}

if ($dirtyOutput -and (-not $Force)) {
    Write-Err "Working tree has uncommitted or untracked changes:"
    Write-Host $dirtyOutput
    Write-Err "Commit or stash your changes first, or re-run with -Force to skip this check."
    exit 1
}

# ---- git pull --ff-only -----------------------------------------------------
Write-Log "Pulling latest changes (git pull --ff-only)..."
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& git pull --ff-only
$gitPullExit = $LASTEXITCODE
$ErrorActionPreference = $prevEAP

if ($gitPullExit -ne 0) {
    Write-Err "git pull --ff-only failed (exit $gitPullExit)."
    Write-Err "Possible causes: diverged history, no upstream branch, or merge conflict."
    Write-Err "Resolve manually (git fetch + git log + git merge/rebase), then retry."
    exit 1
}

# ---- print resulting HEAD ---------------------------------------------------
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$headShort = (& git rev-parse --short HEAD 2>$null).Trim()
$ErrorActionPreference = $prevEAP
Write-Log "Now at: $headShort"

# ---- delegate to install.ps1 ------------------------------------------------
Write-Log "Delegating to bin\install.ps1 (build + migrate + seed)..."
$installScript = Join-Path $ScriptDir 'install.ps1'
& $installScript
exit $LASTEXITCODE
