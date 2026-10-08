# PreToolUse hook for general-researcher — Bash tool
# Allow: firecrawl CLI commands only
# Block: everything else

# Hook input arrives on stdin (not a param) — same as the sibling hooks (#3437).
$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
$command = $payload.tool_input.command

if ($command -match '^firecrawl\s') {
    $output = @{
        hookSpecificOutput = @{
            hookEventName            = "PreToolUse"
            permissionDecision       = "allow"
            permissionDecisionReason = "firecrawl CLI command auto-approved for general-researcher"
        }
    } | ConvertTo-Json -Compress -Depth 4
    Write-Output $output
    exit 0
}

# Deny everything else — researcher should not run arbitrary Bash
$output = @{
    hookSpecificOutput = @{
        hookEventName            = "PreToolUse"
        permissionDecision       = "deny"
        permissionDecisionReason = "general-researcher Bash is restricted to firecrawl commands only"
    }
} | ConvertTo-Json -Compress -Depth 4
Write-Output $output
exit 2
