# Code map — agent-teams (master index, 2026-06-10)

> Produced for Kanban #2162 (over-engineering review gate before H5). Three mapper
> agents inventoried the services; Lead spot-checked load-bearing claims. Detail
> lives in the per-service maps — this file is the rollup + index.
>
> Refresh trigger: before any architecture-level decision (next: H5), or when a
> service gains/loses a subsystem. Companion: [over-engineering-review-2026-06.md](over-engineering-review-2026-06.md).

## Totals (2026-06-10)

| Service | Prod LOC | Test LOC | Files | Key counts |
|---|---|---|---|---|
| api/ | 40,413 | 58,527 (136 files) | 145 src | 29 routers · ~130 endpoints · 18 tables · 63 services · 63 migrations |
| web/ | 27,179 | ~4,300 | 140 | 10 routes · 87 components · lib/api.ts 2,321 LOC · 5 runtime deps |
| langgraph/ | 8,455 | 11,362 | — | 6 specialist nodes (1 factory) · 8 tools / 4 tiers · 5 providers · 25 env knobs |
| **Total** | **~76,000** | **~74,000** | | |

## Per-service maps

- [code-map-api.md](code-map-api.md) — routers/models/schemas/services tables, oversized-file breakdown (tasks.py 3,030 LOC), config-surface notes (9 pydantic-settings fields vs 88 raw os.environ reads).
- [code-map-web.md](code-map-web.md) — routes/components/lib tables, modal/panel duplication facts, flag inventory (FINANCE_PANELS_ENABLED), SSE topology post-#2111.
- [code-map-langgraph.md](code-map-langgraph.md) — graph topology, worker lifecycle, provider matrix, safety-layer inventory (L16/L17/L22/L23), env-knob sprawl, ops surface (compose/profiles/hooks).

## Status rollup (dormant / notable, Lead-spot-checked)

| Item | Status | Evidence |
|---|---|---|
| Stripe/PayPal payments subsystem (~1,100 LOC, 4 files + router) | DORMANT | no FE/worker caller (Lead re-grepped web/: zero hits); never configured |
| Mailgun ingest.py | DORMANT | no internal caller |
| DeepSeek provider path (llm.py + 3 env vars) | DORMANT BY DECISION | #1838 cancelled 2026-06-10 (service degraded; Gemini live) |
| sessions/CTX metering | LIVE since 2026-06-10 | #2135 wired LANGGRAPH_SESSION_ID (session id=16); was dormant before |
| approval_evaluator mirror (langgraph/ vs api/services/) | IN SYNC logically | Lead verified: identical 6 predicate branches both copies; 96-LOC delta = docs. Risk = future drift (no sync test) |
| email/calendar subsystem (4,274 LOC) | LIVE | secretary workflows (#1799/#1859 gated path) |
| safeMarkdown.tsx custom renderer (428 LOC, zero deps) | LIVE — DELIBERATE | #1005 security decision (no HTML sink, 0-XSS audit); do not swap for a md lib without revisiting that decision |
