# Block curl with -X DELETE (or --request DELETE) at the harness layer.
#
# Codified after the user noticed that the trailing-wildcard allowlist patterns
# (e.g. `Bash(curl --silent -H "X-Project-Id: <pid>" "http://localhost:8456:*)`)
# accept any suffix — including a `-X DELETE` that follows the wildcard's anchor
# position. This hook hard-blocks DELETE regardless of how the allowlist matched,
# so accidental DELETE-via-wildcard is impossible from a session.
#
# Both Lead's main session AND every subagent inherit this hook from
# .claude/settings.json — the enforcement is harness-side, immune to context
# compaction or agent-definition skim.
#
# Intentional DELETE (e.g., cleaning a leaked test row, hard-removing a
# soft-deleted project): the user temporarily comments this hook out, runs the
# DELETE, and re-enables. The friction IS the safety gate.

$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
$cmd = $payload.tool_input.command
if (-not $cmd) { exit 0 }

# Only inspect curl invocations. First-word check handles `curl`, `curl.exe`,
# and an env-prefixed form like `FOO=bar curl ...`.
$tokens = ($cmd -replace '^\s+', '') -split '\s+'
$firstWord = $tokens[0]
while ($firstWord -match '^[A-Z_][A-Z0-9_]*=') {
    $tokens = $tokens | Select-Object -Skip 1
    $firstWord = $tokens[0]
}
if ($firstWord -notmatch '^curl(\.exe)?$') { exit 0 }

# Match `-X DELETE` or `--request DELETE` (case-insensitive, word-boundary).
if ($cmd -match '(?i)(?:^|\s)(?:-X|--request)\s+DELETE\b') {
    $reason = @"
curl -X DELETE blocked by .claude/hooks/block-curl-delete.ps1.

The trailing-wildcard allowlist patterns (Bash(curl ... :*)) accept any suffix,
which would let `-X DELETE` slip in via the wildcard tail. This hook hard-blocks
DELETE regardless of how the allowlist matched.

To run an intentional DELETE:
  1. Comment out the block-curl-delete.ps1 hook in .claude/settings.json
  2. Run the DELETE manually in this session
  3. Re-enable the hook

Preferred alternatives for routine task removal:
  - Soft-delete via API: PATCH /api/tasks/{id} with {"process_status": 6}
  - Hard-delete via direct human-approved DB op (separate terminal, manual psql)
"@
    $output = @{
        hookSpecificOutput = @{
            hookEventName            = "PreToolUse"
            permissionDecision       = "deny"
            permissionDecisionReason = $reason
        }
    } | ConvertTo-Json -Compress -Depth 4
    Write-Output $output
    exit 2
}

exit 0
