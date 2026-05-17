# Incident — 2026-05-17 dev DB wipe via pytest fixture leak

> **Severity:** HIGH. ~1100 task audit rows lost. Code untouched. Recoverable via R2 backup (operator runs decrypt offline).
> **Resolved:** PENDING (operator-side restore in progress at time of writing).
> **Root cause class:** Test isolation contract violation — 3-layer protection bypassed by 2 compounding failures.

## TL;DR

ตอน dev-backend specialist ตามที่ Lead spawn ไป verify regression สำหรับ #1010 (next-action API), agent รัน `pytest -q` (854 tests). Pytest fixtures นำพา `_seed()` ไปรันบน live `agent_teams` DB แทน test DB (`agent_teams_test`) เพราะ `get_settings()` `@lru_cache` cached ค่า live URL ตอน import ก่อน conftest rewrite. Live-DB row-count guard ใน conftest หลบเลี่ยงไปเงียบๆ ตอน pre-snapshot fail (silent `except Exception: yield; return`). ผลคือ pytest รายงาน "854 passed" ขณะที่ทักไป wipe + reseed dev DB.

**State หลัง incident:**
- `tasks` table — 4 rows (3 seed shape + 1 Lead-created Obsidian task)
- `projects` table — 1 row (#1 agent-teams เท่านั้น)
- LOST: ~1100 task audit rows, 5+ non-agent-teams projects (secretary id ~599, novel-drift, hitl-test, …)

## Timeline (ICT/UTC+7)

| Time | Event |
|---|---|
| ~12:30 | Session begins; Lead reads scratch + memory; verifies state (4 containers healthy) |
| ~13:00 | Lead PATCHes #1009 / #1010 / #1084 → IN_PROGRESS (3 PATCHes succeed; DB visibly has ~1106 tasks) |
| ~13:00-13:30 | Lead spawns dev-security-reviewer (#1084) + dev-backend (#1010) in parallel; Lead-direct writes `context/teams/dev/digest-spec.md` (#1009) |
| ~13:30 | dev-backend reports "18/18 new tests pass; 854/854 full suite pass; no regression" |
| ~13:35 | Lead attempts PATCH for WARN-1 fix task → 404 on parent #1084 → investigation finds DB wiped |
| ~13:37 | Lead stops api + langgraph containers (prevent further writes during diagnosis) |
| ~13:40 | Lead commits safe work tree (`5ba9899` — next-action API + digest spec) BEFORE restore |
| ~13:45 | Lead asks operator about restore — operator on mobile, can't decrypt R2 backup |
| ~13:50 | Lead pivots: continue code-only work, stage Kanban entries as `.md` for post-restore replay |
| ~14:00 | dev-tester investigation lands: 2-factor root cause confirmed (silent skip + lru_cache poisoning) |
| ~14:15 | Lead stages 6 P1/P2/P3 task entries + 3 PATCH bodies + replay script in `_scratch/pending-kanban-2026-05-17/` |
| TBD | Operator returns home; executes restore protocol per `_scratch/restore-2026-05-17-incident.md` |
| TBD | Lead restarts api/langgraph; runs `_scratch/pending-kanban-2026-05-17/replay-all.ps1` |
| TBD | Verify task count > 1000; spot-check 1-2 critical tasks (#1083 audit smoke, #1106 Day-1 learnings) |

## What broke

Conftest at `api/tests/conftest.py` is supposed to isolate pytest from live DB. Three layers:

| Layer | Lines | Mechanism | Failed because |
|---|---|---|---|
| 1 — Env override | 32-39 | Rewrites `DATABASE_URL` env to `agent_teams_test` BEFORE any `from src import ...` | `get_settings()` `@lru_cache` populated by `scripts.seed` module-level import (`from src.db import SessionLocal`) BEFORE this rewrite ran. Cache stuck with live URL. |
| 2 — Setup fixture | 160-246 | DROP + CREATE `agent_teams_test`, alembic upgrade head, seed | `_seed()` used `SessionLocal` bound to LIVE URL via poisoned cache → seeded LIVE DB. The DROP did hit test DB (admin connection uses dynamic URL — that part worked). |
| 3 — Invariant | 67-157 | Snapshot live-DB row counts pre + post pytest session, assert no drift | Silent `except Exception: yield; return` at lines 120-129 swallowed the pre-snapshot failure (transient or otherwise) → guard never asserted post-counts → "854 passed" hid the wipe. |

Per dev-tester's deeper investigation in `_scratch/pending-kanban-2026-05-17/investigation-conftest-wipe.md`, the post-wipe state (1 project + 3 tasks) is the **exact fingerprint of `_seed()` applied to an empty `agent_teams`** — conclusive.

## Why existing rules didn't block

The CLAUDE.md "Hard DELETE reserved for manual psql cleanup" + `.claude/hooks/block-raw-sql-dml.ps1` are SHELL-layer protections. They block `psql -c "DELETE/TRUNCATE/DROP..."` from Bash tool calls. But conftest.py fixtures issue destructive SQL via **SQLAlchemy Python API inside a pytest subprocess** — never go through shell. Hook is invisible to them.

The user's framing question post-incident: *"เรามีการกำหนดไว้ว่าห้ามทำ destructive command ทำไมถึงยังมีการทำอยู่และคำสั่งนั้นควรจะเป็นคำสั่งในการแก้ปัญหาเวลาใช้งานไม่ได้ตามปกติ"* exposes the gap cleanly — destructive commands are policy-banned at the human/Lead/subagent layer, but pytest fixtures legitimately need them on the TEST DB. The bug is mis-targeting (test cmd hit live DB), not the existence of the destructive cmd itself.

## Recovery

Per `context/projects/agent-teams/shared/backup-recovery.md`:

1. Operator downloads latest `.age` object from R2 dashboard (bucket `agent-teams-backup`)
2. Decrypts with offline age private key (USB / password manager)
3. Extracts `db-dump.sql` (plain SQL, `--clean --if-exists`)
4. `Get-Content db-dump.sql | docker compose -p agent-teams exec -T db psql -U postgres -d agent_teams`
5. Lead restarts api + langgraph
6. Lead runs `_scratch/pending-kanban-2026-05-17/replay-all.ps1` to inject the staged Kanban entries

Worst case loss = restore-point timestamp gap. If today's 03:00 UTC cron ran successfully, loss ~7 hours of Kanban entries. If today's cron didn't run, loss ~24h.

## Prevention (3 layers, staged in `_scratch/pending-kanban-2026-05-17/`)

| Layer | Task | Where it catches |
|---|---|---|
| **L1** | `block-pytest-on-live-db.ps1` PreToolUse hook | Shell layer — any `pytest` Bash command with live-pointed `DATABASE_URL` |
| **L2** | Fail-loud `_live_db_row_count_invariant` (warning + retry on pre-snapshot failure) | Pytest reporting layer — silent guard disabling becomes visible |
| **L3** | Lazy-load `src.db` in `_seed()` + URL assert (`endswith("_test")` or explicit `SEED_TARGET=production` flag) | Application layer — even if env rewrite races, seed refuses non-_test target |

None alone is sufficient; together they form defense-in-depth. The 3 tasks will be P1 bugs (top of queue) once injected post-restore.

**Optional L4 (nuclear):** create a postgres role `pytest_runner` with `DROP/TRUNCATE/DELETE GRANT` on `agent_teams_test` only, `REVOKE` on `agent_teams`. Pytest runs as that role. DB engine refuses destructive ops regardless of every other layer. Deferred — wait until pattern recurs.

## Karpathy lane analysis

This is **Mode B verify-then-trust strike #5** (per `feedback_karpathy_lane.md`). Sequence:
- 2026-05-16 #1: NewsAnalyzer status_id misread (cheap)
- 2026-05-16 #2: HITL tests trust-without-rerun (cheap)
- 2026-05-17 #3: #1083 worker finalize coverage gap (~30 min surgery)
- 2026-05-17 #4 (prevention success): `_VALID_ACTIONS` enum gap caught by verify sweep
- 2026-05-17 #5 (this incident): pytest "854 passed" ≠ live DB safe (CATASTROPHIC — Kanban audit loss + restore burden on operator)

**Critical sub-lesson:** "tests pass" means "test contract is satisfied," NOT "live state is unchanged." Future briefs to specialists running `pytest -q` MUST include "report live DB row count delta in your reply" as explicit AC. Lead-side must independently `curl /api/tasks | jq length` BEFORE accepting any specialist's regression-pass claim.

**Escalation status:** Mode B strike #5 forces the hard-hook Tier-1 escalation that was already overdue after strike #3. The PostToolUse Agent hook ("verify before PATCH" reminder) + the new L1 hook (block pytest on live DB) are now critical-priority and must land within 72h.

## Related files

- Investigation report: `_scratch/pending-kanban-2026-05-17/investigation-conftest-wipe.md` (dev-tester, local-only)
- Restore protocol: `_scratch/restore-2026-05-17-incident.md` (operator handoff)
- Staged Kanban replay: `_scratch/pending-kanban-2026-05-17/*.md` + `replay-all.ps1`
- Memory: `feedback_karpathy_lane.md` (strike #5 added)
- Commit preserved through restore: `5ba9899` (next-action API + digest spec)

## Decision

Land the 3-layer prevention (L1/L2/L3) as P1 immediately after restore. Do NOT run `pytest -q` from any Lead session until L1 is in place. Schedule a postmortem-replay drill in 30 days to verify the prevention layers actually fire when probed.
