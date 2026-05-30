# Whole-project code review — 2026-05-30

**As-of:** 2026-05-30 · **decay_class:** review-on-touch (findings go stale as code changes) · **Status:** DRAFT findings, not yet actioned.
**Method:** 4 read-only reviewers (api / web / langgraph / infra), minimization-focused. Companion: `code-minimization-plan.md` (the LOC-reduction plan). This file = correctness/quality/security FINDINGS (bugs + risks), separate from pure bloat.

## Overall verdict
The codebase is **not recklessly bloated** — service decomposition, data flow, and migrations are sound. The real issues are a handful of correctness edges, a latent prod-safety gate, one broken reference in the review pipeline, and concentrated bloat in a few hotspots (covered in the minimization plan).

## Findings by severity

### HIGH — fix before relying on Mode B / autonomy
- **Ungated demo branches in `langgraph/nodes.py` `general_node`.** The "AUDITOR retry demo" + "AUDITOR escalate demo" branches trigger on `brief.startswith("AUDITOR ... demo —")` with **no env gate** (only the HITL demo checks `HITL_DEMO_ENABLED`). A real task whose title starts with that magic string would silently get demo behavior in production. **Fix:** gate both with `HITL_DEMO_ENABLED`, or move all demo branches to a `demo_node` registered only when the env is set. (Relates to the #1652 Mode-B gate.)

### MED — correctness edges
- **`update_task` makes 3 sequential `session.commit()` calls** (`api/src/routers/tasks.py` ~1963/2003/2032: main mutation → `auto_unblock_dependents` → audit-flag pipeline). Not atomic: a crash between commit 1 and 2 leaves dependents blocked even though the blocking task is already DONE. Edge-case (crash window), not a normal-path bug.
- **`budget_gate.py` `_ALERT_SENT` is an unbounded process-global dict** (date-keyed, never pruned). In a long-lived worker container this is a slow memory leak (negligible/day, unbounded/months). Add a maxsize/TTL cleanup.
- **`_Snap` inline class in `update_task`** (`tasks.py:~1830`) binds `task.title` at class-definition time — works only because the ORM has it loaded; fragile reliance on load order. Fix by passing the 3 fields as explicit kwargs to `estimate_task_cost`.

### MED — broken reference in the review pipeline
- **`context/standards/general/reward-hacking-patterns.md` is MISSING** but 7 agent defs (dev-backend/-sr-backend/-frontend/-sr-frontend/-tester + dev-reviewer/-spec-reviewer) cite it as the source of the reward-hacking pattern catalogue (A–I). The patterns currently live only inline (duplicated). **Fix (humans-only zone → propose):** extract the catalogue from `dev-reviewer.md` into the referenced file; reduce the 5 inline copies to a pointer (~55 LOC saved + fixes the dead ref).

### LOW — quality / a11y / hygiene
- **`except Exception` fire-and-forget ×~15** in backend (push hooks, audit pipeline). Each individually justified; collectively a silent-failure surface that's hard to monitor. Consider a structured "swallowed error" counter/log.
- **Frontend a11y:** `role="dialog"`/`aria-modal` sit on the backdrop div, not the panel (`TaskDetail.tsx:241` + the 10 ad-hoc modals). Screen readers announce the whole viewport. Fixed structurally by the `ModalShell` extraction (see plan E1).
- **Stale-closure ESC handler** in 8 modals (same `eslint-disable exhaustive-deps`); `closeModal` captured stale. Also fixed by `ModalShell`.
- **Stale `# TODO(#955)` in `health_monitor.py`** — "wire push when web push lands"; web push已 landed. Remove + wire (or delete comment).
- **Lazy `import os` inside a hot function** (`tasks.py:~166 _fire_hitl_push`) — move to top-level.
- **`listAuditFlags`/`listProjectAuditTasks` (web/lib/api.ts)** silently `catch { return []; }` per project + double-fetch 500 rows client-side. Add a `console.warn`; dedupe the fetch.

## Notable bloat hotspots (detail in minimization plan)
- `langgraph/nodes.py` = 1,287 LOC god-file (auditor block alone ~486) → the prime "Hermes run_agent.py" analog.
- `api/src/routers/tasks.py` = 2,693 LOC; `update_task` = single 838-line function.
- `.codex/` = ~10,264 LOC near-verbatim mirror of `.claude/` with no sync mechanism (already drifting).
- 155 ephemeral `smoke-push-*`/`hitl-push-*` project dirs + 78 `.deleted/` committed.
