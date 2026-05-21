<#
.SYNOPSIS
    Apply Tier L2 (Pro pilot) preset to .claude/agents/*.md

.DESCRIPTION
    Lead generated this from Kanban #1360 audit. Operator runs after review.

    What changes:
      content-writer     opus       -> sonnet
      thai-proofreader   sonnet     -> haiku
      general            (implicit) -> sonnet  (adds model: line)
      secretary          (implicit) -> sonnet  (adds model: line)
      novel-writer       (implicit) -> sonnet  (adds model: line)
      novel-editor       (implicit) -> sonnet  (adds model: line)

    Stays Opus: dev-sr-backend, dev-sr-frontend, bi-analyst,
                sem-campaign-lead, seo-strategist.
    Stays Haiku: dev-documentor, general-researcher,
                 secretary-email-triage, secretary-job-scout.
    All other agents already at Sonnet — no change.

.EXAMPLE
    PS> .\apply-l2-tier.ps1

.NOTES
    PowerShell 5.1 compatible. Requires git on PATH.
    Restart your Claude Code session after running.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Resolve-Path (Join-Path $ScriptDir '..\..')
Set-Location -LiteralPath $RepoRoot

$DiffDir = Join-Path $RepoRoot 'bin\tier-presets\l2'

$diffs = Get-ChildItem -Path $DiffDir -Filter '*.md.diff' -ErrorAction Stop

if ($diffs.Count -eq 0) {
    Write-Host "No .md.diff files found in $DiffDir" -ForegroundColor Yellow
    exit 0
}

foreach ($diff in $diffs) {
    $agent = $diff.BaseName -replace '\.md$', ''
    Write-Host "Applying L2 to $agent..."

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & git apply --whitespace=fix $diff.FullName
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEAP
    }

    if ($exitCode -ne 0) {
        Write-Host "ERROR: git apply failed for $agent (exit $exitCode)" -ForegroundColor Red
        exit 1
    }
}

Write-Host ""
Write-Host "L2 tier applied to $($diffs.Count) agents."
Write-Host "Restart your Claude Code session to pick up new model: defaults."
