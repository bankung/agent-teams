# HITL resume — design doc (Kanban #950)

## 1. Goals + non-goals

**Goals:**
- Wire LangGraph's `interrupt()` API with Kanban's `interaction_kind ∈ {question, decision}` columns to pause agent execution mid-task, ask the user, and resume cleanly.
- Enable autorun tasks (run_mode='auto_headless') to hit a question/decision gate, block in the UI, accept a typed answer, and resume the agent with the answer as structured input.
- Persist graph execution state via AsyncPostgresSaver so the resumed agent picks up exactly where it left off.
- Preserve answer type safety: decision options are enums from `question_payload`, free-text questions receive string input.

**Non-goals (deferred):**
- Multi-step HITL loops (ask, parse, ask again) — Phase 2 beyond AC scope.
- Async notifications (email, Slack on pending question) — users monitor via FE.
- Checkpoint expiry / garbage collection — existing AsyncPostgresSaver TTL policy (if any) is acceptable.
- Voice / custom input modalities — text + enum radio buttons only.

---

## 2. Engine integration (langgraph/)

### 2.1 Interrupt emission point

**File:** `langgraph/nodes.py` — each specialist node

A specialist node (backend, frontend, devops, tester, reviewer, general) may encounter a decision point during agent execution and decide to pause for user input. Example:

```python
from langgraph.errors import NodeInterrupt

def backend_specialist_node(state: AgentState) -> dict:
    """Real specialist — may emit interrupt() for HITL."""
    ...
    # After reasoning or tool outputs, the agent decides:
    # "I need the user to decide between these options."
    if decision_needed:
        # Question payload mirrors the Kanban column shape:
        # { "text": "...", "options": [...], "answers": [...] }
        interrupt_payload = {
            "text": "Deploy to staging or production?",
            "options": [
                {"value": "staging", "label": "Staging (safer)"},
                {"value": "prod", "label": "Production (live)"},
            ]
        }
        raise NodeInterrupt(interrupt_payload)
```

The LangGraph runtime catches `NodeInterrupt`, checkpoints state, and returns control to the caller (worker) with the halt flag set. The caller PATCHes the task with `halt_reason='decision'` + the interrupt payload → stored in `question_payload`.

### 2.2 AsyncPostgresSaver integration (existing)

**Status:** Already integrated in `langgraph/graph.py:lifespan()` + `_build_graph()`

