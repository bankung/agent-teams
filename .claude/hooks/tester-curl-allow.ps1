# Keeps dev-tester curl on localhost (any port).
#
# Scoped via .claude/agents/dev-tester.md frontmatter (PreToolUse on Bash).
# Other roles (Lead, dev-backend, dev-frontend, dev-devops, dev-reviewer)
# do NOT inherit this hook.
#
# Decision matrix (#3489):
#   any curl segment (chained or not) with no URL, or a URL that is not
#   localhost / 127.0.0.1                          -> deny (block + reason)
#   everything else                                -> no decision (exit 0, no JSON):
#                                                     the main Bash gate decides
# The hook used to emit "allow" for first-word curl + a localhost substring, which
# also auto-approved `curl localhost:8456 ; <anything>` (#3483 H3).
#
# Why localhost-any-port: tester probes the API (8456) AND the web UI (5431)
# AND any future Playwright / dev-tool port (Kanban #406, #705). Localhost-any-port
# is the safety boundary — the dev stack is throwaway/containerized.

$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
$cmd = [string]$payload.tool_input.command
if (-not $cmd) { exit 0 }
. (Join-Path $PSScriptRoot '_shared.ps1')

$foreign = $false
try { $segments = Get-ShellSegments -Command $cmd } catch { $segments = @(); $foreign = $true }   # fault -> deny, never a silent pass
foreach ($seg in $segments) {
    $h = Get-SegmentHead -Tokens $seg
    if ($h -ge $seg.Count -or (Get-CommandLeaf $seg[$h]) -ne 'curl') { continue }
    $urls = @($seg | Where-Object { $_ -match '://' })
    if ($urls.Count -eq 0 -or @($urls | Where-Object { $_ -notmatch '^(?i)https?://(localhost|127\.0\.0\.1)(:\d+)?(/|$)' }).Count) { $foreign = $true }
}
if (-not $foreign) { exit 0 }

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
