---
name: tn-task-update
description: >-
  Update a Kanban task's status / priority / fields the guarded way — BLOCKED only via blocked_by,
  HOLD stays TODO+reason, status changes carry a reason, and DONE is redirected to /tn-task-done.
argument-hint: "<task id> <changes: status=in_progress priority=high ...>"
allowed-tools:
  - Bash(curl:*)
  - Read
  - Write
---

# /tn-task-update — guarded PATCH of a task

`$ARGUMENTS` = `<task id>` followed by the changes (e.g. `status=in_progress priority=high`).

## Step 1 — resolve the active project id
Read `_runtime/lead_project_id.txt` → `X-Project-Id`. If missing, run `/tn-bind`.

## Step 2 — fetch current state
GET `/api/tasks/<id>` and show the current status/priority before changing anything.

## Step 3 — translate + GUARD the changes
Codes: status 1 TODO / 2 IN_PROGRESS / 3 REVIEW / 4 BLOCKED / 5 DONE / 6 CANCELLED ·
priority 1 LOW / 2 NORMAL / 3 HIGH / 4 URGENT.

Apply these guards BEFORE building the PATCH:
- **DONE (5):** do NOT flip here. Redirect to **/tn-task-done** (it verifies AC first). Refuse `status=done`.
- **BLOCKED (4):** never set `process_status=4` directly. A task is BLOCKED only by setting
  `blocked_by=<task_id>` (the FK). If the user means "on hold / waiting", keep it **TODO (1)** and
  record why in `status_change_reason` — do not use status 4 for a soft hold.
- **Any status change** requires a `status_change_reason` (ask for one if not supplied).
- **CANCELLED (6):** allowed (with a reason) — this is the soft-delete/cancel path.

## Step 4 — PATCH + verify
Write the body to `_scratch/tn_update.json` (only the fields being changed + `status_change_reason`),
PATCH `/api/tasks/<id>`, then GET-verify the new values persisted. Report old → new.

## Footgun guards (the point)
1. DONE never flips here → /tn-task-done (AC-verify gate).
2. BLOCKED only via `blocked_by` FK; HOLD = TODO + reason, never raw status=4.
3. Status changes always carry a reason.

## Usage
```
/tn-task-update 1842 status=in_progress priority=high
```
