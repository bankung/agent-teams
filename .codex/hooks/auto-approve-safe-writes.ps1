# Auto-approve Write / Edit tool calls on safe-zone paths so full-auto projects
# (NewsAnalyzer, Writing) can run unattended without per-write permission prompts.
#
# ALLOW-or-pass-through hook only. This hook never denies — that responsibility
# belongs to block-raw-sql-dml.ps1 (and other deny-side hooks). When the path
# does not match a safe-zone prefix, the hook exits 0 with no output, letting
# Codex falls back to its default prompt behavior.
#
# Safe zones (project-root-relative):
#   - api/
#   - web/
#   - context/projects/   (every sub-project + role folders)
#   - _scratch/
#   - .codex/hooks/
#
# NOT auto-approved (still prompt):
#   - context/standards/   (humans-only zone)
#   - context/teams/       (Lead-only zone — preserve guardrail)
#   - .codex/agents/      (subagent definitions — explicit review)
#   - .codex/hooks.json
#   - AGENTS.md
#
# Path-traversal guard: any '..' segment forces a manual review ("ask") instead
# of auto-approve.
#
# Enabled per-project via .codex/hooks.json on the 2 full-auto projects only.
# Must NOT be wired in for agent-teams itself.

$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json

$toolName = $payload.tool_name
if ($toolName -ne 'Write' -and $toolName -ne 'Edit') { exit 0 }

$filePath = $payload.tool_input.file_path
if (-not $filePath) { exit 0 }

# Path-traversal guard — surface for manual review.
if ($filePath -match '\.\.') {
    $output = @{
        hookSpecificOutput = @{
            hookEventName            = "PreToolUse"
            permissionDecision       = "ask"
            permissionDecisionReason = "Path-traversal pattern (..)  — manual review required"
        }
    } | ConvertTo-Json -Compress -Depth 4
    Write-Output $output
    exit 0
}

# Normalize: backslashes -> forward slashes, strip project-root prefix if present.
$normalized = $filePath -replace '\\', '/'

$projectDir = if ($env:CODEX_PROJECT_DIR) {
    $env:CODEX_PROJECT_DIR
} else {
    (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
}
if ($projectDir) {
    $projectDirNorm = ($projectDir -replace '\\', '/').TrimEnd('/')
    if ($normalized.StartsWith($projectDirNorm + '/', [System.StringComparison]::OrdinalIgnoreCase)) {
        $normalized = $normalized.Substring($projectDirNorm.Length + 1)
    }
}

# Drop any leading ./ for consistency.
if ($normalized.StartsWith('./')) {
    $normalized = $normalized.Substring(2)
}

$safePrefixes = @(
    'api/',
    'web/',
    'context/projects/',
    '_scratch/',
    '.codex/hooks/'
)

foreach ($prefix in $safePrefixes) {
    if ($normalized.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        $output = @{
            hookSpecificOutput = @{
                hookEventName            = "PreToolUse"
                permissionDecision       = "allow"
                permissionDecisionReason = "Safe-zone Write/Edit — auto-approved by auto-approve-safe-writes.ps1"
            }
        } | ConvertTo-Json -Compress -Depth 4
        Write-Output $output
        exit 0
    }
}

# No match — pass through; Codex prompts as normal.
exit 0
