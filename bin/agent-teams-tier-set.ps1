<#
.SYNOPSIS
    Per-machine tier toggle for .claude/agents/*.md model defaults.

.DESCRIPTION
    Mirrors bin/agent-teams-tier-set.sh on native Windows (PowerShell 5.1+).

    Tiers:
      max       Operator's full preset. Restores .claude/agents/ to the committed
                baseline (git checkout HEAD). Agents with no model: line default
                to Opus at the harness layer.

      l2 / pro  Pro plan preset. Downgrade routine agents to conserve Opus quota:
                  content-writer     opus       -> sonnet
                  thai-proofreader   sonnet     -> haiku
                  general            (implicit) -> sonnet  (adds model: line)
                  secretary          (implicit) -> sonnet  (adds model: line)
                  novel-writer       (implicit) -> sonnet  (adds model: line)
                  novel-editor       (implicit) -> sonnet  (adds model: line)

      free      Same as l2 (Free plan has similar quota constraints).

.PARAMETER Tier
    Target tier: max, l2, pro, or free.

.PARAMETER DryRun
    Print what would be executed without making any changes.

.EXAMPLE
    PS> .\bin\agent-teams-tier-set.ps1 max
    PS> .\bin\agent-teams-tier-set.ps1 l2
    PS> .\bin\agent-teams-tier-set.ps1 pro
    PS> .\bin\agent-teams-tier-set.ps1 pro --dry-run

.NOTES
    PowerShell 5.1 compatible. Requires git on PATH.
    Restart your Claude Code session after switching tiers.
#>
[CmdletBinding()]
param(
    [Parameter(Position=0)]
    [string]$Tier = '',

    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Resolve-Path (Join-Path $ScriptDir '..')
Set-Location -LiteralPath $RepoRoot

# Normalise aliases before the main dispatch
if ($Tier -eq 'pro') {
    Write-Host "(treating as L2 preset -- Pro plan alias)"
    $Tier = 'l2'
} elseif ($Tier -eq 'free') {
    Write-Host "(treating as L2 preset -- Free plan has similar quota constraints)"
    $Tier = 'l2'
}

switch ($Tier) {
    'max' {
        if ($DryRun) {
            Write-Host "[dry-run] Would: git checkout HEAD -- .claude/agents/"
            exit 0
        }
        Write-Host "==> Applying TIER MAX (operator's committed baseline)..."
        # .claude/agents/ is version-controlled; reverting restores the MAX baseline.
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & git checkout HEAD -- .claude/agents/
            $exitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $prevEAP
        }
        if ($exitCode -ne 0) {
            Write-Host "ERROR: git checkout failed (exit $exitCode)" -ForegroundColor Red
            exit 1
        }
        Write-Host "Done. Restart your Claude Code session to pick up changes."
    }

    'l2' {
        if ($DryRun) {
            Write-Host "[dry-run] Would: & bin\tier-presets\apply-l2.ps1"
            exit 0
        }
        Write-Host "==> Applying TIER L2 (Pro pilot preset)..."
        $applyScript = Join-Path $RepoRoot 'bin\tier-presets\apply-l2.ps1'
        if (-not (Test-Path $applyScript)) {
            Write-Host "ERROR: apply script not found at $applyScript" -ForegroundColor Red
            exit 1
        }
        & $applyScript
        # apply-l2-tier.ps1 prints its own "Restart" message.
    }

    { $_ -in @('', '-h', '--help') } {
        Write-Host @"
Usage: .\bin\agent-teams-tier-set.ps1 max|l2|pro|free [--dry-run]

  max        Operator's full preset. Restores .claude/agents/ to the committed
             baseline via 'git checkout HEAD'. Agents with no explicit model: line
             default to Opus at the harness layer (Claude Code Max plan behavior).

  l2 / pro   Pro plan preset -- routine agents downgraded to Sonnet.
               content-writer     opus       -> sonnet
               thai-proofreader   sonnet     -> haiku
               general            (implicit) -> sonnet  (adds model: line)
               secretary          (implicit) -> sonnet  (adds model: line)
               novel-writer       (implicit) -> sonnet  (adds model: line)
               novel-editor       (implicit) -> sonnet  (adds model: line)

  free       Same as l2 (Free plan has similar quota constraints).

  --dry-run  Print what would be executed without making any changes.

Stays Opus regardless of tier:
  dev-sr-backend, dev-sr-frontend  (sr-* new-surface design)
  bi-analyst, sem-campaign-lead, seo-strategist  (strategist roles)

Stays Haiku regardless of tier:
  dev-documentor, general-researcher
  secretary-email-triage, secretary-job-scout

Restart your Claude Code session after switching tiers to pick up the new
.claude/agents/*.md model defaults.
"@
        exit 0
    }

    default {
        Write-Host "ERROR: Unknown tier: $Tier. Use max|l2|pro|free." -ForegroundColor Red
        exit 1
    }
}
