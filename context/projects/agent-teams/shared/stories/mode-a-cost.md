---
story: mode-a-cost
version: 3
updated: 2026-06-15
updated_by: lead @ #2356
---

<!-- STORY DOC — mutable thread STATE ("what is true NOW"), single writer = Lead.
     Counterpart: the activity rail holds the immutable per-task EVENTS. Rules locked 2026-06-12 (#2332). -->

## Current state

- **Ledger LIVE.** `usage_events` table (migration `0067`, applied) + `POST /api/usage/events` ingest — commit `9696510` (#2354). Server computes `cost_usd` from raw tokens+model; idempotent on `dedup_key` (re-POST → 200, same id). Per-project rate-limit 60req/10s, 429-before-DB-work — commit `7de0c7f` (#2355). NOTE: ingest is **POST-only — no GET** (a GET 405s); read rows via the DB or the hook log.
- **Capture producer LIVE** — commit `2d78a27` (#2355). Three best-effort lifecycle hooks in `.claude/hooks/` (`subagent-stop-capture.ps1`, `precompact-capture.ps1`, `sessionend-capture.ps1`) over shared `parser.ps1`; `settings.json` wired (operator ii, timeout 15, always exit 0). Task attribution via `_runtime/lead_current_task.txt` marker; Lead delta de-dup via `_runtime/usage_watermark_<session>.json`.
- **Live-proven** (read from `_runtime/usage_capture.log`, not hook self-report): AC2 SubagentStop → `usage_events` id=11 (`strategy=agent_id`, Explore/haiku, task_id=2355); AC3 SessionEnd → ids 9/12 (Lead delta, watermark 0→227→235, no double-count). One real Lead session metered at ~in 81.7K / out 583K / cache-read 60.4M tokens → input+cache dominate.
- **Hardened pre-0.6.3** (commit `8570c46`, #2361 — 2-round intense review + security test, 0 blockers/0 majors): token fields bounded `le=1e9` (overflow guard, still accepts real 60M); api host port → `127.0.0.1` (W1, closes LAN exposure); 3 hooks drop conversation content (`last_assistant_message`) from entry-logs (W2). Determinism 10x: 62 tests green, DB 106→106.

- **Read side LIVE — P3 #2356 (commit `3f75a7b`, on `dev`, not pushed).** `GET /api/usage/monthly`: billing-cycle rollup (cut-off day via `?cycle_day` > `COST_CYCLE_DAY` setting > 1, capped 1-28); per-cycle Mode A (`usage_events` by `occurred_at`) + Mode B (`session_runs` by `coalesce(finished_at,started_at)`), summed (DISJOINT paths, NOT double-counted), per-task drilldown (null=unattributed), zero-filled, most-recent first. `occurred_at` clamp `[now-30d, now+5min]` -> 422 on the ingest (AC2 carry-over; blocks backdating into closed cycles). Dashboard `MonthlySpendSection` card (prop-driven for RTL determinism; Mode A est blue + Mode B actual amber + total + drilldown), mounted after `CostSummary`. Operator-level/header-free -> inherits K1 no-auth gap; the per-task `task_title` cross-project disclosure is a NEW widening, registered in `decisions.md`. Reviews APPROVE-WITH-NOTES (0 blocker/major). Verified live (2 cycles A/B split, clamp 422); api usage tests 25, web 323.

## Open threads

- **#2360** — verify PreCompact fires on AUTO-compaction (manual `/compact` does NOT, see Gotchas); then keep the hook or remove it as redundant vs SessionEnd. LOW, milestone 37.
- **MEASURE GATE** (workstream checkpoint, no task id) — answer "is context-reading a *material* share of Mode A tokens?" BEFORE building any optimization (#1678, pickup-pack). Currently **UNANSWERED**: the ledger records session/task token TOTALS, not a context-read line-item — needs more accumulated sessions + finer attribution (or input/cache-read share analysis).
- **#2362** — post-review nits: W2 error-path `$rawIn` in DROP-unparseable fallback; 422 test covers 1 of 4 token fields; hook `project_id` int-validate; parser mtime fallback. LOW, milestone 37.
- **W1 runtime apply** — `docker compose -p agent-teams up -d` from the MAIN repo to make the `127.0.0.1` port binding effective (file shipped in 8570c46; runtime apply pending at deploy).

## Gotchas

- **Hooks bind at session START from `$CLAUDE_PROJECT_DIR/.claude`.** A session launched from a worktree CWD (`.claude/worktrees/*`) never resolves it → capture silently no-ops (0 log lines). Launch `claude` from the MAIN repo dir. (#2355)
- **Manual `/compact` does NOT fire PreCompact in Claude Code v2.1.162** — it routes through a subagent → fires SubagentStop instead (`usage_capture.log` line 14, `last_assistant_message:"/compact"`). Wiring is correct (`matcher:""`). **SessionEnd is the reliable Lead-delta trigger + durable backstop** (every session ends → no cost lost). (#2355 → #2360)
- **`cost_usd` is `Numeric(10,4)`** (max ~$999,999.9999) — token inputs bounded `le=1_000_000_000` to prevent overflow on absurd payloads; bound is generous (real `cache_read` hits 60M+). (#2361)
- **Hooks must NOT log `$rawIn`** (carries `last_assistant_message`). Entry-logs now emit non-content fields only; the DROP-unparseable fallback still logs `$rawIn` → #2362. (W2, #2361)

- **The api container does NOT hot-reload a new route on a bind-mount source edit** (Windows Docker inotify gap, same class as web `WATCHPACK_POLLING` #2386) — `docker compose -p agent-teams restart api` to load a NEW endpoint before live-verifying. Bit #2356 (the `/monthly` route 404'd until restart). (#2356)

## Decisions pointer

- Milestone **#37** (mode-a-cost design). `decisions.md` — grep `usage_events` / `mode-a-cost`.

## Changelog

- v3 2026-06-15 #2356 — read side LIVE: `GET /api/usage/monthly` billing-cycle rollup + `occurred_at` clamp (AC2) + `MonthlySpendSection` dashboard card (commit `3f75a7b`). dev-reviewer + dev-security-reviewer APPROVE-WITH-NOTES (0 blocker/major); Lead folded M1/M2 comment fixes + SW-1 (`task_title` cross-project widening -> decisions.md K1 gap). Verified live (monthly 2 cycles A/B, clamp 422); api usage tests 25, web 323. P3 closes the mode-a-cost build arc (P1 ingest + P2 capture + P3 read all LIVE).
- v2 2026-06-13 #2361 — pre-0.6.3 intense review + security test (0 blockers/0 majors): token overflow guard, W1 port→127.0.0.1, W2 hooks drop conversation content (8570c46); residual nits → #2362; README v0.6.3 section added.
- v1 2026-06-13 #2355 — story opened at P2 close. Ledger + capture producer LIVE; AC2 (SubagentStop) + AC3 (SessionEnd) live-proven; PreCompact-on-/compact gap → follow-up #2360; MEASURE GATE still open.
