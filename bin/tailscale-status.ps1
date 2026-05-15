<#
.SYNOPSIS
    agent-teams — Tailscale status helper for native Windows (PowerShell 5.1+).

.DESCRIPTION
    Wraps `tailscale status` with a friendly header. Exits non-zero if
    Tailscale isn't installed or the daemon isn't reachable, so the script
    can be used as a precondition in deployment / smoke scripts.

    Companion: bin/tailscale-status.sh (macOS / Linux / WSL).
    Setup guide: readme_remote-access.md.

.EXAMPLE
    PS> .\bin\tailscale-status.ps1

.NOTES
    Exit codes:
      0  Tailscale running and connected
      1  tailscale not on PATH (not installed)
      2  tailscale installed but daemon not reachable / not logged in
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

function Write-Log  { param([string]$Msg) Write-Host "==> $Msg" }
function Write-Err  { param([string]$Msg) Write-Host "ERROR: $Msg" -ForegroundColor Red }

Write-Log "agent-teams — Tailscale status"
Write-Host ""

$tsCmd = Get-Command tailscale -ErrorAction SilentlyContinue
if (-not $tsCmd) {
    Write-Err "tailscale is not installed (or not on PATH)."
    Write-Err "Install: https://tailscale.com/download/windows"
    Write-Err "See readme_remote-access.md for the full setup."
    exit 1
}

# `tailscale status` exits non-zero when the daemon isn't responding or the
# host isn't logged in. Relax EAP so PowerShell doesn't trap on stderr; rely
# on $LASTEXITCODE for the decision.
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    & tailscale status
    $statusExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prevEAP
}

if ($statusExit -ne 0) {
    Write-Host ""
    Write-Err "Tailscale daemon is not reachable, or this host is not logged in."
    Write-Err "Try:  tailscale up"
    Write-Err "See readme_remote-access.md for the full setup."
    exit 2
}

exit 0
