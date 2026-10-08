# PreToolUse hook — secretary email-action browser backstop (Kanban #1585, re-scoped #3490).
#
# PRIMARY enforcement for secretary email MUTATIONS is server-side: /api/tools/email/*
# (_enforce_tool_grant_or_403 #1799, then _enforce_operator_tier_or_403 #1859). This
# hook covers the other channel — a secretary agent driving Gmail / Outlook in the
# browser, which would bypass those gates.
#
# WIRING (#3490): frontmatter of the secretary* agents only, matcher
# `mcp__claude-in-chrome__.*|mcp__Claude_Browser__.*`. It used to sit on a global
# `mcp__Claude_in_Chrome__.*` matcher that matched no real tool name, so it never ran;
# a global row would also cost ~2 hook processes per browser call for every agent.
#
# DECISION:
#   browser action whose action_summary STARTS with a mail-mutation verb — send, reply,
#   forward, delete, trash, archive, mark, move, empty, report — in a mail context
#   (webmail host, an email address or mail words anywhere in tool_input) -> deny
#   anything else (navigate to the inbox, read, screenshot, find)        -> no decision
#   unreadable payload                                                   -> no decision
#     (fail-open: the API path stays the authoritative gate)
# KNOWN LIMIT: a click carries coordinates/refs, not a typed action; the heuristic reads
# the action_summary Claude writes for every click/type/key. A click with a vague summary
# gets through — the operator-proof API gate and HITL rule remain the real control.

$ErrorActionPreference = 'Stop'
try {
    $payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $text = [string]($payload.tool_input | ConvertTo-Json -Compress -Depth 6)
} catch { exit 0 }
# read-only browser tools never mutate, whatever their query text says
if ([string]$payload.tool_name -match '__(find|read_page|get_page_text|tabs_\w+|read_console_messages|read_network_requests|resize_window|preview_\w+)$') { exit 0 }

$mailContext = '(?i)(mail\.google\.com|inbox\.google\.com|gmail|googlemail\.com|outlook\.(com|live\.com|office\.com|office365\.com)|mail\.live\.com|hotmail\.com|[\w.+-]+@[\w-]+\.\w|\b(e-?mail|inbox|mailbox|draft(ed)?|message|thread|recipient)s?\b)'
# the action_summary of a click/type/key starts with its verb ("Sends ...", "Opens ..."), so
# match the verb only there: "Opens the Sent folder" / "Reads the reply" stay reads
$mutation = '^(?i)\s*(send|reply|replies|forward|delete|trash|archive|mark|move|empty|report)(s|es)?\b'
$verbs = @([regex]::Matches($text, '"action_summary"\s*:\s*"((?:\\.|[^"\\])*)"') | ForEach-Object { $_.Groups[1].Value } | Where-Object { $_ -match $mutation })
if ($verbs.Count -and $text -match $mailContext) {
    $null = $verbs[0] -match $mutation
    $out = @{
        hookSpecificOutput = @{
            hookEventName            = "PreToolUse"
            permissionDecision       = "deny"
            permissionDecisionReason = "secretary-email-action-gate: mail mutation in the browser ('$($Matches[0])') is denied — use the gated /api/tools/email path (operator-proof) or leave it for the operator (#1585, #3490)"
        }
    } | ConvertTo-Json -Compress -Depth 6
    Write-Output $out
}
exit 0
