# context-bloat-guard.ps1 — Kanban #1786
# Detect+alert (NON-blocking) size guard for IN-REPO project bootstrap context.
# Scans EVERY real in-repo project's top-level shared/*.md (context/projects/*/shared)
# and warns when any single doc exceeds a threshold. Filename-agnostic — works
# for any team (dev/seo/content/...). Archives (*archive*) + soft-deleted (.deleted)
# + ephemeral smoke/hitl/test fixtures excluded. ALWAYS exit 0 — never blocks.
#
# SCOPE: a repo-level hook only sees IN-REPO projects (agent-teams + any
# working_path=null project under context/projects/). Projects with an external
# working_path live outside this repo and are the auditor's context_footprint
# metric's job (#1213), NOT this hook's.
#
# Wired as a PostToolUse(Write|Edit) hook (.claude/settings.json). Loads at
# session start.

$root = if ($env:CLAUDE_PROJECT_DIR) { $env:CLAUDE_PROJECT_DIR } else { (git rev-parse --show-toplevel 2>$null) }
if (-not $root) { exit 0 }
$projects = Join-Path $root 'context\projects'
if (-not (Test-Path $projects)) { exit 0 }

$singleKB = 100   # any single top-level shared doc over this -> warn (catches the decisions.md-244KB class, any project/filename)

$warnings = @()
Get-ChildItem -Path $projects -Directory | ForEach-Object {
  $proj = $_.Name
  if ($proj -match '^(\.deleted|smoke-push-|hitl-push-proj-|test-)') { return }   # skip soft-deleted + ephemeral smoke/hitl/test fixtures (context/projects/ has ~185 of these — see cleanup task)
  $shared = Join-Path $_.FullName 'shared'
  if (-not (Test-Path $shared)) { return }
  Get-ChildItem -Path $shared -Filter '*.md' -File |
    Where-Object { $_.Name -notlike '*archive*' } |
    ForEach-Object {
      $kb = [math]::Round($_.Length / 1KB)
      if ($kb -gt $singleKB) {
        $warnings += ('  {0}: shared\{1}  {2}KB  (> {3}KB)' -f $proj, $_.Name, $kb, $singleKB)
      }
    }
}

if ($warnings.Count -gt 0) {
  Write-Host ''
  Write-Host '  [context-bloat-guard] In-repo context docs over threshold — compact (split active+archive, see #1583/#1786):' -ForegroundColor Yellow
  ($warnings | Sort-Object) | ForEach-Object { Write-Host $_ -ForegroundColor Yellow }
  Write-Host '  (warning only — not blocking; external-working_path projects = auditor #1213)'
  Write-Host ''
}
exit 0
