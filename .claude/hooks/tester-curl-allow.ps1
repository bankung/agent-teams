# Keeps dev-tester HTTP calls on localhost (any port).
#
# Scoped via .claude/agents/dev-tester.md frontmatter (PreToolUse on Bash|PowerShell).
# Other roles (Lead, dev-backend, dev-frontend, dev-devops, dev-reviewer)
# do NOT inherit this hook.
#
# Decision matrix (#3489, #3495):
#   deny (block + fix-it reason) when
#   - an HTTP segment (curl / Invoke-RestMethod / Invoke-WebRequest / irm / iwr) has a target
#     that is missing or not localhost / 127.0.0.1 / [::1]: every bare (non-flag, non-flag-value)
#     word counts, not only `://` words (#3500 item 3), or it is re-routed (--next / --connect-to /
#     --resolve / proxy / config file, incl. clustered -sx / -Kfile);
#   - a non-local `scheme://` URL appears anywhere (runners: find -exec, watch, ssh, cmd /c, Start-Process);
#   - an HTTP verb rides as an argument of another command, or Bash runs a `$var` command;
#   - a proxy / CURL_HOME env assignment, wget, or a Bash backtick appears next to an HTTP verb.
#   everything else -> no decision (exit 0, no JSON): the main Bash gate decides.
# The hook used to emit "allow" for first-word curl + a localhost substring, which
# also auto-approved `curl localhost:8456 ; <anything>` (#3483 H3).
# shortcut: .NET HTTP ([Net.WebClient], [Net.Http.HttpClient]), a ~/.curlrc the tester wrote itself, and in
# PowerShell a bare host after a ( ) expression stay opaque; upgrade: a real parser if the gate log shows misses.
#
# Why localhost-any-port: tester probes the API (8456) AND the web UI (5431)
# AND any future Playwright / dev-tool port (Kanban #406, #705). Localhost-any-port
# is the safety boundary — the dev stack is throwaway/containerized.

$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
$cmd = [string]$payload.tool_input.command
if (-not $cmd) { exit 0 }
. (Join-Path $PSScriptRoot '_shared.ps1')
$ps = $payload.tool_name -eq 'PowerShell'

$http      = @('curl', 'invoke-restmethod', 'invoke-webrequest', 'irm', 'iwr')
$inert     = @('grep', 'rg', 'echo', 'printf', 'select-string', 'sls', 'findstr', 'write-host', 'write-output', 'git')
$local     = '^(?i)(https?://)?(localhost|127\.0\.0\.1|\[::1\])(:\d+)?([/?#]|$)'
# flags whose next word is a value, not a target (curl: case-sensitive; PowerShell: any case)
$curlValue = @('-o', '-H', '-d', '-X', '-w', '-u', '-A', '-e', '-b', '-c', '-F', '-T', '-m', '-D', '-E', '-r', '-C', '-Y', '-y',
               '--output', '--header', '--data', '--data-raw', '--data-binary', '--data-urlencode', '--json',
               '--request', '--write-out', '--user', '--user-agent', '--referer', '--cookie', '--cookie-jar',
               '--form', '--upload-file', '--max-time', '--connect-timeout', '--retry', '--dump-header',
               '--cacert', '--cert', '--key', '--max-redirs', '--limit-rate', '--retry-delay', '--retry-max-time',
               '--max-filesize', '--output-dir', '--range', '--continue-at', '--speed-limit', '--speed-time')
$valueLetters = @('o', 'H', 'd', 'X', 'w', 'u', 'A', 'e', 'b', 'c', 'F', 'T', 'm', 'D', 'E', 'r', 'C', 'Y', 'y')   # -sSo FILE
$psValue   = @('-headers', '-method', '-body', '-outfile', '-contenttype', '-timeoutsec', '-infile', '-useragent',
               '-websession', '-sessionvariable', '-credential', '-maximumredirection', '-transferencoding')
