# Auto-approves localhost curl from dev-tester subagent (any port).
#
# Scoped via .claude/agents/dev-tester.md frontmatter (PreToolUse on Bash).
# Other roles (Lead, dev-backend, dev-frontend, dev-devops, dev-reviewer)
# do NOT inherit this hook — they fall through to settings.json's normal
# allow/ask flow.
#
# Decision matrix:
#   curl + localhost:<port> OR 127.0.0.1:<port> -> allow (skip permission prompt)
#   curl + non-localhost                         -> deny (block + reason)
#   not curl                                     -> neutral (exit 0, no JSON)
#
# Why localhost-any-port auto-allow: tester probes the API (8456) AND the
# web UI (5431) AND any future Playwright / dev-tool port. Hard-coding 8456
# only forced workarounds via `docker compose exec wget` for V2 web smoke
# (Kanban #406), which lost ergonomics (-X / -w / -H). Localhost-any-port
# is the safety boundary — the dev stack is throwaway/containerized.
# (Kanban #705)

$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
$cmd = $payload.tool_input.command

if (-not $cmd) { exit 0 }

# First word check — only inspect curl invocations.
$trimmed = $cmd -replace '^\s+', ''
$firstWord = ($trimmed -split '\s+')[0]

if ($firstWord -ne 'curl') { exit 0 }

$isLocalhost = ($cmd -match '://(localhost|127\.0\.0\.1):\d+')

if ($isLocalhost) {
    $output = @{
        hookSpecificOutput = @{
            hookEventName            = "PreToolUse"
            permissionDecision       = "allow"
            permissionDecisionReason = "localhost curl auto-approved for dev-tester (.claude/hooks/tester-curl-allow.ps1)"
        }
    } | ConvertTo-Json -Compress -Depth 4
    Write-Output $output
    exit 0
}

$reason = @"
Non-localhost curl blocked from dev-tester role.

dev-tester is scoped to localhost (any port) and 127.0.0.1 (any port) —
typically API on 8456 and web on 5431, plus any future dev-tool ports.
External destinations require explicit user approval; this hook denies them
by default to prevent accidental network calls during smoke probes. If you
genuinely need an external curl, propose it in your final report and let
Lead surface to user.
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
