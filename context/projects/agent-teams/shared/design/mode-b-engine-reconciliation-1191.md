# Mode-B langgraph engine — #1191 reconciliation, decomposition & browser-bridge decision

> **Status:** design deliverable. Lead-promoted 2026-06-02 from a read-only `dev-sr-backend` reconciliation (Kanban #1191; operator chose *design + decompose*, not build). Evidence cited against the MAIN repo (`langgraph/*`, `api/*`) at this date. Full draft with every file:line citation: `_scratch/mode-b-engine-design-1191.md` (transient) + git.

## TL;DR verdict — the engine is ~85% already built

#1191's "Phase 1 core engine" is **substantially already built** in the `langgraph/` service: a compiled LangGraph StateGraph, an `AsyncPostgresSaver` checkpointer (resumable state under the `langgraph` schema), a multi-turn tool loop with permission gate + sandbox + audit + deterministic context compaction, a full **HITL `interrupt()`→Kanban BLOCKED→resume** bridge, an auditor self-critique loop, and approval-policy auto-approve/deny. It is wired via a **Kanban poll model** (`GET /api/tasks/next-autorun`), not the `POST /api/workflows/<name>/invoke` shape #1191 imagined.

**#1191 as filed conflates two milestones.** Rescoped:
- **M1 — core harness validated (generic, model-agnostic):** mostly built; the genuine remaining work is *validation + one experiment*, not engine-building.
- **M2 — secretary domain (browser/Gmail + triage node):** the only large greenfield piece; separable; multi-week.

## Reconciliation (AC → current reality)

| AC | Verdict | Note |
|---|---|---|
| 0 — langgraph dep + install | **DONE** | `langgraph/pyproject.toml` pins `langgraph==1.2.0` + postgres checkpoint; container builds live. (Targets `langgraph/`, NOT `api/` as the AC said.) |
| 1 — StateGraph classify→action→execute→report | **DONE (engine) / MISSING (secretary node shape)** | Generic `supervisor→specialist→auditor` graph exists; only `backend` is a real specialist (4 stubs). The secretary-domain triage node shape is M2. |
| 2 — Postgres state, resumable | **DONE** | `AsyncPostgresSaver` + per-task thread `task-{id}`; resume idempotent (LangGraph 1.2.0). |
| 3 — `POST /api/workflows/<name>/invoke` | **DONE-DIFFERENTLY** | No such endpoint; the **poll model** (`next-autorun`) + langgraph `POST /invoke` are superior — inherit budget/consent/run_mode gates for free. **AC rewritten, not built.** |
| 4 — browser/Gmail tool | **MISSING (real net-new)** | Tool registry is fs/vcs/shell/http only; zero browser/gmail/chrome/playwright. **M2.** See decision below. |
| 5 — HITL interrupt→BLOCKED→resume | **DONE (coded)** | Full bridge; decision-task `chosen_id` + idempotency + give-up handled; L16/L23 sanitized. Live-model validation folded into M1 control-flow check. |
| 6 — Mode-A-vs-B cost benchmark ≤70% | **MISSING** | Measurement plumbing ready (`session_runs.total_cost_usd`; prompt-cache measured 77.5% input reduction); the A-vs-B experiment was never run. Gated on the B2 keystone. |
| 7 — design doc | **satisfied by THIS doc** (§ below) | |

**Cross-cutting UNPROVEN — the B2 keystone:** no real model has completed a multi-step tool task end-to-end through the harness (Gemini broke turn 2 on `thought_signature`, per `harness-readiness-test-plan.md:29`). The engine is *coded + unit-tested*, not *proven for real work*. This gates the cost benchmark and the credibility of AC[1]/AC[5] live.

## Browser-bridge decision (AC[4] fork) — **Playwright/Selenium sidecar (Option B), staged**

The secretary reaches authenticated Gmail/LinkedIn via **Chrome MCP** in interactive (Mode-A) sessions today — driving the operator's real logged-in browser. Mode B runs headless with no operator present.

- **Option A (Chrome-MCP bridge):** best auth/session reuse + minimal container footprint, BUT structurally couples autonomy to operator presence (defeats "headless"), and puts an autonomous LLM at the wheel of the operator's fully-authenticated real browser = unacceptable unattended blast radius; external-dependency drift.
- **Option B (Playwright headless sidecar):** purpose-built for headless/no-desktop, pinnable + isolated, fits the existing tool-tier/permission/audit model as "just another network-tier tool." Real cost = auth/session reuse (seeded encrypted `storageState` + refresh, surviving Google/LinkedIn bot-detection + 2FA) — a *bounded, solvable ops problem*.

**Recommendation: Option B, staged** — B-stage-1 (navigate+read a public page, proves plumbing) → B-stage-2 (seeded encrypted Gmail storageState, **read-only** triage, NO send, HITL-on-send) → B-stage-3 (action tier behind approval-policy + HITL). Option A reserved as a Mode-A-only fallback. **Hard prerequisite regardless of option:** a NEW permission tier above `DESTRUCTIVE` — `IDENTITY`/`EXTERNAL_AUTH` — for "acts as the operator on an authenticated external account," defaulting to HITL-on-every-call; browser-send NEVER auto_allow (ties the `mode-b-authorization-chain.md` #1205 doctrine).

## Mode-A-vs-Mode-B guidance + migration path (AC[7])

**When to use each:**
- **Mode A** (interactive spawn): design-heavy/novel-surface work, anything needing per-action human approval, expensive/irreversible actions, exploration. Production path today; highest judgment + safety; higher per-task cost.
- **Mode B** (headless engine, `run_mode=auto_pickup`): repetitive, well-specified, bounded tasks with a known + gated action space (email triage, status sweeps, scheduled routine). Lower per-task cost; scalable. Currently *in active development — treat results as drafts*; fully-autonomous end-to-end is *Roadmap*.

**Cost argument (AC[6] target):** Mode A measured ~$21/day for ~15 spawns (~$1.40/spawn); lifecycle-program scale is 100s/day. Mode B's edge: prompt-cache amortization (measured 77.5% input reduction on a 10-iteration run), no interactive overhead, bounded tool loop + compaction. AC[6] is the empirical check: same task, A vs B, assert Mode-B ≤70%.

**Migration (staged):** M1 core-harness validated → M2 secretary domain → per-project runtime deps (#1652; Phase-1 guard #1800 shipped) → gradual cutover (route cheapest/most-repetitive templates to Mode B first, Mode-A fallback on halt, widen as confidence grows; Mode A stays default for novel/risky work indefinitely).

## Decomposition (filed as Kanban children under #1191)

- **M1 closure (near-term):** B2 keystone (multi-turn tool-use with a real model — **the gate**); compaction-fires-across-iterations validation; control-flow live (auditor/HITL/error-recovery/permission-gate); Mode-A-vs-B cost benchmark (AC[6], ≤70%).
- **M2 epic (multi-week, separable):** Playwright browser sidecar + `IDENTITY` permission tier + Gmail read-only triage (seeded storageState) + secretary `classify→action→execute→report` node + one real triage task.

ACs already DONE in `langgraph/` (no task filed): AC[0] dep, AC[2] checkpoint/resume, AC[5] HITL bridge (coded), AC[1] generic engine. AC[3] rewritten (poll model). AC[7] = this doc.