- Checkpointer: `AsyncPostgresSaver.from_conn_string(DATABASE_URI)` initialized at lifespan startup (graph.py:184-186).
- Schema: `langgraph` (option B per Kanban #851) — created via `CREATE SCHEMA IF NOT EXISTS` before saver setup.
- Thread keying: `thread_id=f"task-{task_id}"` already in use (graph.py:289) — one thread per task, checkpoints are task-scoped.
- Tables: AsyncPostgresSaver auto-creates `checkpoint` + `checkpoint_writes` + `blobs` under the langgraph schema.

**What's already there:**
- The saver persists graph state (messages, intermediate outputs) keyed by `(thread_id, checkpoint_id)`.
- Resuming a paused task uses the same thread_id, so `graph.ainvoke(..., config={"configurable": {"thread_id": f"task-{task_id}"}})` automatically loads the checkpoint.

**What we need:**
- Document which checkpoint tables store HITL state so admin queries can inspect pending pauses.
- Optional: add a partial index on checkpoint metadata to speed "pending HITL tasks" queries if needed later (AC7 timeout policy may require this).

### 2.3 Resume entry point

**Endpoint:** Extend existing `POST /invoke` or create `POST /invoke?resume=<task_id>&answer=<text>`

The worker polls `GET /api/tasks/next-autorun`, which returns `resume_tasks` (list of task_ids with `halt_reason='question'` or `halt_reason='decision'` waiting for an answer). When the FE PATCHes `interaction_answer`, the worker or a scheduled job should:

1. Read the task's `question_payload` to extract the expected input type (free-text or enum).
2. Validate the user's answer against the payload schema.
3. Call the graph resume: `graph.ainvoke(resume_state, config=...)` with the answer injected into the initial message or state.

**Approach (chosen):**
- Extend the worker poll loop (`langgraph/worker.py:_poll_once`) to check `resume_tasks` from the next-autorun response.
- For each resume task: validate the answer, invoke the graph with the answer in a new `HumanMessage`, and re-run the specialist node.
- The graph's checkpoint ensures the specialist node sees the prior context + the new answer message.

**Code skeleton (worker.py):**

```python
async def _poll_once(...) -> None:
    ...
    next_ar = await _fetch_next_autorun(client, cfg, headers)
    
    # 1. Handle new work (existing)
    if next_ar.next_task:
        await _invoke_task(client, graph_module, cfg, next_ar.next_task, headers)
    
    # 2. NEW: Handle resumed HITL tasks (AC2)
    if next_ar.resume_tasks:
        for task_id in next_ar.resume_tasks:
            await _resume_hitl_task(client, graph_module, cfg, task_id, headers)
    
    # 3. Log pending questions (existing / enhanced)
    if next_ar.pending_questions:
        logger.info("pending HITL: %d question(s) awaiting user input", len(next_ar.pending_questions))
```

**Resume idempotency:**
- If the user PATCHes the answer twice (e.g., double-click submit), the second invoke will re-run the graph from the same checkpoint with the same answer message appended again.
- LangGraph's message reducer (add_messages) will deduplicate or sequence them depending on the timestamp. Document that double-submission is safe but produces a duplicate message in the checkpoint log.

### 2.4 Checkpoint state contract on resume

When resuming, the graph must receive the user's answer in a format the specialist node recognizes:

```python
# On resume, construct a HumanMessage with the answer:
answer_from_db = task.question_payload["answers"][-1]["value"]  # Last valid answer
resume_state = {
    "messages": [...prior messages..., HumanMessage(content=f"User answered: {answer_from_db}")],
    "task_id": task_id,
    ...
}
graph.ainvoke(resume_state, config={"configurable": {"thread_id": f"task-{task_id}"}})
```

The specialist node's logic should include a branch: *if the last message is a HumanMessage containing the user's answer, process it instead of asking again.*

### 2.5 Failure modes

| Scenario | Handling |
|----------|----------|
| **Checkpoint missing** | Graph fails on ainvoke with "thread not found" or similar. Worker catches exception, PATCHes task with `halt_reason='langgraph_error: checkpoint missing'`, BLOCKED. |
| **Engine crash mid-resume** | Worker exception handler catches, retries on next poll. Task stays BLOCKED until manual intervention. |
| **Answer doesn't match schema** | Answer validation in worker BEFORE invoke. If invalid, PATCH task with `halt_reason='invalid_answer: expected enum, got X'`. |
| **Specialist node doesn't handle answer** | Specialist node ignores HumanMessage + emits another interrupt immediately. Creates a tight loop. Timeout policy (AC6) caps iterations. |

---

## 3. Kanban data model

### 3.1 Existing columns (already in schema via migration 0019)

- `interaction_kind` VARCHAR(16) NOT NULL DEFAULT 'work' — 'work' | 'question' | 'decision'
- `question_payload` JSONB NULL — { "text": "...", "options": [...], "answers": [...] }
- `resume_context` JSONB NULL — free-form state (not used for HITL, reserved for Lead re-spawn logic)

### 3.2 New column: `interaction_options` (potential)

**Status:** OPTIONAL for AC1 design. `question_payload.options` is sufficient; no new DB column needed.

If future work wants to query "tasks with decision option=X" efficiently, add:
- `interaction_options` JSONB NOT NULL DEFAULT '[]' — cached enum list for indexing.
- Populated on PATCH: if `interaction_kind='decision'` and `question_payload.options` is set, copy to this column.

**For AC1:** Omit this; normalize via question_payload only.

### 3.3 Migration strategy (none needed for AC1)

All required columns already exist post-migration 0019. The design reuses:
- `interaction_kind` to discriminate question/decision tasks.
- `question_payload` as the canonical struct for prompt + options + answer history.
- `process_status` + `halt_reason` for workflow state (BLOCKED with halt_reason='question' | 'decision').

No new migration required.

### 3.4 Answer append logic (existing via Kanban #832)

The router already has `append_answer()` service in `src/services/task_interaction.py` (used in routers/tasks.py:901-903). It appends a new answer object to `question_payload.answers[]` and returns the updated payload.

```python
# PATCH /api/tasks/{id}
# { "new_answer": "prod", "interaction_kind": "decision" }
# 
# Router applies: updates["question_payload"] = append_answer(...)
```

This is the FE → DB path. Reuse it.

---

## 4. FE surface (web/)

### 4.1 TaskCard — HITL indicator

**File:** `web/components/TaskCard.tsx`

Add a "?" or decision icon badge when `interaction_kind ∈ {question, decision}` + `halt_reason ∈ {question, decision}`:

```jsx
// In TaskCard component, line ~99
{task.interaction_kind === 'question' && task.halt_reason === 'question' && (
  <span className="text-amber-600 dark:text-amber-400" title="Awaiting answer">
    <Icon name="help-circle" size={16} />
  </span>
)}
{task.interaction_kind === 'decision' && task.halt_reason === 'decision' && (
  <span className="text-blue-600 dark:text-blue-400" title="Awaiting decision">
    <Icon name="git-branch" size={16} />  {/* or similar */}
  </span>
)}
```

### 4.2 TaskDetail — prompt + answer input

**File:** `web/components/TaskDetail.tsx`

On the detail modal, after the task description, render an interactive section when `interaction_kind ∈ {question, decision}` AND `halt_reason ∈ {question, decision}` AND `process_status=4 (BLOCKED)`:

```jsx
{task.interaction_kind === 'question' && task.halt_reason === 'question' && (
  <PromptSection
    prompt={task.question_payload?.text}
    answers={task.question_payload?.answers}
    onSubmit={(newAnswer) => submitAnswer(task.id, newAnswer)}
  />
)}

{task.interaction_kind === 'decision' && task.halt_reason === 'decision' && (
  <DecisionSection
    prompt={task.question_payload?.text}
    options={task.question_payload?.options}  // [{ value, label }, ...]
    answers={task.question_payload?.answers}
    onSubmit={(newAnswer) => submitAnswer(task.id, newAnswer)}
  />
)}
```

### 4.3 Answer submission handler

```jsx
async function submitAnswer(taskId: number, answerValue: string) {
  const res = await fetch(`/api/tasks/${taskId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      "X-Project-Id": projectId.toString(),
    },
    body: JSON.stringify({
      new_answer: answerValue,
      new_answer_by: "user", // FE always marks answers as user-supplied
    }),
  });
  if (res.ok) {
    // Refresh task detail (SSE or poll)
    refreshTask(taskId);
  } else {
    alert(`Failed: ${res.statusText}`);
  }
}
```

### 4.4 Styling

- **Question prompt:** amber/yellow chip — "pending user input"
- **Decision options:** blue chip — "user decision required"
- **Answer history:** gray/muted text — show prior answers (if any) for context

---

## 5. Resume contract

### 5.1 Type safety for answers

The worker validates answers against `question_payload` schema BEFORE invoking the graph:

```python
def _validate_answer(question_payload: dict, answer_value: Any) -> str:
    """Validate user answer matches the expected type."""
    if not question_payload:
        raise ValueError("No question_payload to validate against")
    
    if "options" in question_payload and question_payload["options"]:
        # Decision: answer must be one of the enum values
        valid_values = {opt["value"] for opt in question_payload["options"]}
        if answer_value not in valid_values:
            raise ValueError(f"Answer '{answer_value}' not in options: {valid_values}")
    # Else: free-text question — any string is acceptable
    
    return str(answer_value)  # Normalize to string
