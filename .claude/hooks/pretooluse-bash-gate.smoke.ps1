# Smoke test for pretooluse-bash-gate.ps1 — focused on the #2706 bind-bootstrap
# allow, plus regression coverage that the deny guards + no-binding fallthrough
# still behave. Table-driven: command shape -> expected permissionDecision.
#
# The no-binding condition (the exact "fresh session" bug #2706 fixes) is forced
# via APPROVAL_POLICIES_GATE_PROJECT_FILE pointing at a file that does NOT exist,
# so Get-ProjectId returns $null with no live API / _runtime dependency.
#
# cmd.exe redirection (< stdin, 2> stderrfile) mirrors approval-policies-gate.smoke.ps1:
# it stops PS 5.1 from wrapping native-command stderr into NativeCommandError objects.
#
# Run:  powershell -NoProfile -ExecutionPolicy Bypass -File .claude/hooks/pretooluse-bash-gate.smoke.ps1
# Exit: 0 on all-pass, non-zero on any failure.

$ErrorActionPreference = 'Stop'
$hook = Join-Path $PSScriptRoot 'pretooluse-bash-gate.ps1'
if (-not (Test-Path $hook)) {
    Write-Output "[FATAL] Hook not found at $hook"
    exit 2
}

$tmpDir = Join-Path $PSScriptRoot 'pretooluse-bash-gate-tmp'
if (Test-Path $tmpDir) { Remove-Item -Recurse -Force $tmpDir }
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

# A project-id fixture path that is NEVER written -> Get-ProjectId returns $null
# -> the gate is in the "no per-session binding" state (the bug condition).
$noBindingFile = Join-Path $tmpDir 'no-binding.txt'

function Invoke-Hook {
    param([string]$JsonInput)
    $env:APPROVAL_POLICIES_GATE_PROJECT_FILE = $noBindingFile
    $stdinFile  = Join-Path $tmpDir ("stdin-"  + [Guid]::NewGuid().ToString() + ".json")
    $stderrFile = Join-Path $tmpDir ("stderr-" + [Guid]::NewGuid().ToString() + ".log")
    Set-Content -Path $stdinFile -Value $JsonInput -Encoding utf8 -NoNewline
    $stdout = ''
    try {
        $cmdLine = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$hook`" < `"$stdinFile`" 2> `"$stderrFile`""
        $stdout = & cmd.exe /c $cmdLine
    } finally {
        Remove-Item -Force $stdinFile  -ErrorAction SilentlyContinue
        Remove-Item -Force $stderrFile -ErrorAction SilentlyContinue
        Remove-Item Env:\APPROVAL_POLICIES_GATE_PROJECT_FILE -ErrorAction SilentlyContinue
    }
    if ($null -eq $stdout) { $stdout = '' }
    return (($stdout | ForEach-Object { $_.ToString() }) -join "`n")
}

function Get-Decision {
    param([string]$HookOutput)
    if ([string]::IsNullOrWhiteSpace($HookOutput)) { return $null }
    # Pull the decision value directly. A brace-balanced JSON extract is fragile
    # because permissionDecisionReason can itself contain literal {braces} (e.g. the
    # curl-DELETE guard mentions {id} / {"process_status": 6}); match the field instead.
    $m = [regex]::Match($HookOutput, '"permissionDecision"\s*:\s*"(allow|deny|ask)"')
    if ($m.Success) { return $m.Groups[1].Value }
    return "<no-decision-json: $HookOutput>"
}

# tool_input.command is the only field the bind-bootstrap branch reads.
function New-Input { param([string]$Cmd)
    return (@{ tool_name = 'Bash'; tool_input = @{ command = $Cmd }; session_id = 'smoke-no-binding-0000' } | ConvertTo-Json -Compress -Depth 6)
}

