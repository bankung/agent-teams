---
name: tn-bind
description: >-
  Bind this session to an agent-teams project by NAME — resolve it via the API and
  persist the binding so every tn-* command (and the spawn-block hook) targets the right
  project. Use at session start, or any time you switch projects, or to fix a stale binding.
argument-hint: "<project name>"
allowed-tools:
  - Bash(curl:*)
  - Bash(echo:*)
  - Read
  - Write
metadata:
  version: 1.0.0
  category: platform
  tags: [platform, bind, project, setup]
---

# /tn-bind — resolve a project by name and persist the session binding

The project name is in `$ARGUMENTS`. This writes the canonical binding marker that
`_runtime/lead_project_id.txt` holds — read by the spawn-block hook AND every tn-* skill.

## Step 1 — resolve the project by name

```
curl --silent "http://localhost:8456/api/projects/by-name/<URL-encoded name>" \
  -o _scratch/tn_bind_resp.json -w "%{http_code}"
```

- **200** → parse `id`, `team`, `name`. Continue to Step 2.
- **404** → the name didn't match an active project. List the live ones and STOP (ask which):
  ```
  curl --silent "http://localhost:8456/api/projects?status=1" -o _scratch/tn_bind_list.json -w "%{http_code}"
  ```
  Print each project's `name` / `id` / `team`. Do NOT guess a project.
- **any other** → show the raw response body and STOP.

## Step 2 — persist the binding (session-scoped, #2679)

Write the resolved project id (a SINGLE integer) to a PER-SESSION file plus the legacy
global file:

1. Get this session's id: `echo $CLAUDE_CODE_SESSION_ID` (Bash). It equals the `session_id`
   the cost-capture hooks receive in their payload.
2. Write `<id>` to `_runtime/lead_project_id_<sid>.txt` — the per-session binding the
   cost-capture hooks read. Each session has its own file; UUIDs never collide, so a stale
   file from another session can never be mis-read (this is what fixes the cross-session
   mis-attribution: incident 2026-06-05 / stale 599 / ledger 2355).
3. Write `<id>` to `_runtime/lead_project_id.txt` — the legacy GLOBAL file, still read by the
   gate/spawn/notify hooks AND the tn-* skills (until Phase B #2680 migrates them).
   Overwriting it is fine (this session is now the most-recent binder for those readers).
4. Best-effort housekeeping: prune `_runtime/lead_project_id_*.txt` older than ~7 days.

## Step 3 — announce

> Session bound to **<name>** (team=**<team>**, id=**<id>**).

From here, every `/api/tasks*` call uses `X-Project-Id: <id>` in the header AND `project_id: <id>`
in the body.

---

## Why this exists
A stale `lead_project_id.txt` silently mis-targets every tn-* command and the spawn-block hook
(incident 2026-06-05: the file held 599/secretary while the work was on 1/agent-teams, because a
project switch never re-wrote it). `/tn-bind` makes re-binding a single deliberate step.

## Usage
```
/tn-bind agent-teams
/tn-bind secretary
```

## Related skills

- **tn-tasks-next** — the first skill to run after binding; surfaces the next actionable task for the bound project.
- **tn-audit** — the health audit skill that also resolves from the bound project; run after bind to get a quick project status.
