# PreToolUse hook for general-researcher — Bash tool (frontmatter-scoped).
# Allowlist (#3495): every segment is a bare `firecrawl search|scrape|map|crawl` (or --status/--version)
#   or a bare read-only filter, with no env prefix / wrapper, no redirect, and the only file write a
#   firecrawl -o under agent-teams _scratch/ -> no decision: the main Bash gate decides.
#   Anything else -> deny with the fix recipe.
# #3489: was "first word firecrawl -> allow", which also auto-approved a chained command.
# #3495 review: filters are allowlisted per flag (sort -o / --compress-program write or execute),
#   firecrawl --api-url / FIRECRAWL_API_URL= would ship the API key elsewhere, and the
#   env / init / setup / login / download subcommands write files.

# Hook input arrives on stdin (not a param) — same as the sibling hooks (#3437).
$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
. (Join-Path $PSScriptRoot '_shared.ps1')

$cmd  = [string]$payload.tool_input.command
$root = ((Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path -replace '\\', '/').TrimEnd('/')
function Test-ScratchPath([string]$p) {
    $p = $p -replace '\\', '/' -replace '^/([A-Za-z])/', '$1:/'   # Git Bash /c/... -> C:/...
    $p -notmatch '\.\.' -and ($p -match '^(\./)?_scratch/[^/]' -or
        ($p.Length -gt "$root/_scratch/".Length -and $p.StartsWith("$root/_scratch/", [StringComparison]::OrdinalIgnoreCase)))
}

try {   # a tokenizer fault falls to the deny below, never to a silent pass
    $ok = $true; $sawFirecrawl = $false
    foreach ($seg in (Get-ShellSegments -Command $cmd)) {
        # no env prefix (NODE_OPTIONS=, FIRECRAWL_API_URL=) or wrapper, and a bare name (./grep is not grep)
        if ($seg.Count -eq 0 -or (Get-SegmentHead -Tokens $seg) -ne 0 -or $seg[0] -match '[\\/=]') { $ok = $false; break }
        $leaf = Get-CommandLeaf $seg[0]
        $rest = @(if ($seg.Count -gt 1) { $seg[1..($seg.Count - 1)] })
        switch ($leaf) {
            'firecrawl' {
                $sawFirecrawl = $true
                $sub = @($rest | Where-Object { $_ -notlike '-*' })[0]
                if ($sub -notin @('search', 'scrape', 'map', 'crawl') -and -not ($rest.Count -eq 1 -and $rest[0] -in @('--status', '--version', '--help'))) { $ok = $false }
                if ($sub -eq 'scrape' -and @($rest | Where-Object { $_ -match '^https?://' }).Count -gt 1) { $ok = $false }   # multi-URL scrape writes .firecrawl/
                for ($i = 0; $i -lt $rest.Count; $i++) {
                    $a = $rest[$i]; $out = $null
                    if ($a -match '^--(api-url|api-key|proxy)' -or $a -ceq '-k') { $ok = $false }
                    if ($a -ceq '-o' -or $a -eq '--output') { $out = if ($i + 1 -lt $rest.Count) { $rest[++$i] } else { '' } }
                    elseif ($a -cmatch '^-o=?(.+)$' -or $a -match '^--output=(.+)$') { $out = $Matches[1] }
                    if ($null -ne $out -and -not (Test-ScratchPath $out)) { $ok = $false }
                }
            }
            'sort' {   # flag allowlist: -o, -T, -S, --compress-program, --files0-from are not on it
                for ($i = 0; $i -lt $rest.Count; $i++) {
                    $a = $rest[$i]
                    if ($a -cin @('-k', '-t')) { $i++ }
                    elseif ($a -like '-*' -and $a -cnotmatch '^-[bdfghinMrRsuVz]+$|^-[kt].|^--(reverse|numeric-sort|unique|ignore-case|general-numeric-sort|human-numeric-sort|version-sort|stable|key=.+|field-separator=.+)$') { $ok = $false }
                }
            }
            'uniq' {   # a positional operand is an output file
                for ($i = 0; $i -lt $rest.Count; $i++) {
                    $a = $rest[$i]
                    if ($a -cin @('-f', '-s', '-w')) { $i++ }
                    elseif ($a -cnotmatch '^-[cdDiuz]+$|^-[fsw]\d+$') { $ok = $false }
                }
            }
            'jq' { if (@($rest | Where-Object { $_ -match '\benv\b|\$ENV' }).Count) { $ok = $false } }   # env would hand FIRECRAWL_API_KEY to the model
            { $_ -in @('head', 'tail', 'grep', 'wc', 'cut', 'tr') } { }
            default { $ok = $false }
        }
        if (-not $ok) { break }
    }
    # The tokenizer drops redirects, so check the raw text. shortcut: a `>` inside a quoted
    # search query also denies (the reason says to drop it); upgrade: quote-aware scan.
    if ($ok -and $sawFirecrawl -and ($cmd -replace '\d?>&\d|\d?>\s*(/dev/null|nul)\b', '') -notmatch '>') { exit 0 }
} catch { }

$reason = @"
general-researcher Bash runs firecrawl only. Fix the command, do not switch shell or tool:
- ONE firecrawl search / scrape / map / crawl per Bash call: no ; && || chaining, no cd / mkdir /
  variables / VAR=value prefixes, no --api-url / --api-key.
- Save output with -o <ABSOLUTE path under agent-teams/_scratch/> (scrape, search, crawl all take -o), never > or >>.
  One URL per scrape call.
- Piping to head / tail / grep / jq / wc / sort / uniq / cut / tr is fine (bare names, no sort -o / uniq output file).
- Several pages = several Bash calls in ONE message.
Example: firecrawl scrape "https://example.com/docs" -o C:/Users/.../agent-teams/_scratch/research-topic-page1.md
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
