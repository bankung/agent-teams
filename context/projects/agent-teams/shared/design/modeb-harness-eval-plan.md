# Mode B + Harness — Eval / Benchmark Test Plan

> **Status:** design (consultation 2026-06-23, no implementation; build DEFERRED). Decision owner: operator.
> **Purpose:** validate the Mode B headless engine + harness via a **repeatable, scored** exam-style benchmark (a regression benchmark — measure Mode B today vs after each change), not a one-shot smoke test.
> **External dataset links verified via web search 2026-06-23; confirm the exact HuggingFace path before download.**
> **One file, two editions** (kept together to avoid duplication — datasets/grading/protocol are shared; only scope + prerequisites differ). See §0.

## 0. Two editions

| Edition | Scope | Runnable on CURRENT Mode B? | Prerequisite |
|---|---|---|---|
| **A — Harness Testing** | brief-contained categories only (cat 1–5, 8, 9 + reading-comp with the passage embedded in the brief) | ✅ **Yes, today** | none |
| **B — Full Test** | Harness edition **+** cat 6 (cross-task) **+** cat 7 (memory/decision read) | ❌ not yet | cat 6 needs **Task A** (explicit handoff); cat 7 needs **Task B** (retrieval, itself measure-gated) |

The split exists because of the §7 finding: current Mode B feeds each task **only its own brief** — no cross-task channel, no memory/decision pull. So the cross-task + memory-read categories test capabilities **that are not built yet**.

## 1. Goal & scope

- **In scope:** the autorun drain loop (`GET /api/tasks/next-autorun` → execute → repeat), output quality on objective + subjective questions, **cross-task context** (Full edition), **durable-context reading** (Full edition), the **HITL `interrupt()` → BLOCKED → resume** path, and **cost / time** per task.
- **Out of scope:** Mode A.
- **Model under test:** the Mode B local model (gemma/ollama, in-container). It is small → **calibrate difficulty** (mix easy/medium) so scores are informative, not floored at zero.

## 2. Test project

- **Recommended:** a **dedicated throwaway project** (e.g. `modeb-eval`) — clean board, easy reset, no contamination of real data, sidesteps live-DB-row sensitivity.
- **Teardown / reset** plan required so the benchmark is repeatable across runs.

## 3. Task set — ~90 tasks (1 question = 1 task)

| # | Category | Count | Edition | Source (§4) | Grading | Tests |
|---|---|---|---|---|---|---|
| 1 | Objective MCQ — knowledge | 20 | Harness | MMLU + ARC-Challenge | exact-match (letter) | knowledge · drain |
| 2 | Objective MCQ — truthfulness/commonsense | 10 | Harness | TruthfulQA-MC + HellaSwag | exact-match | hallucination resistance |
| 3 | Free-text — math | 15 | Harness | GSM8K (+ a few MATH) | normalized numeric match | multi-step reasoning |
| 4 | Reading comprehension (**passage in brief**) | 10 | Harness | SQuAD v2 | exact-match / token-F1 | grounding in *provided* context |
| 5 | Code generation | 10 | Harness | HumanEval / MBPP (+EvalPlus) | run unit tests | gradeable free-text · exec |
| 8 | Open-ended subjective (essay) | 6 | Harness | MT-Bench / AlpacaEval-style | rubric **or** LLM-judge (§5) | subjective output · judge path |
| 9 | **HITL-trigger** | 5 | Harness | CUSTOM (ambiguous / decision) | verify `interrupt→BLOCKED→resume` | Mode B's signature path |
| 6 | **Multi-hop / CROSS-TASK** | 8 (~3 chains) | **Full** (needs Task A) | HotpotQA (supporting docs split across 2–3 tasks via `blocked_by`) | exact-match final + intermediate | cross-task context passing |
| 7 | **Memory / decision READ** | 6 | **Full** (needs Task B) | CUSTOM — plant known values first | exact-match to planted value | durable-context reading |

## 4. Data sources (references, verified 2026-06-23)

**Objective/MCQ:** MMLU `cais/mmlu` (+MMLU-Pro `TIGER-Lab/MMLU-Pro`) · ARC `allenai/ai2_arc` (Easy/Challenge) · TruthfulQA `truthful_qa` · HellaSwag `Rowan/hellaswag` / CommonsenseQA.
**Free-text gradeable:** GSM8K `openai/gsm8k` · MATH (Hendrycks, harder — use a maintained mirror) · SQuAD v2 `rajpurkar/squad_v2` · HotpotQA `hotpot_qa` (multi-hop → the cross-task chain).
**Code (test-graded):** HumanEval `openai_humaneval` · MBPP `mbpp` · EvalPlus `evalplus/evalplus`.
**Subjective (judge):** MT-Bench (github `lm-sys/FastChat`) · AlpacaEval 2.0 (github `tatsu-lab/alpaca_eval`).
**Optional Thai/multilingual:** Belebele `facebook/belebele` (Thai subset) · XQuAD `google/xquad` · TyDiQA · XCOPA `cambridgeltl/xcopa` · Typhoon ThaiLLM Leaderboard · SEACrowd.

