---
name: tn-task-create
description: >-
  Open a Kanban task on the active agent-teams project the correct way — project_id
  in the request BODY (not just the header), acceptance_criteria included at creation,
  a cheap 5-point self-review before the POST, and correct status semantics. Use whenever
  the operator or the Lead needs to create a task and wants the API footguns handled
  automatically.
argument-hint: "<natural-language description of the task to create> [review=deep]"
allowed-tools:
  - Bash(curl:*)
  - Read
  - Write
  - Task
metadata:
  version: 1.1.0
  category: kanban
  tags: [kanban, task, create, mutate, self-review]
---

# /tn-task-create — paved-path Kanban task creation

You are creating ONE Kanban task on the agent-teams backend (FastAPI, `http://localhost:8456`).
The user's request is in `$ARGUMENTS`. Follow this procedure exactly — it encodes the
recurring API footguns so they cannot recur.

## Step 1 — resolve the active project id (NON-NEGOTIABLE)

Resolve the session-bound project id by running `powershell -File bin/lead-project-id.ps1` — it prints THIS session's id (`_runtime/lead_project_id_<sid>.txt`) and exits non-zero if this session is unbound; on a non-zero exit STOP and run `/tn-bind`, never read the global `lead_project_id.txt` (it may hold another concurrent session's project). [#2680]
That value is BOTH the `X-Project-Id` header AND the `project_id` field in the body.

- If the file is missing/empty: STOP and ask the operator which project to bind to. Do not guess.

## Step 2 — turn `$ARGUMENTS` into a well-formed task

From the natural-language request, derive:

- **title** — concise (≤200 chars), imperative.
- **description** — fuller context (optional, ≤20000 chars).
- **task_type** — exactly one of `bug` | `feature` | `chore` | `docs` | `refactor`. Infer from intent.
- **task_kind** — `ai` (default; agent does the work) or `human` (operator does it).
- **priority** — integer; omit to default to normal unless the operator signals urgency.
- **acceptance_criteria** — 2–5 CONCRETE, independently-verifiable items. REQUIRED at creation.
  If the operator supplied AC, use theirs verbatim. Otherwise draft them and they will be visible
  in the printed result for the operator to amend. Each item: `{"text": "...", "status": "pending"}`.
  Use `"status": "na"` only for a criterion deliberately deferred (and put the follow-up reference in `notes`).

## Step 2.5 — self-review the drafted task BEFORE the POST (Tier A default; Tier B opt-in)

A token-cheap quality gate so newly-opened tasks stop shipping small scope/completeness errors.
Tier A is the DEFAULT and costs ~nothing (a self-read, no spawn); only Tier B costs real tokens.

### Tier A — 5-point self-review checklist (DEFAULT, no spawn, run every time)

Read your own draft against these 5 points and fix any miss before Step 3:

1. **SCOPE** — does every AC belong to THIS task? Drop any AC that is really a different task's
   concern (catches scope bleed-in — see the worked example below).
2. **COMPLETENESS / FOOTGUN** — is any failure-mode or operational gotcha left unstated? (e.g. a
   run/permission caveat, a migration-ordering trap, a humans-only-zone boundary, a non-ASCII write path.)
3. **VERIFIABLE** — is each AC concretely checkable (a command, a file, a row, an observable value)?
   Rewrite a vague AC ("works well") into something a verifier can pass/fail.
4. **REDUNDANT / CONTRADICTION** — does any AC restate an already-true fact, duplicate another AC,
   or conflict with one? Remove or merge.
5. **GROUNDED** — are the load-bearing claims (paths, columns, endpoints, library behavior) verified
   against the real code/config, not assumed? Glob/grep/curl the critical ones first.

This is a Lead-applied soft discipline (NOT a hard hook) — consistent with the project's
"self-review over hard gates" posture. Apply it, fix the draft, then continue.

### Tier B — `review=deep` (OPTIONAL, token-costly, opt-in)

If `$ARGUMENTS` contains `review=deep`, route the drafted spec through the **dev-spec-reviewer**
agent (read-only; findings written to `_scratch/`) BEFORE the POST. Fold its findings into the
draft, then continue. It costs a real spawn, so it is opt-in — not the default.

**Escalation trigger — when to reach for `review=deep` instead of checklist-only:**
new-surface / architectural / security-sensitive / many-AC / >50-LOC-expected tasks → `review=deep`.
Everything routine → Tier A checklist only.

### Boundary vs `/tn-spec` (so the two never overlap)

`/tn-spec` = 2-round **adversarial pre-idea hardening** of a *fuzzy* idea, before you even know the
AC. This Step 2.5 = a **light review of an already-drafted task** — a Tier-A self-read by default,
a Tier-B single-pass dev-spec-reviewer audit on demand. Use tn-spec upstream when the idea itself is
unclear; use this when the task is drafted and you just want it correct before the POST.

### Worked example — the checklist catching a real miss (#2488)

When #2488 (pluggable secrets backend) was first drafted, an AC about a *security-policy* concern
(`CREDENTIALS_MASTER_KEY` is already fail-closed) had bled in — it belonged to a different security
task, not the secrets-backend task. **Point 1 (SCOPE)** catches exactly this: the AC did not belong
to THIS task, so it was trimmed. A missing infisical-run operational caveat was also caught by
**Point 2 (FOOTGUN)**. Both were originally found only on a manual re-review — the checklist makes
that catch the default, with no spawn.

## Step 3 — build the payload file

Write the JSON to `_scratch/agent_task_create_payload.json`. Shape (only `project_id`,
`title`, `task_type`, `acceptance_criteria` are essential; the rest are optional):

```json
{
  "project_id": <id from step 1>,
  "title": "<title>",
  "description": "<description or omit>",
  "task_type": "feature",
  "task_kind": "ai",
  "acceptance_criteria": [
    {"text": "<verifiable criterion 1>", "status": "pending"},
    {"text": "<verifiable criterion 2>", "status": "pending"}
  ]
}
```

> `priority` is omitted — the API defaults to NORMAL (2). Pass `"priority": 3` for HIGH or `"priority": 4` for URGENT only when the operator signals urgency. (Scale: LOW=1 NORMAL=2 HIGH=3 URGENT=4.)

> **Non-ASCII note:** if `title`/`description`/AC contain Thai / arrows / emoji, write the payload as
> a UTF-8 file and POST with `curl --data-binary @file` — NEVER a PowerShell-inline `-d "...ไทย..."`
> (the console codepage mangles non-ASCII to literal '?' before it reaches the API; irrecoverable).

## Step 4 — POST it (header AND body both carry project_id)

```
curl --silent -X POST \
  -H "X-Project-Id: <id>" \
  -H "Content-Type: application/json" \
  --data-binary @_scratch/agent_task_create_payload.json \
  http://localhost:8456/api/tasks \
  -o _scratch/agent_task_create_resp.json \
  -w "%{http_code}"
```

- HTTP **201** → continue to Step 5.
- HTTP **200** is NOT a valid success code for POST /api/tasks — treat it as an error (show body verbatim, STOP).
- HTTP **422** → almost always means `project_id` was missing from the BODY. Open the response file,
  show the raw error verbatim, FIX the payload, retry. Never report success on a non-2xx.
- Any other non-2xx → show the raw response body verbatim and STOP. Do not claim the task was created.

## Step 5 — verify (don't trust the POST)

GET the created id back and confirm it really persisted with AC populated:

```
curl --silent -H "X-Project-Id: <id>" http://localhost:8456/api/tasks/<new_id>
```

Confirm: `project_id` matches, `acceptance_criteria` is non-empty, `process_status` is 1 (TODO).

## Step 6 — report

Print: created task **id**, title, `task_type`, AC count, and the AC list. If `review=deep` ran,
add a one-line note of what the dev-spec-reviewer changed. One block, no fluff.

---

## Footgun guards encoded here (the whole point of this command)

1. **`project_id` goes in the BODY**, not only the `X-Project-Id` header. Header-alone returns
   a silent **422** that can look like a phantom create. Always send both.
2. **`acceptance_criteria` is set AT CREATION** — never create-then-patch-AC-later. AC defines "done".
3. **Status semantics:** new tasks are `process_status` **1 (TODO)** — the default; do not set it.
   - **Never** set `process_status: 4` (BLOCKED) directly. BLOCKED is expressed ONLY via the
     `blocked_by: <task_id>` FK. If a task is on HOLD / waiting on something external, keep it
     **TODO (1)** and record the reason (e.g. in `description` or a later `status_change_reason`),
     NOT status 4.
4. **Enums:** `task_type` ∈ {bug, feature, chore, docs, refactor}; `task_kind` ∈ {ai, human};
   each AC `status` ∈ {pending, passed, failed, na}.
5. **Self-review is the cheap default (Step 2.5):** the common small errors (scope bleed-in, an
   unstated footgun, a vague/redundant AC) are catchable by a self-read with no spawn — do it every
   time. Reserve `review=deep` (a real spawn) for high-stakes tasks.
6. **DONE-flip discipline (for later, not this command):** flipping a task to DONE (5) requires
   verifying every AC first and PATCHing the AC array — see `/tn-task-done`.

## Usage

```
# Simplest — natural-language request, AC drafted by the skill + Tier-A self-review
/tn-task-create add a rate-limit guard to the email endpoints, with tests

# With explicit urgency signal (skill will set priority=HIGH)
/tn-task-create fix the broken GET /api/tasks pagination — urgent, blocking QA

# High-stakes / new-surface task — opt into the deep dev-spec-reviewer pass before POST
/tn-task-create design the pluggable secrets backend (Infisical + .env fallback) review=deep

# Common operator mistake to avoid: "add to milestone X" means open a task ONLY — do NOT implement
/tn-task-create open a task for the milestone-46 skill-eval pass; assign to ms46
```

The Lead can also invoke this via the Skill tool (skill name = `tn-task-create`).

## Namespace mechanism (how this command is named)

This is a Claude Code **skill**: a `SKILL.md` under `.claude/skills/tn-task-create/`. The directory
name (`tn-task-create`) becomes the invoked name (`/tn-task-create`). The `tn-` family is grouped by
the shared prefix (flat skills, no colon sub-namespace). New/edited skill files are picked up after a
Claude Code **restart**.

## Related skills
- `tn-spec` — adversarial PRE-idea hardening of a fuzzy idea (upstream of this; see the boundary note in Step 2.5)
- `tn-task-done` — close a task you just created, once its AC are verified
- `tn-task-update` — mutate fields on an already-created task (status, priority, etc.)
- `tn-milestone-create` — create a milestone to group the new task under
