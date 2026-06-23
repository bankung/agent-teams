---
name: tn-report
description: >-
  Append one Lead activity checkpoint to a task's activity rail (the #980 tool_calls
  surface, source='lead'). Use whenever the Lead wants to record what just happened on
  a task — a subagent spawn, a test/tool result, an AC verified, a commit, a status flip,
  a blocker, a tool/skill gap, or a free-form note — so the run is auditable and the
  future improvement-auditor can mine the gap/blocked signals.
argument-hint: "<task_id> <kind> <summary>"
allowed-tools:
  - Bash(curl:*)
  - Read
  - Write
metadata:
  version: 1.0.0
  category: kanban
  tags: [kanban, activity-rail, report, mutate, audit]
---

# /tn-report — paved-path Lead activity checkpoint

You are appending ONE Lead checkpoint to a task's activity rail on the agent-teams
backend (FastAPI, `http://localhost:8456`). The rail is the existing #980 `tool_calls`
table — Lead rows ride alongside engine tool-call rows (`source='lead'`, #2320). The
user's request is in `$ARGUMENTS` as `<task_id> <kind> <summary>`. Follow this exactly.

## Step 1 — resolve the active project id (NON-NEGOTIABLE)

Read the session-bound project id from `_runtime/lead_project_id.txt` (a single integer).
That value is the `X-Project-Id` header for the POST + GET.

- If the file is missing/empty: STOP and ask the operator which project to bind to.

## Step 2 — parse `$ARGUMENTS`

- **task_id** — the first token (integer). The task that owns this checkpoint.
- **kind** — the second token. MUST be one of:
  `spawn | tool_result | ac_verified | commit | status_change | blocked | tool_gap | skill_gap | note`.
- **summary** — the rest of the line. Human-readable evidence, 1..2000 chars. The
  server strips non-printables and caps at 2000 (#2136), but keep it concise.

If `kind` is not in the list, STOP and ask — do not guess; the API will 422 anyway.

## Step 3 — WHEN TO REPORT (pick the right kind)

| Moment | kind | summary should say |
|---|---|---|
| Spawned a subagent | `spawn` | which agent + the one-line brief (set optional `tool_name` to the agent name) |
| A test / tool / curl produced a result | `tool_result` | what ran + pass/fail + the load-bearing number |
| Verified an acceptance criterion | `ac_verified` | which AC + how it was verified |
| Made a commit | `commit` | short sha + one-line subject |
| Flipped task status | `status_change` | old → new + why |
| Task is blocked / waiting | `blocked` | what it's blocked on (set `success:false`) |
| A needed tool was missing | `tool_gap` | the capability that was missing (improvement signal) |
| A needed skill was missing | `skill_gap` | the skill/playbook that was missing (improvement signal) |
| Anything else worth a checkpoint | `note` | the note |

> `blocked` / `tool_gap` / `skill_gap` are the IMPROVEMENT SIGNAL the future
> auditor mines — prefer them over a generic `note` when they fit.

## Step 4 — build the payload file (non-ASCII safe)

Write the JSON to `_scratch/tn_report_payload.json` as a **UTF-8 file**. Shape:

```json
{
  "source": "lead",
  "kind": "<kind>",
  "summary": "<summary>"
}
```

Optional fields: `"success": false` (default true; set false for `blocked`),
`"tool_name": "<label>"` (e.g. the spawned agent name on `kind:'spawn'`).

> **Non-ASCII note (platform convention):** if the summary contains Thai / arrows /
> emoji, you MUST write the JSON to a UTF-8 file and POST with `curl --data-binary @file`.
> NEVER PowerShell-inline `curl --data "...ไทย..."` — the console codepage mangles
> non-ASCII to literal '?' before it reaches the API (irrecoverable; incident #2124).

## Step 5 — POST it

```
curl --silent -X POST \
  -H "X-Project-Id: <id>" \
  -H "Content-Type: application/json" \
  --data-binary @_scratch/tn_report_payload.json \
  http://localhost:8456/api/tasks/<task_id>/tool-calls \
  -o _scratch/tn_report_resp.json \
  -w "%{http_code}"
```

- HTTP **201** → continue to Step 6.
- HTTP **422** → invalid `kind`, missing/empty `summary`, or `summary` > 2000.
  Open the response file, show the raw error verbatim, FIX, retry.
- HTTP **400** → X-Project-Id missing OR the task belongs to another project.
- HTTP **404** → unknown task_id. **410** → task is soft-deleted (rail is gone).
- Any other non-2xx → show the raw body verbatim and STOP; do not claim success.

## Step 6 — verify (don't trust the POST)

The POST returns **201 with the created row as the body** (`response_model=ToolCallRead`) —
already saved to `_scratch/tn_report_resp.json` in Step 5. Verify against THAT (no second
GET round-trip — T3/#2541): open the response file and confirm it carries a numeric `id`,
`source:"lead"`, the right `kind`, your `summary`, and that the engine-only fields
(`tier`, `input_json`, `duration_ms`, `permission_decision`) are `null` (lead rows never
fill them). The 201 body IS the persisted row, so a passing check here proves the write.

Fallback: if the 201 body is missing/empty/unparseable for any reason, GET the rail back
and confirm the newest row matches:

```
curl --silent -H "X-Project-Id: <id>" \
  http://localhost:8456/api/tasks/<task_id>/tool-calls
```

## Step 7 — report

Print one line: the created row **id**, task_id, kind, and the (possibly truncated)
summary. No fluff.

---

## Footgun guards encoded here

1. **`source:"lead"` is the discriminator** — the POST URL is shared with the engine
   tool-call path (#981); the body's `source` field is what routes you to the lead
   contract. Omit it and you'll be validated as an engine row (422 on missing tool_name).
2. **`kind` is a closed enum** — gated by Pydantic Literal (no DB CHECK). A typo 422s.
3. **`summary` 1..2000, sanitized server-side** — non-printables → '?', capped at 2000.
4. **One checkpoint per call** — the rail is append-only; no PATCH/DELETE.
5. **Non-ASCII → UTF-8 file + `--data-binary`** (#2124). Never inline curl with non-ASCII.

## Usage

```
/tn-report 2320 spawn dev-sr-backend: build the lead-activity dual-contract on #980 rail
/tn-report 2320 tool_result api targeted suite 41/41 green incl. lead 201 + GET roundtrip
/tn-report 2320 blocked waiting on operator go for the prepaid Gemini key
```

The Lead can also invoke this via the Skill tool (skill name = `tn-report`).

## Namespace mechanism

This is a Claude Code **skill**: a `SKILL.md` under `.claude/skills/tn-report/`. The
directory name becomes the invoked name (`/tn-report`). Part of the flat `tn-` skill
family (no plugin / colon namespace in v1). New skill files are picked up after a Claude
Code **restart**.

## Related skills
- `tn-tasks-next` — find which task to report on next (what is the current active work)
- `tn-task` — inspect a task's full detail and existing rail before appending a checkpoint
