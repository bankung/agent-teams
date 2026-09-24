# NOT WIRED (#3327) — settings.json runs pretooluse-bash-gate.ps1 for BOTH the
# Bash and the PowerShell matcher, and this file's logic lives there as GUARD 3,
# an in-process mirror. This copy is kept as the readable reference. An edit
# HERE ALONE changes nothing at runtime: edit the mirror too, or edit the gate.
#
# Force a permission prompt for an HTTP DELETE — curl `-X DELETE` / `--request
# DELETE`, or the PowerShell cmdlets' `-Method DELETE` — regardless of how the
# allowlist matched.
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
# .claude/settings.json — the enforcement is harness-side, immune to context
# compaction or agent-definition skim.

$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
$cmd = $payload.tool_input.command
if (-not $cmd) { exit 0 }

# Two invocation families are inspected (#3327):
#   curl / curl.exe  — first-word check, which also handles `FOO=bar curl ...`.
#   PowerShell HTTP cmdlets — word-boundary check, NOT first-word: the native
#     shape assigns the result (`$r = Invoke-WebRequest ... -Method DELETE`), so
#     the cmdlet is not the first token. Aliases irm/iwr included for the same
#     reason a bare `curl.exe` is: leaving one spelling open reopens the hole.
$tokens = ($cmd -replace '^\s+', '') -split '\s+'
$firstWord = $tokens[0]
while ($firstWord -match '^[A-Z_][A-Z0-9_]*=') {
    $tokens = $tokens | Select-Object -Skip 1
    $firstWord = $tokens[0]
}
$isCurl   = $firstWord -match '^curl(\.exe)?$'
$isPsHttp = $cmd -match '(?i)\b(Invoke-RestMethod|Invoke-WebRequest|irm|iwr)\b'
if (-not ($isCurl -or $isPsHttp)) { exit 0 }

# curl spells it `-X DELETE` / `--request DELETE`; the PowerShell cmdlets spell it
# `-Method DELETE` — any argument order, value quoted or bare, and either
# space-separated or colon-bound (`-Method:DELETE`, standard PowerShell named-
# parameter syntax). Case-insensitive. `-Method` is matched in full, so a longer
# parameter name like `-MethodX` cannot satisfy it; PowerShell's unambiguous-prefix
# abbreviation (`-Meth DELETE`) is NOT matched — allowlist entries are written out
# in full, which is the auto-approval this guard exists to override.
if (($isCurl   -and $cmd -match '(?i)(?:^|\s)(?:-X|--request)\s+DELETE\b') -or
    ($isPsHttp -and $cmd -match '(?i)(?:^|\s)-Method(?:\s*:\s*|\s+)["'']?DELETE\b')) {
    $reason = @"
HTTP DELETE detected — forcing permission prompt (overriding allowlist).

The trailing-wildcard allowlist patterns (Bash(curl ... :*), and the same shape
for a PowerShell HTTP entry) accept any suffix, which would let `-X DELETE` or
`-Method DELETE` slip in via the wildcard tail. This hook routes every HTTP
DELETE through the normal permission prompt so the user gets a deliberate
yes/no on each one.

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
