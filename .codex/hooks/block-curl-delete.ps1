# Force a permission prompt for curl -X DELETE (or --request DELETE) regardless
# of how the allowlist matched.
#
# Codified after the user noticed that the trailing-wildcard allowlist patterns
# (e.g. `Bash(curl --silent -H "X-Project-Id: <pid>" "http://localhost:8456:*)`)
# accept any suffix — including a `-X DELETE` that follows the wildcard's anchor
# position. This hook overrides allowlist auto-approval and routes every curl
# DELETE through the normal permission prompt, so the user gets a deliberate
# yes/no on each one. No hook-toggle gymnastics required for intentional
# DELETEs — just click "yes" at the prompt.
#
# Both Lead's main session AND every subagent inherit this hook from
# .codex/hooks.json — the enforcement is harness-side, immune to context
# compaction or agent-definition skim.

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
curl DELETE detected — forcing permission prompt (overriding allowlist).

The trailing-wildcard allowlist patterns (Bash(curl ... :*)) accept any suffix,
which would let `-X DELETE` slip in via the wildcard tail. This hook routes
every curl DELETE through the normal permission prompt so the user gets a
deliberate yes/no on each one.

If you (the user) intend this DELETE: click "yes" at the prompt.
Otherwise: click "no".

Preferred alternatives for routine task removal:
  - Soft-delete via API: PATCH /api/tasks/{id} with {"process_status": 6}
  - Hard-delete via direct human-approved DB op (separate terminal, manual psql)
"@
    $output = @{
        hookSpecificOutput = @{
            hookEventName            = "PreToolUse"
            permissionDecision       = "ask"
            permissionDecisionReason = $reason
        }
    } | ConvertTo-Json -Compress -Depth 4
    Write-Output $output
    exit 0
}

exit 0
