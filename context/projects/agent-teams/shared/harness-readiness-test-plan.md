# Harness readiness — test plan ("when is the langgraph headless harness 'done'?")

Owner: Lead. Source: rolled up from #1710 (validation), #1717 (compaction), #1720 (hardening) + dev-reviewer findings. Updated 2026-05-31.

## Two milestones (don't conflate)

- **M1 — Harness CORE done** (generic engine, model-agnostic). ← the "truly done" we get first.
- **M2 — Secretary-level done** (domain: secretary node + email/calendar/browser tools). ← later, separate.

The teardown of the Gemini test setup (project 661 + GCP) stays OPEN until **M1** is green — we still need a live model to run the integration tests below.

---

## M1 coverage — what proves the core harness is "done"

### A. Context management (Tier 1)
| id | test | status |
|---|---|---|
| A1 | compaction unit tests (under/over budget, pairing, multi-call unit, env reader) | ✅ #1717 |
| A2 | HALT multi-call orphan test (H-1) | ⏳ #1720 |
| A3 | budget-floor-exceeded observability (M-2) | ⏳ #1720 |
| A4 | **end-to-end loop test that ACTUALLY fires compaction across >=4 iterations** (current loop tests stay under budget → compaction never exercised in integration). Scripted fake model emitting oversized ToolMessages. | ❌ NEW |
| A5 | idempotency (compact twice = same) + exact-boundary + a single huge ToolMessage inside the recent-N window | ❌ NEW |

### B. Multi-turn tool-use end-to-end with a REAL model — THE keystone gap
| id | test | status |
|---|---|---|
| B1 | turn-1 tool call works (git_status invoked) | ✅ #1710 |
| B2 | **a real model completes a MULTI-STEP tool task end-to-end through our harness**: pickup → tool call → result → reason → final answer → DONE. UNPROVEN — Gemini `thought_signature` broke turn 2. Needs a multi-turn-capable model (billing-enabled gemini-2.0-flash OR native gemini provider OR other). | ❌ NEW (critical) |
| B3 | a single AIMessage with >=2 tool_calls executed end-to-end (exercises the H-1 path live) | ❌ NEW |

> B2 is the keystone — it's what actually proves "the harness works for real work." Everything else is supporting. It's the reason teardown (B) stays open.

### C. Control-flow paths (coded; need integration validation with a real model)
| id | test | status |
|---|---|---|
| C1 | auditor: PASS / AUTO_RESOLVE-retry / ESCALATE with a real model's JSON verdict | ❌ |
| C2 | HITL: pause (question/decision) → operator answer → resume → DONE end-to-end | ❌ |
| C3 | error recovery: a tool returns failure → model adapts → completes | ❌ |
| C4 | permission gate: a halt-tier tool → task halts for review (does NOT auto-run) | ❌ |

### D. Robustness / quality
| id | test | status |
|---|---|---|
| D1 | realistic multi-step task (5–8 tool calls, some large outputs) → completes with good quality, compaction keeps context bounded, no overflow | ❌ |
| D2 | loop-budget exhaustion → graceful halt (TOOL_LOOP_HALT_REASON) | partial (unit) |
| D3 | provider matrix: anthropic + real-openai + working-gemini produce equivalent behavior | ❌ |

---

## M2 — Secretary-level (separate milestone, OUT of current scope)
- secretary specialist node in the engine (only `backend` exists today; 4 roles are stubs).
- domain tools in the registry: email / calendar / browser (today: fs/vcs/http/shell only).
- per-tool tests + one real secretary task (triage → draft) end-to-end.

---

## Sequencing recommendation
1. **#1720** — H-1 (HALT orphan) + M-1 (perf) + M-2 (floor warning). Unblocks multi-call correctness. ← in progress
2. **A4 / A5** — prove compaction actually fires in the loop (cheap; fake-model integration + unit). 
3. **B2 (keystone)** — get ONE multi-turn-capable model working, run a real multi-step task end-to-end. Needs Gemini billing (2.0-flash paid) OR a native gemini provider OR another key. ← keep Gemini setup (B) open for this.
4. **C / D** — validate auditor / HITL / error / robustness with that working model.
5. M1 green → **teardown** (revert env to anthropic+project 1, delete project 661 + GCP) → decide whether to start M2.

## Open dependency for B2 (decision needed later)
To run B2 we need a model that round-trips multi-turn tool calls. Cheapest options, in order:
- (a) enable billing on the Gemini project → `gemini-2.0-flash` (non-thinking, paid tier from $300 credit) — likely round-trips fine.
- (b) a native `gemini` provider branch via langchain-google-genai (handles thought_signature) — bigger build.
- (c) any other OpenAI-compatible key without the thinking-model quirk.