> **License:** mostly research/permissive (MIT/Apache/CC-BY) but a few carry specific terms — check per-dataset before redistributing; prefer sampling a slice + citing over copying whole datasets.

## 5. Grading / oracle

- **Per task, the AC carries the expected answer (the oracle)** — reuse `acceptance_criteria`; the grader compares the produced answer to it.
- objective → exact-match · math/reading → normalized match / token-F1 · code → run tests · **subjective → OPEN DECISION:** rubric/keyword (cheap, deterministic) vs LLM-judge (flexible, +cost, non-deterministic, needs a judge model).
- **Answer-capture loop:** define WHERE Mode B writes its answer + HOW the grader reads it back.
- **Pass-bar:** target per category (objective ≥ X%, code ≥ Y%, cross-task 100% chains, memory-read 100% exact, HITL 100% resume) — else no verdict.

## 6. Hard dimensions

- **HITL (cat 9):** force a gate → verify `interrupt → BLOCKED` → answer → `resume` continues. Also exercises Seven Gates / Helgrind (v0.8.0).
- **Cross-task ordering + isolation (cat 6):** chains MUST use `blocked_by`; verify independent tasks do NOT leak into each other.
- **Negative/failure cases:** unanswerable / trap questions; observe wrong-answer / error / retry / fail.
- **Determinism:** gemma is non-deterministic → one-shot vs N runs (e.g. 3×) for variance; fix/record temperature.
- **Cost/time:** record tokens / time-per-task / total drain / metered cost as a primary output (Mode B's value is cheapness).

## 7. Grounded capability findings (CONFIRMED via code 2026-06-23)

Current Mode B assembles per-task context as: system prefix (`build_cached_system_content` = CLAUDE.md + team playbook + agent def, [nodes.py:345](langgraph/nodes.py:345)) + `HumanMessage(brief)` where **`brief` = the task's OWN description/title** ([worker.py:766](langgraph/worker.py:766)); `intermediate_results` starts **empty** every run ([worker.py:769](langgraph/worker.py:769)); only this-task prior-halt is carried (resume). Therefore:

- **No cross-task channel** — a `blocked_by` dependent resumes for *ordering* only; it does NOT receive the blocker's output. → **cat 6 needs Task A** (explicit handoff).
- **No memory/story/decision retrieval** — nothing pulls those into context. → **cat 7 needs Task B** (retrieval, measure-gated).
- The injection guard `sanitize_for_agent_context` already exists (used for prior-halt) — any future context-injection (A/B) MUST route through it.

**Design principle for A/B (locked in consultation):** the goal is the **minimum sufficient context**, not maximum. The harness should be a precision-first context optimizer; broad "pull everything relevant" retrieval risks *lowering* quality (context rot). Cross-task passing (A) = **explicit/declared** handoff (deterministic); background grounding (B) = **retrieval** (fuzzy, measure-gated). See the A/B tasks below.

## 8. Execution protocol

1. Seed the eval project (tasks + AC/oracles; plant cat-7 ground truth; wire cat-6 `blocked_by` chains).
2. Start the Mode B drain → run to empty/stop/stuck.
3. Capture each answer + tokens/time/cost.
4. Grade against oracles → per-category + overall score.
5. (Repeat N× for variance.)
6. Compare to pass-bar → verdict. Teardown / reset.

The **Harness edition** runs steps 1–6 today (cat 1–5,8,9). The **Full edition** adds cat 6 (after Task A) + cat 7 (after Task B).

## 9. Open decisions (operator)

Subjective grading method · pass-bars · run count + temperature · new project vs reuse · include Thai slice · the Task-B measure-gate threshold (§ Task B).

## Tasks (Kanban)

- **Eval project (Full + Harness):** **#2555** — milestone `mode-b-engine` (#26)
- **Task A — explicit cross-task handoff:** **#2556** — milestone `context-management` (#42); unlocks cat 6
- **Task B — retrieval layer (MEASURE-GATED):** **#2557** — milestone `context-management` (#42); `blocked_by #2555`; unlocks cat 7; precondition = the measure-gate (run on #2555's Harness-edition eval)

### Live board (updated 2026-06-24)

**Eval board `modeb-eval` (id=706, team=dev) is live with the full HARNESS edition — 76 dataset-sourced tasks** (ids #2574–#2649), each citing dataset + split + row_idx in its AC oracle:

| Cat | Source | Count | Grading |
|---|---|---|---|
| 1 knowledge-MCQ | MMLU (cais/mmlu) 10 + ARC-Challenge 10 | 20 | exact-match letter |
| 2 truthfulness/commonsense | TruthfulQA-mc1 5 + HellaSwag 5 | 10 | exact-match letter |
| 3 math | GSM8K 15 | 15 | normalized numeric |
| 4 reading-comp (passage-in-brief) | SQuAD v2 (answerable) 10 | 10 | token-F1 / exact |
| 5 code-gen | HumanEval 6 + MBPP 4 | 10 | run unit tests |
| 8 subjective | MT-Bench prompts 6 | 6 | rubric / LLM-judge |
| 9 HITL-trigger | CUSTOM (ambiguous) 5 | 5 | BLOCKED + halt_reason in {question,decision} |

All TODO / `run_mode=auto_pickup` / `task_kind=ai`. Verified: `next-autorun` (X-Project-Id 706) returns #2574 → drainable by the Mode B worker. (The 7 hand-authored smoke starters #2567–#2573 were soft-deleted once the dataset-sourced set landed, for uniform provenance.)

**Oracle-integrity lesson (caught in Lead independent verify, NOT trusting the seeding agent):** TruthfulQA `mc1_targets` lists the correct answer FIRST on every row → naïve dataset-order presentation makes every oracle "A" (position bias; a model that always guesses A scores 100% and a reasoning model is penalised). FIX = shuffle choices per item + recompute the oracle letter (applied to #2635–#2639; new distribution C,B,A,A,G). Any future MCQ seed from a correct-first dataset MUST shuffle. MMLU / ARC / HellaSwag answer indices are already distributed (verified non-degenerate: MMLU `B,C,D,B,B,A,A,D,B,C`).

- **Consent gate:** `POST /grant-consent` is operator-token-gated (403 for Lead) → seeded as `auto_pickup` (needs NO consent; next-autorun drains `auto_pickup` AND `auto_headless`). For true `auto_headless`: operator grants consent, then flip the set.
- **To run a drain:** point the langgraph worker at board 706 (`LANGGRAPH_PROJECT_ID=706` or multi-board) + restart the langgraph container.
- **Still open under #2555:** grading/answer-capture harness (where Mode B writes its answer + auto-compare to oracle) + per-category pass-bars. Cat 6 (cross-task) → #2556 (Task A); cat 7 (memory-read) → #2557 (Task B). `budget_daily_usd` did NOT persist on create (router omits budget_* on POST) — PATCH a cap before running against a paid model.

### Grading harness v1 (built 2026-06-24, #2555 AC3)

**Answer-capture (verified from code):** Mode B writes the model's `final_result` — sanitized + **truncated to 400 chars** — into `tasks.status_change_reason` on the DONE PATCH ([worker.py:1098](langgraph/worker.py:1098)). The grader reads it via `GET /api/tasks`. cat9 (HITL) is instead graded by `process_status==4 (BLOCKED)` + `halt_reason in {question,decision}` (no answer text).

**Harness:** `_scratch/grade_modeb_eval.py` (stdlib-only, READ-ONLY — no PATCH). Run AFTER a drain:
`docker compose -p agent-teams exec -T api python - < _scratch/grade_modeb_eval.py` → prints + writes `_scratch/modeb_eval_scorecard.md` (per-category + overall objective score). Self-test 28/28 (Lead re-ran independently).

| Auto-graded (60) | Method | Deferred to v2 (16) | Why deferred |
|---|---|---|---|
| cat1 + cat2 (30) | exact-match letter | cat5 code (10) | 400-char capture truncates multi-line code + needs exec sandbox |
| cat3 (15) | normalized numeric | cat8 subjective (6) | needs rubric / LLM-judge (plan §9 open) |
| cat4 (10) | substring / token-F1 ≥ 0.5 | | |
| cat9 (5) | BLOCKED + halt_reason | | |

**Still open:** (a) **pass-bars** per category (operator decision, §9 — harness reports raw % until set). (b) Full cat5/cat8 fidelity needs **wider answer-capture** (Mode-B-engine change: persist full `final_result` somewhere other than the 400-char `status_change_reason` — live-test-gated) + an exec sandbox + a judge model. (c) Real scores need a drain (currently 76 not_drained).

### Cross-task handoff — #2556 / cat 6 (built 2026-06-24)

`worker.py` now injects a DONE blocker's `status_change_reason` into the dependent task's brief: pure helper `build_brief_with_handoff(task, blocker_task)` + a blocker fetch in `_poll_once`. Sanitized via `sanitize_for_agent_context`, clearly delimited, **driven by `blocked_by`** (fires only when blocker `process_status==5`; independent tasks untouched; first-invoke only). Reaches the LLM through the existing specialist node — **nodes.py NOT touched**. New tests `langgraph/tests/test_worker_cross_task_handoff.py` (AC1–4 + not-DONE + empty-output edges); py_compile OK (Lead re-ran). Scoped to worker.py + the test file.

**Operator-pending before cat 6 can seed:** (1) run the langgraph pytest suite (in-session pytest is hook-blocked; expect maybe 1–2 fixture fix rounds). (2) a live 2-task `blocked_by` chain drained on Mode B to confirm AC1 end-to-end. THEN seed cat 6 (HotpotQA supporting docs split across `blocked_by` chains, plan §3). Work B / cat 7 (#2557 retrieval) can reuse the same brief-append channel.

---
*Source: consultation session 2026-06-23 (no implementation). Live board seeded + graded 2026-06-24 (#2555 AC1/AC2 done, AC3 harness v1; #2556 cross-task handoff implemented — pending operator pytest + live chain). Findings grounded against the MAIN repo same day. Sibling docs: `async-hitl-gates.md`, `naming-bestiary.md`.*
