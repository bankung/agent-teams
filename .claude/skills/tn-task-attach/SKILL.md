---
name: tn-task-attach
description: >-
  Attach a task to a milestone (or detach it) on the active project — sets tasks.milestone_id, with
  a same-project sanity check first. Use to group a task under a milestone/sprint.
argument-hint: "<task id> <milestone id>   (use 'none' as milestone id to detach)"
allowed-tools:
  - Bash(curl:*)
  - Read
  - Write
---

# /tn-task-attach — link a task to a milestone

`$ARGUMENTS` = `<task id> <milestone id>`. A milestone id of `none`/`null` DETACHES the task.

## Step 1 — resolve the active project id
Read `_runtime/lead_project_id.txt` → `X-Project-Id`. If missing, run `/tn-bind`.

## Step 2 — sanity-check both belong to the bound project
```
curl --silent -H "X-Project-Id: <id>" http://localhost:8456/api/tasks/<task_id> -w " task:%{http_code}\n"
curl --silent -H "X-Project-Id: <id>" http://localhost:8456/api/milestones/<milestone_id> -w " ms:%{http_code}\n"
```
Both must be 200 (the API scopes by the header, so a 404 means it's not on this project). If either
is 404 → STOP and report. (The server also enforces same-project on the PATCH; this is a clearer
pre-check.) Skip the milestone GET when detaching.

## Step 3 — attach (or detach)
Write `_scratch/tn_attach.json` = `{"milestone_id": <milestone_id>}` (or `{"milestone_id": null}` to detach), then:
```
curl --silent -X PATCH -H "X-Project-Id: <id>" -H "Content-Type: application/json" \
  -d @_scratch/tn_attach.json http://localhost:8456/api/tasks/<task_id> \
  -o _scratch/tn_attach_resp.json -w "%{http_code}"
```
GET-verify the task's `milestone_id` is now set (or null). Report task id + its new milestone.

## Footgun guards
1. Task and milestone must be on the SAME project (the bound one) — checked in Step 2 + enforced server-side.
2. Detach with `milestone_id: null`, never by inventing a 0/placeholder id.
