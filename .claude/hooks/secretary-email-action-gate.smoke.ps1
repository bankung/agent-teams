# Smoke test for secretary-email-action-gate.ps1 (#1585, #3490, #3500, #3511).
# Table-driven: browser tool payload -> expected decision ('deny' or 'none' = no JSON output).
# cmd.exe stdin redirection mirrors pretooluse-bash-gate.smoke.ps1 (no PS 5.1 NativeCommandError wrapping).
#
# Run:  powershell -NoProfile -ExecutionPolicy Bypass -File .claude/hooks/secretary-email-action-gate.smoke.ps1
#       SMOKE_HOOKS=<dir> runs the hook copy in <dir> instead (old-vs-new evidence).
# Exit: 0 on all-pass, 1 on any failure.

$hookDir = if ($env:SMOKE_HOOKS) { $env:SMOKE_HOOKS } else { $PSScriptRoot }
$hook = Join-Path $hookDir 'secretary-email-action-gate.ps1'
if (-not (Test-Path $hook)) { "[FATAL] Hook not found at $hook"; exit 2 }
$tmp = [IO.Path]::Combine([IO.Path]::GetTempPath(), "secretary-gate-smoke-$PID.json")

function Get-Decision($obj) {
    $obj | ConvertTo-Json -Compress -Depth 8 | Set-Content $tmp -Encoding utf8 -NoNewline
    $out = cmd.exe /c "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$hook`" < `"$tmp`" 2>nul"
    $d = [regex]::Match(($out -join ''), '"permissionDecision"\s*:\s*"([\w-]+)"').Groups[1].Value
    if ($d) { $d } else { 'none' }
}
function C($s) { @{ tool_name = 'mcp__Claude_Browser__computer'; tool_input = @{ action = 'left_click'; action_summary = $s } } }
function K($key, $s) { @{ tool_name = 'mcp__Claude_Browser__computer'; tool_input = @{ action = 'key'; text = $key; action_summary = $s } } }
function J($js) { @{ tool_name = 'mcp__claude-in-chrome__javascript_tool'; tool_input = @{ action = 'javascript_exec'; text = $js } } }
function B($item) { @{ tool_name = 'mcp__Claude_Browser__browser_batch'; tool_input = @{ actions = @($item) } } }

