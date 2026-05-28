<#
.SYNOPSIS
    agent-teams — heal the web container after a .next corruption (hot-reload race).

.DESCRIPTION
    Default action: docker compose -p agent-teams restart web  (~6s fix).
    -Clean switch:  stop web, remove host web/.next/, then bring web back up.

.PARAMETER Clean
    Full heal — stops web, deletes web/.next/ (best-effort), then starts web.

.EXAMPLE
    .\bin\web-heal.ps1            # fast restart
    .\bin\web-heal.ps1 -Clean     # remove .next and restart
#>
[CmdletBinding()]
param(
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'

$ComposeProject = 'agent-teams'
$PollTimeout    = 60   # seconds to wait for HTTP 200 after restart
$WebUrl         = 'http://localhost:5431'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Resolve-Path (Join-Path $ScriptDir '..')
Set-Location -LiteralPath $RepoRoot

if ($Clean) {
    Write-Host "==> [web-heal] -Clean: stopping web container"
    & docker compose -p $ComposeProject stop web
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: docker compose stop failed." -ForegroundColor Red
        exit $LASTEXITCODE
    }

    $NextDir = Join-Path $RepoRoot.Path 'web\.next'
    if (Test-Path -LiteralPath $NextDir) {
        Write-Host "==> [web-heal] removing $NextDir (best-effort)"
        try {
            Remove-Item -Recurse -Force -LiteralPath $NextDir -ErrorAction Stop
        } catch {
            Write-Host "    (removal failed or directory in use — continuing): $_"
        }
    } else {
        Write-Host "==> [web-heal] $NextDir not found — skipping removal"
    }

    Write-Host "==> [web-heal] bringing web back up"
    & docker compose -p $ComposeProject up -d web
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: docker compose up -d web failed." -ForegroundColor Red
        exit $LASTEXITCODE
    }
} else {
    Write-Host "==> [web-heal] restarting web container"
    & docker compose -p $ComposeProject restart web
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: docker compose restart web failed." -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

# Poll until HTTP 200 or timeout.
Write-Host "==> [web-heal] polling $WebUrl (timeout ${PollTimeout}s)..."
$Elapsed  = 0
$FinalCode = 'none'
$Success  = $false

while ($Elapsed -lt $PollTimeout) {
    try {
        $Response = Invoke-WebRequest -Uri $WebUrl -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
        $FinalCode = $Response.StatusCode
        if ($FinalCode -eq 200) {
            $Success = $true
            break
        }
    } catch {
        # Non-200 or connection refused — keep polling
        $FinalCode = 'error'
    }
    Start-Sleep -Seconds 2
    $Elapsed += 2
}

if ($Success) {
    Write-Host "==> [web-heal] SUCCESS — $WebUrl returned HTTP 200" -ForegroundColor Green
} else {
    Write-Host "==> [web-heal] FAIL — $WebUrl did not return 200 within ${PollTimeout}s (last code: $FinalCode)" -ForegroundColor Red
    exit 1
}
