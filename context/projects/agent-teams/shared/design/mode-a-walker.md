# Design — Mode A continuous walker ("ZommmBeeean Service") — v0.8.0 #5 (#2531)

> **Status:** build doc — walker **#2531**, milestone **v0.8.0 (#50)**. Lead-authored 2026-06-24.
> Altitude **skill-first** (operator sign-off 2026-06-24, matching #2531's own stated AC altitude).
> Companions: `mode-a-autonomy-boundary.md` (the 4-ring *policy*) + `async-hitl-gates.md` (the gate
> *mechanism* the "stuck" path consumes). Consumes Task C **#2566**'s `next-autorun.gate_resume_tasks`
> + the `resume_context` self-sufficiency contract.
>
> **Codename:** ZommmBeeean Service — *"Powered by Caffeine, Driven by Deadlines."* This doc is named
> after the durable concept (`mode-a-walker`) so a codename change stays a find/replace, not a rename.

## 1. What it is

The Mode-A continuous walker automates the *"pick the next actionable task + start it"* step the
operator does by hand today. It is a normal Lead session, bound to a project, that runs a CONTINUOUS
work-drain loop: select the next actionable task in scope → run the full Lead lifecycle → select the
next → until one of {operator stop, stuck, list empty}. Conceptually `/loop` self-pace, but the
payload is cross-task **work-selection**, not a fixed prompt.

**Non-negotiable framing (from the autonomy boundary):** the walker IS a normal Mode-A Lead session.
Per-action permission prompts stay **ON**; **no `--dangerously-skip-permissions`, ever.** The
automation is in task *selection + sequencing*, never in bypassing a gate.

## 2. Build altitude — a SKILL, not a service (locked 2026-06-24)

Built as a **Lead skill / prompt-pattern** layered over existing primitives, NOT a persisted backend
entity:
- the `next-autorun` picker (`gate_resume_tasks` + `next_task`) + the `tn-tasks-next` ordering
  (milestone → blocker → priority);
- the existing bootstrap (session → project binding);
- the existing HITL/gate flow + `resume_context` (the "stuck" path).

The walker "profile" is a **skill-local construct** (the scope list — §4), NOT a DB table. Promote it
to a first-class persisted entity only **if** profiles must survive/share across sessions (a v0.9.0
question). Rationale: minimum-viable for single-project v0.8.0; the loop + boundary are orchestration
logic, which is the skill's domain — and a subagent cannot author `.claude/**` anyway.

The skill lives at `.claude/skills/zommmbeeean/SKILL.md` — the self-mod gate means the **operator
installs it with the literal `ii`** (Lead drafts to `_scratch/` first).

## 3. Two orthogonal innovations (why v0.8.0 = single-project)

ZommmBeeean carries TWO independent innovations:
1. **the continuous autonomous loop** (THIS build, v0.8.0); and
2. **multi-project / cross-team binding** that breaks the hard-wired 1-session-1-project assumption
   (DEFERRED to v0.9.0).

Only (2) destabilizes the foundational session model, so it is staged separately. v0.8.0 reuses the
existing 1-session-1-project binding. The clean rollout hinges on ONE discipline: code the task-source
as "next actionable in MY SCOPE (a list of one)", never "next task in project X" — then v0.9.0 is a
*widening*, not a rewrite (§4).

## 4. The scope-as-list seam (AC1)

The walker's task-source is **"next actionable in MY SCOPE"**, where SCOPE is a *list/collection* that
in v0.8.0 holds exactly one project id (the bound project) — NOT a hard-coded single-project alias.
v0.9.0 widens the list (adds project ids) with NO change to the picker or the binding logic; the loop
iterates the scope list, unioning each project's `next-autorun`.

**Verifiable seam:** a second project id can be added to the scope representation without editing the
pick/binding logic.

## 5. The drain loop (AC2)

Per iteration:
1. **Select** — for each project in scope, `GET /api/tasks/next-autorun`. Precedence:
   **`gate_resume_tasks` (resume) BEFORE `next_task` (fresh)** — a resumed task carries its answer in
   `resume_context` and must *continue*, not restart. Across scope + within a project, order by the
   `tn-tasks-next` policy (milestone → blocker → priority). [v0.8.0: scope = 1 project.]
2. **Execute** — run the FULL normal Lead lifecycle on the picked task: open → `in_progress`,
   research + specialist subagents under the existing permission gates, verify EVERY AC before the
   DONE flip, write `decisions.md` + the activity rail, **commit locally** (reversible; push is gated
   — §7), flip DONE.
3. **Advance** — select the next, with NO per-task operator re-initiation.

**All per-task discipline is preserved UNCHANGED** — golden rules, AC-verify-before-DONE, permission +
budget/cost gates. The loop must NOT weaken discipline by draining faster. Verifiable: a run
auto-advances through ≥2 tasks without re-prompting, each closed task showing its AC verified.

## 6. Clean termination + no busy-loop (AC3)

The loop stops + reports the reason on EXACTLY one of:
- **(a) operator stop** — the operator interrupts / signals stop;
- **(b) stuck** — a task hits a blocker/HITL it cannot self-resolve;
- **(c) list empty** — no actionable task across scope.

A **stuck** task is PARKED so it is not re-selected next iteration (no busy-loop): the walker opens a
`task_gate` → task `ps=8` (HALTED_PENDING_USER) → structurally excluded from `next_task` AND (until
answered) from `gate_resume_tasks`. When the operator answers via Telegram, `resolve_gate` flips it
back and surfaces it on `gate_resume_tasks` for resume.

**Stuck-policy dial (documented, independent of project count):**
- **file-gate-and-continue (DEFAULT)** — park the stuck task (open a gate + notify), DRAIN to the next
  actionable task, keep going. Maximizes throughput.
- **hard-stop-on-first-stuck** — stop the whole loop on the first stuck task (reason (b)). For
  high-stakes / low-trust runs.

## 7. The 4-ring autonomy boundary (reference)

Full policy: `mode-a-autonomy-boundary.md`. Summary:
- **Ring 1 — Auto:** select + run the lifecycle, commit locally, advance.
- **Ring 2 — Notify, don't block:** Telegram on gate-opened / blocked / done / walker-stopped-or-empty.
  Fire-and-continue.
- **Ring 3 — HITL-gate (halt `ps=8` + open a gate + async-wait on Telegram):** an unresolvable
  decision or `requires_human_review` (simple buttons); **git push** (informed-approval — the card
  carries diff-stat + pre-push keyword-scan + test result; the scan stays the real leak guard, the tap
  is not).
- **Ring 4 — Never auto, terminal-only:** key/secret provisioning; external third-party actions;
  secretary email (stays secretary-only); `.claude/**` self-mod (literal `ii`); raw DB writes (API
  only); budget/cost-cap breach (hard halt).

## 8. Compaction resilience (AC4)

Continuity across an auto-compaction is preserved WITHOUT relying on in-context memory:
- **per-task subagent spawning** keeps the Lead context lean (the heavy work lives in subagent
  contexts, discarded on return) → fewer compactions;
- the **activity rail + `decisions.md`** record completed-vs-pending durably;
- **the picker is the source of truth** — after a compaction (or a fresh session) the walker
  re-derives "where am I" from `next-autorun` (live task rows) + `resume_context`, NOT from
  conversation memory. A gate-resumed task's entire "where I was + what was answered" lives in
  `task.resume_context` (the §8/§12 self-sufficiency contract in `async-hitl-gates.md`).

Verifiable: a drain run crossing ≥1 in-session compaction resumes on the correct remaining task
without losing completed-vs-pending.

## 9. What it consumes (from Task C #2566)

- `GET /api/tasks/next-autorun` → `gate_resume_tasks` (TODO tasks, all gates answered, `halt_reason`
  NULL) — the walker RESUMES these from `resume_context`, does NOT start them fresh. Provably disjoint
  from `next_task` (which excludes any task with an open/answered gate).
- The `resume_context` self-sufficiency contract: `answered_gates[<gate_id>]` (the answer fold written
  by `resolve_gate`) ∪ the opener's halt-snapshot.

## 10. v0.9.0 deferral + open dials (AC5)

**Explicitly OUT of scope for v0.8.0, deferred to v0.9.0:** multi-project / cross-team binding. The
build must NOT foreclose it — satisfied by the §4 scope-as-list seam.

Open dials for the v0.9.0 pickup:
- multi-project scope ordering (round-robin vs a global milestone/priority merge across projects);
- whether the walker profile becomes a persisted entity (survives/shares across sessions) vs stays
  skill-local;
- cross-team scope (a walker spanning dev + another team's lifecycle);
- idle-wait vs stop on "list empty but gates outstanding" (v0.8.0 STOPS; v0.9.0 may idle-poll for gate
  answers).

## 11. Deferred backend hardening (from #2531)

- `ix_task_gates_answered` partial index (`task_id WHERE status='answered'`) for the picker's
  answered-gate EXISTS — added in this build (W-1).
- The gate-opener's ATOMIC resume-snapshot capture (fold the halt-snapshot into the gate-open body) —
  currently a PATCH-based contract with a two-write window; a v0.8.0+ hardening option, not required
  for the walker to function.

---
*Companion to `mode-a-autonomy-boundary.md` (policy) + `async-hitl-gates.md` (gate mechanism).
#2531 / milestone v0.8.0 (#50).*
