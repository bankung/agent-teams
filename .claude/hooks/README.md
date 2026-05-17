# Claude Code hooks (`.claude/hooks/`)

PreToolUse / PostToolUse hooks registered in `.claude/settings.json`. Both Lead's main session AND every spawned subagent inherit these — enforcement is harness-side, immune to context compaction or agent-definition skim.

Hook input/output contract: stdin is JSON with `tool_input.command`; stdout is a JSON envelope with `hookSpecificOutput.permissionDecision` set to `"allow"` / `"deny"` / `"ask"`, plus `permissionDecisionReason`. Exit code 0 = no decision (transparent); exit code 2 = decision in stdout.

## Active hooks (Bash matcher)

| Hook | Purpose | Bypass valve |
| --- | --- | --- |
| `block-raw-sql-dml.ps1` | DENY `psql -c` / `python -c` calls that contain DELETE/UPDATE/INSERT/TRUNCATE/DROP/ALTER TABLE. Diagnostic SELECT/`\d`/EXPLAIN pass through. Codified rule: see `.claude/docs/lessons.md` "Raw SQL DML is human-only" (strike #1 Kanban #483, 2026-05-09). | Edit settings.json to remove the hook entry, or run the command in a terminal outside Claude Code. |
| `block-curl-delete.ps1` | DENY curl `-X DELETE` calls so subagents can't delete API resources outside the Lead-approved flow. | (see hook source) |
| `block-bitdefender-triggers.ps1` | DENY command shapes Bitdefender heuristics treat as malware (false-positive triggers that nuke the executable). Also wired on the PowerShell matcher. | (see hook source) |
| `block-pytest-on-live-db.ps1` | DENY `pytest` invocations when `DATABASE_URL` env points at the live `agent_teams` DB (anything not ending in `_test`). Unset `DATABASE_URL` is ALLOWED (conftest's in-process rewrite handles it). Codified after the 2026-05-17 dev DB wipe — see `context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md`. | Set `BYPASS_LIVE_DB_PYTEST_HOOK=1` in the SAME shell. The hook honours the bypass and writes a `[BYPASS] ...` marker to stderr for the audit trail. |

## Per-agent permission hooks (loaded via subagent frontmatter, not settings.json)

- `tester-curl-allow.ps1` — auto-approve localhost curl for the tester agent.
- `researcher-web-allow.ps1` — auto-approve WebSearch / WebFetch on whitelisted domains for the researcher agent.
- `researcher-firecrawl-allow.ps1` — auto-approve Firecrawl skill calls for the researcher agent.
- `auto-approve-safe-writes.ps1` — auto-approve Writes/Edits to safe paths (`_scratch/`, role-state, etc.); see the smoke file alongside.

## Manual smoke (`block-pytest-on-live-db.ps1`)

Operator runs these post-restore to verify the hook behaves correctly:

```powershell
# ALLOW — DATABASE_URL unset (conftest rewrite handles it)
$env:DATABASE_URL = ""; '{"tool_input": {"command": "pytest -q"}}' | powershell -File .claude/hooks/block-pytest-on-live-db.ps1
# -> exit 0, no stdout

# ALLOW — DATABASE_URL ends with _test
$env:DATABASE_URL = "postgresql://x@db:5432/agent_teams_test"; '{"tool_input": {"command": "pytest -q"}}' | powershell -File .claude/hooks/block-pytest-on-live-db.ps1
# -> exit 0

# DENY — live URL
$env:DATABASE_URL = "postgresql://x@db:5432/agent_teams"; '{"tool_input": {"command": "pytest -q"}}' | powershell -File .claude/hooks/block-pytest-on-live-db.ps1
# -> exit 2, JSON envelope with permissionDecision=deny

# BYPASS — live URL + env flag
$env:DATABASE_URL = "postgresql://x@db:5432/agent_teams"; $env:BYPASS_LIVE_DB_PYTEST_HOOK = "1"; '{"tool_input": {"command": "pytest -q"}}' | powershell -File .claude/hooks/block-pytest-on-live-db.ps1
# -> exit 0 + stderr "[BYPASS] block-pytest-on-live-db.ps1 BYPASSED ..."

# IGNORED — non-pytest command
'{"tool_input": {"command": "echo hello"}}' | powershell -File .claude/hooks/block-pytest-on-live-db.ps1
# -> exit 0
```
