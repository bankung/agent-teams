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
#   browser action whose action_summary STARTS with a mail-only verb — send, reply, forward,
#   delete, trash, archive, empty, discard — or a click/press whose object is a Send/Delete/...
#   control, or a Ctrl+Enter send                                        -> deny, any page
#     (a click payload has no URL, so "mail context" was only words in the summary and honest
#     summaries like "Clicks the Send button" passed; these agents are read + draft only, #3511)
#   a weak verb — mark, move, report, submit — in a mail context
#   (webmail host, an email address or mail words anywhere in tool_input) -> deny
#   javascript_tool code that clicks / submits / dispatches events / fetches -> deny
#     (no action_summary to read; these agents are read + draft only, #3500)
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

# PS 5.1 ConvertTo-Json writes ' as ' and " as \" — match the decoded text (#3500 review W1)
$plain = try { [regex]::Unescape($text) } catch { $text }
$mailContext = '(?i)(mail\.google\.com|inbox\.google\.com|gmail|googlemail\.com|outlook\.(com|live\.com|office\.com|office365\.com)|mail\.live\.com|hotmail\.com|[\w.+-]+@[\w-]+\.\w|\b(e-?mail|inbox|mailbox|draft(ed)?|message|thread|recipient|conversation|compose)s?\b)'
# the action_summary of a click/type/key starts with its verb ("Sends ...", "Opens ..."), so
# match the verb only there: "Opens the Sent folder" / "Reads the reply" stay reads
# delete / empty of typed text ("Deletes the existing search term") is editing, not mail
$notText = '(?!\s+(the\s+|a\s+)?(\w+\s+){0,2}(text|characters?|query|words?|letters?|keywords?|terms?|value|input|field|box|contents)\b)'
$strong = '^(?i)\s*(send(s|ing)?|repl(y|ies|ying)|forward(s|ing)?|delet(e|es|ing)' + $notText + '|trash(es|ing)?|archiv(e|es|ing)|empt(y|ies|ying)' + $notText + '|discard(s|ing)?(?![^\r\n]*\b(cookie|banner|pop-?up)s?\b)|mov(e|es|ing)\b[^\r\n]{0,40}\bto\s+(the\s+)?(trash|spam|junk|bin)\b)\b'
$weak = '^(?i)\s*(mark|move(?!s?\s+(the\s+)?(cursor|mouse|pointer))|report|submit(?![^\r\n]*\b(search|query|filter)\b))(s|es)?\b'
# ...or a click / press whose OBJECT is the control: "Clicks 'Send'", "Clicks the Delete option in ...",
# "Presses the Delete key" — not a title that contains the word ("the 'Forward Deployed Engineer' job",
# "the email titled 'Please send ...'") and not folder navigation ("Clicks Trash in the left sidebar")
$buttonish = '(?i)\b(click|press|tap|select|choose|hit|confirm)(s|es|ing|ed)?\s+(on\s+)?(the\s+)?[''"]?(send|delete|trash|archive|discard|forward|reply|report spam|spam|remove|unsubscribe)[''"]?(\s+(button|icon|option|key|menu item|anyway( dialog)?))?(?=\s+(on|in|at|for|from|below|above|next|of|to|under)\b|\s*[,.;]|\s*$)(?![^\r\n]*\b(sidebar|folders?|labels?|nav(igation)?|tabs?|filters?|search|cookies?|banners?)\b)|\blabell?ed\s+[''"]?(send|delete|trash|archive|discard|forward|reply)\b'
$hotkey = '(?i)\b(ctrl|control|cmd|command|meta|super)(\s*\+\s*(shift|alt))?\s*\+\s*(enter|return|kp_enter)\b'   # Gmail / Outlook send shortcut
$jsMutation = '(?i)\.click\s*\(|\bonclick\s*\(|[''"]click[''"]\s*\]\s*\(|click\.call|dispatchEvent|\.(request)?submit\s*\(|\bfetch\s*\(|XMLHttpRequest|sendBeacon'
$summaries = @([regex]::Matches($text, '"action_summary"\s*:\s*"((?:\\.|[^"\\])*)"') | ForEach-Object { $v = $_.Groups[1].Value; try { [regex]::Unescape($v) } catch { $v } })
$hit = @($summaries | Where-Object { $_ -match $strong -or $_ -match $buttonish }) | Select-Object -First 1
if (-not $hit -and $plain -match $hotkey) { $hit = $Matches[0] }
if (-not $hit -and $plain -match $mailContext) { $hit = @($summaries | Where-Object { $_ -match $weak }) | Select-Object -First 1 }
if (-not $hit -and ([string]$payload.tool_name -match 'javascript_tool$' -or $text -match '"name"\s*:\s*"javascript_tool"') -and $plain -match $jsMutation) {
    $hit = "page script $($Matches[0])"
}
if ($hit) {
    $hit = ([string]$hit).Substring(0, [Math]::Min(80, ([string]$hit).Length))
    $out = @{
        hookSpecificOutput = @{
            hookEventName            = "PreToolUse"
            permissionDecision       = "deny"
            permissionDecisionReason = "secretary-email-action-gate: mail mutation in the browser ('$hit') is denied — use the gated /api/tools/email path (operator-proof) or leave it for the operator (#1585, #3490, #3500)"
        }
    } | ConvertTo-Json -Compress -Depth 6
    Write-Output $out
}
exit 0
