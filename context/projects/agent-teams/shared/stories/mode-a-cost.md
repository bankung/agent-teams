---
story: mode-a-cost
version: 1
updated: 2026-06-13
updated_by: lead @ #2355
---

<!-- STORY DOC — mutable thread STATE ("what is true NOW"), single writer = Lead.
     Counterpart: the activity rail holds the immutable per-task EVENTS. Rules locked 2026-06-12 (#2332). -->

## Current state

- **Ledger LIVE.** `usage_events` table (migration `0067`, applied) + `POST /api/usage/events` ingest — commit `9696510` (#2354). Server computes `cost_usd` from raw tokens+model; idempotent on `dedup_key` (re-POST → 200, same id). Per-project rate-limit 60req/10s, 429-before-DB-work — commit `7de0c7f` (#2355). NOTE: ingest is **POST-only — no GET** (a GET 405s); read rows via the DB or the hook log.
- **Capture producer LIVE** — commit `2d78a27` (#2355). Three best-effort lifecycle hooks in `.claude/hooks/` (`subagent-stop-capture.ps1`, `precompact-capture.ps1`, `sessionend-capture.ps1`) over shared `parser.ps1`; `settings.json` wired (operator ii, timeout 15, always exit 0). Task attribution via `_runtime/lead_current_task.txt` marker; Lead delta de-dup via `_runtime/usage_watermark_<session>.json`.
- **Live-proven** (read from `_runtime/usage_capture.log`, not hook self-report): AC2 SubagentStop → `usage_events` id=11 (`strategy=agent_id`, Explore/haiku, task_id=2355); AC3 SessionEnd → ids 9/12 (Lead delta, watermark 0→227→235, no double-count). One real Lead session metered at ~in 81.7K / out 583K / cache-read 60.4M tokens → input+cache dominate.

## Open threads

- **#2360** — verify PreCompact fires on AUTO-compaction (manual `/compact` does NOT, see Gotchas); then keep the hook or remove it as redundant vs SessionEnd. LOW, milestone 37.
- **MEASURE GATE** (workstream checkpoint, no task id) — answer "is context-reading a *material* share of Mode A tokens?" BEFORE building any optimization (#1678, pickup-pack). Currently **UNANSWERED**: the ledger records session/task token TOTALS, not a context-read line-item — needs more accumulated sessions + finer attribution (or input/cache-read share analysis).

## Gotchas

- **Hooks bind at session START from `$CLAUDE_PROJECT_DIR/.claude`.** A session launched from a worktree CWD (`.claude/worktrees/*`) never resolves it → capture silently no-ops (0 log lines). Launch `claude` from the MAIN repo dir. (#2355)
- **Manual `/compact` does NOT fire PreCompact in Claude Code v2.1.162** — it routes through a subagent → fires SubagentStop instead (`usage_capture.log` line 14, `last_assistant_message:"/compact"`). Wiring is correct (`matcher:""`). **SessionEnd is the reliable Lead-delta trigger + durable backstop** (every session ends → no cost lost). (#2355 → #2360)

## Decisions pointer

- Milestone **#37** (mode-a-cost design). `decisions.md` — grep `usage_events` / `mode-a-cost`.

## Changelog

- v1 2026-06-13 #2355 — story opened at P2 close. Ledger + capture producer LIVE; AC2 (SubagentStop) + AC3 (SessionEnd) live-proven; PreCompact-on-/compact gap → follow-up #2360; MEASURE GATE still open.
