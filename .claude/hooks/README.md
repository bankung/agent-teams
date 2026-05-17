# Claude Code hooks (`.claude/hooks/`)

PreToolUse / PostToolUse hooks registered in `.claude/settings.json`. Both Lead's main session AND every spawned subagent inherit these — enforcement is harness-side, immune to context compaction or agent-definition skim.

Hook input/output contract: stdin is JSON with `tool_input.command`; stdout is a JSON envelope with `hookSpecificOutput.permissionDecision` set to `"allow"` / `"deny"` / `"ask"`, plus `permissionDecisionReason`. Exit code 0 = no decision (transparent); exit code 2 = decision in stdout.

## Active hooks (PreToolUse)

| Hook | Purpose | Bypass valve |
| --- | --- | --- |
| `block-raw-sql-dml.ps1` | DENY `psql -c` / `python -c` calls that contain DELETE/UPDATE/INSERT/TRUNCATE/DROP/ALTER TABLE. Diagnostic SELECT/`\d`/EXPLAIN pass through. Codified rule: see `.claude/docs/lessons.md` "Raw SQL DML is human-only" (strike #1 Kanban #483, 2026-05-09). | Edit settings.json to remove the hook entry, or run the command in a terminal outside Claude Code. |
| `block-curl-delete.ps1` | DENY curl `-X DELETE` calls so subagents can't delete API resources outside the Lead-approved flow. | (see hook source) |
| `block-bitdefender-triggers.ps1` | DENY command shapes Bitdefender heuristics treat as malware (false-positive triggers that nuke the executable). Also wired on the PowerShell matcher. | (see hook source) |
| `block-pytest-on-live-db.ps1` | DENY `pytest` invocations targeting the live `agent_teams` DB. Covers four paths: (1) parent-shell `DATABASE_URL` env not ending in `_test`; (2) inline bash `DATABASE_URL=...` prefix not ending in `_test` (L1.5); (3) `docker compose exec ... pytest` unless `DOCKER_PYTEST_VERIFIED=1` is set after the operator has manually confirmed the container env (L1.5); (4) `python -c "...pytest..."` denied outright as a likely bypass attempt (L1.5). `python -m pytest` is treated the same as bare `pytest`. Unset `DATABASE_URL` + no bypass triggers is ALLOWED (conftest's in-process rewrite handles it). Codified after the 2026-05-17 dev DB wipe — see `context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md`. L1.5 extension: Kanban #1119. | Set `BYPASS_LIVE_DB_PYTEST_HOOK=1` in the SAME shell. The hook honours the bypass and writes a `[BYPASS] ...` marker to stderr for the audit trail. For `docker compose exec ... pytest` specifically, the narrower attestation valve is `DOCKER_PYTEST_VERIFIED=1` — set ONLY after running `docker compose exec api printenv DATABASE_URL` and confirming a `_test` suffix. |

## Active hooks (PostToolUse)

| Hook | Event | Purpose | Bypass valve |
| --- | --- | --- | --- |
| `agent-verify-before-patch.ps1` | PostToolUse `Agent` | Injects a "[KARPATHY MODE B GUARD]" reminder into Lead's next conversation turn after every SUCCESSFUL specialist spawn. Forces an independent verify (Read file / narrow pytest / Glob / GET /api/...) before Lead can PATCH Kanban state based on a subagent "done" claim. Skips on Agent errors. Codified after strike #5 (2026-05-17 dev DB wipe of ~1100 audit rows) — soft layer (`feedback_karpathy_lane.md` + CLAUDE.md golden rule) proven insufficient at 5 recurrences. See `context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md` and CLAUDE.md golden-rule "Karpathy lane" bullet. Kanban #1110. | Remove the hook entry from `.claude/settings.json` `hooks.PostToolUse[]`. (No env-flag bypass — the reminder is informational, not blocking, so an in-band override is unnecessary.) |

## Per-agent permission hooks (loaded via subagent frontmatter, not settings.json)

- `tester-curl-allow.ps1` — auto-approve localhost curl for the tester agent.
- `researcher-web-allow.ps1` — auto-approve WebSearch / WebFetch on whitelisted domains for the researcher agent.
- `researcher-firecrawl-allow.ps1` — auto-approve Firecrawl skill calls for the researcher agent.
- `auto-approve-safe-writes.ps1` — auto-approve Writes/Edits to safe paths (`_scratch/`, role-state, etc.); see the smoke file alongside.

## Manual smoke (`block-pytest-on-live-db.ps1`)

Operator runs these post-restore to verify the hook behaves correctly. The
first five cases cover the original L1 surface; the last five cover L1.5
(Kanban #1119) — bash-command-string parsing for inline env, `docker compose
exec`, `python -c`, and `python -m pytest`.

```powershell
# ALLOW — DATABASE_URL unset (conftest rewrite handles it)
$env:DATABASE_URL = ""; '{"tool_input": {"command": "pytest -q"}}' | powershell -File .claude/hooks/block-pytest-on-live-db.ps1
# -> exit 0, no stdout

# ALLOW — DATABASE_URL ends with _test
$env:DATABASE_URL = "postgresql://x@db:5432/agent_teams_test"; '{"tool_input": {"command": "pytest -q"}}' | powershell -File .claude/hooks/block-pytest-on-live-db.ps1
# -> exit 0

# DENY — live URL in parent-shell env
$env:DATABASE_URL = "postgresql://x@db:5432/agent_teams"; '{"tool_input": {"command": "pytest -q"}}' | powershell -File .claude/hooks/block-pytest-on-live-db.ps1
# -> exit 2, JSON envelope with permissionDecision=deny

# BYPASS — live URL + env flag
$env:DATABASE_URL = "postgresql://x@db:5432/agent_teams"; $env:BYPASS_LIVE_DB_PYTEST_HOOK = "1"; '{"tool_input": {"command": "pytest -q"}}' | powershell -File .claude/hooks/block-pytest-on-live-db.ps1
# -> exit 0 + stderr "[BYPASS] block-pytest-on-live-db.ps1 BYPASSED ..."

# IGNORED — non-pytest command
'{"tool_input": {"command": "echo hello"}}' | powershell -File .claude/hooks/block-pytest-on-live-db.ps1
# -> exit 0

# --- L1.5 (Kanban #1119) ---

# DENY — inline DATABASE_URL= prefix pointing at live DB (incident-replica)
$env:DATABASE_URL = ""; '{"tool_input": {"command": "DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/agent_teams docker compose exec api pytest tests/test_db_safety.py"}}' | powershell -File .claude/hooks/block-pytest-on-live-db.ps1
# -> exit 2, JSON envelope with permissionDecision=deny

# DENY — python -c bypass attempt
$env:DATABASE_URL = ""; '{"tool_input": {"command": "python -c \"import pytest; pytest.main()\""}}' | powershell -File .claude/hooks/block-pytest-on-live-db.ps1
# -> exit 2

# DENY — docker compose exec ... pytest without DOCKER_PYTEST_VERIFIED
$env:DATABASE_URL = ""; $env:DOCKER_PYTEST_VERIFIED = ""; '{"tool_input": {"command": "docker compose exec api pytest tests/"}}' | powershell -File .claude/hooks/block-pytest-on-live-db.ps1
# -> exit 2

# ALLOW — docker compose exec ... pytest WITH DOCKER_PYTEST_VERIFIED=1
# Set DOCKER_PYTEST_VERIFIED=1 ONLY after `docker compose exec api printenv DATABASE_URL`
# confirms the container points at agent_teams_test.
$env:DATABASE_URL = ""; $env:DOCKER_PYTEST_VERIFIED = "1"; '{"tool_input": {"command": "docker compose exec api pytest tests/"}}' | powershell -File .claude/hooks/block-pytest-on-live-db.ps1
# -> exit 0

# ALLOW — python -m pytest with env unset (conftest rewrite handles it)
$env:DATABASE_URL = ""; '{"tool_input": {"command": "python -m pytest tests/"}}' | powershell -File .claude/hooks/block-pytest-on-live-db.ps1
# -> exit 0
```

### Running pytest in the container (post-L1.5)

The previous `Bash(docker compose exec api pytest:*)` allowlist entries in
`.claude/settings.json` bypassed L1 entirely. After L1.5, the canonical flow
for container-side pytest is:

1. `docker compose exec api printenv DATABASE_URL`
2. Verify the URL ends in `_test`.
3. In the SAME shell, `$env:DOCKER_PYTEST_VERIFIED = "1"`.
4. Run `docker compose exec api pytest ...` — the hook will allow.

Skipping step 1-2 and just setting the env flag is the operator's
attestation that the check was performed; it is auditable post-hoc via
shell history.
