# Smoke test for draft-approval-policies-gate.ps1 (Kanban #1274).
#
# Table-driven: 6 input shapes + their expected permissionDecision.
# Mocks the API + _runtime/lead_project_id.txt via environment-variable
# overrides that the hook honors when set (no live HTTP, no live filesystem
# beyond temp fixture files).
#
# Run:  powershell -NoProfile -ExecutionPolicy Bypass -File _scratch/draft-approval-policies-gate.smoke.ps1
# Exit: 0 on all-pass, non-zero on any failure.

$ErrorActionPreference = 'Stop'
$hook = Join-Path $PSScriptRoot 'approval-policies-gate.ps1'
if (-not (Test-Path $hook)) {
    Write-Output "[FATAL] Hook not found at $hook"
    exit 2
}

# Build a tmp dir under _scratch/ for fixture files.
$tmpDir = Join-Path $PSScriptRoot 'approval-policies-gate-tmp'
if (Test-Path $tmpDir) { Remove-Item -Recurse -Force $tmpDir }
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

# Fixture 1 — project_id file (valid integer).
$projectIdFile = Join-Path $tmpDir 'lead_project_id.txt'
'1' | Set-Content -Path $projectIdFile -NoNewline

# Fixture 2 — full ruleset that exercises every action verb + matcher kind.
$rulesPolicy = @{
    approval_policies = @{
        rules = @(
            @{
                name = 'allow-linkedin-fetch'
                match = @{ tool_name = 'WebFetch'; target_url_pattern = '^https://linkedin\.com/.*' }
                action = 'auto_approve'
                reason = 'linkedin posts pre-approved by operator policy'
            },
            @{
                name = 'deny-bash-rm-rf'
                match = @{ tool_name = 'Bash'; content_predicate = 'rm\s+-rf' }
                action = 'auto_deny'
                reason = 'rm -rf is reserved for human-only execution'
            },
            @{
                name = 'requires-attention-chrome-click'
                match = @{ tool_name = 'mcp__Claude_in_Chrome__click' }
                action = 'requires_attention'
                reason = 'chrome click on any URL needs human review per policy'
            }
        )
    }
} | ConvertTo-Json -Depth 8

$rulesPolicyFile = Join-Path $tmpDir 'policy-with-rules.json'
$rulesPolicy | Set-Content -Path $rulesPolicyFile -Encoding utf8

# Fixture 3 — project row with null approval_policies (empty-policy case).
$emptyPolicyFile = Join-Path $tmpDir 'policy-empty.json'
'{ "id": 1, "name": "agent-teams", "approval_policies": null }' | Set-Content -Path $emptyPolicyFile -Encoding utf8

# Fixture 4 — invalid policy fixture path (simulates API-down).
$missingPolicyFile = Join-Path $tmpDir 'policy-does-not-exist.json'   # NB: never written

function Invoke-Hook {
    param([string]$JsonInput, [string]$PolicyFixture, [string]$ProjectIdFixture)
    # Per-test env scope: set, invoke via stdin from a temp file, then clear.
    # cmd.exe wrapper redirects stderr to a temp file so PS 5.1 doesn't wrap
    # native-command stderr into NativeCommandError ErrorRecord objects (which
    # interact badly with $ErrorActionPreference='Stop' and corrupt output).
    $env:APPROVAL_POLICIES_GATE_PROJECT_FILE = $ProjectIdFixture
    $env:APPROVAL_POLICIES_GATE_POLICY_FILE  = $PolicyFixture
    $stdinFile = Join-Path $tmpDir ("stdin-" + [Guid]::NewGuid().ToString() + ".json")
    $stderrFile = Join-Path $tmpDir ("stderr-" + [Guid]::NewGuid().ToString() + ".log")
    Set-Content -Path $stdinFile -Value $JsonInput -Encoding utf8 -NoNewline
    $stdout = ''
    $stderr = ''
    try {
        # Use cmd.exe shell redirection (<, 2>) so native powershell.exe receives
        # JSON on stdin and stderr lands in a file — bypasses PS 5.1's quirky
        # NativeCommandError wrapping entirely.
        $cmdLine = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$hook`" < `"$stdinFile`" 2> `"$stderrFile`""
        $stdout = & cmd.exe /c $cmdLine
        if (Test-Path $stderrFile) {
            $stderr = (Get-Content -Raw -Path $stderrFile)
            if (-not $stderr) { $stderr = '' }
        }
    } finally {
        Remove-Item -Force $stdinFile  -ErrorAction SilentlyContinue
        Remove-Item -Force $stderrFile -ErrorAction SilentlyContinue
        Remove-Item Env:\APPROVAL_POLICIES_GATE_PROJECT_FILE -ErrorAction SilentlyContinue
        Remove-Item Env:\APPROVAL_POLICIES_GATE_POLICY_FILE  -ErrorAction SilentlyContinue
    }
    if ($null -eq $stdout) { $stdout = '' }
    $combined = ($stdout | ForEach-Object { $_.ToString() }) -join "`n"
    if ($stderr) { $combined = "[stderr] $stderr`n[stdout] $combined" }
    return $combined
}

