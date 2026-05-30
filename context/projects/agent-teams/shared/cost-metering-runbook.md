# Cost metering — Mode-A usage-reporting runbook (#1689)

**Status:** G1 (estimates) shipped (#1688). G2 (real metering) **partial** — this doc is the Mode-A reporting contract + the scope split.

## The two modes

- **Mode A** — Claude Code (the Lead) does the primary orchestration. The platform does **not** make these LLM calls, so it cannot auto-meter them; usage must be **reported back**.
- **Mode B** — the langgraph headless engine. The platform owns the LLM call and *could* auto-capture tokens, but this is **gated by #1652** (runtime/dependency gate). Not yet wired.

## What's ready (G2 Part A)

- `PATCH /api/session_runs/{id}` computes cost **server-side** via `cost_tracker.compute_cost` and now accepts prompt-cache token inputs:
  - `total_input_tokens`, `total_output_tokens` (existing)
  - `cache_read_input_tokens` — billed **0.10×** the base input rate
  - `cache_creation_input_tokens` — billed **1.25×** the base input rate
  - `provider` + `model` — drive the pricing-table lookup
- `GET /api/projects/stats` `cost_usage` SUMs `session_runs` → once runs carry real tokens, **METERED** cost goes non-zero (distinct from G1's "Estimated").
- Client-supplied `total_cost_usd` is ignored (server-authoritative).

## The Mode-A reporting procedure

For a task whose token usage is known:

1. `POST /api/sessions/{session_id}/runs` with `{task_id}` → returns a `session_run` id. Do this **once per logical run**.
2. `PATCH /api/session_runs/{run_id}` with: `status=finished`, `finished_at`, `provider`, `model`, `total_input_tokens`, `total_output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`. Server recomputes + stores `total_cost_usd`.
3. The run now appears in `/api/projects/stats` `cost_usage` (metered).

## Idempotency

PATCH **overwrites** token/cost fields (does not increment). Re-reporting the same run id with the same tokens is therefore **safe** (no double-count). Rule: POST one `session_run` per logical run, then PATCH that id — do **not** POST a new run per retry.

## Known limitations / gated

- Claude Code does not currently expose precise per-task token counts to the Lead, so Mode-A reporting is **semi-manual / best-effort** until a usage-surface exists. A convenience "report usage by task_id" endpoint + automatic capture is a **#1652-era enhancement**.
- Mode-B (langgraph) auto-metering: **gated by #1652**.
- Live "real non-zero metered" end-to-end verification (#1689 AC3) requires a real provider key configured (compact_runner / langgraph / parser calls all need a key). The plumbing is **unit-test-verified** (`api/tests/test_session_run_cache_metering.py`) without a key.

## Out of scope (by prior design)

- `ai_task_parser` cost: explicitly excluded per **#856** (one-off parse calls, not task runs).
- `compact_runner` cost is currently metered into `session_compacts.compact_cost_usd` (separate audit table). Consolidating it into `session_runs` is a possible follow-up — a semantic decision, deferred.
