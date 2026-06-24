---
name: tn-milestone-done
description: >-
  Close a milestone (set milestone_status='released') the disciplined way — first check that its
  child tasks are actually finished; warn/halt if not. The milestone analogue of /tn-task-done.
argument-hint: "<milestone id>"
allowed-tools:
  - Bash(curl:*)
  - Read
metadata:
  version: 1.0.0
  category: kanban
  tags: [kanban, milestone, done, mutate, gate]
---

# /tn-milestone-done — release a milestone after verifying its tasks

Milestone id is in `$ARGUMENTS`. NOTE: milestones have NO "done" state — the terminal/complete
state is **`released`**. This skill sets `milestone_status: "released"`.

## Step 1 — resolve the active project id
Resolve `X-Project-Id` by running `powershell -File bin/lead-project-id.ps1` — it prints THIS session's bound project id and exits non-zero if this session is unbound (→ STOP, run `/tn-bind`). Never read the global `lead_project_id.txt` (it may hold another concurrent session's project). [#2680]

## Step 2 — fetch the milestone WITH its rollup
```
curl --silent -H "X-Project-Id: <id>" http://localhost:8456/api/milestones/<milestone_id> \
  -o _scratch/tn_ms_done.json -w "%{http_code}"
```
The detail response carries a `rollup`: `total` (active child tasks, incl. cancelled), `done`
(process_status=5 count), `by_process_status`, and `progress_pct` (done / non-cancelled total).

## Step 3 — the discipline gate
- If `progress_pct` is **100** (every non-cancelled child task is DONE) → proceed to Step 4.
- If `total` equals 0 OR all child tasks are cancelled (`done + cancelled == total`, with zero
  non-cancelled tasks) → treat as RELEASABLE. Print an operator note: "Milestone has no
  non-cancelled tasks (all cancelled or none attached) — releasing." Then proceed to Step 4.
- If **< 100** → there are unfinished child tasks. **WARN**: list the incomplete buckets from
  `by_process_status` and STOP. Ask the operator to confirm before releasing, or finish/attach the
  open tasks first. Do not silently release a milestone with open work.

## Step 4 — release
```
curl --silent -X PATCH -H "X-Project-Id: <id>" -H "Content-Type: application/json" \
  -d '{"milestone_status":"released"}' http://localhost:8456/api/milestones/<milestone_id> \
  -o _scratch/tn_ms_done_resp.json -w "%{http_code}"
```
GET-verify `milestone_status` is now `released`. Report: milestone id, title, final status, rollup.

## Footgun guards
1. "done" = **released** (not a literal 'done' state).
2. Don't release a milestone with unfinished child tasks without explicit operator confirmation
   (Step 3 gate — mirrors /tn-task-done's AC gate).

## Related skills
- `tn-milestone-create` — create a new milestone to replace or follow the one you're releasing
- `tn-milestones` — review all milestone progress percentages before deciding to release
