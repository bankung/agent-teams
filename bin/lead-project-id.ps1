# bin/lead-project-id.ps1 — resolve THIS session's bound project id (#2680, Phase B).
#
# Per-session binding: reads _runtime/lead_project_id_<CLAUDE_CODE_SESSION_ID>.txt
# (written by tn-bind / bootstrap step 4). Prints the integer project id to stdout
# and exits 0 on success.
#
# Exits 1 (with a 'run /tn-bind' message on stderr) when there is NO own-session
# binding. Callers — especially MUTATING tn-* skills (task-create/update/done/
# attach, report, milestone-*, etc.) — MUST abort on a non-zero exit rather than
# fall back to the legacy global lead_project_id.txt, whose value could belong to
# another concurrent session (the cross-session wrong-project-write hole this closes).
#
# Why a CLI: one place for the per-session resolution + the UUID guard + the
# fail-loud contract, so the ~12 tn-* skills stop each cat-ing the global file.

$ErrorActionPreference = 'Stop'

try {
    $sid = $env:CLAUDE_CODE_SESSION_ID
    # UUID-shape guard (same as the hook resolvers, #2692 MINOR-1): reject empty /
    # malformed ids so a crafted value can't traverse out of _runtime.
    if (-not $sid -or $sid -notmatch '^[a-zA-Z0-9\-]{8,64}$') {
        [Console]::Error.WriteLine("lead-project-id: no/invalid CLAUDE_CODE_SESSION_ID -- run /tn-bind <project> in this session")
        exit 1
    }

    $repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
    $file = Join-Path $repoRoot ("_runtime/lead_project_id_$sid.txt")
    if (-not (Test-Path -LiteralPath $file)) {
        [Console]::Error.WriteLine("lead-project-id: no per-session binding for this session -- run /tn-bind <project>")
        exit 1
    }

    $raw = (Get-Content -LiteralPath $file -Raw).Trim()
    $projId = 0
    if (-not [int]::TryParse($raw, [ref]$projId) -or $projId -le 0) {
        [Console]::Error.WriteLine("lead-project-id: binding file malformed ('$raw') -- re-run /tn-bind <project>")
        exit 1
    }

    Write-Output $projId
    exit 0
} catch {
    [Console]::Error.WriteLine("lead-project-id: " + $_.Exception.Message)
    exit 1
}