function Get-Decision {
    param([string]$HookOutput)
    if ([string]::IsNullOrWhiteSpace($HookOutput)) { return $null }
    # The hook emits one JSON object to stdout. Stderr WARN lines may be
    # mixed in (via 2>&1 in PS 5.1 they wrap as NativeCommandError text).
    # Extract the JSON via regex over the entire blob — robust to wrapping.
    $jsonMatch = [regex]::Match($HookOutput, '\{[^{}]*"hookSpecificOutput"[^{}]*\{[^{}]*permissionDecision[^{}]*\}[^{}]*\}')
    if (-not $jsonMatch.Success) {
        return "<no-decision-json: $HookOutput>"
    }
    try {
        $obj = $jsonMatch.Value | ConvertFrom-Json
        return $obj.hookSpecificOutput.permissionDecision
    } catch {
        return "<unparseable: $($jsonMatch.Value)>"
    }
}

$tests = @(
    @{
        Name = 'C1 allow rule on WebFetch + linkedin URL'
        Input = '{"tool_name":"WebFetch","tool_input":{"url":"https://linkedin.com/posts/create","prompt":"draft"}}'
        Policy = $rulesPolicyFile
        ProjectId = $projectIdFile
        Expected = 'allow'
    },
    @{
        Name = 'C2 deny rule on Bash + rm -rf content'
        Input = '{"tool_name":"Bash","tool_input":{"command":"rm -rf /tmp/danger"}}'
        Policy = $rulesPolicyFile
        ProjectId = $projectIdFile
        Expected = 'deny'
    },
    @{
        Name = 'C3 requires_attention rule on mcp__Claude_in_Chrome__click'
        Input = '{"tool_name":"mcp__Claude_in_Chrome__click","tool_input":{"url":"https://example.com","selector":"#submit"}}'
        Policy = $rulesPolicyFile
        ProjectId = $projectIdFile
        Expected = 'ask'
    },
    @{
        Name = 'C4 no rule matched falls through to ask'
        Input = '{"tool_name":"WebFetch","tool_input":{"url":"https://random-site.example/"}}'
        Policy = $rulesPolicyFile
        ProjectId = $projectIdFile
        Expected = 'ask'
    },
    @{
        Name = 'C5 empty approval_policies (null) falls through to ask'
        Input = '{"tool_name":"WebFetch","tool_input":{"url":"https://linkedin.com/posts/create"}}'
        Policy = $emptyPolicyFile
        ProjectId = $projectIdFile
        Expected = 'ask'
    },
    @{
        Name = 'C6 policy fixture missing (simulates API down) — fail-open ask'
        Input = '{"tool_name":"WebFetch","tool_input":{"url":"https://linkedin.com/posts/create"}}'
        Policy = $missingPolicyFile
        ProjectId = $projectIdFile
        Expected = 'ask'
    }
)

$failCount = 0
foreach ($t in $tests) {
    Write-Output "----- $($t.Name) -----"
    Write-Output "[CMD] env APPROVAL_POLICIES_GATE_POLICY_FILE=$($t.Policy)"
    Write-Output "[CMD] env APPROVAL_POLICIES_GATE_PROJECT_FILE=$($t.ProjectId)"
    Write-Output "[CMD] stdin: $($t.Input)"

    $rawOut = Invoke-Hook -JsonInput $t.Input -PolicyFixture $t.Policy -ProjectIdFixture $t.ProjectId
    Write-Output "[OUT] $rawOut"

    $decision = Get-Decision -HookOutput $rawOut
    $expectedDisplay = if ($null -eq $t.Expected) { '<pass-through>' } else { $t.Expected }
    $actualDisplay   = if ($null -eq $decision)   { '<pass-through>' } else { $decision }

    if ($decision -eq $t.Expected) {
        Write-Output "[PASS] expected=$expectedDisplay actual=$actualDisplay"
    } else {
        Write-Output "[FAIL] expected=$expectedDisplay actual=$actualDisplay"
        $failCount++
    }
    Write-Output ""
}

# Cleanup tmp fixtures.
Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue

Write-Output "============================"
if ($failCount -gt 0) {
    Write-Output "$failCount test(s) FAILED."
    exit 1
} else {
    Write-Output "All 6 tests PASSED."
    exit 0
}
