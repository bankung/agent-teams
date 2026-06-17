---
name: tn-milestone-create
description: >-
  Create a milestone on the active agent-teams project — project_id in the body (must match the
  X-Project-Id header), correct default status. Use to open a new milestone/sprint to group tasks.
argument-hint: "<title> [| description]"
allowed-tools:
  - Bash(curl:*)
  - Read
  - Write
metadata:
  version: 1.0.0
  category: kanban
  tags: [kanban, milestone, create, mutate]
---

# /tn-milestone-create — open a milestone the right way

`$ARGUMENTS` = the title, optionally `title | description`.

## Step 1 — resolve the active project id
Read `_runtime/lead_project_id.txt` → use as BOTH `X-Project-Id` header AND `project_id` in the body
(they MUST match — the server 400s on mismatch). If missing, run `/tn-bind` first.

## Step 2 — build the payload
Write to `_scratch/tn_ms_create.json`:
```json
{
  "project_id": <id>,
  "title": "<title>",
  "description": "<description or omit>",
  "milestone_status": "planned"
}
```
Optional: `sort_order` (float, for manual ordering), `start_date` / `target_date` (YYYY-MM-DD; start must be <= target).

## Step 3 — POST + verify
```
curl --silent -X POST -H "X-Project-Id: <id>" -H "Content-Type: application/json" \
  -d @_scratch/tn_ms_create.json http://localhost:8456/api/milestones \
  -o _scratch/tn_ms_create_resp.json -w "%{http_code}"
```
- **201** → report the new milestone id + title + status.
- **400** → body project_id != header, OR project doesn't exist. Show raw error, fix, retry.
- **422** → bad status / start_date>target_date. Show raw error.

## Footgun guards
1. `project_id` in the BODY and it must equal the `X-Project-Id` header.
2. New milestones default to `milestone_status: "planned"` — do NOT create one as `released`.
   Valid states: planned / active / released / cancelled. (Close one with `/tn-milestone-done`.)

## Usage
```
/tn-milestone-create Q3 hardening sprint | stabilize the tn-* family + cost forecast
```

## Related skills
- `tn-milestone-done` — release the milestone you created here once all its tasks are done
- `tn-milestones` — list existing milestones before creating a new one to avoid duplicates
- `tn-task-attach` — attach tasks to the new milestone id returned by this skill
