# Auto-approves WebFetch (https-only) + WebSearch from dev-researcher subagent.
#
# Scoped via .codex/agents/dev-researcher.md frontmatter (PreToolUse on WebFetch + WebSearch).
# Other roles (Lead, dev-backend, dev-frontend, dev-devops, dev-tester, dev-reviewer,
# dev-documentor) do NOT inherit this hook — they fall through to Codex's
# normal allow/ask flow.
#
# Decision matrix:
#   WebFetch + https:// URL    -> allow (skip permission prompt)
#   WebFetch + non-https URL   -> deny  (http://, file://, ftp://, anything else)
#   WebSearch (any query)      -> allow (output is URL list, no content; the
#                                       subsequent WebFetch goes through this hook)
#   anything else              -> neutral (exit 0, no JSON; normal Codex flow)
#
# Why https-only: researcher's role-purpose is fetching public web docs, which
# are universally https today. http:// / file:// / ftp:// are either insecure,
# local-FS bypass attempts, or legacy — none of which a researcher legitimately
# needs. Floor = https keeps a safety boundary even if a fetched page's content
# carries a prompt-injection trying to redirect researcher to fetch internal IPs.
#
# Risk profile: researcher is read-only on local FS. Worst case from this
# auto-approve is wasted tokens + a bad summary; Lead reads + applies before
# anything reaches target-project code. (Kanban #812 closed with ~29 tool uses
# = mostly WebFetch prompts the user had to approve one-by-one.)

$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
$toolName = $payload.tool_name

if (-not $toolName) { exit 0 }

if ($toolName -eq 'WebSearch') {
    $output = @{
        hookSpecificOutput = @{
            hookEventName            = "PreToolUse"
            permissionDecision       = "allow"
            permissionDecisionReason = "WebSearch auto-approved for dev-researcher (.codex/hooks/researcher-web-allow.ps1)"
        }
    } | ConvertTo-Json -Compress -Depth 4
    Write-Output $output
    exit 0
}

if ($toolName -eq 'WebFetch') {
    $url = $payload.tool_input.url
    if (-not $url) { exit 0 }

    if ($url -match '^https://') {
        $output = @{
            hookSpecificOutput = @{
                hookEventName            = "PreToolUse"
                permissionDecision       = "allow"
                permissionDecisionReason = "https WebFetch auto-approved for dev-researcher (.codex/hooks/researcher-web-allow.ps1)"
            }
        } | ConvertTo-Json -Compress -Depth 4
        Write-Output $output
        exit 0
    }

    $reason = @"
Non-https WebFetch blocked from dev-researcher role.

URL: $url

dev-researcher is scoped to https:// URLs only (public web documentation).
http://, file://, ftp:// and other schemes are denied by default to prevent:
  - insecure transport leaking the fetched content
  - local-FS bypass attempts (file://) — researcher uses Read tool for local files
  - legacy / unusual schemes that aren't part of the role's purpose

If you genuinely need a non-https fetch, propose it in your final report and
let Lead surface to user for case-by-case approval.
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
