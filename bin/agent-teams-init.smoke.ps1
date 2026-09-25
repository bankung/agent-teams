<#
.SYNOPSIS
    Smoke for agent-teams-init.ps1 (Kanban #796).

.DESCRIPTION
    Creates a tempdir, runs the CLI against a unique project name, asserts the
    expected files landed and settings.json has been filtered. Cleans up the
    tempdir on the way out.

    Kanban #3335 — the two #3321 stub checks also run (via Test-RenderedStub)
    as a negative control against the repo's own harness CLAUDE.md, proving
    they actually discriminate rendered-stub from harness-copy.

    NOTE: This smoke creates a real DB row. The `finally` block soft-deletes
    it via `DELETE /api/projects/{id}` before exit, so runs stop accumulating
    active rows. No repo folder is involved — this smoke always passes a
    -WorkingPath, and the API scaffolds context/projects/<name>/ only for
    working_path=null projects.
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

# Kanban #3321/#3335 — the rendered CLAUDE.md stub names this project and
# contains zero markdown-link syntax; shared by the positive assertion and
# the #3335 negative control below so both paths run the same code.
function Test-RenderedStub {
    param([string]$Raw, [string]$ProjectName)
    [PSCustomObject]@{
        ContainsProjectName = $Raw.Contains($ProjectName)
        NoMarkdownLinks      = ($Raw -notmatch '\]\(')
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
    $claudeMdPath = Join-Path $tmp 'CLAUDE.md'
    Assert-True (Test-Path $claudeMdPath) "CLAUDE.md present"
    if (Test-Path $claudeMdPath) {
        # Kanban #3321 — CLAUDE.md must be the rendered pointer stub, not the
        # agent-teams repo's own harness copy: it names this project + links
        # back to /zb-bind, and it contains zero markdown link syntax.
        $claudeMdRaw = Get-Content -LiteralPath $claudeMdPath -Raw
        $stubCheck = Test-RenderedStub -Raw $claudeMdRaw -ProjectName $projectName
        Assert-True ($stubCheck.ContainsProjectName) "CLAUDE.md is the rendered stub (contains the scaffolded project name)"
        Assert-True ($stubCheck.NoMarkdownLinks) "CLAUDE.md stub contains no markdown links"
    }

    # Kanban #3335 — negative control: the same two checks must FAIL against
    # the repo's own harness CLAUDE.md, proving they discriminate rather than
    # passing unconditionally (the earlier #3321 '/zb-bind' check did not).
    $repoClaudeMdPath = Join-Path $scriptDir '..\CLAUDE.md'
    if (Test-Path -LiteralPath $repoClaudeMdPath) {
        $repoClaudeMdRaw = Get-Content -LiteralPath $repoClaudeMdPath -Raw
        $negCheck = Test-RenderedStub -Raw $repoClaudeMdRaw -ProjectName $projectName
        Assert-True (-not $negCheck.ContainsProjectName) "negative control: repo CLAUDE.md fails contains-project-name"
        Assert-True (-not $negCheck.NoMarkdownLinks) "negative control: repo CLAUDE.md fails no-markdown-links"
    } else {
        Assert-True $false "negative control: repo CLAUDE.md not found at $repoClaudeMdPath"
    }
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

    # Kanban #3322 — soft-delete the project row this run created; by-name
    # 404s (CLI died before creating it) are a clean non-finding, not a fault.
    try {
        $proj = Invoke-RestMethod -Uri "$ApiUrl/api/projects/by-name/$projectName" -Method Get
        Invoke-RestMethod -Uri "$ApiUrl/api/projects/$($proj.id)" -Method Delete | Out-Null
        Write-Host "Soft-deleted project id=$($proj.id) ($projectName)."
    } catch {
        Write-Host "No project row to clean up (by-name lookup failed): $($_.Exception.Message)"
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