$tests = @(
    # mail-only verbs / controls / hotkey: deny on any page (#3511 — a click payload has no URL)
    @('Sends the reply to bob@example.com', (C 'Sends the reply to bob@example.com'), 'deny'),
    @('Archives the thread', (C 'Archives the thread'), 'deny'),
    @('Clicks the Send button on the draft', (C 'Clicks the Send button on the draft'), 'deny'),
    @('Empties the Gmail trash', (C 'Empties the Gmail trash'), 'deny'),
    @('Deletes the selected conversations', (C 'Deletes the selected conversations'), 'deny'),
    @("Clicks the 'Send' button (quoted)", (C "Clicks the 'Send' button in the Gmail compose window"), 'deny'),
    @('Clicks the "Send" button (dquoted)', (C 'Clicks the "Send" button in the Gmail compose window'), 'deny'),
    @('Clicks Send (bare label)', (C 'Clicks Send in the Gmail compose window'), 'deny'),
    @("Clicks 'Delete' on a thread", (C "Clicks 'Delete' on the selected Gmail thread"), 'deny'),
    @('Clicks the Delete option', (C 'Clicks the Delete option in the Outlook inbox toolbar'), 'deny'),
    @('Clicks the Send button (no mail word)', (C 'Clicks the Send button'), 'deny'),
    @('Clicks Archive in the toolbar (no mail word)', (C 'Clicks Archive in the toolbar'), 'deny'),
    @('Presses the Delete key (no mail word)', (K 'Delete' 'Presses the Delete key'), 'deny'),
    @('Presses Ctrl+Enter to send', (K 'ctrl+Enter' 'Presses Ctrl+Enter to send the draft message'), 'deny'),
    @('ctrl+Return, vague summary', (K 'ctrl+Return' 'Presses the compose shortcut'), 'deny'),
    @('Clicking the Send button', (C 'Clicking the Send button'), 'deny'),
    @('Sending the draft', (C 'Sending the draft'), 'deny'),
    @('Deleting the selected email', (C 'Deleting the selected email'), 'deny'),
    @('Opens the draft and clicks Send', (C 'Opens the draft and clicks Send'), 'deny'),
    @('Confirms the Send anyway dialog', (C 'Confirms the Send anyway dialog'), 'deny'),
    @('Clicks Unsubscribe', (C 'Clicks Unsubscribe'), 'deny'),
    @('Clicks Remove on the selected thread', (C 'Clicks Remove on the selected thread'), 'deny'),
    @('Moves the selected item to Trash', (C 'Moves the selected item to Trash'), 'deny'),
    @('Clicks Report spam', (C 'Clicks Report spam'), 'deny'),
    @('... the one labelled Delete', (C 'Clicks the toolbar icon that is shown above the conversation list, the one labelled Delete'), 'deny'),
    @('Discards the cookie banner', (C 'Discards the cookie banner'), 'none'),
    @('Control+Enter, vague summary', (K 'Control+Enter' 'Presses the shortcut'), 'deny'),
    @('ctrl+shift+Enter, vague summary', (K 'ctrl+shift+Enter' 'Presses the shortcut'), 'deny'),
    @('batch: Deletes the selected conversations', (B @{ name = 'computer'; input = @{ action = 'left_click'; action_summary = 'Deletes the selected conversations' } }), 'deny'),
    # weak verbs: deny only in a mail context
    @('Submits the inbox cleanup dialog', (C 'Submits the inbox cleanup dialog'), 'deny'),
    @('Marks the thread as read', (C 'Marks the thread as read'), 'deny'),
    @('Moves the message to Spam', (C 'Moves the message to the Spam folder'), 'deny'),
    # page scripts that click / submit / send: deny on any page
    @('js .click()', (J "document.querySelector('[aria-label=Send]').click()"), 'deny'),
    @('js el.onclick()', (J 'el.onclick()'), 'deny'),
    @("js q['click']()", (J "q['click']()"), 'deny'),
    @('js click.call', (J 'HTMLElement.prototype.click.call(q)'), 'deny'),
    @('batch js requestSubmit', (B @{ name = 'javascript_tool'; input = @{ action = 'javascript_exec'; text = 'document.forms[0].requestSubmit()' } }), 'deny'),
    # read-only browsing and job-site work: no decision
    @('Opens the Sent folder in Gmail', (C 'Opens the Sent folder in Gmail'), 'none'),
    @('Clicks the first message in the inbox', (C 'Clicks the first message in the inbox'), 'none'),
    @('Clicks the Trash folder in Gmail', (C 'Clicks the Trash folder in Gmail'), 'none'),
    @('Submits the search for invoices in Gmail', (C 'Submits the search for invoices in Gmail'), 'none'),
    @('Submits a search for invoices in Gmail', (C 'Submits a search for invoices in Gmail'), 'none'),
    @('Submits the Gmail search query', (C 'Submits the Gmail search query'), 'none'),
    @('Hovers over the Delete icon', (C 'Hovers over the Delete icon on the email'), 'none'),
    @('Scrolls to find the Reply button', (C 'Scrolls down to find the Reply button on the message'), 'none'),
    @('Moves the cursor over a message', (C 'Moves the cursor over the first message in the inbox'), 'none'),
    @('Deletes the existing search text', (C 'Deletes the existing search text'), 'none'),
    @('Clicks Submit on the JobsDB form', (C 'Clicks Submit on the JobsDB application form'), 'none'),
    @('Marks the job as saved', (C 'Marks the job as saved'), 'none'),
    @('Clicks the Apply button on the job page', (C 'Clicks the Apply button on the job page'), 'none'),
    @('js document.title', (J 'document.title'), 'none'),
    # #3511 review F1-F3: a title / typed text / folder nav that merely contains the word
    @("Clicks the email titled 'Please send ...'", (C "Clicks the email titled 'Please send the signed contract' in the list"), 'none'),
    @("Clicks the email titled 'Please reply ...'", (C "Clicks the email titled 'Please reply by Friday'"), 'none'),
    @("Clicks the 'Forward Deployed Engineer' job card", (C "Clicks the 'Forward Deployed Engineer' job card in the results list"), 'none'),
    @("Clicks the 'Customer Reply Specialist' listing", (C "Clicks the 'Customer Reply Specialist' job listing"), 'none'),
    @("Types 'how to send cold outreach'", @{ tool_name = 'mcp__Claude_Browser__computer'; tool_input = @{ action = 'type'; text = 'how to send cold outreach'; action_summary = "Types 'how to send cold outreach' in the LinkedIn search box" } }, 'none'),
    @('Scrolls to forward-looking statements', (C 'Scrolls down to forward-looking statements section of the job page'), 'none'),
    @('Clicks Trash in the left sidebar', (C 'Clicks Trash in the left sidebar'), 'none'),
    @('Clicks Spam in the Gmail sidebar', (C 'Clicks Spam in the Gmail sidebar'), 'none'),
    @('Selects the Archive filter chip', (C 'Selects the Archive filter chip'), 'none'),
    @('Deletes the previous keyword', (C 'Deletes the previous keyword from the search box'), 'none'),
    @('Deletes the existing search term', (C 'Deletes the existing search term'), 'none'),
    @('Empties the search field', (C 'Empties the search field'), 'none'),
    @('Clicks the Reply count under the post', (C 'Clicks the Reply count under the post'), 'none'),
    @('Clicks Show more on the job description', (C 'Clicks Show more on the job description'), 'none')
)

$fail = 0
foreach ($t in $tests) {
    $got = Get-Decision $t[1]
    if ($got -eq $t[2]) { "[PASS] {0,-5} {1}" -f $got, $t[0] } else { $fail++; "[FAIL] got={0,-5} want={1,-5} {2}" -f $got, $t[2], $t[0] }
}
Remove-Item $tmp -ErrorAction SilentlyContinue
"$($tests.Count - $fail)/$($tests.Count) pass"
if ($fail) { exit 1 }
exit 0
