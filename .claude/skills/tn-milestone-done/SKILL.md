---
name: tn-milestone-done
description: >-
  Close a milestone (set milestone_status='released') the disciplined way — first check that its
  child tasks are actually finished; warn/halt if not. The milestone analogue of /tn-task-done.
argument-hint: "<milestone id>"
allowed-tools:
  - Bash(curl:*)
  - Read
---

# /tn-milestone-done — release a milestone after verifying its tasks

Milestone id is in `$ARGUMENTS`. NOTE: milestones have NO "done" state — the terminal/complete
state is **`released`**. This skill sets `milestone_status: "released"`.

## Step 1 — resolve the active project id
Read `_runtime/lead_project_id.txt` → `X-Project-Id`. If missing, run `/tn-bind`.

## Step 2 — fetch the milestone WITH its rollup
```
curl --silent -H "X-Project-Id: <id>" http://localhost:8456/api/milestones/<milestone_id> \
  -o _scratch/tn_ms_done.json -w "%{http_code}"
```
The detail response carries a `rollup`: `total` (active child tasks, incl. cancelled), `done`
(process_status=5 count), `by_process_status`, and `progress_pct` (done / non-cancelled total).

## Step 3 — the discipline gate
- If `progress_pct` is **100** (every non-cancelled child task is DONE) → proceed to Step 4.
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