$reroute   = '^-[xK:]$|^(?i:--next|--connect-to|--resolve|--proxy|--preproxy|--config|--doh-url|-proxy|-proxycredential|-proxyusedefaultcredentials)$'   # -x/-K case-sensitive (-X = method)

$foreign = $false
# Bash: the statement view keeps {x} / (..) inside words, so a curl line is judged whole (#3495 T1);
# PowerShell needs the default view (it splits @{..} header tables off the line).
try { $segments = Get-ShellSegments -Command $cmd -PowerShell:$ps -KeepBrackets:(-not $ps) } catch { $segments = @(); $foreign = $true }   # fault -> deny, never a silent pass
$leaves = @($segments | ForEach-Object { $_ } | ForEach-Object { Get-CommandLeaf $_ })
$hasHttp = @($leaves | Where-Object { $http -contains $_ }).Count -gt 0
foreach ($u in [regex]::Matches($cmd, '(?i)\b(https?|ftps?|wss?)://[^\s''"`]*')) { if ($u.Value -notmatch $local) { $foreign = $true } }
if ($leaves -contains 'wget' -or ($hasHttp -and ($cmd -match '(?i)\b(\w*_proxy|curl_home)\s*=' -or (-not $ps -and $cmd.Contains('`'))))) { $foreign = $true }

foreach ($seg in $segments) {
    if ($foreign) { break }
    $h = Get-SegmentHead -Tokens $seg
    if ($h -ge $seg.Count) { continue }
    $leaf = Get-CommandLeaf $seg[$h]
    if ($http -notcontains $leaf) {
        $carried = @(if ($h + 1 -lt $seg.Count) { $seg[($h + 1)..($seg.Count - 1)] | Where-Object { $http -contains (Get-CommandLeaf $_) } })
        if (($carried.Count -and $inert -notcontains $leaf) -or (-not $ps -and $seg[$h] -match '^\$')) { $foreign = $true }
        continue
    }
    $targets = 0
    for ($i = $h + 1; $i -lt $seg.Count; $i++) {
        $t = $seg[$i]; $name = ($t -split '=', 2)[0]; $val = $null
        if ($name -cmatch $reroute -or ($leaf -eq 'curl' -and $t -cmatch '^-[A-Za-z]*[xK]')) { $foreign = $true; break }
        if ($name -in @('--url', '-uri')) {                                       # -uri: PowerShell, any case
            $val = if ($t.Contains('=')) { ($t -split '=', 2)[1] } elseif ($i + 1 -lt $seg.Count) { $seg[++$i] } else { '' }
        }
        elseif ($curlValue -ccontains $t -or $psValue -contains $t -or
                ($leaf -eq 'curl' -and $t -cmatch '^-[A-Za-z]{2,}$' -and $valueLetters -ccontains [string]$t[-1])) { $i++; continue }
        elseif ($t -like '-*' -or $t -eq '@' -or $t -match '^\d+$') { continue }   # bare flag / PS hashtable stub / number
        else { $val = $t }
        $targets++
        if ($val -notmatch $local) { $foreign = $true; break }
    }
    if ($targets -eq 0) { $foreign = $true }
}
if (-not $foreign) { exit 0 }

$reason = @"
dev-tester HTTP calls are limited to localhost / 127.0.0.1 / [::1] (any port: API 8456, web 5431, dev tools).
Fix the command:
- One plain curl / Invoke-RestMethod line with the URL inline (no variable, no backticks, no wrapper such as
  find -exec / watch / cmd /c / Start-Process), e.g. curl -s -H "X-Project-Id: 1" http://localhost:8456/api/tasks/1
  or Invoke-RestMethod -Uri "http://localhost:8456/api/tasks/1" -Headers @{ "X-Project-Id" = "1" }.
- Every non-flag word on the line counts as a target - no second host, no --next, --connect-to, --resolve,
  proxy (-x, *_proxy=) or config-file (-K) flags. Use curl, not wget.
If you genuinely need an external host, do not work around this hook: propose the exact command in
your final report and let Lead surface it to the user.
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
