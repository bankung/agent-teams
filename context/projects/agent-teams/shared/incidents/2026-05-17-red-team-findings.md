# Red-team findings — DB wipe attack surface (2026-05-17)

> **Sibling doc:** `2026-05-17-dev-db-wipe.md` (the incident postmortem itself).
> This doc captures the comprehensive adversarial audit Lead conducted during the wait-for-restore window, plus dev-security-reviewer's independent cross-check.
> **Status:** 8 new prevention tasks staged at `_scratch/pending-kanban-2026-05-17/10..17-*.md` for post-restore replay.

## Operator framing

> "ตอนนี้ตรวจสอบเยอะเยอะเลยครับ เพราะว่าถ้าอยู่ดีดี มันเปลี่ยนฐานข้อมูลที่ชี้เพื่อทำสิ่งเหล่านี้ได้ มันก็มีความเสี่ยงว่าในการทำงานซักวันนึงมันอาจจะพยายามไปชี้ที่ฐานข้อมูล production ได้ ซึ่งกรณีแบบนี้เคยเกิดขึ้นแล้วกับบริษัทอื่น"

Translation: "Investigate thoroughly — if the system can silently switch the DB pointer to do destructive things while running normally, one day it might point at production. This has happened at other companies."

(Real-world precedent: GitLab 2017 prod DB incident, various "ran migration on prod" cases.)

## Updated root-cause attribution for the 2026-05-17 wipe

Original investigation (dev-tester) identified `_seed()` lru_cache poisoning as the most likely cause. **Red-team revises this:**

