<#
.SYNOPSIS
    Smoke for agent-teams-init.ps1 (Kanban #796).

.DESCRIPTION
    Creates a tempdir, runs the CLI against a unique project name, asserts the
    expected files landed and settings.json has been filtered. Cleans up the
    tempdir on the way out.

    NOTE: This smoke creates a real DB row (the agent-teams API has no consumer
    DELETE endpoint exposed here). The project name carries a random suffix so
    repeated smoke runs never collide, but the rows accumulate. Soft-delete
    cleanup is a manual chore for now.
#>
[CmdletBinding()]
param(
    [string]$ApiUrl = 'http://localhost:8456'
)

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$cli = Join-Path $scriptDir 'agent-teams-init.ps1'

if (-not (Test-Path -LiteralPath $cli)) {
    Write-Error "CLI not found at $cli"
    exit 1
}

$suffix = [Guid]::NewGuid().ToString('N').Substring(0, 8)
$projectName = "smoke-cli-test-$suffix"
$tmp = Join-Path ([IO.Path]::GetTempPath()) "agent-teams-cli-smoke-$suffix"

$failures = @()

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) {
        $script:failures += $Message
        Write-Host "  FAIL: $Message" -ForegroundColor Red
    } else {
        Write-Host "  OK  : $Message" -ForegroundColor Green
    }
}

try {
    Write-Host "Smoke project: $projectName"
    Write-Host "Tempdir      : $tmp"
    Write-Host ""

    # Run the CLI — capture exit code via $LASTEXITCODE.
    & $cli -Name $projectName -WorkingPath $tmp -Team dev -ApiUrl $ApiUrl
    $cliExit = $LASTEXITCODE

    Write-Host ""
    Write-Host "Assertions:"
    Assert-True ($cliExit -eq 0) "CLI exit code is 0 (got $cliExit)"
    Assert-True (Test-Path (Join-Path $tmp 'CLAUDE.md')) "CLAUDE.md present"
    Assert-True (Test-Path (Join-Path $tmp '.claude\agents\dev-backend.md')) "dev-backend.md present"
    Assert-True (-not (Test-Path (Join-Path $tmp '.claude\agents\novel-writer.md'))) "novel-writer.md absent (team=dev)"

    $settingsPath = Join-Path $tmp '.claude\settings.json'
    Assert-True (Test-Path $settingsPath) "settings.json present"
    if (Test-Path $settingsPath) {
        $settingsRaw = Get-Content -LiteralPath $settingsPath -Raw
        # Server-side filter strips agent-teams-specific permissions; verify the
        # by-name/agent-teams URL didn't leak into the scaffolded copy.
        Assert-True ($settingsRaw -notmatch 'by-name/agent-teams') "settings.json filtered (no by-name/agent-teams leak)"
    }

    # Idempotency: second run = all skipped, 0 copied. Snapshot file mtimes
    # before/after to verify zero writes (Write-Host output can't be reliably
    # captured cross-process so we check filesystem state directly).
    Write-Host ""
    Write-Host "Re-run for idempotency check..."
    $beforeSnap = Get-ChildItem -LiteralPath $tmp -Recurse -File |
        ForEach-Object { "$($_.FullName)|$($_.LastWriteTimeUtc.Ticks)" } | Sort-Object
    & $cli -Name $projectName -WorkingPath $tmp -Team dev -ApiUrl $ApiUrl | Out-Null
    $rerunExit = $LASTEXITCODE
    $afterSnap = Get-ChildItem -LiteralPath $tmp -Recurse -File |
        ForEach-Object { "$($_.FullName)|$($_.LastWriteTimeUtc.Ticks)" } | Sort-Object
    Assert-True ($rerunExit -eq 0) "Re-run exit code is 0 (got $rerunExit)"
    $diff = Compare-Object -ReferenceObject @($beforeSnap) -DifferenceObject @($afterSnap)
    Assert-True ($null -eq $diff -or $diff.Count -eq 0) "Re-run wrote zero files (idempotent)"

} finally {
    if (Test-Path -LiteralPath $tmp) {
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host ""
        Write-Host "Cleaned up tempdir."
    }
}

Write-Host ""
if ($failures.Count -gt 0) {
    Write-Host "SMOKE FAILED ($($failures.Count) assertion(s))" -ForegroundColor Red
    foreach ($f in $failures) { Write-Host "  - $f" -ForegroundColor Red }
    exit 1
}

Write-Host "SMOKE PASSED" -ForegroundColor Green
exit 0