```

### 5.2 Answer in the graph

The specialist node receives the answer as a HumanMessage in the conversation:

```python
# Worker injects on resume:
resume_initial_state = {
    "messages": [
        ...prior messages (loaded from checkpoint)...,
        HumanMessage(content=f"User's answer: {validated_answer}")
    ],
    "task_id": task_id,
    ...
}
result = await graph.ainvoke(resume_initial_state, config={"configurable": {"thread_id": f"task-{task_id}"}})
```

The specialist node (backend_specialist_node, etc.) can then:
- Check if the last message is from the user (HumanMessage with the answer).
- Parse it, pass to the LLM as context.
- Continue reasoning with the user's input.

### 5.3 No string concatenation rule (AC #4)

- ✓ Answer is received as a structured value (string, enum).
- ✓ Injected into message content cleanly.
- ✗ Never concatenate with prompt template strings (e.g., "The user said: " + answer).

The specialist node code should treat the answer as an independent data point, not glued to the prompt.

---

## 6. Timeout policy (AC #6)

### 6.1 Per-project configuration

**New column:** `projects.hitl_timeout_hours` INTEGER NULL

- NULL → pause indefinitely (default, safe default).
- Non-null integer N → task halted for HITL > N hours triggers `halt_reason='hitl_timeout'`.

**Migration:** 
```python
op.add_column("projects", sa.Column("hitl_timeout_hours", sa.Integer, nullable=True))
```

### 6.2 Enforcement: on-demand during next-autorun

When the worker polls `GET /api/tasks/next-autorun`, the API computes:

```python
# In routers/tasks.py::get_next_autorun()
now = datetime.now(timezone.utc)
timeout_hours = session_project.hitl_timeout_hours

