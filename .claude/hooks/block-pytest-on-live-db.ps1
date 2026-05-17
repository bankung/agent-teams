# Block pytest invocations whose DATABASE_URL points at the live agent_teams DB.
# Belt-and-suspenders against the conftest in-process isolation being bypassed
# (e.g. via get_settings() lru_cache poisoning). Pairs with L2 (conftest fail-loud)
# and L3 (lazy seed). Codified after the 2026-05-17 dev DB wipe — see
# context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md.
#
# L1.5 (Kanban #1119) extends the original env-var check with bash-command-string
# parsing — covers inline `DATABASE_URL=...` prefixes, `docker compose exec ... pytest`
# (container env unverifiable from parent shell scope), `python -m pytest`, and
# DENIES `python -c "...pytest..."` outright as a bypass attempt.
#
# Manual test — operator runs these post-restore to verify:
#
#   # ALLOW case (DATABASE_URL unset, bare pytest)
#   $env:DATABASE_URL = ""; '{"tool_input": {"command": "pytest -q"}}' | powershell -File .claude/hooks/block-pytest-on-live-db.ps1
#   # -> expect exit 0 (no JSON / no deny)
#
#   # ALLOW case (DATABASE_URL ends with _test)
#   $env:DATABASE_URL = "postgresql://x@db:5432/agent_teams_test"; '{"tool_input": {"command": "pytest -q"}}' | powershell -File .claude/hooks/block-pytest-on-live-db.ps1
#   # -> expect exit 0
#
#   # DENY case (live URL via env var)
#   $env:DATABASE_URL = "postgresql://x@db:5432/agent_teams"; '{"tool_input": {"command": "pytest -q"}}' | powershell -File .claude/hooks/block-pytest-on-live-db.ps1
#   # -> expect exit 2 with permissionDecision=deny JSON
#
#   # BYPASS valve
#   $env:DATABASE_URL = "postgresql://x@db:5432/agent_teams"; $env:BYPASS_LIVE_DB_PYTEST_HOOK = "1"; '{"tool_input": {"command": "pytest -q"}}' | powershell -File .claude/hooks/block-pytest-on-live-db.ps1
#   # -> expect exit 0 + stderr [BYPASS] marker
#
#   # IGNORED case (non-pytest command)
#   '{"tool_input": {"command": "echo hello"}}' | powershell -File .claude/hooks/block-pytest-on-live-db.ps1
#   # -> expect exit 0
#
#   # L1.5 — DENY inline DATABASE_URL= prefix pointing at live DB
#   $env:DATABASE_URL = ""; '{"tool_input": {"command": "DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/agent_teams docker compose exec api pytest tests/test_db_safety.py"}}' | powershell -File .claude/hooks/block-pytest-on-live-db.ps1
#   # -> expect exit 2 with permissionDecision=deny JSON (incident-replica)
#
#   # L1.5 — DENY python -c bypass attempt
#   $env:DATABASE_URL = ""; '{"tool_input": {"command": "python -c \"import pytest; pytest.main()\""}}' | powershell -File .claude/hooks/block-pytest-on-live-db.ps1
#   # -> expect exit 2 with permissionDecision=deny JSON
#
#   # L1.5 — DENY docker compose exec ... pytest without DOCKER_PYTEST_VERIFIED
#   $env:DATABASE_URL = ""; $env:DOCKER_PYTEST_VERIFIED = ""; '{"tool_input": {"command": "docker compose exec api pytest tests/"}}' | powershell -File .claude/hooks/block-pytest-on-live-db.ps1
#   # -> expect exit 2 with permissionDecision=deny JSON
#
#   # L1.5 — ALLOW docker compose exec ... pytest WITH DOCKER_PYTEST_VERIFIED=1
#   $env:DATABASE_URL = ""; $env:DOCKER_PYTEST_VERIFIED = "1"; '{"tool_input": {"command": "docker compose exec api pytest tests/"}}' | powershell -File .claude/hooks/block-pytest-on-live-db.ps1
#   # -> expect exit 0 (operator confirmed container env)
#
#   # L1.5 — ALLOW python -m pytest with env unset (conftest rewrite handles it)
#   $env:DATABASE_URL = ""; '{"tool_input": {"command": "python -m pytest tests/"}}' | powershell -File .claude/hooks/block-pytest-on-live-db.ps1
#   # -> expect exit 0

$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
$cmd = $payload.tool_input.command

if (-not $cmd) { exit 0 }

# Word-boundary, case-insensitive match on pytest. Skips wrappers that merely
# mention the word in path strings / commit messages — first-word check would
# miss `docker compose exec api pytest ...`, so substring with boundary is
# correct here. `python -m pytest` also matches the boundary.
if ($cmd -notmatch '(?i)\bpytest\b') { exit 0 }

# Bypass valve — emits an audit marker to stderr but allows the call.
# Applies to ALL subsequent checks (env-var, inline, python -c, docker exec).
if ($env:BYPASS_LIVE_DB_PYTEST_HOOK -eq "1") {
    [Console]::Error.WriteLine("[BYPASS] block-pytest-on-live-db.ps1 BYPASSED via BYPASS_LIVE_DB_PYTEST_HOOK=1")
    exit 0
}

# Shared helper: emit the standard PreToolUse deny envelope + exit 2.
function Deny-Pytest {
    param([string]$Reason)
    $output = @{
        hookSpecificOutput = @{
            hookEventName            = "PreToolUse"
            permissionDecision       = "deny"
            permissionDecisionReason = $Reason
        }
    } | ConvertTo-Json -Compress -Depth 4
    Write-Output $output
    exit 2
}

