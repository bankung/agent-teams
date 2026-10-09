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
# Bound mode (#3486): project id 1 + a policy fixture with no rules, no HTTP — so the
# gate's own default (allow) is visible and a destructive-class ask is distinguishable.
$bindingFile = Join-Path $tmpDir 'binding.txt'
$policyFile  = Join-Path $tmpDir 'policy.json'
Set-Content -Path $bindingFile -Value '1' -Encoding ascii -NoNewline
Set-Content -Path $policyFile -Value '{"approval_policies": null, "is_killed": false}' -Encoding ascii

function Invoke-Hook {
    param([string]$JsonInput, [switch]$Bound)
    $env:APPROVAL_POLICIES_GATE_PROJECT_FILE = if ($Bound) { $bindingFile } else { $noBindingFile }
    if ($Bound) { $env:APPROVAL_POLICIES_GATE_POLICY_FILE = $policyFile }
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
        Remove-Item Env:\APPROVAL_POLICIES_GATE_POLICY_FILE -ErrorAction SilentlyContinue
    }
    if ($null -eq $stdout) { $stdout = '' }
    return (($stdout | ForEach-Object { $_.ToString() }) -join "`n")
}

function Get-Decision {
    param([string]$HookOutput)
    if ([string]::IsNullOrWhiteSpace($HookOutput)) { return 'none' }
    # Pull the decision value directly. A brace-balanced JSON extract is fragile
    # because permissionDecisionReason can itself contain literal {braces} (e.g. the
    # curl-DELETE guard mentions {id} / {"process_status": 6}); match the field instead.
    $m = [regex]::Match($HookOutput, '"permissionDecision"\s*:\s*"(allow|deny|ask)"')
    if ($m.Success) { return $m.Groups[1].Value }
    return "<no-decision-json: $HookOutput>"
}