if timeout_hours is not None:
    for task in pending_hitl_tasks:  # tasks with halt_reason in {question, decision}
        elapsed_hours = (now - task.updated_at).total_seconds() / 3600
        if elapsed_hours > timeout_hours:
            # Timeout → halt the task
            await db.execute(
                update(Task)
                .where(Task.id == task.id)
                .values(halt_reason='hitl_timeout')
            )
```

**Alternative (cron-based):** A scheduled job runs every N minutes and updates timed-out tasks. On-demand is cheaper (no background job) and simpler (single place to compute timeout). **Choose on-demand.**

### 6.3 Failure recovery

When a HITL-timeout task is halted, it stays in BLOCKED state with `halt_reason='hitl_timeout'`. The user can:
- Manually answer via the FE (clears halt_reason, resumes).
- Or manually cancel the task (process_status→CANCELLED).

---

## 7. Audit + history

### 7.1 Answer capture

Answers are already appended to `question_payload.answers[]` (Kanban #832). The shape:

```json
{
  "text": "Question or prompt",
  "options": [
    {"value": "a", "label": "Option A"},
    {"value": "b", "label": "Option B"}
  ],
  "answers": [
    {
      "value": "a",
      "answered_by": "user",
      "answered_at": "2026-05-16T10:30:00Z",
      "valid": true
    },
    {
      "value": "b",
      "answered_by": "user",
      "answered_at": "2026-05-16T10:35:00Z",
      "valid": true,
      "invalidated_reason": "user changed their mind"
    }
  ]
}
```

The audit trigger (migration 0015 + refined #912) already snapshots `question_payload` in `tasks_history.snapshot`, so the full answer chain is preserved per update.

### 7.2 Status trace

- Task created → `process_status=1 (TODO)`, `interaction_kind='work'`
- Agent runs → `process_status=2 (IN_PROGRESS)`
- Agent hits interrupt → worker PATCHes → `process_status=4 (BLOCKED)`, `halt_reason='question'`, `question_payload={..., answers:[]}`
- User answers → FE PATCHes → `new_answer=<value>` appended, task stays `BLOCKED` pending resume
- Worker resumes → graph runs with answer injected
- Graph completes → worker PATCHes → `process_status=5 (DONE)`, `completed_at=now`, `status_change_reason=final_result[:400]`

Each PATCH is snapshotted in tasks_history.snapshot, so the full HITL flow is traceable.

---

## 8. Sub-task decomposition (4-6 sub-tasks)

### Sub-task 1: Engine — interrupt + checkpoint + resume (Kanban #950.1)

**Scope:**
- Modify specialist nodes to emit `NodeInterrupt` when a decision point is reached (backend_specialist_node as the canonical example; other specialists can follow the same pattern or remain stubs).
- Document how the worker polls resume_tasks and invokes graph.ainvoke for resumed tasks.
- Verify AsyncPostgresSaver checkpoints are persisted correctly across interrupt/resume.

**Acceptance criteria:**
- Backend specialist node can emit `NodeInterrupt(payload)` for a mock question.
- Worker detects resume_tasks from next-autorun.
- Graph resumes and loads prior checkpoint.
- Answer is injected as HumanMessage without string concatenation.
- Unit test: mock a backend task that emits interrupt, verify checkpoint is saved, resume with answer, verify final result includes the answer.

**Effort:** ~3 AC, 1 subagent (dev-backend)

---

### Sub-task 2: API — resume hook + answer validation (Kanban #950.2)

**Scope:**
- Add `_validate_answer()` function in a new service module or in task_interaction.py.
- Extend worker._poll_once to consume resume_tasks and call _resume_hitl_task().
- Implement _resume_hitl_task(): fetch task, validate answer, invoke graph, PATCH result.
- Test: POST /invoke with a task that has question_payload, answer value, verify graph runs.

**Acceptance criteria:**
- Answer validation rejects enum answers not in options.
- Answer validation accepts free-text.
- Worker loops over resume_tasks, resumes each via graph.ainvoke.
- Task state transitions correctly: BLOCKED + answer → re-run → DONE or re-interrupt.
- Error handling: invalid answer → halt_reason='invalid_answer_*', BLOCKED (not resumed).
- Smoke test: autorun task with HITL → answer via API → task completes.

**Effort:** ~2 AC, 1 subagent (dev-backend)

---

### Sub-task 3: FE — prompt + answer UI (Kanban #950.3)

**Scope:**
- TaskCard: add HITL indicator badge (question icon for 'question', decision icon for 'decision').
- TaskDetail modal: render PromptSection (free-text input) or DecisionSection (radio buttons).
- Submit handler: PATCH new_answer, refresh detail on success.
- Styling: amber for question, blue for decision.

**Acceptance criteria:**
- Task with halt_reason='question' and interaction_kind='question' shows a ? badge on card.
- TaskDetail modal renders the prompt text + text input.
- Radio buttons appear for decision task with options from question_payload.
- Submit button calls PATCH /api/tasks/{id} with new_answer.
- On success, detail refreshes and shows the pending status update.
- No answer can be submitted if question_payload is missing (error toast).

**Effort:** ~2 AC, 1 subagent (dev-frontend)

---

### Sub-task 4: Timeout policy + per-project config (Kanban #950.4)

**Scope:**
- Add `hitl_timeout_hours` column to projects table (migration).
- Update TaskRead schema to expose the project's timeout.
- Implement on-demand timeout check in GET /api/tasks/next-autorun.
- Document timeout behavior in README.

**Acceptance criteria:**
- Project can set hitl_timeout_hours=NULL (indefinite) or =N hours.
- GET /api/tasks/next-autorun checks elapsed time for pending HITL tasks.
- Tasks exceeding timeout are auto-halted with halt_reason='hitl_timeout'.
- Task stays BLOCKED; user can still answer or cancel manually.
- Unit test: create HITL task, set timeout=1 hour, simulate elapsed time, verify halt.

**Effort:** ~1 AC, 1 subagent (dev-backend)

---

### Sub-task 5: Smoke test — end-to-end autorun + HITL (Kanban #950.5)

**Scope:**
- Create a Kanban task with run_mode='auto_headless', task_kind='ai'.
- Autorun picks it up, agent runs, hits an interrupt (mocked decision node).
- Worker detects halt, PATCHes task to BLOCKED + question_payload.
- FE displays prompt + options.
- User submits answer via API.
- Worker resumes agent (next poll cycle).
- Agent completes, worker PATCHes task to DONE.
- Trace the entire flow in task_history.

**Acceptance criteria:**
- Autorun starts (process_status→2).
- Agent emits interrupt (halt_reason='question', process_status→4).
- FE can fetch and render the prompt.
- Answer submission succeeds (PATCH 200).
- Worker resumes and completes (process_status→5).
- All state transitions are logged in task_history.

**Effort:** ~2 AC, 1 subagent (dev-tester, possibly paired with dev-backend for agent mocking)

---

### Sub-task 6: Optional — dashboard indicator for pending HITL (future phase 2)

**Out of scope for AC1** but worth noting:
- Add a "pending HITL" card on the dashboard showing tasks awaiting user input.
- Quick-action button to jump to TaskDetail for answering.

**Defer to phase 2 or a follow-up task.**

---

## 9. Open questions for Lead/user

1. **Graph resume behavior — should the specialist node automatically pick up the answer, or does the worker inject it as a message and let the node decide?**
   - Option A: Worker constructs `HumanMessage(content=f"User answered: {answer}")` and injects into messages. Specialist node logic can check `messages[-1].content` and parse it.
   - Option B: Worker passes answer in a separate field of the initial state (e.g., `state["user_answer"]`), specialist node reads it directly without message parsing.
   - **Recommendation:** Option A (message-based) is more idiomatic to LangGraph and plays well with multi-turn LLM reasoning. Choice?

2. **Timeout policy — on-demand (check in GET /next-autorun) or background cron job?**
   - Option A: On-demand (compute timeout on every poll). Zero infrastructure, but each poll does extra DB work.
   - Option B: Background cron / APScheduler job runs every 5 minutes, updates timed-out tasks. Cleaner separation, but adds a background service.
   - **Recommendation:** Option A (on-demand) is simpler for MVP. Choose?

3. **Decision enum validation — strict (value must be in options) or lenient (any string accepted for decision)?**
   - Option A: Strict — reject answers not in options list. Safer but requires schema sync.
   - Option B: Lenient — accept any string for decision, specialist node decides if valid. More flexible but risks silent user errors.
   - **Recommendation:** Option A (strict). Choose?

4. **Checkpoint cleanup — should old checkpoints for completed tasks be garbage-collected?**
   - AsyncPostgresSaver has no built-in TTL. For long-running projects, checkpoint tables may grow large.
   - Option A: Manual cleanup via admin API (later task).
   - Option B: Document for now; defer cleanup to phase 2 when checkpoint growth is measured.
   - **Recommendation:** Option B (defer). Choose?

5. **Interrupt payload shape — standardize on question_payload JSONB, or introduce a separate interrupt_payload column?**
   - Current design reuses question_payload for both the prompt (from specialist) and answer history (from FE).
   - Option A: Single column (question_payload) — simpler, all HITL state in one place.
   - Option B: Split interrupt_payload (read-only from specialist) + answer_history (writable by FE) — cleaner separation, more columns.
   - **Recommendation:** Option A (reuse question_payload). Choose?

6. **Answer history — should we keep invalidated answers for audit, or delete them?**
   - Current pattern (Kanban #832) marks invalid answers with `valid: false` rather than deleting.
   - Option A: Keep all answers, mark validity flag. Full audit trail.
   - Option B: Delete invalid answers; only keep the current valid answer. Simpler, less history.
   - **Recommendation:** Option A (keep all). Aligns with existing #832 pattern. Confirm?

---

## 10. Dependency order + reference links

**External references:**
- LangGraph `interrupt()` docs: https://langchain-ai.github.io/langgraph/how-tos/add-interrupt/
- AsyncPostgresSaver: langgraph-checkpoint-postgres 3.1.0 (installed; see langgraph/pyproject.toml:16)
- Kanban #830 (interaction_kind + question_payload): migration 0019_tasks_interaction_kind.py
- Kanban #832 (answer append logic): src/services/task_interaction.py, routers/tasks.py:883-903
- Kanban #852 (worker poll loop): langgraph/worker.py
- Kanban #850 (graph setup): langgraph/graph.py

**Sub-task order:**
1. **Sub-task 1** (engine interrupt) → must land first; unblocks worker resume logic.
2. **Sub-task 2** (API resume) → depends on 1; implements worker loop.
3. **Sub-task 3** (FE UI) → can start in parallel with 2; depends on answer PATCH in 2.
4. **Sub-task 4** (timeout config) → can start in parallel; no hard dependency.
5. **Sub-task 5** (smoke test) → depends on 1, 2, 3; runs after all are merged.

**No hard database migration needed for AC1.** Column reuse from migration 0019 is sufficient.

---

## Summary

This design wires LangGraph's interrupt/checkpoint mechanism with Kanban's interaction columns to enable pausable AI agent execution. The engine emits `NodeInterrupt` when a decision is needed, the worker checkpoints and blocks the task, the FE prompts the user, and on answer submission the worker resumes the graph with the answer injected as a message. Timeout policy is enforced on-demand during poll time. Type safety is maintained via answer validation before graph resume. No new data model columns are needed; the design reuses existing `question_payload` and related fields from Kanban #830.
