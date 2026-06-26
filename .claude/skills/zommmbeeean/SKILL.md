---
name: zommmbeeean
description: >-
  Run the Mode-A continuous auto-run walker ("ZommmBeeean Service") on the bound project — a hands-off
  work-drain loop that auto-picks the next actionable task, runs the full Lead lifecycle, commits
  locally, parks on a gate (Telegram HITL) and walks on to the next, and stops cleanly on
  operator-stop / stuck / list-empty. Use when the operator says "run the walker", "walk the board",
  "drain the board", "auto-run", "zommmbeeean", "keep working tasks until you're stuck/done".
argument-hint: "[stop:drain|hardstop] [max:N]  — drain (default) parks+continues; hardstop stops on first stuck"
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - Edit
  - Write
  - Agent
  - Skill
  - AskUserQuestion
metadata:
  version: 0.1.0
  category: walker
  tags: [mode-a, walker, autonomous, drain-loop, hitl, v0.8.0]
---

# /zommmbeeean — Mode A continuous walker

*"Powered by Caffeine, Driven by Deadlines."* A continuous work-drain loop layered over the
existing primitives (`next-autorun` picker, `tn-tasks-next` ordering, the bootstrap binding, the
async-HITL gate flow + `resume_context`). It automates the *"pick the next task + start it"* step —
**nothing else**. Design: `shared/design/mode-a-walker.md`; boundary: `shared/design/mode-a-autonomy-boundary.md`.

> **Framing (non-negotiable):** this is a normal Mode-A Lead session. Per-action permission prompts
> stay **ON**. **Never** `--dangerously-skip-permissions`. The automation is in task *selection +
> sequencing*, never in bypassing a gate. All per-task discipline (golden rules, AC-verify-before-DONE,
> permission + budget gates, the activity rail) is preserved **unchanged** — draining faster must
> never weaken discipline.

## 0. Parse `$ARGUMENTS` + preconditions

- **stop-policy** — `drain` (DEFAULT) = park a stuck task on a gate and walk on to the next;
  `hardstop` = stop the whole loop on the first stuck task.
- **max** — optional cap: stop after N tasks complete (a safety governor for the first runs).
- **Bound project:** resolve via `powershell -File bin/lead-project-id.ps1` (this session's id). If
  unbound → run `/tn-bind` first, then re-invoke.
- **Poller (for gate answers):** the operator should have the `telegram-poller` compose service running
  (`docker compose -p agent-teams --profile telegram up -d telegram-poller` — #2698, restart-resilient,
  survives `restart api`). If it is NOT running, gates
  still open + notify, but taps won't auto-resolve — the walker still parks + walks on; the operator
  resolves later. Mention this once at start, don't block on it.

## 1. The scope (AC1 — scope-as-list)

`SCOPE` is a **list** of project ids. v0.8.0: it holds exactly ONE id (the bound project). Always
iterate `for pid in SCOPE` — **never hard-code a single project** anywhere in the pick/binding logic.
v0.9.0 widens the list with no rewrite. (For v0.8.0 the list is `[<bound id>]`.)

## 2. The drain loop

Repeat until a stop condition (§4) fires:

**Step A — SELECT the next task.** For each `pid` in `SCOPE`: `GET /api/tasks/next-autorun`
(`-H "X-Project-Id: <pid>"`). From the response build the candidate set:
- `gate_resume_tasks[]` — tasks whose gates are all answered → **RESUME** (continue from
  `resume_context`, never restart).
- `next_task` — the top fresh runnable TODO (already milestone/priority-ordered server-side).

**Precedence:** a `gate_resume` task is picked **before** a fresh `next_task` (an answered task must
continue, not restart). Across `SCOPE` + ties, order by the `tn-tasks-next` policy
(milestone → blocker → priority). If the candidate set is **empty across all of `SCOPE`** →
**STOP (reason: list-empty)** (§4c).

**Step B — EXECUTE via the full normal Lead lifecycle.**
- **If a `gate_resume` task:** READ `task.resume_context` first. The operator's answer is in
  `resume_context.answered_gates[<gate_id>].answer`; the halt-snapshot (where you paused) is in the
  other `resume_context` keys. **Continue from there** — do not re-run completed steps.
- **If a fresh `next_task`:** open → `in_progress` (PATCH `process_status=2`), run the lifecycle —
  research + specialist subagents under the existing permission gates, scoped verification, write
  `decisions.md` + the activity rail (`/tn-report`).
- **Verify EVERY acceptance criterion** before the DONE flip; PATCH the AC array with verdicts, then
  `process_status=5` (use `/tn-task-done`). If any AC is unmet → it's a **stuck** condition (§3), not
  a DONE.
- **Commit locally** when the task's work is a committable unit (`/tn-git-commit` — scoped staging +
  keyword scan; **never pushes**). **Push is Ring-3 gated** (§3) — never push without the gate.
- **Notify task-done** (Ring 2).

**Step C — WALK ON.** Go back to Step A. **No per-task operator re-initiation** — the loop
auto-advances. (This is the core of AC2: ≥2 tasks auto-advance, each closed with AC verified.)

## 3. Stuck → park on a gate (Ring 3)

A task is **stuck** when it hits something you cannot self-resolve: an ambiguous / under-specified /
conflicting requirement, an AC you cannot verify, anything flagged `requires_human_review`, or a
**git push**.