# tool_input.command is the only field the bind-bootstrap branch reads.
function New-Input { param([string]$Cmd, [string]$Tool = 'Bash')
    return (@{ tool_name = $Tool; tool_input = @{ command = $Cmd }; session_id = 'smoke-no-binding-0000' } | ConvertTo-Json -Compress -Depth 6)
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
    @{ Name='DENY bitdefender LASTEXITCODE chain -> deny'; Cmd='echo hi ; $rc = $LASTEXITCODE'; Expected='deny' },
    # --- #3486 bound session: destructive class -> ask, everything else keeps default allow ---
    @{ B=1; Name='CLASS rm -rf repo dir -> ask';            Cmd='rm -rf "C:/Users/x/MorAI/mobile"'; Expected='ask' },
    @{ B=1; Name='CLASS cd && rm -r src -> ask';             Cmd='cd web && rm -r app/dev-route'; Expected='ask' },
    @{ B=1; Name='SAFE rm -rf _scratch/x -> allow';          Cmd='rm -rf _scratch/_validate_1 _scratch/_ctl'; Expected='allow' },
    @{ B=1; Name='SAFE S=scratchpad; rm -rf $S/x -> allow';  Cmd='S="C:/Users/x/AppData/Local/Temp/claude/abc/scratchpad"; rm -rf "$S/probe"'; Expected='allow' },
    @{ B=1; Name='CLASS rm -rf _scratch/../src -> ask';      Cmd='rm -rf _scratch/../src'; Expected='ask' },
    @{ B=1; Name='CLASS git checkout -- file -> ask';        Cmd='git -C web checkout -- app/layout.tsx'; Expected='ask' },
    @{ B=1; Name='CLASS git restore file -> ask';            Cmd='git restore shared/decisions.md'; Expected='ask' },
    @{ B=1; Name='SAFE git restore --staged -> allow';       Cmd='git restore --staged shared/decisions.md'; Expected='allow' },
    @{ B=1; Name='CLASS git reset --hard -> ask';            Cmd='git status && git reset --hard HEAD~1'; Expected='ask' },
    @{ B=1; Name='CLASS git clean -fd -> ask';               Cmd='git clean -fd'; Expected='ask' },
    @{ B=1; Name='CLASS git push --force -> ask';            Cmd='git push --force-with-lease origin dev'; Expected='ask' },
    @{ B=1; Name='CLASS git push --delete -> ask';           Cmd='git push origin --delete old-branch'; Expected='ask' },
    @{ B=1; Name='CLASS git branch -D -> ask';               Cmd='git branch -D scratch/x'; Expected='ask' },
    @{ B=1; Name='CLASS git stash drop -> ask';              Cmd='git stash drop'; Expected='ask' },
    @{ B=1; Name='CLASS git rm -r -> ask';                   Cmd='git rm -r mobile-frontend'; Expected='ask' },
    @{ B=1; Name='CLASS curl -XDELETE after cd -> ask';      Cmd='cd x && curl -s -XDELETE http://localhost:8456/api/tasks/1'; Expected='ask' },
    @{ B=1; Name='CLASS for-loop $(curl -X DELETE) -> ask';  Cmd='for id in 1 2; do d=$(curl -s -X DELETE "http://localhost:8456/api/projects/$id"); echo $d; done'; Expected='ask' },
    @{ B=1; Name='CLASS docker volume rm -> ask';            Cmd='docker rm db; docker volume rm morai_pgdata'; Expected='ask' },
    @{ B=1; Name='CLASS docker compose -f down -v -> ask';   Cmd='docker compose -f docker-compose.yml down -v'; Expected='ask' },
    @{ B=1; Name='CLASS docker system prune -> ask';         Cmd='docker system prune -af'; Expected='ask' },
    @{ B=1; Name='SAFE docker compose rm -f db-test -> allow'; Cmd='docker compose -p agent-teams --profile test rm -f db-test'; Expected='allow' },
    @{ B=1; Name='SAFE commit msg naming rm -rf -> allow';   Cmd='git commit -m "drop the rm -rf / git reset --hard / curl -X DELETE examples"'; Expected='allow' },
    @{ B=1; Name='SAFE heredoc body naming rm -rf -> allow'; Cmd="cat > _scratch/n.md <<'EOF'`nrm -rf mobile/ and git checkout -- x`nEOF"; Expected='allow' },
    @{ B=1; Name='SAFE node -e string -> allow';             Cmd='node -e "require(''child_process''); // rm -rf web"'; Expected='allow' },
    @{ B=1; Name='SAFE plain git checkout branch -> allow';  Cmd='git checkout dev'; Expected='allow' },
    @{ B=1; Name='CLASS git checkout HEAD file -> ask';      Cmd='git checkout HEAD api/main.py'; Expected='ask' },   # #3500
    @{ B=1; Name='SAFE git checkout -b x base -> allow';     Cmd='git checkout -b feat origin/dev'; Expected='allow' },
    @{ B=1; Name='CLASS find -delete -> ask';                Cmd='find . -name "*.pyc" -delete'; Expected='ask' },
    @{ B=1; Name='CLASS find -exec rm -rf -> ask';           Cmd='find web -type d -exec rm -rf {} +'; Expected='ask' },
    @{ B=1; Name='CLASS find -exec rm (files) -> ask';       Cmd='find . -type f -exec rm {} +'; Expected='ask' },
    @{ B=1; Name='CLASS git checkout file.py -> ask';        Cmd='git checkout api/main.py'; Expected='ask' },
    # #3511: flags after a brace word (HEAD@{1}, web/{a,b}) are seen via the -KeepBrackets statement view
    @{ B=1; Name='CLASS checkout HEAD@{1} -- file -> ask';  Cmd='git checkout HEAD@{1} -- api/x.py'; Expected='ask' },
    @{ B=1; Name='CLASS checkout stash@{0} -- file -> ask'; Cmd='git checkout stash@{0} -- api/x.py'; Expected='ask' },
    @{ B=1; Name='CLASS checkout HEAD@{1} . -> ask';        Cmd='git checkout HEAD@{1} .'; Expected='ask' },
    @{ B=1; Name='CLASS reset HEAD@{1} --hard -> ask';      Cmd='git reset HEAD@{1} --hard'; Expected='ask' },
    @{ B=1; Name='CLASS push HEAD@{1}:dev --force -> ask';  Cmd='git push origin HEAD@{1}:dev --force'; Expected='ask' },
    @{ B=1; Name='CLASS branch x@{u} -D -> ask';            Cmd='git branch x@{u} -D'; Expected='ask' },
    @{ B=1; Name='CLASS rm web/{a,b} -rf -> ask';           Cmd='rm web/{a,b} -rf'; Expected='ask' },
    @{ B=1; Name='CLASS compose -f x{1}.yml down -v -> ask'; Cmd='docker compose -f x{1}.yml down -v'; Expected='ask' },
    @{ B=1; Name='CLASS brace group rm -rf -> ask';         Cmd='{ rm -rf web; }'; Expected='ask' },
    @{ B=1; Name='CLASS function body rm -rf -> ask';       Cmd='f(){ rm -rf web; }'; Expected='ask' },
    @{ B=1; Name='SAFE rm -rf _scratch/{a,b} -> allow';     Cmd='rm -rf _scratch/{a,b}'; Expected='allow' },
    @{ B=1; Name='SAFE git log HEAD@{1} -> allow';          Cmd='git log HEAD@{1}'; Expected='allow' },
    @{ B=1; T='PowerShell'; Name='PS %{ Remove-Item -Recurse } -> ask'; Cmd='gci web | %{Remove-Item -Recurse $_}'; Expected='ask' },
    @{ B=1; Name='SAFE find _scratch -delete -> allow';      Cmd='find _scratch -name "*.json" -delete'; Expected='allow' },
    @{ B=1; Name='CLASS bash -c rm -rf -> ask';              Cmd='bash -c "rm -rf web/app"'; Expected='ask' },
    @{ B=1; T='PowerShell'; Name='PS Remove-Item -Recurse -> ask';     Cmd='Remove-Item -Recurse -Force C:\repo\web\app'; Expected='ask' },
    @{ B=1; T='PowerShell'; Name='PS irm -Method Delete -> ask';       Cmd='$r = Invoke-RestMethod -Uri http://localhost:8456/api/tasks/1 -Method Delete'; Expected='ask' },
    @{ B=1; T='PowerShell'; Name='PS Get-ChildItem -> no decision';    Cmd='Get-ChildItem -Recurse _scratch'; Expected='none' },
    # --- #3486 GUARD 5 bypasses (were allow) + sanctioned shape ---
    @{ B=1; Name='G5 compose -f exec api pytest -> deny';    Cmd='docker compose -f docker-compose.yml exec api pytest -q'; Expected='deny' },
    @{ B=1; Name='G5 compose --project-directory exec -> deny'; Cmd='docker compose --project-directory . exec -T api pytest'; Expected='deny' },
    @{ B=1; Name='G5 docker-compose exec -> deny';           Cmd='docker-compose exec api pytest tests/x.py'; Expected='deny' },
    @{ B=1; Name='G5 docker-compose run api -> deny';        Cmd='docker-compose run --rm api pytest'; Expected='deny' },
    @{ B=1; Name='G5 python3 -c pytest -> deny';             Cmd='python3 -c "import pytest; pytest.main([])"'; Expected='deny' },
    @{ B=1; Name='G5 cd && exec pytest -> deny';             Cmd='cd api && docker compose exec api pytest'; Expected='deny' },
    @{ B=1; Name='G5 sanctioned api-test -> allow';          Cmd='docker compose -p agent-teams --profile test run --rm api-test pytest -q tests/test_x.py'; Expected='allow' },
    @{ B=1; Name='G5 api-test with -k quoted -> allow';      Cmd='docker compose -p agent-teams --profile test run --rm api-test pytest -q tests/test_x.py -k "a and b"'; Expected='allow' },
    @{ B=1; Name='G5 commit msg naming exec pytest -> allow'; Cmd='git commit -m "GUARD 5 denies docker compose exec api pytest"'; Expected='allow' },
    # --- #3486 GUARD 2 per segment ---
    @{ B=1; Name='G2 cd && psql -c DELETE -> deny';          Cmd='cd db && psql -U postgres -c "DELETE FROM tasks WHERE id=1"'; Expected='deny' },
    @{ B=1; Name='G2 docker exec psql DROP -> deny';         Cmd='docker exec -i pg psql -U postgres -d t -c "DROP TABLE doctors; CREATE TABLE doctors(id int)"'; Expected='deny' },
    @{ B=1; Name='G2 heredoc into psql DROP -> deny';       Cmd="docker exec -i pg psql -U t -d t <<'SQL'`nDROP TABLE IF EXISTS doctors;`nINSERT INTO doctors VALUES (1);`nSQL"; Expected='deny' },
    @{ B=1; Name='G2 sudo -u postgres psql -c -> deny';     Cmd='sudo -u postgres psql -c "DELETE FROM tasks"'; Expected='deny' },
    @{ B=1; Name='G2 other heredoc + psql SELECT -> allow';  Cmd="cat > _scratch/n.md <<'EOF'`nnever DELETE FROM tasks`nEOF`npsql -c `"SELECT 1`""; Expected='allow' },
    @{ B=1; Name='QUOTED <<X cannot hide exec pytest -> deny'; Cmd="echo `"<<X`"`ndocker compose exec api pytest`nX"; Expected='deny' },
    @{ B=1; Name='G5 uv run python -c pytest -> deny';       Cmd='uv run python -c "import pytest; pytest.main()"'; Expected='deny' },
    @{ B=1; Name='CLASS timeout 60 git reset --hard -> ask'; Cmd='timeout 60 git reset --hard'; Expected='ask' },
    @{ B=1; Name='CLASS curl -sX DELETE -> ask';             Cmd='curl -sX DELETE http://localhost:8456/api/projects/5'; Expected='ask' },
    @{ B=1; Name='CLASS rm -rf $TEMPLATES_DIR -> ask';       Cmd='rm -rf $TEMPLATES_DIR'; Expected='ask' },
    @{ B=1; Name='G2 commit msg naming psql DELETE -> allow'; Cmd='git commit -m "psql -c DELETE FROM is denied"'; Expected='allow' }
)

# Review-batch rows (#3483) live in a DATA file: written into this script, payloads such as
# `powershell -Command Remove-Item -Recurse ...` make AMSI/Bitdefender block the whole smoke.
$tests += Get-Content -Raw -Encoding utf8 (Join-Path $PSScriptRoot 'pretooluse-bash-gate.smoke-cases.json') | ConvertFrom-Json

$failCount = 0
foreach ($t in $tests) {
    $tool     = if ($t.T) { $t.T } else { 'Bash' }
    $rawOut   = Invoke-Hook -JsonInput (New-Input -Cmd $t.Cmd -Tool $tool) -Bound:([bool]$t.B)
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
