<#
.SYNOPSIS
    Zero-config scaffolder for agent-teams orchestration harness (MVP-E, Kanban #796).

.DESCRIPTION
    Host-side CLI companion to the GET /api/scaffold/<team>/files endpoint (MVP-D, #795).
    The agent-teams API runs in Docker and cannot see Windows host paths, so the manifest
    must be fetched + written from the host. This script:
      1. Find-or-creates a project row via the API.
      2. Fetches the manifest (CLAUDE.md + .claude/** + context/teams/<team>/ + standards subset).
      3. Base64-decodes content_b64 and writes each rel_path under -WorkingPath.

    Idempotent: existing target files are skipped (recorded under "skipped"); zero changes
    on a second run against the same target.

.PARAMETER Name
    Project name. Pattern enforced server-side: ^[a-zA-Z0-9_-]{1,64}$.

.PARAMETER WorkingPath
    Absolute Windows path where the harness should land. Created silently if missing.

.PARAMETER Team
    One of: dev, novel, general, content, seo, data-analytics, sem.
    Drives which roster + standards subset the manifest carries.

.PARAMETER ApiUrl
    Base URL of the agent-teams API. Default http://localhost:8456.

.PARAMETER Force
    No-op for MVP. Reserved for a future overwrite mode (clobber existing files).

.EXAMPLE
    .\bin\agent-teams-init.ps1 -Name myapp -WorkingPath C:\code\myapp -Team dev

.NOTES
    PowerShell 5.1 compatible (no PS7-only syntax). Pure PowerShell — no Python on host.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Name,
    [Parameter(Mandatory)][string]$WorkingPath,
    [Parameter(Mandatory)][ValidateSet('dev','novel','general','content','seo','data-analytics','sem')][string]$Team,
    [string]$ApiUrl = 'http://localhost:8456',
    [switch]$Force  # MVP no-op; reserved
)

$ErrorActionPreference = 'Stop'

# --- 1. Validate args ---------------------------------------------------------
if ([string]::IsNullOrWhiteSpace($Name)) {
    Write-Error "Name cannot be empty."
    exit 1
}
# Mirror server-side regex so we fail fast with a clearer message.
if ($Name -notmatch '^[a-zA-Z0-9_-]{1,64}$') {
    Write-Error "Name must match [a-zA-Z0-9_-]{1,64}. Got: $Name"
    exit 1
}

# Require an absolute path so we don't silently scaffold under the cwd.
# [IO.Path]::IsPathRooted picks up drive-rooted ('C:\..') AND UNC paths.
if (-not [IO.Path]::IsPathRooted($WorkingPath)) {
    Write-Error "WorkingPath must be absolute. Got: $WorkingPath"
    exit 1
}
# Normalize separators + collapse '..' segments. GetFullPath does NOT touch the
# filesystem so it works on a path that doesn't exist yet.
$WorkingPath = [IO.Path]::GetFullPath($WorkingPath)

if (-not (Test-Path -LiteralPath $WorkingPath)) {
    Write-Warning "WorkingPath does not exist; creating: $WorkingPath"
    New-Item -ItemType Directory -Path $WorkingPath -Force | Out-Null
}

Write-Verbose "Name        : $Name"
Write-Verbose "WorkingPath : $WorkingPath"
Write-Verbose "Team        : $Team"
Write-Verbose "ApiUrl      : $ApiUrl"

# --- 2. Find-or-create project ------------------------------------------------
$project = $null
try {
    # Invoke-RestMethod throws on non-2xx — the 404 path lands in the catch.
    $project = Invoke-RestMethod -Uri "$ApiUrl/api/projects/by-name/$Name" -Method GET
    Write-Host "Found existing project id=$($project.id) team=$($project.team)"
    if ($project.team -ne $Team) {
        Write-Warning "Existing project team=$($project.team) does not match requested -Team $Team. Continuing with existing team."
    }
} catch {
    $statusCode = $null
    if ($_.Exception.Response) {
        # PS 5.1 surfaces HttpWebResponse here; .StatusCode is the enum.
        $statusCode = [int]$_.Exception.Response.StatusCode
    }
    if ($statusCode -eq 404) {
        Write-Host "Project '$Name' not found; creating..."
        # `paths` is a required nested object server-side (legacy multi-stack DTO).
        # For zero-config we default all three lanes to $WorkingPath — the
        # scaffolder doesn't read them; they're a record of where the user
        # pointed the project on the host.
        $body = @{
            name         = $Name
            team         = $Team
            working_path = $WorkingPath
            paths        = @{
                web = $WorkingPath
                api = $WorkingPath
                db  = $WorkingPath
            }
        } | ConvertTo-Json -Compress
        try {
            $project = Invoke-RestMethod -Uri "$ApiUrl/api/projects" -Method POST `
                -ContentType 'application/json' -Body $body
            Write-Host "Created project id=$($project.id)"
        } catch {
            Write-Error "POST /api/projects failed: $($_.Exception.Message)"
            exit 1
        }
    } else {
        Write-Error "GET /api/projects/by-name/$Name failed (status=$statusCode): $($_.Exception.Message)"
        exit 1
    }
}

if (-not $project -or -not $project.id) {
    Write-Error "Could not resolve project id."
    exit 1
}

# --- 3. Fetch manifest --------------------------------------------------------
# Endpoint returns {team, project_name, project_id, files: [{rel_path, content_b64}, ...]}.
# Manifest includes the settings.json placeholder substitutions already (server-side
# filter, MVP-D / Kanban #795) — we just write bytes verbatim.
$manifestUrl = "$ApiUrl/api/scaffold/$Team/files?project_name=$Name&project_id=$($project.id)"
Write-Verbose "Fetching manifest: $manifestUrl"
try {
    $manifest = Invoke-RestMethod -Uri $manifestUrl -Method GET
} catch {
    Write-Error "GET scaffold manifest failed: $($_.Exception.Message)"
    exit 1
}

if (-not $manifest.files -or $manifest.files.Count -eq 0) {
    Write-Error "Manifest returned 0 files — abort. Verify team=$Team is supported server-side."
    exit 1
}

Write-Verbose "Manifest carries $($manifest.files.Count) files"

# --- 4. Walk files ------------------------------------------------------------
$copied  = @()
$skipped = @()
$errors  = @()

foreach ($f in $manifest.files) {
    # API ships rel_path POSIX-style ('.claude/agents/foo.md'). PowerShell's Join-Path
    # tolerates forward slashes, but normalizing first avoids any provider weirdness
    # and gives cleaner verbose output.
    $rel = $f.rel_path.Replace('/', '\')
    $target = Join-Path $WorkingPath $rel

    if (Test-Path -LiteralPath $target) {
        $skipped += $f.rel_path
        continue
    }

    try {
        $dir = Split-Path -Parent $target
        if ($dir -and -not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
        $bytes = [Convert]::FromBase64String($f.content_b64)
        [IO.File]::WriteAllBytes($target, $bytes)
        $copied += $f.rel_path
    } catch {
        $errors += [pscustomobject]@{
            rel_path = $f.rel_path
            error    = $_.Exception.Message
        }
    }
}

# --- 5. Create shared/ + role folders ----------------------------------------
# Mirrors api/src/services/project_scaffold.py: creates the dev-template trio
# under shared/ and per-team role folders under WorkingPath.
#
# Role folders are sourced from the manifest's role_folders field (single-source
# of truth in backend TEAM_ROSTERS — Kanban #1620 AC#8). No local roster copy.
$roles = $manifest.role_folders
if (-not $roles -or $roles.Count -eq 0) {
    Write-Error "Manifest did not return role_folders for team=$Team — cannot create role directories."
    exit 1
}

# Locate the bundled templates — resolve relative to the script, NOT cwd.
$ScriptDir2 = Split-Path -Parent $MyInvocation.MyCommand.Path
$TemplatesDir = Join-Path $ScriptDir2 '..\api\src\templates\project_shared'
$TemplatesDir = [IO.Path]::GetFullPath($TemplatesDir)
$SharedTemplates = @('decisions.md', 'api-contracts.md', 'db-schema.md')

# shared/
$sharedDir = Join-Path $WorkingPath 'shared'
if (-not (Test-Path -LiteralPath $sharedDir)) {
    New-Item -ItemType Directory -Path $sharedDir -Force | Out-Null
}
foreach ($tpl in $SharedTemplates) {
    $dest = Join-Path $sharedDir $tpl
    if (Test-Path -LiteralPath $dest) {
        $skipped += "shared/$tpl"
        continue
    }
    $src = Join-Path $TemplatesDir $tpl
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination $dest
        $copied += "shared/$tpl"
    } else {
        $errors += [pscustomobject]@{ rel_path = "shared/$tpl"; error = "Template not found at $src" }
    }
}

# role folders + .gitkeep
foreach ($role in $roles) {
    $roleDir  = Join-Path $WorkingPath $role
    $keepFile = Join-Path $roleDir '.gitkeep'
    if (-not (Test-Path -LiteralPath $roleDir)) {
        New-Item -ItemType Directory -Path $roleDir -Force | Out-Null
    }
    if (Test-Path -LiteralPath $keepFile) {
        $skipped += "$role/.gitkeep"
    } else {
        [IO.File]::WriteAllBytes($keepFile, @())
        $copied += "$role/.gitkeep"
    }
}

# --- 6. Summary ---------------------------------------------------------------
Write-Host ""
Write-Host "Scaffolded $WorkingPath"
Write-Host ("  copied : {0}" -f $copied.Count)
Write-Host ("  skipped: {0}" -f $skipped.Count)
Write-Host ("  errors : {0}" -f $errors.Count)

if ($VerbosePreference -eq 'Continue') {
    if ($copied.Count -gt 0) {
        Write-Verbose "Copied files:"
        foreach ($p in $copied) { Write-Verbose "  + $p" }
    }
    if ($skipped.Count -gt 0) {
        Write-Verbose "Skipped (already exist):"
        foreach ($p in $skipped) { Write-Verbose "  = $p" }
    }
}

if ($errors.Count -gt 0) {
    Write-Host ""
    Write-Host "Errors:"
    foreach ($e in $errors) {
        Write-Host ("  ! {0}: {1}" -f $e.rel_path, $e.error)
    }
    exit 1
}

exit 0