1. **Persist the halt-snapshot FIRST:** PATCH the task `resume_context` with where you paused
   (`step`, any partial draft, a cursor) so a fresh run can resume self-sufficiently (the
   `async-hitl-gates.md` §8 contract). Do this *before* opening the gate (the two-write window is a
   known #2531 hardening item).
2. **Open the gate:** `POST /api/tasks/<id>/gates` with the right tier:
   - `decision` / `hitl` → **simple** (approve/reject buttons);
   - `commit` (incl. **push**) → **informed-approval** — the gate card MUST carry diff-stat +
     pre-push keyword-scan result + test result (the scan stays the real leak guard, the tap is not);
   - `key` / `external` → **Ring 4, NOT chat-answerable** (see §5) — do not open a Telegram gate;
     stop/park for the terminal.
   Opening the gate halts the task (`ps=8`) and fires the Telegram notify automatically.
3. **Apply the stuck-policy dial:**
   - **`drain` (DEFAULT):** the parked task (`ps=8`) is now excluded from the picker → WALK ON to the
     next task (back to Step A). The operator answers async via Telegram → `resolve_gate` flips the
     task onto `gate_resume_tasks` → a later iteration resumes it from `resume_context`.
   - **`hardstop`:** **STOP (reason: stuck)** after opening the gate.

A parked task is **never re-selected** until answered (no busy-loop — AC3).

## 4. Stop conditions — always exit with a LABELLED reason (AC3)

- **(a) operator-stop** — the operator interrupts or sends a stop message. Between iterations, honor
  any new operator input immediately.
- **(b) stuck** — only under `hardstop` (the first stuck task).
- **(c) list-empty** — no actionable task across `SCOPE` (Step A empty).
- **(d) max** — the optional `max:N` cap reached.

On stop: print a summary — tasks completed (ids), tasks parked-on-gate (ids + tiers), the stop
reason — and **notify** (walker-stopped/empty, Ring 2).

## 5. The 4-ring autonomy boundary (hard — `mode-a-autonomy-boundary.md`)

- **Ring 1 — Auto:** select + run the lifecycle, commit locally, walk on.
- **Ring 2 — Notify, don't block:** Telegram on **gate-opened / blocked / done / walker-stopped-or-empty**
  only (fire-and-continue). NOT on routine task-start / subagent-spawn (no phone spam).
- **Ring 3 — HITL-gate:** §3 (decisions / `requires_human_review` = simple; push = informed-approval).
- **Ring 4 — NEVER auto, terminal-only (never chat-approve):** key/secret provisioning · external
  third-party actions · **secretary email** (stays secretary-only — the walker never sends mail) ·
  `.claude/**` self-mod (needs the operator's literal `ii`) · raw DB writes (API only) ·
  **budget / cost-cap breach (hard halt)**. On hitting a Ring-4 need → stop or park for the operator
  at a terminal; do NOT open a chat-answerable gate.

## 6. Compaction resilience (AC4)

- **Spawn subagents for the heavy work** — their context is discarded on return, keeping the Lead
  context lean (fewer compactions).
- The **picker (`next-autorun`) + `resume_context` + the activity rail** are the SOURCE OF TRUTH for
  "where am I". After ANY compaction (or a fresh session), **re-derive position from them**, never
  from in-context memory: re-`GET next-autorun`; a `gate_resume` task carries its full state in
  `resume_context`.
- Re-verify task state at every pickup (a row may have moved since you last looked).

## 7. Tests + proactive compaction (operator-locked 2026-06-25)

**Tests — never round-trip the operator per task.** In-session `pytest` is hook-blocked
(operator-run only), so do NOT stop the drain to ask for a test run each task:
- **Batch it.** During the drain, mark each task's full-suite AC `na` (deferred-to-operator-pytest)
  with a one-line rationale; at the drain's END give ONE `pytest tests -q` (cwd `/repo/api`)
  covering all touched modules — the operator runs it once, not per task.
- **Verify inline what you can** (no asking): endpoints by live-curl, FE by the agent's vitest,
  `py_compile` in-container for syntax. These are the per-task proof; the batched pytest is the
  final backstop.
- **Long-term fix = CI (#2708).** Once the workflow is green, every push runs the full suite
  automatically → the full-suite AC becomes "CI-verified on push" and the manual-pytest ask disappears.

**Compaction — task boundaries are the safe points.** Durable state lives in Kanban + the activity
rail + local commits + design docs, and §6 re-derives position at every pickup — so a compaction
*between* tasks loses nothing. There is **no live per-turn token gauge** to read; rely on harness
auto-compaction (it fires at clean boundaries) + the §6 re-verify after it, and on a very long run
proactively `/compact` **at a task boundary** (never mid-task). Mirror of operator memory
`feedback_walker_continuous_no_per_task_ask`.

## Why this exists

Encodes the operator's by-hand "pick the next task + start it" step as a disciplined, hands-off
drain so a bound project advances without per-task prompting — while every existing gate, permission,
and verification rule stays exactly as strict. Single-project for v0.8.0; multi-project is a v0.9.0
*widening* of `SCOPE` (§1), not a rewrite.

## Related
- `/tn-tasks-next` — the ordering policy this loop reuses.
- `/tn-task-done` · `/tn-git-commit` · `/tn-report` — the per-task lifecycle paved paths.
- `shared/design/mode-a-walker.md` · `shared/design/mode-a-autonomy-boundary.md` · `shared/design/async-hitl-gates.md`.
