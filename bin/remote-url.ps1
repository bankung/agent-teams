<#
.SYNOPSIS
    agent-teams — print the Tailscale MagicDNS URL for this host (Windows).

.DESCRIPTION
    Reads the local Tailscale state via `tailscale status --json` and prints
    the `http://<this-machine>.<tailnet>.ts.net:<port>` URL that other tailnet
    devices use to reach the agent-teams stack on this host.

    Honors WEB_PORT (default 5431) — set $env:WEB_PORT=8080 to override.

    Companion: bin/remote-url.sh (macOS / Linux / WSL).
    Setup guide: readme_remote-access.md.

.EXAMPLE
    PS> .\bin\remote-url.ps1
    http://homelab.tailfoo123.ts.net:5431/p/agent-teams

.NOTES
    Exit codes:
      0  URL printed
      1  tailscale not on PATH
      2  tailscale daemon not reachable, or `Self` block missing from JSON
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$WebPort = if ($env:WEB_PORT) { $env:WEB_PORT } else { '5431' }

$tsCmd = Get-Command tailscale -ErrorAction SilentlyContinue
if (-not $tsCmd) {
    Write-Host "ERROR: tailscale is not installed (or not on PATH)." -ForegroundColor Red
    Write-Host "ERROR: See readme_remote-access.md for the full setup." -ForegroundColor Red
    exit 1
}

$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    $statusJson = & tailscale status --json 2>$null
    $statusExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prevEAP
}

if ($statusExit -ne 0 -or -not $statusJson) {
    Write-Host "ERROR: Tailscale daemon is not reachable, or this host is not logged in." -ForegroundColor Red
    Write-Host "ERROR: Try:  tailscale up" -ForegroundColor Red
    exit 2
}

try {
    $parsed = $statusJson | ConvertFrom-Json
} catch {
    Write-Host "ERROR: Could not parse 'tailscale status --json' output." -ForegroundColor Red
    exit 2
}

# `Self.DNSName` is the FQDN — `<machine>.<tailnet>.ts.net.` with a trailing dot.
$dns = $parsed.Self.DNSName
if (-not $dns) {
    Write-Host "ERROR: Tailscale status JSON missing Self.DNSName — is MagicDNS enabled?" -ForegroundColor Red
    Write-Host "ERROR: See readme_remote-access.md ('MagicDNS — turn it on')." -ForegroundColor Red
    exit 2
}

# Strip the trailing dot.
$host_ = $dns.TrimEnd('.')

Write-Host ("http://{0}:{1}/p/agent-teams" -f $host_, $WebPort)
exit 0
