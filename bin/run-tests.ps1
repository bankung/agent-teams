<#
.SYNOPSIS
    agent-teams - operator-run pytest wrapper with live-DB before/after drift check (#3479).

.DESCRIPTION
    Runs pytest inside the api container, snapshots per-project task counts from
    /api/projects/stats before and after, and writes the result to
    _runtime/test-results/<timestamp>.txt + latest.txt (UTF-8, no BOM) so the Lead can read it.
    OPERATOR-ONLY: refuses to run inside a Claude Code tool shell (CLAUDECODE=1).

.PARAMETER Full
    Run the whole suite.

.EXAMPLE
    .\bin\run-tests.ps1 tests/test_pricing.py
    .\bin\run-tests.ps1 tests/test_x.py::test_y tests/test_z.py
    .\bin\run-tests.ps1 -Full
#>
[CmdletBinding()]
param(
    [switch]$Full,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Selectors
)

# Guard first: the Bash gate blocks "pytest"; an agent running this wrapper would bypass it.
if ($env:CLAUDECODE -eq '1') {
    Write-Host 'run-tests.ps1 is operator-only (Claude Code tool shells must not run pytest; see #3479)' -ForegroundColor Red
    exit 3
}

$ErrorActionPreference = 'Stop'

$ComposeProject = 'agent-teams'
$StatsUrl       = 'http://localhost:8456/api/projects/stats'

if ((-not $Full -and -not $Selectors) -or ($Full -and $Selectors)) {
    Write-Host 'Usage: .\bin\run-tests.ps1 <selector> [<selector>...]   (scoped, e.g. tests/test_x.py::test_y)'
    Write-Host '       .\bin\run-tests.ps1 -Full                        (whole suite)'
    exit 2
}

Push-Location -LiteralPath (Join-Path $PSScriptRoot '..')
$RepoRoot   = (Get-Location).Path
$PrevVerify = $env:DOCKER_PYTEST_VERIFIED   # restored in finally so a later `claude` launched from this shell does not inherit the attestation
# a failed run must never leave the previous run's latest.txt readable as a fresh result
Remove-Item -LiteralPath (Join-Path $RepoRoot '_runtime\test-results\latest.txt') -Force -ErrorAction SilentlyContinue
$exit = 1
try {

# id -> @{ Name; Total } summed over every status bucket
function Get-Snapshot {
    $snap = @{}
    # PS 5.1 emits the JSON array as ONE pipeline object; assign first, then enumerate
    $data = Invoke-RestMethod -Uri $StatsUrl -TimeoutSec 20
    foreach ($p in $data) {
        $sum = 0
        foreach ($c in $p.counts.PSObject.Properties) { $sum += [int]$c.Value }
        $snap["$($p.id)"] = @{ Name = "$($p.name)"; Total = $sum }
    }
    return $snap
}

try { $before = Get-Snapshot } catch {
    Write-Host "ERROR: cannot read live counts from $StatsUrl - refusing to run pytest without a baseline: $_" -ForegroundColor Red
    exit 4
}

$env:DOCKER_PYTEST_VERIFIED = '1'   # the operator running this script is the attestation
$dockerArgs = @('compose', '-p', $ComposeProject, 'exec', '-T', 'api', 'pytest', '-q') + @($Selectors)
$cmdText    = 'docker ' + ($dockerArgs -join ' ')
Write-Host "==> [run-tests] $cmdText"

$out = New-Object System.Collections.Generic.List[string]
$ErrorActionPreference = 'Continue'   # PS 5.1: native stderr arrives as ErrorRecord; do not throw on it
try {
    & docker @dockerArgs 2>&1 | ForEach-Object {
        $line = "$_"
        Write-Host $line
        $out.Add($line)
    }
    $exit = $LASTEXITCODE
} catch {
    $exit = 125
    $msg = "ERROR: docker invocation failed: $_"
    Write-Host $msg -ForegroundColor Red
    $out.Add($msg)
}
$ErrorActionPreference = 'Stop'

try { $after = Get-Snapshot } catch {
    Write-Host "WARNING: post-run stats failed: $_" -ForegroundColor Yellow
    $after = $null
}

$rows  = New-Object System.Collections.Generic.List[string]
$drift = New-Object System.Collections.Generic.List[string]
$rows.Add('project (id)                      before   after   delta')
if ($null -eq $after) {
    $drift.Add('DRIFT: UNKNOWN (post-run stats unavailable)')
} else {
    foreach ($id in (@($before.Keys) + @($after.Keys) | Sort-Object { [int]$_ } -Unique)) {
        $b = 0; $a = 0; $name = '?'
        if ($before.ContainsKey($id)) { $b = $before[$id].Total; $name = $before[$id].Name }
        if ($after.ContainsKey($id))  { $a = $after[$id].Total;  $name = $after[$id].Name }
        $d = $a - $b
        $rows.Add(('{0,-30} {1,8} {2,7} {3,7}' -f "$name ($id)", $b, $a, $d))
        if ($d -ne 0) { $drift.Add(('DRIFT: {0} ({1}) {2:+#;-#;0}' -f $name, $id, $d)) }
    }
    if ($drift.Count -eq 0) { $drift.Add('DRIFT: none') }
}
$drift.Add('note: scheduled template fires can add +1 TODO rows; the conftest live-DB sentinel is the primary guard')

$now   = Get-Date
$sha   = (& git rev-parse --short HEAD 2>$null)
$head  = @(
    "local: $($now.ToString('yyyy-MM-dd HH:mm:ss zzz'))",
    "utc:   $($now.ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss'))Z",
    "git:   $sha",
    "cmd:   $cmdText",
    "pytest exit code: $exit",
    '',
    '--- live DB counts (sum of task counts per project) ---'
)
$text = ($head + $rows + @('') + $drift + @('', '--- pytest output ---') + $out) -join "`r`n"

$dir = Join-Path $RepoRoot '_runtime\test-results'
if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
$file   = Join-Path $dir ($now.ToString('yyyyMMdd-HHmmss') + '.txt')
$latest = Join-Path $dir 'latest.txt'
$enc    = New-Object Text.UTF8Encoding($false)
[IO.File]::WriteAllText($file, $text, $enc)
[IO.File]::WriteAllText($latest, $text, $enc)

Write-Host ''
$drift | ForEach-Object { Write-Host $_ }
Write-Host "==> [run-tests] result: $file (also $latest)  exit=$exit"
} finally {
    if ($null -eq $PrevVerify) { Remove-Item Env:\DOCKER_PYTEST_VERIFIED -ErrorAction SilentlyContinue } else { $env:DOCKER_PYTEST_VERIFIED = $PrevVerify }
    Pop-Location
}
exit $exit
