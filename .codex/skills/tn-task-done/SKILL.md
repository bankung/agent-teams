---
name: tn-task-done
description: >-
  Flip a Kanban task to DONE the disciplined way — verify EVERY acceptance criterion,
  PATCH the AC array with verdicts, then set process_status=5. Refuses to flip if any
  criterion is unmet. Use whenever a task looks finished and you want to close it correctly.
argument-hint: "<task id>"
allowed-tools:
  - Bash(curl:*)
  - Bash(date:*)
  - Read
  - Write
---

# /tn-task-done — verify acceptance criteria, then close

Task id is in `$ARGUMENTS`. This encodes the universal AC discipline so a task can never be
flipped DONE on unverified criteria.

## Step 1 — resolve the active project id

Read `_runtime/lead_project_id.txt` (single integer) → use as `X-Project-Id`.
If missing/empty: STOP and run `/tn-bind <project>` first.

## Step 2 — fetch the task

```
curl --silent -H "X-Project-Id: <id>" http://localhost:8456/api/tasks/<task_id> \
  -o _scratch/tn_done_task.json -w "%{http_code}"
```
Read its `acceptance_criteria`.

## Step 3 — verify EACH criterion (do not trust prior claims)

- If `acceptance_criteria` is null/empty → say so explicitly in the report; you MAY proceed
  to the flip, but note there were no criteria to verify.
- Otherwise, copy the FULL criteria list into your report. For EACH item, INDEPENDENTLY verify
  it — run the check, read the file, curl the endpoint; find the concrete evidence. Then set:
  `status` (passed / failed / na), `verified_by` (e.g. "lead"), `verified_at` (UTC now, e.g.
  `date -u +%Y-%m-%dT%H:%M:%SZ`), `notes` (the evidence).
- **HARD GATE:** if ANY criterion ends up `pending` or `failed` (can't be verified passed/na) →
  **STOP. Do NOT flip.** Report which are unmet; offer to file a follow-up task or halt for the
  operator's decision.

## Step 4 — PATCH the AC array BEFORE the flip

Write the verified array to `_scratch/tn_done_ac.json` and:
```
curl --silent -X PATCH -H "X-Project-Id: <id>" -H "Content-Type: application/json" \
  -d @_scratch/tn_done_ac.json http://localhost:8456/api/tasks/<task_id> \
  -o _scratch/tn_done_ac_resp.json -w "%{http_code}"
```
Then GET the task again and confirm the statuses persisted (all passed/na).

## Step 5 — flip to DONE

Only after Step 4 persisted:
```
curl --silent -X PATCH -H "X-Project-Id: <id>" -H "Content-Type: application/json" \
  -d '{"process_status":5,"status_change_reason":"<why it is done>"}' \
  http://localhost:8456/api/tasks/<task_id> -o _scratch/tn_done_flip_resp.json -w "%{http_code}"
```
GET-verify `process_status=5` and `completed_at` is set.

## Step 6 — report
Print: task id, title, EACH criterion + its verdict + evidence, and the final status.

---

## Footgun guards encoded here
1. **Never flip DONE with an unmet criterion** — pending/failed AC blocks the flip (Step 3 gate).
2. **AC array is PATCHed BEFORE the status flip** (some servers gate the DONE flip on AC state;
   and the verified verdicts must be persisted regardless).
3. **Verify independently — don't trust** the task's own notes or a prior agent's "it passed".
4. **`na`** is for a criterion deliberately deferred — record the follow-up reference in `notes`.

## Usage
```
/tn-task-done 1842
```