$tests = @(
    # --- #2706 bind-bootstrap allow (the fix) ---
    @{ Name='POS echo session-id -> allow';          Cmd='echo $CLAUDE_CODE_SESSION_ID'; Expected='allow' },
    @{ Name='POS curl GET by-name -> allow';         Cmd='curl --silent "http://localhost:8456/api/projects/by-name/agent-teams" -o _scratch/tn_bind_resp.json -w "%{http_code}"'; Expected='allow' },
    @{ Name='POS curl GET projects?status -> allow'; Cmd='curl --silent "http://localhost:8456/api/projects?status=1" -o _scratch/tn_bind_list.json -w "%{http_code}"'; Expected='allow' },
    # --- #2711 quoted-echo tolerance + bind-binding-write allow ---
    @{ Name='POS echo quoted session-id -> allow';   Cmd='echo "$CLAUDE_CODE_SESSION_ID"'; Expected='allow' },
    @{ Name='POS printf write per-session -> allow';  Cmd="printf '1' > _runtime/lead_project_id_0570819a-692a-4945-b78c-e81357e8f000.txt"; Expected='allow' },
    @{ Name='POS printf write global -> allow';       Cmd="printf '1' > _runtime/lead_project_id.txt"; Expected='allow' },
    @{ Name='POS printf write unquoted/no-space -> allow'; Cmd='printf 599>_runtime/lead_project_id.txt'; Expected='allow' },
    # --- #2711 bind-write narrowness: only the exact binding-marker shape rides it ---
    @{ Name='NEG printf to other path -> ask';        Cmd="printf '1' > _runtime/other.txt"; Expected='ask' },
    @{ Name='NEG printf non-digit content -> ask';    Cmd="printf 'x' > _runtime/lead_project_id.txt"; Expected='ask' },
    @{ Name='NEG printf write then chained rm -> ask'; Cmd="printf '1' > _runtime/lead_project_id.txt ; rm -rf /tmp/x"; Expected='ask' },
    @{ Name='NEG printf append (>>) -> ask';          Cmd="printf '1' >> _runtime/lead_project_id.txt"; Expected='ask' },
    @{ Name='NEG printf unicode-digit content -> ask'; Cmd="printf $([char]0x0661) > _runtime/lead_project_id.txt"; Expected='ask' },
    @{ Name='NEG echo asymmetric quote -> ask';       Cmd='echo "$CLAUDE_CODE_SESSION_ID'; Expected='ask' },
    # --- narrowness: arbitrary / mutating shapes must NOT ride the bypass ---
    @{ Name='NEG echo other -> ask';                 Cmd='echo hello world'; Expected='ask' },
    @{ Name='NEG curl by-name -X DELETE -> ask';     Cmd='curl --silent -X DELETE "http://localhost:8456/api/projects/by-name/agent-teams"'; Expected='ask' },
    @{ Name='NEG curl by-name POST body -> ask';     Cmd='curl --silent -X POST --data-binary @x.json "http://localhost:8456/api/projects/by-name/agent-teams"'; Expected='ask' },
    @{ Name='NEG curl other endpoint GET -> ask';    Cmd='curl --silent "http://localhost:8456/api/tasks/2706"'; Expected='ask' },
    # --- chaining-bypass hardening: resolve-URL curl must NOT smuggle a 2nd command ---
    @{ Name='NEG curl by-name ; chained -> ask';     Cmd='curl --silent "http://localhost:8456/api/projects/by-name/agent-teams" ; rm -rf /tmp/x'; Expected='ask' },
    @{ Name='NEG curl by-name && chained -> ask';    Cmd='curl --silent "http://localhost:8456/api/projects/by-name/agent-teams" && curl -X POST http://evil/'; Expected='ask' },
    @{ Name='NEG curl by-name | piped -> ask';       Cmd='curl --silent "http://localhost:8456/api/projects/by-name/agent-teams" | sh'; Expected='ask' },
    @{ Name='NEG curl by-name $(subshell) -> ask';   Cmd='curl --silent "http://localhost:8456/api/projects/by-name/$(whoami)"'; Expected='ask' },
    @{ Name='NEG resolve-URL in header, foreign target -> ask'; Cmd='curl --silent -H "Referer: http://localhost:8456/api/projects/by-name/x" http://evil.example/'; Expected='ask' },
    @{ Name='NEG curl by-name --config -> ask';      Cmd='curl --silent --config /tmp/evil.cfg "http://localhost:8456/api/projects/by-name/agent-teams"'; Expected='ask' },
    @{ Name='NEG curl by-name -K config -> ask';     Cmd='curl --silent -K /tmp/evil.cfg "http://localhost:8456/api/projects/by-name/agent-teams"'; Expected='ask' },
    @{ Name='NEG ECHO uppercase -> ask';             Cmd='ECHO $CLAUDE_CODE_SESSION_ID'; Expected='ask' },
    @{ Name='NEG echo newline-injection -> ask';     Cmd="echo`n`$CLAUDE_CODE_SESSION_ID"; Expected='ask' },
    # --- deny-guard regression (must still short-circuit BEFORE the bootstrap allow) ---
    @{ Name='DENY psql DELETE FROM -> deny';         Cmd='psql -U postgres -d agent_teams -c "DELETE FROM tasks WHERE id=1"'; Expected='deny' },
    @{ Name='DENY pytest inline live DB -> deny';    Cmd='DATABASE_URL=postgresql://postgres:postgres@db:5432/agent_teams pytest -x'; Expected='deny' },
    @{ Name='DENY bitdefender LASTEXITCODE chain -> deny'; Cmd='echo hi ; $rc = $LASTEXITCODE'; Expected='deny' }
)

$failCount = 0
foreach ($t in $tests) {
    $rawOut   = Invoke-Hook -JsonInput (New-Input -Cmd $t.Cmd)
    $decision = Get-Decision -HookOutput $rawOut
    if ($decision -eq $t.Expected) {
        Write-Output ("[PASS] {0,-5} {1}" -f $decision, $t.Name)
    } else {
        Write-Output ("[FAIL] expected={0} actual={1}  {2}" -f $t.Expected, $decision, $t.Name)
        $failCount++
    }
}

Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue

Write-Output "============================"
if ($failCount -gt 0) {
    Write-Output "$failCount test(s) FAILED."
    exit 1
} else {
    Write-Output "All $($tests.Count) tests PASSED."
    exit 0
}
