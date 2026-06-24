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
metadata:
  version: 1.0.0
  category: kanban
  tags: [kanban, task, done, mutate, ac-verify]
---

# /tn-task-done — verify acceptance criteria, then close

Task id is in `$ARGUMENTS`. This encodes the universal AC discipline so a task can never be
flipped DONE on unverified criteria.

## Step 1 — resolve the active project id

Resolve `X-Project-Id` by running `powershell -File bin/lead-project-id.ps1` (prints THIS session's bound project id; exits non-zero if unbound — never read the global `lead_project_id.txt`, it may hold another session's project). [#2680]
If it exits non-zero: STOP and run `/tn-bind <project>` first.

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

## Step 4 — build the FULL close payload (AC verdicts + the flip together)

Write ONE object to `_scratch/tn_done_payload.json` carrying both the verified AC array and
the DONE flip, so they land in a SINGLE PATCH (one round-trip — T2/#2541):
```json
{
  "acceptance_criteria": [ ...all items with status/verified_by/verified_at/notes... ],
  "process_status": 5,
  "status_change_reason": "<why it is done>"
}
```
**HARD GATE still applies:** assemble this payload ONLY after Step 3 verified every criterion
as passed/na. If any ended up pending/failed, do not build it — STOP (see Step 3).

## Step 5 — combined PATCH, then verify ONCE

```
curl --silent -X PATCH -H "X-Project-Id: <id>" -H "Content-Type: application/json" \
  -d @_scratch/tn_done_payload.json http://localhost:8456/api/tasks/<task_id> \
  -o _scratch/tn_done_resp.json -w "%{http_code}"
```
Then GET the task ONCE and confirm BOTH persisted: every AC status is passed/na AND
`process_status=5` with `completed_at` set. (One PATCH + one GET replaces the old
PATCH-AC → GET → PATCH-done → GET — proven live on #2542; verify-don't-trust unchanged.)

## Step 6 — report
Print: task id, title, EACH criterion + its verdict + evidence, and the final status.

---

## Footgun guards encoded here
1. **Never flip DONE with an unmet criterion** — pending/failed AC blocks the flip (Step 3 gate).
2. **AC verdicts + the DONE flip land in ONE PATCH** (Step 4/5) — the verified verdicts and
   `process_status=5` are sent together and applied atomically (one round-trip — T2/#2541);
   confirm BOTH persisted with the single GET afterward. (The verdicts must persist regardless
   of the flip, so the HARD GATE in Step 3 still runs first.)
3. **Verify independently — don't trust** the task's own notes or a prior agent's "it passed".
4. **`na`** is for a criterion deliberately deferred — record the follow-up reference in `notes`.

## Usage
```
/tn-task-done 1842
```

## Related skills
- `tn-task-update` — make non-DONE status changes (in_progress, review, cancelled) without the AC gate
- `tn-task-create` — create the task that will eventually be closed here
