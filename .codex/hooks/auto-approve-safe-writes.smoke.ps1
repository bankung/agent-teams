# Smoke test for auto-approve-safe-writes.ps1.
# Invokes the hook with 5 input shapes and asserts the expected decision for each.
# Exits 0 on all-pass, non-zero on any fail.

$ErrorActionPreference = 'Stop'
$hook = Join-Path $PSScriptRoot 'auto-approve-safe-writes.ps1'

if (-not (Test-Path $hook)) {
    Write-Output "[FAIL] Hook not found at $hook"
    exit 1
}

function Invoke-Hook {
    param([string]$JsonInput)
    $stdout = $JsonInput | powershell -NoProfile -ExecutionPolicy Bypass -File $hook
    return $stdout
}

function Get-Decision {
    param([string]$HookOutput)
    if ([string]::IsNullOrWhiteSpace($HookOutput)) { return $null }
    try {
        $obj = $HookOutput | ConvertFrom-Json
        return $obj.hookSpecificOutput.permissionDecision
    } catch {
        return "<unparseable: $HookOutput>"
    }
}

$tests = @(
    @{
        Name     = 'Write on api/src/foo.py'
        Input    = '{"tool_name":"Write","tool_input":{"file_path":"api/src/foo.py","content":"x"}}'
        Expected = 'allow'
    },
    @{
        Name     = 'Write on context/standards/web/buttons.md'
        Input    = '{"tool_name":"Write","tool_input":{"file_path":"context/standards/web/buttons.md","content":"x"}}'
        Expected = $null
    },
    @{
        Name     = 'Bash (not Write/Edit)'
        Input    = '{"tool_name":"Bash","tool_input":{"command":"ls"}}'
        Expected = $null
    },
    @{
        Name     = 'Write on api/../../../etc/passwd (path-traversal)'
        Input    = '{"tool_name":"Write","tool_input":{"file_path":"api/../../../etc/passwd","content":"x"}}'
        Expected = 'ask'
    },
    @{
        Name     = 'Edit on _scratch/foo.json'
        Input    = '{"tool_name":"Edit","tool_input":{"file_path":"_scratch/foo.json","old_string":"a","new_string":"b"}}'
        Expected = 'allow'
    }
)

$failCount = 0
foreach ($t in $tests) {
    $output   = Invoke-Hook -JsonInput $t.Input
    $decision = Get-Decision -HookOutput $output

    $expectedDisplay = if ($null -eq $t.Expected) { '<pass-through>' } else { $t.Expected }
    $actualDisplay   = if ($null -eq $decision)   { '<pass-through>' } else { $decision }

    if ($decision -eq $t.Expected) {
        Write-Output "[PASS] $($t.Name) - expected=$expectedDisplay actual=$actualDisplay"
    } else {
        Write-Output "[FAIL] $($t.Name) - expected=$expectedDisplay actual=$actualDisplay"
        $failCount++
    }
}

if ($failCount -gt 0) {
    Write-Output ""
    Write-Output "$failCount test(s) failed."
    exit 1
}

exit 0
