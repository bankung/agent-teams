# Block pytest invocations whose DATABASE_URL points at the live agent_teams DB.
# Belt-and-suspenders against the conftest in-process isolation being bypassed
# (e.g. via get_settings() lru_cache poisoning). Pairs with L2 (conftest fail-loud)
# and L3 (lazy seed). Codified after the 2026-05-17 dev DB wipe — see
# context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md.
#
# Manual test — operator runs these post-restore to verify:
#
#   # ALLOW case (DATABASE_URL unset)
#   $env:DATABASE_URL = ""; '{"tool_input": {"command": "pytest -q"}}' | powershell -File .claude/hooks/block-pytest-on-live-db.ps1
#   # -> expect exit 0 (no JSON / no deny)
#
#   # ALLOW case (DATABASE_URL ends with _test)
#   $env:DATABASE_URL = "postgresql://x@db:5432/agent_teams_test"; '{"tool_input": {"command": "pytest -q"}}' | powershell -File .claude/hooks/block-pytest-on-live-db.ps1
#   # -> expect exit 0
#
#   # DENY case (live URL)
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

$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
$cmd = $payload.tool_input.command

if (-not $cmd) { exit 0 }

# Word-boundary, case-insensitive match on pytest. Skips wrappers that merely
# mention the word in path strings / commit messages — first-word check would
# miss `docker compose exec api pytest ...`, so substring with boundary is
# correct here.
if ($cmd -notmatch '(?i)\bpytest\b') { exit 0 }

# Bypass valve — emits an audit marker to stderr but allows the call.
if ($env:BYPASS_LIVE_DB_PYTEST_HOOK -eq "1") {
    [Console]::Error.WriteLine("[BYPASS] block-pytest-on-live-db.ps1 BYPASSED via BYPASS_LIVE_DB_PYTEST_HOOK=1")
    exit 0
}

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

$output = @{
    hookSpecificOutput = @{
        hookEventName            = "PreToolUse"
        permissionDecision       = "deny"
        permissionDecisionReason = $reason
    }
} | ConvertTo-Json -Compress -Depth 4

Write-Output $output
exit 2
