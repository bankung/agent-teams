---
name: tn-task-create
description: >-
  Open a Kanban task on the active agent-teams project the correct way — project_id
  in the request BODY (not just the header), acceptance_criteria included at creation,
  and correct status semantics. Use whenever the operator or the Lead needs to create
  a task and wants the API footguns handled automatically.
argument-hint: "<natural-language description of the task to create>"
allowed-tools:
  - Bash(curl:*)
  - Read
  - Write
---

# /tn-task-create — paved-path Kanban task creation

You are creating ONE Kanban task on the agent-teams backend (FastAPI, `http://localhost:8456`).
The user's request is in `$ARGUMENTS`. Follow this procedure exactly — it encodes the
recurring API footguns so they cannot recur.

## Step 1 — resolve the active project id (NON-NEGOTIABLE)

Read the session-bound project id from `_runtime/lead_project_id.txt` (a single integer).
That value is BOTH the `X-Project-Id` header AND the `project_id` field in the body.

- If the file is missing/empty: STOP and ask the operator which project to bind to. Do not guess.

## Step 2 — turn `$ARGUMENTS` into a well-formed task

From the natural-language request, derive:

- **title** — concise (≤200 chars), imperative.
- **description** — fuller context (optional, ≤20000 chars).
- **task_type** — exactly one of `bug` | `feature` | `chore` | `docs` | `refactor`. Infer from intent.
- **task_kind** — `ai` (default; agent does the work) or `human` (operator does it).
- **priority** — integer; omit to default to normal unless the operator signals urgency.
- **acceptance_criteria** — 2–5 CONCRETE, independently-verifiable items. REQUIRED at creation.
  If the operator supplied AC, use theirs verbatim. Otherwise draft them and they will be visible
  in the printed result for the operator to amend. Each item: `{"text": "...", "status": "pending"}`.
  Use `"status": "na"` only for a criterion deliberately deferred (and put the follow-up reference in `notes`).

## Step 3 — build the payload file

Write the JSON to `_scratch/agent_task_create_payload.json`. Shape (only `project_id`,
`title`, `task_type`, `acceptance_criteria` are essential; the rest are optional):

```json
{
  "project_id": <id from step 1>,
  "title": "<title>",
  "description": "<description or omit>",
  "task_type": "feature",
  "task_kind": "ai",
  "acceptance_criteria": [
    {"text": "<verifiable criterion 1>", "status": "pending"},
    {"text": "<verifiable criterion 2>", "status": "pending"}
  ]
}
```

> `priority` is omitted — the API defaults to NORMAL (2). Pass `"priority": 3` for HIGH or `"priority": 4` for URGENT only when the operator signals urgency. (Scale: LOW=1 NORMAL=2 HIGH=3 URGENT=4.)

## Step 4 — POST it (header AND body both carry project_id)

```
curl --silent -X POST \
  -H "X-Project-Id: <id>" \
  -H "Content-Type: application/json" \
  -d @_scratch/agent_task_create_payload.json \
  http://localhost:8456/api/tasks \
  -o _scratch/agent_task_create_resp.json \
  -w "%{http_code}"
```

- HTTP **201** → continue to Step 5.
- HTTP **200** is NOT a valid success code for POST /api/tasks — treat it as an error (show body verbatim, STOP).
- HTTP **422** → almost always means `project_id` was missing from the BODY. Open the response file,
  show the raw error verbatim, FIX the payload, retry. Never report success on a non-2xx.
- Any other non-2xx → show the raw response body verbatim and STOP. Do not claim the task was created.

## Step 5 — verify (don't trust the POST)

GET the created id back and confirm it really persisted with AC populated:

```
curl --silent -H "X-Project-Id: <id>" http://localhost:8456/api/tasks/<new_id>
```

Confirm: `project_id` matches, `acceptance_criteria` is non-empty, `process_status` is 1 (TODO).

## Step 6 — report

Print: created task **id**, title, `task_type`, AC count, and the AC list. One line, no fluff.

---

## Footgun guards encoded here (the whole point of this command)

1. **`project_id` goes in the BODY**, not only the `X-Project-Id` header. Header-alone returns
   a silent **422** that can look like a phantom create. Always send both.
2. **`acceptance_criteria` is set AT CREATION** — never create-then-patch-AC-later. AC defines "done".
3. **Status semantics:** new tasks are `process_status` **1 (TODO)** — the default; do not set it.
   - **Never** set `process_status: 4` (BLOCKED) directly. BLOCKED is expressed ONLY via the
     `blocked_by: <task_id>` FK. If a task is on HOLD / waiting on something external, keep it
     **TODO (1)** and record the reason (e.g. in `description` or a later `status_change_reason`),
     NOT status 4.
4. **Enums:** `task_type` ∈ {bug, feature, chore, docs, refactor}; `task_kind` ∈ {ai, human};
   each AC `status` ∈ {pending, passed, failed, na}.
5. **DONE-flip discipline (for later, not this command):** flipping a task to DONE (5) requires
   verifying every AC first and PATCHing the AC array — see the planned `/tn-task-done`.

## Usage

```
/tn-task-create add a rate-limit guard to the email endpoints, with tests
```

The Lead can also invoke this via the Skill tool (skill name = `tn-task-create`).

## Namespace mechanism (how this command is named)

This is a Codex **skill**: a `SKILL.md` under `.codex/skills/tn-task-create/`. The directory
name (`tn-task-create`) becomes the invoked name (`/tn-task-create`). v1 deliberately uses a flat
skill (no plugin) to prove the paved-path pattern cheaply. Flat skills have no colon sub-namespace,
so the command family is grouped by the shared **`tn-`** prefix instead. A true colon namespace like
`/tn:task-create` would require shipping these inside a **plugin** (with `plugin.json` + marketplace) —
that is an optional future graduation, not v1. New skill files are picked up after a Codex
**restart** (creating the `.codex/skills/` dir for the first time requires a restart to begin watching it).

## Future slice (NOT built in v1 — tracked, do not implement here)

Once this pattern is proven, expand the `tn-` family (each is its own flat skill dir):
- `/tn-task-done <id>` — AC-verify-then-DONE (copy AC into the report, verify each, PATCH AC array, then flip to 5).
- `/tn-milestone` — create/attach milestones.
- `/tn-bind <project>` — project bootstrap (resolve by name → write `_runtime/lead_project_id.txt`).
- `/tn-next` — pick up the next auto-run task.
