# PreToolUse hook for general-researcher — Bash tool (frontmatter-scoped).
# Every segment is firecrawl or a read-only filter (`firecrawl ... | head`) -> no decision:
#   the main Bash gate decides. Anything else (incl. `firecrawl ... ; <cmd>`) -> deny.
# #3489: was "first word firecrawl -> allow", which also auto-approved a chained command.

# Hook input arrives on stdin (not a param) — same as the sibling hooks (#3437).
$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
. (Join-Path $PSScriptRoot '_shared.ps1')

try {   # a tokenizer fault falls to the deny below, never to a silent pass
    $segs = Get-ShellSegments -Command ([string]$payload.tool_input.command)
    $leaves = @($segs | ForEach-Object { $h = Get-SegmentHead -Tokens $_; if ($h -lt $_.Count) { Get-CommandLeaf $_[$h] } else { '' } })
    $filters = @('firecrawl', 'head', 'tail', 'grep', 'jq', 'wc', 'sort', 'uniq', 'cut', 'tr')
    if ($leaves -contains 'firecrawl' -and @($leaves | Where-Object { $filters -notcontains $_ }).Count -eq 0) { exit 0 }
} catch { }

$output = @{
    hookSpecificOutput = @{
        hookEventName            = "PreToolUse"
        permissionDecision       = "deny"
        permissionDecisionReason = "general-researcher Bash is restricted to firecrawl commands (optionally piped to head/tail/grep/jq/wc/sort/uniq/cut/tr)"
    }
} | ConvertTo-Json -Compress -Depth 4
Write-Output $output
exit 2