# ---------------------------------------------------------------------------
# L1.5 check #1 — `python -c "...pytest..."` is a likely bypass attempt.
# Catch it before any env / suffix logic because there is no legitimate reason
# to invoke pytest this way; the shell-quoted Python wrapper exists only to
# hide intent from naive substring matchers.
# ---------------------------------------------------------------------------
if ($cmd -match '(?i)python\s+-c\s+["''][^"'']*pytest') {
    $reason = @"
pytest blocked: invocation via 'python -c "...pytest..."' looks like a hook-bypass attempt.

There is no legitimate reason to invoke pytest via 'python -c' in this repo.
If you have a real need, run pytest directly (or via 'python -m pytest') so
the L1 hook + conftest in-process rewrite can verify the DB target.

See context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md.

Bypass valve (rare legitimate needs): set BYPASS_LIVE_DB_PYTEST_HOOK=1 in
the SAME shell — the hook honours it and emits a [BYPASS] marker for audit.
"@
    Deny-Pytest -Reason $reason
}

# ---------------------------------------------------------------------------
# L1.5 check #2 — inline `DATABASE_URL=...` prefix in the bash command string.
# Parent-shell env-var check (below) misses this because bash inline env does
# NOT propagate to the PowerShell parent scope. Pattern: `DATABASE_URL=<url>`
# optionally followed by other env vars and the actual command.
# ---------------------------------------------------------------------------
$inlineMatch = [regex]::Match($cmd, '(?i)DATABASE_URL=([^\s"'']+)')
if ($inlineMatch.Success) {
    $inlineUrl  = $inlineMatch.Groups[1].Value
    $normalized = ($inlineUrl -replace '\?.*$', '') -replace '/+$', ''
    if ($normalized -notmatch '(?i)_test$') {
        $reason = @"
pytest blocked: inline DATABASE_URL=$inlineUrl in the bash command string
points at a non-_test DB.

Inline `DATABASE_URL=... pytest ...` (or `DATABASE_URL=... docker compose exec
api pytest ...`) bypasses the parent-shell env check because bash inline env
does not propagate to the PowerShell parent scope. This is the exact pattern
that wiped the dev DB on 2026-05-17.

Either:
  - drop the inline prefix and let conftest's in-process rewrite handle it, or
  - set the inline URL explicitly to ...agent_teams_test.

See context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md.

Bypass valve (rare legitimate needs): set BYPASS_LIVE_DB_PYTEST_HOOK=1 in
the SAME shell — the hook honours it and emits a [BYPASS] marker for audit.
"@
        Deny-Pytest -Reason $reason
    }
}

# ---------------------------------------------------------------------------
# L1.5 check #3 — `docker compose exec ... pytest` uses CONTAINER env, not the
# parent-shell env. The L1 env-var check cannot verify the container's
# DATABASE_URL, so deny unless the operator has set DOCKER_PYTEST_VERIFIED=1
# AFTER manually confirming the container env points at a _test DB.
# ---------------------------------------------------------------------------
if ($cmd -match '(?i)docker\s+compose\s+(-p\s+\S+\s+)?exec\s+.*\bpytest\b') {
    if ($env:DOCKER_PYTEST_VERIFIED -ne "1") {
        $reason = @"
pytest blocked: 'docker compose exec ... pytest' uses CONTAINER env, not the
parent shell env. The L1 hook cannot verify the container's DATABASE_URL
from outside the container.

Before re-running, MANUALLY verify the container's DATABASE_URL targets a
_test DB:

    docker compose exec api printenv DATABASE_URL

If — and only if — the printed URL ends in `_test`, set
DOCKER_PYTEST_VERIFIED=1 in the SAME shell and retry. This is the operator
attestation that you checked the container env.

See context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md.

Bypass valve (rare legitimate needs): set BYPASS_LIVE_DB_PYTEST_HOOK=1 in
the SAME shell — the hook honours it and emits a [BYPASS] marker for audit.
"@
        Deny-Pytest -Reason $reason
    }
}

# ---------------------------------------------------------------------------
# Original L1 check — parent-shell DATABASE_URL env var.
# Covers bare `pytest`, `python -m pytest`, and any other invocation where
# the parent shell already exported DATABASE_URL.
# ---------------------------------------------------------------------------
$dbUrl = $env:DATABASE_URL

# Unset / empty -> ALLOW. conftest's in-process rewrite handles the _test suffix.
if (-not $dbUrl) { exit 0 }

# Strip trailing slash + query string before suffix check.
$normalized = ($dbUrl -replace '\?.*$', '') -replace '/+$', ''
if ($normalized -match '(?i)_test$') { exit 0 }

# Live URL (or unrecognized suffix) -> DENY.
$reason = @"
pytest blocked: DATABASE_URL is live-pointed ($dbUrl).
Either unset DATABASE_URL (let conftest's in-process rewrite handle it)
or set DATABASE_URL=postgresql://...agent_teams_test explicitly.

This hook prevents the 2026-05-17 dev DB wipe class of incident
(pytest fixtures leaking destructive DDL/DML into the live agent_teams
DB via lru_cache poisoning). See context/projects/agent-teams/shared/
incidents/2026-05-17-dev-db-wipe.md.

Bypass valve (for rare legitimate live-DB pytest needs): set env var
BYPASS_LIVE_DB_PYTEST_HOOK=1 in the SAME shell — the hook honours it
and emits a warning marker for audit.
"@

Deny-Pytest -Reason $reason
