---
name: tn-task
description: >-
  Show ONE Kanban task — its fields and acceptance criteria, formatted — for the active
  agent-teams project. Read-only. Handy to review a task (and its AC) before /tn-task-done.
argument-hint: "<task id>"
allowed-tools:
  - Bash(curl:*)
  - Read
---

# /tn-task — show a single task, formatted

Task id is in `$ARGUMENTS`. Read-only (no mutations).

## Step 1 — resolve the active project id
Read `_runtime/lead_project_id.txt` (single integer) → `X-Project-Id`. If missing, run `/tn-bind` first.

## Step 2 — fetch
```
curl --silent -H "X-Project-Id: <id>" http://localhost:8456/api/tasks/<task_id> \
  -o _scratch/tn_task.json -w "%{http_code}"
```
- **404** → not found on this project (maybe the binding points elsewhere — check `/tn-bind`).

## Step 3 — print (decode the integer codes)
- **process_status**: 1 TODO · 2 IN_PROGRESS · 3 REVIEW · 4 BLOCKED · 5 DONE · 6 CANCELLED
- **priority**: 1 LOW · 2 NORMAL · 3 HIGH · 4 URGENT

Show: `#id` · title · status · priority · task_type/task_kind · milestone_id · blocked_by (if any) ·
then the **acceptance_criteria** list (each: text + status) · then a trimmed description.

Keep it compact and scannable.
