# PreToolUse hook for dev-researcher — Bash tool
# Allow: firecrawl CLI commands only
# Block: everything else

param($inputJson)

$input = $inputJson | ConvertFrom-Json
$command = $input.tool_input.command

if ($command -match '^firecrawl\s') {
    $output = @{
        hookSpecificOutput = @{
            hookEventName            = "PreToolUse"
            permissionDecision       = "allow"
            permissionDecisionReason = "firecrawl CLI command auto-approved for dev-researcher"
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
        permissionDecisionReason = "dev-researcher Bash is restricted to firecrawl commands only"
    }
} | ConvertTo-Json -Compress -Depth 4
Write-Output $output
exit 2
