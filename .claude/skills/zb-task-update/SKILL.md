---
name: zb-task-update
description: >-
  Update a Kanban task's status / priority / milestone / fields the guarded way — BLOCKED only via
  blocked_by, HOLD stays TODO+reason, status changes carry a reason, milestone attach/detach is
  same-project checked, and DONE is redirected to /zb-task-done.
argument-hint: "<task id> <changes: status=in_progress priority=high milestone=<id|none> ...>"
allowed-tools:
  - Bash(curl:*)
  - Read
  - Write
metadata:
  version: 1.1.0
  category: kanban
  tags: [kanban, task, update, milestone, mutate]
---

# /zb-task-update — guarded PATCH of a task

`$ARGUMENTS` = `<task id>` followed by the changes (e.g. `status=in_progress priority=high`).

## Step 1 — resolve the active project id
Resolve `X-Project-Id` by running `powershell -File bin/lead-project-id.ps1` — it prints THIS session's bound project id and exits non-zero if this session is unbound (→ STOP, run `/zb-bind`). Never read the global `lead_project_id.txt` (it may hold another concurrent session's project). [#2680]

## Step 2 — fetch current state
GET `/api/tasks/<id>` and show the current status/priority before changing anything.

## Step 3 — translate + GUARD the changes
Codes: status 1 TODO / 2 IN_PROGRESS / 3 REVIEW / 4 BLOCKED / 5 DONE / 6 CANCELLED ·
priority 1 LOW / 2 NORMAL / 3 HIGH / 4 URGENT.

Apply these guards BEFORE building the PATCH:
- **DONE (5):** do NOT flip here. Redirect to **/zb-task-done** (it verifies AC first). Refuse `status=done`.
- **BLOCKED (4):** never set `process_status=4` directly. A task is BLOCKED only by setting
  `blocked_by=<task_id>` (the FK). If the user means "on hold / waiting", keep it **TODO (1)** and
  record why in `status_change_reason` — do not use status 4 for a soft hold.
- **Any status change** requires a `status_change_reason` (ask for one if not supplied).
- **CANCELLED (6):** allowed (with a reason) — this is the soft-delete/cancel path.
- **milestone=<id>** (attach) → `milestone_id: <id>`; first `GET /api/milestones/<id>` with the same
  `X-Project-Id` must return 200 (404 = not on the bound project → STOP; the server also enforces it).
  **milestone=none** (detach) → `milestone_id: null`, never a 0/placeholder id.

## Step 4 — PATCH + verify
Write the body to `_scratch/tn_update_<sid>.json` (only the fields being changed + `status_change_reason`;
`<sid>` = session id — see /zb-bind "Scratch filenames"),
PATCH `/api/tasks/<id>`, then GET-verify the new values persisted. Report old → new.

## Footgun guards (the point)
1. DONE never flips here → /zb-task-done (AC-verify gate).
2. BLOCKED only via `blocked_by` FK; HOLD = TODO + reason, never raw status=4.
3. Status changes always carry a reason.
4. Milestone and task must be on the same (bound) project; detach is `null`.

## Usage
```
/zb-task-update 1842 status=in_progress priority=high
/zb-task-update 1842 milestone=57
/zb-task-update 1842 milestone=none
```

## Related skills
- `zb-task-done` — the only correct path to set status=DONE (AC-verify gate)
- `zb-task-create` — create a new task before there is anything to update
- `zb-milestones` — find the milestone id to attach to
