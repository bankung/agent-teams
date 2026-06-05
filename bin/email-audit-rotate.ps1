<#
.SYNOPSIS
    Rotate, archive, and prune the secretary email-action audit log.

.DESCRIPTION
    Three-phase maintenance for _runtime/email-actions.jsonl:

    1. ROTATE  — if the active log is non-empty, append its lines to the current
                 ISO-week file (_runtime/email-actions-YYYY-Wnn.jsonl) then
                 truncate the active file to empty.  Idempotent when active file
                 is empty or missing.

    2. ARCHIVE — gzip any weekly file whose LastWriteTime is older than 90 days
                 (System.IO.Compression.GZipStream; no external deps).
                 Removes the original .jsonl after a successful write.

    3. PRUNE   — delete any *.jsonl.gz archive whose LastWriteTime is older than
                 1 year.

    # WHY/detail: shared/decisions.md & Kanban #1585

.PARAMETER RuntimeDir
    Path to the _runtime/ directory.  Defaults to <repo-root>/_runtime/ (resolved
    from this script's location).  Use a scratch dir for testing.

.PARAMETER DryRun
    Log intended actions without making any filesystem changes.

.EXAMPLE
    PS> .\bin\email-audit-rotate.ps1

.EXAMPLE
    PS> .\bin\email-audit-rotate.ps1 -DryRun

.EXAMPLE
    PS> .\bin\email-audit-rotate.ps1 -RuntimeDir C:\tmp\test-runtime

.NOTES
    PowerShell 5.1 compatible.  ZERO external dependencies.
    Exit codes:
      0  success (including no-op when nothing to do)
      1  unexpected fatal error
#>
[CmdletBinding()]
param(
    [string]$RuntimeDir = '',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
function Write-Log  { param([string]$Msg) Write-Host "==> $Msg" }
function Write-Warn { param([string]$Msg) Write-Host "WARN: $Msg" -ForegroundColor Yellow }
function Write-Err  { param([string]$Msg) Write-Host "ERROR: $Msg" -ForegroundColor Red }

function Get-ISOWeek {
    <#
    Returns the ISO-8601 week number for a given date (PS 5.1 compatible).
    Mirrors the ISO rule: week 1 = the week containing the first Thursday.
    #>
    param([datetime]$Date = (Get-Date))
    # .NET's Calendar.GetWeekOfYear with ISO rules
    $cal = [System.Globalization.CultureInfo]::InvariantCulture.Calendar
    $rule = [System.Globalization.CalendarWeekRule]::FirstFourDayWeek
    $first = [System.DayOfWeek]::Monday
    $cal.GetWeekOfYear($Date, $rule, $first)
}

function Get-ISOWeekYear {
    <#
    Returns the ISO year for a date (may differ from calendar year near Jan 1).
    e.g. 2026-01-01 (Thursday) belongs to ISO year 2026, week 1.
    e.g. 2025-12-29 (Monday) belongs to ISO year 2026, week 1.
    #>
    param([datetime]$Date = (Get-Date))
    $week = Get-ISOWeek $Date
    # If week >= 52 and date is in January → year belongs to prior ISO year
    if ($week -ge 52 -and $Date.Month -eq 1) { return $Date.Year - 1 }
    # If week -eq 1 and date is in December → year belongs to next ISO year
    if ($week -eq 1 -and $Date.Month -eq 12) { return $Date.Year + 1 }
    return $Date.Year
}

# --------------------------------------------------------------------------
# Resolve RuntimeDir
# --------------------------------------------------------------------------
if (-not $RuntimeDir) {
    $ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
    $RepoRoot   = Resolve-Path (Join-Path $ScriptDir '..')
    $RuntimeDir = Join-Path $RepoRoot '_runtime'
}

$DryTag = if ($DryRun) { '[DRY-RUN] ' } else { '' }
Write-Log "${DryTag}email-audit-rotate starting.  RuntimeDir=$RuntimeDir"

if (-not (Test-Path -LiteralPath $RuntimeDir)) {
    Write-Warn "RuntimeDir '$RuntimeDir' does not exist — nothing to do."
    exit 0
}

# --------------------------------------------------------------------------
# Phase 1 — ROTATE
# --------------------------------------------------------------------------
$ActiveFile = Join-Path $RuntimeDir 'email-actions.jsonl'

if (-not (Test-Path -LiteralPath $ActiveFile)) {
    Write-Log "Active log not present — skipping rotate phase."
} else {
    $ActiveItem = Get-Item -LiteralPath $ActiveFile
    if ($ActiveItem.Length -eq 0) {
        Write-Log "Active log is empty — skipping rotate phase."
    } else {
        $Now     = Get-Date
        $ISOYear = Get-ISOWeekYear $Now
        $ISOWeek = Get-ISOWeek $Now
        $WeekTag = '{0}-W{1:D2}' -f $ISOYear, $ISOWeek
        $WeekFile = Join-Path $RuntimeDir "email-actions-$WeekTag.jsonl"

        Write-Log "${DryTag}ROTATE: $ActiveFile -> $WeekFile  ($([int]$ActiveItem.Length) bytes)"

        if (-not $DryRun) {
            try {
                # Append active lines to the week file, then truncate active.
                $Lines = [System.IO.File]::ReadAllText($ActiveFile, [System.Text.Encoding]::UTF8)
                [System.IO.File]::AppendAllText($WeekFile, $Lines, [System.Text.Encoding]::UTF8)
                # Truncate: open with FileStream and set length 0
                $fs = [System.IO.File]::Open($ActiveFile, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
                try { $fs.SetLength(0) } finally { $fs.Close() }
                Write-Log "ROTATE: done.  Week file: $WeekFile"
            } catch {
                Write-Err "ROTATE failed: $_"
                exit 1
            }
        }
    }
}

# --------------------------------------------------------------------------
# Phase 2 — ARCHIVE  (gzip weekly files older than 90 days)
# --------------------------------------------------------------------------
$ArchiveThreshold = (Get-Date).AddDays(-90)

$WeeklyFiles = @(Get-ChildItem -LiteralPath $RuntimeDir -Filter 'email-actions-*-W*.jsonl' -File -ErrorAction SilentlyContinue)
foreach ($wf in $WeeklyFiles) {
    if ($wf.LastWriteTime -lt $ArchiveThreshold) {
        $GzPath = $wf.FullName + '.gz'
        Write-Log "${DryTag}ARCHIVE: $($wf.FullName) -> $GzPath  (LastWriteTime=$($wf.LastWriteTime.ToString('yyyy-MM-dd')))"

        if (-not $DryRun) {
            $ok = $false
            try {
                $fsIn  = [System.IO.File]::OpenRead($wf.FullName)
                $fsOut = [System.IO.File]::Create($GzPath)
                $gz    = New-Object System.IO.Compression.GZipStream($fsOut, [System.IO.Compression.CompressionMode]::Compress)
                try {
                    $fsIn.CopyTo($gz)
                    $ok = $true
                } finally {
                    $gz.Close()
                    $fsOut.Close()
                    $fsIn.Close()
                }
            } catch {
                Write-Warn "ARCHIVE: gzip failed for '$($wf.FullName)': $_ — leaving original intact."
                # Remove partial gz if it exists
                if (Test-Path -LiteralPath $GzPath) {
                    Remove-Item -LiteralPath $GzPath -Force -ErrorAction SilentlyContinue
                }
            }

            if ($ok) {
                try {
                    Remove-Item -LiteralPath $wf.FullName -Force
                    Write-Log "ARCHIVE: done.  Removed original $($wf.FullName)"
                } catch {
                    Write-Warn "ARCHIVE: gzip OK but could not remove original '$($wf.FullName)': $_"
                }
            }
        }
    } else {
        Write-Log "ARCHIVE: skip '$($wf.Name)' (LastWriteTime=$($wf.LastWriteTime.ToString('yyyy-MM-dd')), not yet 90 days old)"
    }
}

# --------------------------------------------------------------------------
# Phase 3 — PRUNE  (delete .jsonl.gz archives older than 1 year)
# --------------------------------------------------------------------------
$PruneThreshold = (Get-Date).AddDays(-365)

$GzFiles = @(Get-ChildItem -LiteralPath $RuntimeDir -Filter 'email-actions-*.jsonl.gz' -File -ErrorAction SilentlyContinue)
foreach ($gz in $GzFiles) {
    if ($gz.LastWriteTime -lt $PruneThreshold) {
        Write-Log "${DryTag}PRUNE: $($gz.FullName)  (LastWriteTime=$($gz.LastWriteTime.ToString('yyyy-MM-dd')))"
        if (-not $DryRun) {
            try {
                Remove-Item -LiteralPath $gz.FullName -Force
                Write-Log "PRUNE: done."
            } catch {
                Write-Warn "PRUNE: could not delete '$($gz.FullName)': $_"
            }
        }
    } else {
        Write-Log "PRUNE: skip '$($gz.Name)' (LastWriteTime=$($gz.LastWriteTime.ToString('yyyy-MM-dd')), not yet 1 year old)"
    }
}

Write-Log "${DryTag}email-audit-rotate complete."
exit 0
