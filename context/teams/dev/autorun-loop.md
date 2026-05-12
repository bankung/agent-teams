# Headless auto-run loop — dev team

**Added:** Kanban #830 + #835 (2026-05-12). Extends MVP-3 pickup loop (`full-auto.md`) with question/decision blocking support.

This file covers the **question-gate loop**: how Lead uses `GET /api/tasks/next-autorun`, how subagents report ambiguity, how Lead creates a blocker question task, and how the loop resumes when the user resolves it via the Kanban drawer.

---

## When this applies

Any session running the MVP-3 pickup loop (full-auto or interactive with `LEAD_AUTOPICKUP=1`). The question-gate replaces the old halt-and-stop pattern for ambiguity: instead of deadlocking the session, Lead parks the ambiguity as a user-visible question task and continues with other work (or idles) until the user answers.

---

## Loop tick — using `/next-autorun`

Each pickup cycle, call:

```
curl --silent -H "X-Project-Id: <id>" http://localhost:8456/api/tasks/next-autorun
```

Response fields:

| Field | Type | Meaning |
|---|---|---|
| `next_task` | TaskRead \| null | Highest-priority ready task (`run_mode IN auto_pickup/auto_headless`, `halt_reason IS NULL`, no active blocker) |
| `resume_tasks` | TaskRead[] | Tasks halted with `halt_reason IS NOT NULL` whose blocker is now DONE |
| `pending_questions` | TaskRead[] | Open question/decision tasks (`interaction_kind IN question/decision`, not DONE) |
| `blocked_count` | int | Count of TODO/IN_PROGRESS tasks blocked by at least one non-DONE task |

### Decision tree (in order)

1. **`resume_tasks` non-empty** — resume first (question was just answered; unblock before picking fresh work).
   - For each: re-spawn the subagent with `resume_context` + the latest valid answer from `question_payload.answer_history` (see Resume spawn section below).
   - Mark the resume task `process_status=2` + `started_at=<now>`, clear `halt_reason=null` before spawning.
2. **`next_task` non-null** — standard pickup (MVP-3 flow in `full-auto.md`).
3. **Both null, `pending_questions` non-empty** — log `"Waiting for user: N question(s) open — queue paused"`. Enter idle (policy from `full-auto.md`).
4. **All null/empty** — queue empty or all tasks blocked by non-question blockers. Log and idle as per MVP-3.

---

## Subagent HALT report — ambiguity during work

When a subagent encounters ambiguity it cannot resolve on its own, it MUST stop immediately and return a structured HALT report in its final message to Lead. It must NOT guess, skip, or defer silently.

### HALT report format

```
HALT — ambiguity encountered

halt_reason: <one sentence, starts with "Question:" or "Decision:">
question: <the specific question the user needs to answer>
options: [<option A>, <option B>, ...]  # omit if free-text answer expected
partial_work_done:
  <bullet list of what was completed before the halt>
resume_context:
  <JSON-serialisable dict of state needed to continue — file paths, partial outputs, decisions already made>
```

**Rules for subagents:**
- `halt_reason` prefix — `"Question:"` for factual/clarifying questions; `"Decision:"` for trade-off choices where the user must pick.
- `options` — include when choices are discrete (pick one). Omit for open-ended questions.
- `resume_context` — MUST be JSON-serialisable (no Python objects, no file handles). Store file paths as strings, partial outputs as strings or lists. Lead serialises this into `tasks.resume_context`.
- Do NOT attempt to create the blocker task yourself — Lead handles that.

---

## Lead: creating the question blocker task

When Lead receives a HALT report from a subagent:

1. **PATCH the halted task** with:
   - `halt_reason = <subagent's halt_reason>`
   - `resume_context = <subagent's resume_context dict>`
   - `process_status` stays at 2 (in_progress) — halted ≠ done.

2. **Create a new question task** (POST /api/tasks):
   ```json
   {
     "project_id": <p>,
     "title": "Question: <brief summary>",
     "interaction_kind": "question",
     "question_payload": {
       "question": "<subagent's question>",
       "options": ["<A>", "<B>"] // null if free-text
     },
     "parent_task_id": <halted_task_id>,
     "blocked_by": null,
     "process_status": 2,
     "task_kind": "human",
     "run_mode": "manual",
     "priority": 3
   }
   ```
   The question task gets `process_status=2` (in_progress / waiting) — it appears in the IN PROGRESS lane with the ❓ badge.

3. **Block the halted task** on the new question task:
   `PATCH /api/tasks/<halted_id>` with `{"blocked_by": <question_task_id>}`.

4. **Continue the loop** — pick up the next ready task (step 2 of decision tree). The question task is now in the user's queue; the halted task is blocked.

---

## Auto-unblock trigger (backend — no Lead action needed)

When the user answers the question in the Kanban drawer and marks the question task DONE (`process_status=5`), the backend automatically:

- Clears `blocked_by` on any task that was blocked by this question task.
- Clears `halt_reason` if it starts with `"Question:"` or `"Decision:"` on those same tasks.

These tasks then appear in `resume_tasks` on the next `/next-autorun` tick.

---

## Resume spawn — re-spawning after question is answered

When a task appears in `resume_tasks`:

1. Fetch the task: `GET /api/tasks/<id>` — read `resume_context` and `question_payload`.
2. Extract the latest valid answer: `question_payload.answer_history[-1]` where `is_valid=true`.
3. PATCH the task: `{"halt_reason": null, "process_status": 2}` (start_at already set; no need to bump).
4. Spawn the subagent with the resume brief (see `autorun-spawn-convention.md` for full template). Key injections:
   - `## Resume context` block: `resume_context` dict verbatim (JSON block).
   - `## Question answer` block: `question` + `answer` + `answered_by` + `answered_at`.
   - Full original task description still applies — the subagent must re-read and continue.
5. Subagent continues from where it left off. If it encounters a second ambiguity, it emits another HALT report and the cycle repeats.

---

## Interaction_kind field reference

| Value | Meaning | Who creates |
|---|---|---|
| `work` | Agent-executable task (default) | Backlog authors / Lead |
| `question` | Factual or clarifying question for the user | Lead (from HALT report) |
| `decision` | Trade-off choice — user picks between discrete options | Lead (from HALT report) |

`question` and `decision` tasks always appear in the IN PROGRESS lane (no separate lane). Cards show ❓ (question) or ⚡ (decision) badge.

---

## Relation to MVP-3 and full-auto.md

- `full-auto.md` MVP-3 uses the old pickup query directly. This loop REPLACES that query with `/next-autorun` for sessions that support question-gating.
- The halt-and-stop patterns in `full-auto.md` (decisions 4+5: Option A/B, scope creep) are still valid for NON-question ambiguities. The question-gate is for work-blocking clarifications, not policy violations.
- `full-auto.md` "Out of scope — blocked_by integration" note is now resolved: `blocked_by` is integrated into `/next-autorun` for question blockers.

---

## Anti-patterns

- Subagent guesses instead of returning HALT report → corrupted output, no resume path.
- Lead creates question task but forgets to set `blocked_by` on the halted task → both tasks appear ready; subagent is re-spawned without the answer.
- Lead clears `halt_reason` before question task is DONE → task re-enters pickup queue prematurely.
- Lead checks `resume_tasks` but spawns WITHOUT passing `resume_context` → subagent starts from scratch, duplicates partial work.
