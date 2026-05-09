# Auto-approves localhost:8456 curl from dev-tester subagent.
#
# Scoped via .claude/agents/dev-tester.md frontmatter (PreToolUse on Bash).
# Other roles (Lead, dev-backend, dev-frontend, dev-devops, dev-reviewer)
# do NOT inherit this hook — they fall through to settings.json's normal
# allow/ask flow.
#
# Decision matrix:
#   curl + localhost:8456 OR 127.0.0.1:8456 -> allow (skip permission prompt)
#   curl + non-localhost                     -> deny (block + reason)
#   not curl                                 -> neutral (exit 0, no JSON)
#
# Why localhost-only auto-allow: tester runs many probes against the local
# dev API. Each unique flag combination ("-H X-Project-Id:..." x N + "-d ..."
# + "-X POST" etc.) misses the brittle prefix patterns in settings.json.
# Localhost is the safety boundary — the dev DB is throwaway/containerized.

$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
$cmd = $payload.tool_input.command

if (-not $cmd) { exit 0 }

# First word check — only inspect curl invocations.
$trimmed = $cmd -replace '^\s+', ''
$firstWord = ($trimmed -split '\s+')[0]

if ($firstWord -ne 'curl') { exit 0 }

$isLocalhost = ($cmd -match 'localhost:8456') -or ($cmd -match '127\.0\.0\.1:8456')

if ($isLocalhost) {
    $output = @{
        hookSpecificOutput = @{
            hookEventName            = "PreToolUse"
            permissionDecision       = "allow"
            permissionDecisionReason = "localhost:8456 curl auto-approved for dev-tester (.claude/hooks/tester-curl-allow.ps1)"
        }
    } | ConvertTo-Json -Compress -Depth 4
    Write-Output $output
    exit 0
}

$reason = @"
Non-localhost curl blocked from dev-tester role.

dev-tester is scoped to the local dev API at http://localhost:8456 (or
127.0.0.1:8456). External destinations require explicit user approval — this
hook denies them by default to prevent accidental network calls during smoke
probes. If you genuinely need an external curl, propose it in your final
report and let Lead surface to user.
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