**Primary weapon:** `_purge_db_per_test` autouse fixture in `api/tests/test_user_next_action.py` (dev-backend's new file for #1010) — issues `delete(Task) + delete(TaskHistory) + delete(Project)` via SessionLocal.

**Trigger:** SessionLocal bound to live `agent_teams` URL — most likely path is lru_cache poisoning (per dev-tester H2), but **the bug isn't ONE poisoning path — it's that the WEAPON (purge fixtures) has NO target check.** Even after L3 closes the `_seed` route, any future poisoning path re-arms the same weapon.

**Why "854 passed" hid it:** `_live_db_row_count_invariant` silent skip on pre-snapshot failure (L2 fix).

**Why post-wipe = exactly 3 tasks + 1 project:** `_seed()` is idempotent — only the FIRST teardown's seed succeeded; subsequent teardowns found existing project and skipped.

## Defense layer architecture (after L1-L13 land)

```
+-------------------------------------------------------------------------+
| L0 — Policy (CLAUDE.md briefing)                                        | shipped
| Lead must include "report live DB count delta" AC in pytest specs       |
+-------------------------------------------------------------------------+
| L1 — Harness hook (block-pytest-on-live-db.ps1)                         | shipped
| Catches: pytest with parent-shell $env:DATABASE_URL pointing at live    |
| MISSES: inline env, docker exec, python -c, IDE runners (see L1.5)      |
+-------------------------------------------------------------------------+
| L1.5 — Hook command-string parser (task 15)                             | staged
| Catches: inline DATABASE_URL=, docker compose exec, python -m pytest    |
+-------------------------------------------------------------------------+
| L2 — Conftest fail-loud (test_conftest_invariant.py)                    | shipped
| Catches: silent skip of live-DB-drift guard                             |
+-------------------------------------------------------------------------+
| L3 — Settings + seed gate (drop @lru_cache + endswith("_test") gate)    | shipped
| Catches: lru_cache poisoning route into _seed()                         |
+-------------------------------------------------------------------------+
| L4 — Postgres role grants (task 08)                                     | staged
| Catches: ANYTHING that hits live DB with pytest_runner role             |
+-------------------------------------------------------------------------+
| L5 — PostToolUse Agent hook (task 09)                                   | staged
| Catches: Lead trusting agent reports without verify (Karpathy Mode B)   |
+-------------------------------------------------------------------------+
| L6 — Purge fixture URL gate (task 10) — THE CRITICAL ONE                | staged
| Catches: ANY destructive fixture running against non-_test DB           |
+-------------------------------------------------------------------------+
| L7 — Langgraph DATABASE_URI validation (task 11)                        | staged
| Catches: langgraph misconfig (separate env var L1 doesn't see)          |
+-------------------------------------------------------------------------+
| L8 — Api lifespan DB allowlist (task 12)                                | staged
| Catches: api/backup booting against rogue DB (docker-compose typo etc.) |
+-------------------------------------------------------------------------+
| L10 — Alembic MIGRATION_TARGET gate (task 13)                           | staged
| Catches: future destructive migration applied to live without flag      |
+-------------------------------------------------------------------------+
| L11 — _build_engine pytest canary (task 14)                             | staged
| Catches: src.db engine binding wrong URL during pytest (loud warning)   |
+-------------------------------------------------------------------------+
| L12 — Backup min-size + retention guard (task 16)                       | staged
| Catches: empty-DB backup overwriting good backups via retention         |
+-------------------------------------------------------------------------+
| L13 — bin/reset scripts hardening (task 17)                             | staged
| Catches: `down -v` from worktree / wrong compose project / no confirm   |
+-------------------------------------------------------------------------+
```

**Gate:** L0 + L1 + L2 + L3 SHIPPED in commit 53f0f37. L4-L13 staged for post-restore.

## Threat-model matrix (after all layers land)

| Attack vector | Layer that catches |
|---|---|
| pytest with $env:DATABASE_URL live (parent shell) | L1 |
| pytest with inline `DATABASE_URL=...` prefix | L1.5 |
| pytest via `docker compose exec ... pytest` | L1.5 + L4 + L6 |
| pytest from `python -m pytest` | L1.5 + L4 + L6 |
| pytest from `python -c "import pytest..."` | L1.5 (DENY outright) |
| pytest from VS Code Test Explorer | L4 + L6 |
| Purge fixture wipes wrong DB | **L6** (the critical disarm) |
| `_seed()` against live | L3 |
| Other `api/scripts/*.py` against live | L6 pattern (manual audit per script) |
| Conftest invariant silently disabled | L2 |
| docker-compose.yml DATABASE_URL changed | L8 |
| docker-compose.yml DATABASE_URI changed (langgraph) | L7 |
| Alembic migration with destructive op | L10 |
| Engine binds wrong at module import (test race) | L11 (warning) + L6 (disarm) |
| `docker compose down -v` typo / wrong project | L13 (confirm prompt) |
| `dropdb` / `docker volume rm` | Operator discipline only (no app layer) |
| Mode B: Lead trusts agent without verify | L5 |
| Backup of rogue DB uploaded | L8 + L12 |
| Retention deletes only-valid backup | L12 |

## Confirmed defenses (hands-on verified)

| Defense | Verification |
|---|---|
| L1 hook DENY on `$env:DATABASE_URL` live | Direct probe with hook input JSON returned `permissionDecision: deny` |
| L1 hook ALLOW on unset DATABASE_URL | Probe returned exit 0 |
| L1 hook ALLOW on URL ending `_test` | Probe returned exit 0 |
| L3 _seed URL gate | `docker compose exec api python -c "._seed()"` with live URL → `RuntimeError: refusing to seed against URL ... 'agent_teams' does not end with '_test'` |
| L3 SEED_TARGET escape | Same + `SEED_TARGET=production` → gate passes; idempotent seed no-ops |
| App DELETE endpoints soft-delete | Read confirmed `RecordStatus.DELETED` (status=0), no hard DELETE |
| `_seed()` idempotency | Read line 84-89: returns 0 early if "agent-teams" project exists |
| Alembic migrations destructive DDL count | Grepped all 33 migrations: zero TRUNCATE/DROP TABLE/DELETE FROM |

## Findings dev-security-reviewer added (cross-check)

| ID | Severity | Finding |
|---|---|---|
| WARN-1 | HIGH | settings.json line 92-93 allowlists `Bash(docker compose exec api pytest:*)` — empirical test post-restore required to confirm whether L1 hook fires on allowlisted commands |
| WARN-2 | HIGH | Backup runner pg_dump with `--clean --if-exists` could upload empty-DB backup if env wrong → retention deletes good backups |
| WARN-3 | MEDIUM | 4th purge site Lead missed: `test_tool_calls.py:855` (single-task `delete(Task)` — pattern violation even though narrow) |
| NIT-1 | LOW | `bin/reset.sh` + `bin/reset.ps1` lack `-p agent-teams` flag → could target wrong compose project |
| NIT-2 | LOW | `row_changed_listener.py` opens separate asyncpg connection via `_database_url()` — not gated by L8 |
| NIT-3 | LOW | `alembic/env.py` top-level `settings = get_settings()` — in-process alembic invocations would bind at import time |

## Factual correction to Lead's threat model

dev-security-reviewer caught:

> Lead's backup runner threat model states "if env mutated mid-flight, next cron tick picks up NEW url." This is **incorrect**. `BackupConfig.from_env()` is called once at lifespan start (`main.py:106`) and the config object is frozen in the `BackupRunner` instance. Mid-process env mutation does NOT affect subsequent cron ticks. The risk is instead at lifespan start: if the container boots with a wrong DATABASE_URL, all backups for the container's entire lifetime are wrong.

Lead acknowledges + updates: L8 (lifespan validation) is therefore the load-bearing control for backup correctness, not per-tick URL validation.

## Karpathy lane sub-lesson

This red-team adds a 6th observation to Mode B drift catalog: **trusting that one fix (L3) addresses the root cause when the actual weapon (L6 territory) was left armed.** Without this red-team, L1+L2+L3 would have shipped and we'd have felt safe — while the purge fixtures remained loaded.

**Sub-lesson:** when fixing a bug, list ALL the gun barrels (the destructive code paths), not just the trigger (the path that fired). The 2026-05-17 wipe is fixed in 3 places now:
- L3 = the specific trigger that fired this time (lru_cache route)
- L2 = the safety that silently disabled itself
- L6 = the gun barrel (purge fixtures) — **without this, the next poisoning route re-loads the gun**

## Decision

All P1 prevention tasks (10, 11, 12, 08, 09) MUST land before any `pytest -q` runs from a Lead session. P2 (13, 14, 15, 16) and P3 (17) land in normal priority order post-restore.

The CLAUDE.md golden rule "L0 pytest briefing discipline" is in effect immediately and survives restore.

## Cross-references

- Incident postmortem: `2026-05-17-dev-db-wipe.md`
- Lead's working notes: `_scratch/red-team-findings-2026-05-17.md` (full unedited investigation)
- Security review report: `_scratch/pending-kanban-2026-05-17/security-review-redteam-cross-check.md`
- 8 staged Kanban entries: `_scratch/pending-kanban-2026-05-17/10..17-*.md`
- Operator action list: `_scratch/OPERATOR_TODO_when_at_computer.md`
