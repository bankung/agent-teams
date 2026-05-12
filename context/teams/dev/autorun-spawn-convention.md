# Auto-run spawn brief convention — dev team

**Added:** Kanban #835 (2026-05-12). Companion to `autorun-loop.md`. Covers the required sections that EVERY headless auto-run spawn brief must include.

---

## Required HALT section (all headless spawn briefs)

Every spawn brief for a task picked up via the headless loop (`run_mode IN auto_pickup/auto_headless`) MUST include the following section verbatim (substituting `<task_id>`):

```
## Ambiguity gate (headless mode — required reading)

This task was picked up by the headless auto-run loop. You are working unattended.

If at ANY point you encounter ambiguity, a decision you cannot make alone, or missing information that blocks correct implementation:

1. STOP immediately. Do NOT guess, skip the ambiguous part, or leave a TODO.
2. Return a HALT report in your final message with this structure:

   HALT — ambiguity encountered

   halt_reason: <one sentence starting with "Question:" or "Decision:">
   question: <the specific question the user needs to answer>
   options: [<A>, <B>]   # omit if free-text answer expected
   partial_work_done:
     - <bullet: what you completed before halting>
   resume_context:
     <JSON — everything Lead needs to continue from this point>
       Example keys: files_modified, last_completed_step, data_already_computed, decisions_already_made

3. Do NOT create blocker tasks yourself — Lead handles that.
4. Do NOT mark the task done — Lead handles that too.

If there is no ambiguity: complete the task fully and return your normal result report.
```

---

## Required RESUME section (resume spawn briefs only)

When spawning a subagent to RESUME a previously halted task (task was in `resume_tasks` from `/next-autorun`), add this section AFTER the ambiguity gate:

```
## Resume context (this task was paused at a question gate)

The task was previously halted. Here is the checkpoint state:

### Question that was asked
<question text from question_payload.question>

### User's answer
Value: <answer_history[-1].value>
Answered by: <answer_history[-1].answered_by>
At: <answer_history[-1].answered_at>

### Partial work when halted
<resume_context dict, formatted as JSON block>

Continue from the "partial_work_done" checkpoint above. The work listed there is already done — do not redo it.
Apply the user's answer to the decision point that caused the halt, then complete the remainder of the task.
```

---

## Spawn brief template (headless pickup — full skeleton)

```
# <Role> spawn brief — <task title> (#<id>)

## Task
- ID: <id>
- Title: <title>
- Priority: <priority>
- run_mode: <auto_pickup | auto_headless>

## Context
- Project: <name> (id=<id>, team=dev)
- API base: http://localhost:8456
- X-Project-Id header: <id>
- Working repo path: <path>

## Spec
<full task description verbatim>

## Standards
<relevant standards excerpts — see dev.md lane mapping>

## Ambiguity gate (headless mode — required reading)
<paste the full HALT section from above>

[## Resume context — only for resume spawns]
<paste the full RESUME section if this is a resume>

## Output contract
Return a final report including:
- Files modified (path + line range)
- Tests run (command + exit code)
- TypeScript check (if FE) / pytest result (if BE) — verbatim exit code + line count
- HALT report (if ambiguity was hit) OR confirmation of full completion
```

---

## What "resume_context" should contain

The subagent filling in `resume_context` should include whatever is needed for a fresh spawn to pick up mid-task. Useful patterns:

```json
{
  "files_modified": ["api/src/routers/tasks.py", "api/src/schemas/task.py"],
  "last_completed_step": "Migration written and tested; PATCH route handler partially updated through line 142",
  "pending_steps": ["Add auto_unblock call in PATCH handler", "Write 3 remaining tests"],
  "data_already_computed": {},
  "decisions_already_made": {
    "chose_jsonb_over_text": "question_payload is JSONB not TEXT for structured options"
  }
}
```

Keys are free-form. Prefer specific steps over vague status. Lead stores this as-is into `tasks.resume_context`.

---

## Interaction with full-auto.md decision matrix

The HALT report is for work-blocking clarifications only. The full-auto.md decision matrix handles:
- Reviewer WARNs → fold or file follow-up (no HALT)
- Option A/B decisions on wire contracts → `{"halt_reason": "Option A/B decision needed: ..."}` (old pattern, no question task created)
- Scope creep → `{"halt_reason": "Scope creep proposed: ..."}` (old pattern, no question task created)

Use the question-gate (this convention) when: the subagent needs a factual answer or user preference to CONTINUE — not when it's raising a review concern or proposing a different direction.
