<#
.SYNOPSIS
    agent-teams — full reset for native Windows (PowerShell 5.1+).

.DESCRIPTION
    Tears down the stack AND deletes the Postgres volume, then re-runs install.ps1.
    DESTRUCTIVE: every row in the DB is gone after this.

    Bypass the prompt with: $env:AGENT_TEAMS_RESET_YES = '1'; .\bin\reset.ps1
      OR pass -Yes as a switch parameter.
#>
[CmdletBinding()]
param(
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'

$ExpectedProject = 'agent-teams'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Resolve-Path (Join-Path $ScriptDir '..')
Set-Location -LiteralPath $RepoRoot

# Refuse to run from a worktree — the wipe must target the main checkout's
# compose project specifically (L13 prevention).
$RepoRootPath = $RepoRoot.Path
if ($RepoRootPath -match '\.claude[\\/]worktrees[\\/]') {
    Write-Host "ERROR: refusing to run from a worktree ($RepoRootPath)." -ForegroundColor Red
    Write-Host "       cd to the main repo checkout first."
    exit 1
}

# Confirm cwd is actually a compose project root.
if (-not (Test-Path -LiteralPath (Join-Path $RepoRootPath 'docker-compose.yml'))) {
    Write-Host "ERROR: docker-compose.yml not found in $RepoRootPath." -ForegroundColor Red
    Write-Host "       reset.ps1 must run from the main repo root."
    exit 1
}

if (($env:AGENT_TEAMS_RESET_YES -ne '1') -and (-not $Yes)) {
    Write-Host @"
This will:
  - Stop all agent-teams containers (compose project: $ExpectedProject).
  - DELETE the Postgres volume (every project, task, and history row is gone).
  - Re-build and re-seed from scratch.

Type 'WIPE' to continue, anything else to abort.
"@
    $answer = Read-Host
    if ($answer -ne 'WIPE') {
        Write-Host "Aborted."
        exit 0
    }
}

Write-Host "==> docker compose -p $ExpectedProject down -v"
& docker compose -p $ExpectedProject down -v
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: docker compose down -v failed." -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "==> Re-running installer..."
& (Join-Path $ScriptDir 'install.ps1')
exit $LASTEXITCODE
