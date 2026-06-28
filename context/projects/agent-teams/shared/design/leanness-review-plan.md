# Leanness review — measured plan + Phase-0 findings

**Status:** Phase 0 (measure) COMPLETE — 2026-06-26. Review/measurement only; zero code edits.
**Trigger:** operator noticed `zb-bind` costs ~500 tok → "are we lean elsewhere?" → measure-first review of load / perf / token spend.

## Governing rule
Measure before optimizing. Phase 0 is the gate: only run a downstream phase the data justifies.
Each pass has a stop-gate (diminishing returns — same posture as the API audit campaign #2665→#2691, which went real-perf → cold-leanness → zero).

## Phase 0 — what we measured (all read-only)

**Telemetry is trustworthy.** Interactive cost is captured per session from the Claude Code transcript by the SessionEnd/PreCompact/SubagentStop hooks ([sessionend-capture.ps1](.claude/hooks/sessionend-capture.ps1) → `POST /api/usage/events`), keyed by `session_id`+`project_id`+`task_id`+`agent_name`, de-duped by `message.id`.
- Cross-check transcript ↔ what the hook flushed (`_runtime/usage_capture.log`) = **EXACT on all token counts** (session 01046e4f: in 19869 / out 94513 / cache_read 12.55M / cache_create 547871).
- Dollar side also correct: `cost_tracker` prices `claude-opus-4-8` at $5/$25 + cache 1.25×/0.10× = authoritative ([claude-api] pricing table); the hook's $12.1623 reconciled to the penny.
- **Data usable from ~2026-06-13** (migration 0067 / #2355 built the capture). Before that: no interactive capture. `session_runs` / `usage/daily` = Mode-B local-model + legacy only, NOT the interactive store. `usage_events` has the richest fields (cache split, `agent_name` Lead-vs-subagent) but **only a POST endpoint — no GET** (the gap that forced manual transcript parsing).
- Naïve transcript parsing **double-counts ~2×** — each assistant message is logged twice; de-dup by `message.id` (the hook already does this).

**Baseline (4 sessions, opus-4-8 rates, de-duped):**

| session | turns | cache_read | hit | turn-1 floor | avg ctx/turn | cost |
|---|---|---|---|---|---|---|
| db1f24ad | 370 | 111.7M | 95% | 36.6k | 317k | $116.76 |
| 9befd476 | 251 | 65.6M | 95% | 35.3k | 274k | $64.69 |
| 01046e4f | 87 | 12.5M | 96% | 37.1k | 151k | $12.16 |
| 0570819a | 53 | 7.3M | 95% | 37.2k | 146k | $8.44 |

**Aggregate:** $202 total · avg **$50.51/session** · avg **190 turns** · cache-hit **95.3%** · **cache_read = 49% of cost**.

## Phase-0 verdict — the prior was wrong, measurement corrected it

cache_read (49% of cost) is where the cost concentrates, but the trimmable slice inside it is small:

| cache_read component | size/turn | we control? | ~% of cost |
|---|---|---|---|
| fixed docs (CLAUDE.md 4.7k + dev.md 3.9k + MEMORY.md 5.6k) | **14.2k** | yes | **~2.7%** |
| whole turn-1 floor (~36k incl. system prompt + tools ~22k) | ~36k | mostly harness | ~6.8% |
| **accumulated context** (history + tool results, avg/turn 146k→317k) | **dominant** | yes | **~42%** |

**The cost driver is accumulated context per turn on long sessions — NOT fixed bootstrap/MEMORY.md.**
Tail sessions dominate (db1f24ad: 370 turns = $116 ≈ 2–3× the average).

## Reframed plan
- **Phase 1 (trim CLAUDE.md/MEMORY.md/playbook):** real but **~1–3%**. Do it because it's cheap (`zb-memory-compact`), not because it's the win.
- **Session hygiene = the real lever (~42% of cost):** compact/clear more often, leaner Lead-side tool outputs, shorter tail sessions. Mostly discipline (CLAUDE.md "Context hygiene" already partial), but **blocked on visibility** — no per-session cost surface exists → see task below.
- **Runtime perf (API/FE):** clean (audit #2665→#2691 = 1 LOW finding in 5 batches). Don't re-hunt.

## Tasks opened from this work
- **#2727** `[bug][cost]` — `cost_tracker` stale Opus rates: non-4-8 opus resolves to the $15/$75 alias; current opus-4-6/4-7 are $5/$25. Latent today (system runs 4-8, priced correctly) but a 3× over-charge for any non-4-8 opus.
- **#2728** `[feature][cost]` — per-session cost read/aggregate on `usage_events` (the missing GET) so tail-session cost is visible and session-hygiene becomes measurable. UI surface = follow-up (out of scope).

## Open / not-yet-actioned
- Behavioral levers (clear cadence, lean outputs) = discipline; CLAUDE.md edit would need operator `ii` (self-mod gate).
- Interactive-session context is managed by the Claude Code harness, not our app — app-side lever is visibility + discipline, not auto-compaction of the Lead transcript.
