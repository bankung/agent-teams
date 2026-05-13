<#
.SYNOPSIS
    agent-teams — full reset for native Windows (PowerShell 5.1+).

.DESCRIPTION
    Tears down the stack AND deletes the Postgres volume, then re-runs install.ps1.
    DESTRUCTIVE: every row in the DB is gone after this.

    Bypass the prompt with: $env:AGENT_TEAMS_RESET_YES = '1'; .\bin\reset.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Resolve-Path (Join-Path $ScriptDir '..')
Set-Location -LiteralPath $RepoRoot

if ($env:AGENT_TEAMS_RESET_YES -ne '1') {
    Write-Host @"
This will:
  - Stop all agent-teams containers.
  - DELETE the Postgres volume (every project, task, and history row is gone).
  - Re-build and re-seed from scratch.

Type 'yes' to continue, anything else to abort.
"@
    $answer = Read-Host
    if ($answer -ne 'yes') {
        Write-Host "Aborted."
        exit 0
    }
}

Write-Host "==> docker compose down -v"
& docker compose down -v
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: docker compose down -v failed." -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "==> Re-running installer..."
& (Join-Path $ScriptDir 'install.ps1')
exit $LASTEXITCODE
