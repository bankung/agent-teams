# Spawn prompt template

Use the `Agent` tool with `subagent_type` matching the role. Available role names depend on the active team's roster — see `.claude/teams/<active.team>.md`. Examples:
- `team='dev'` → `dev-frontend | dev-backend | dev-devops | dev-tester | dev-reviewer`
- `team='novel'` → `novel-writer | novel-editor`

```
Agent({
  subagent_type: "<role>",
  description: "<3-5 word task summary>",
  name: "<role>-<short-slug>",
  prompt: <see structure below>
})
```

## Prompt structure

```markdown
# Task
<concrete instruction — golden path + edges to cover>
<if Kanban-tracked: "Kanban task ID: <id>" so the subagent can reference it>

# Active project
**Name:** <from API>
**Description:** <one line>

# Working directory
`<absolute path from projects.paths.<lane>>`

Do not touch files outside this path, except:
- `context/projects/<active>/<role>/` (your own folder)
  Absolute: `<absolute path to agent-teams>/context/projects/<active>/<role>/`

# Per-project shared (read-only, source of truth)

## context/projects/<active>/shared/decisions.md
<paste full content>

## context/projects/<active>/shared/api-contracts.md  (if relevant)
<paste full content or relevant section>

## context/projects/<active>/shared/db-schema.md  (if relevant)
<paste full content or relevant section>

# Standards (read-only, cross-project)

## context/standards/general.md
<paste full content — includes Kanban schema codes>

## context/standards/<framework-1>/  (per lane mapping)
<paste content of each file>

## context/standards/<framework-2>/
<...>

(If a framework folder is missing or empty, note "standards/X not yet written" and proceed.)

# Your prior state
Read `<absolute path>/context/projects/<active>/<role>/current-state.md` (if present) before starting.

# Constraints
- Do not write `context/projects/<active>/shared/*` (Lead writes — propose instead).
- Do not write `context/standards/*` ever (humans maintain — flag insights in the final report).
- No direct DB writes — use FastAPI endpoints.
- Every Write/Edit/Bash will prompt the user; if denied, stop and report with the reason.
- Do only what was asked. No refactors or features outside scope.
- If the task has `acceptance_criteria` (check `curl -H "X-Project-Id: <id>" /api/tasks/<id>`), your final report MUST include a per-criterion verdict table + JSON block matching the shape in `.claude/agents/dev-tester.md` `### 2d` or `.claude/agents/dev-reviewer.md` "Acceptance criteria audit" bullet. NEVER report status='pending' — that means you didn't check.

# X-Project-Id header on task endpoints (Kanban #695, mandatory)

Subagents inherit the session-bound project from Lead's spawn brief. Every Bash
`curl http://localhost:8456/api/tasks*` call MUST include
`-H "X-Project-Id: <id>"` matching the project Lead bound at session start.
Forgetting it 400s — the gate is intentional. Examples:

```bash
# List tasks for the session-bound project
curl --silent -H "X-Project-Id: <id>" http://localhost:8456/api/tasks

# Detail / PATCH / DELETE
curl --silent -H "X-Project-Id: <id>" http://localhost:8456/api/tasks/<task_id>
curl --silent -X PATCH -H "X-Project-Id: <id>" -H "Content-Type: application/json" \
  -d '{"process_status": 2}' http://localhost:8456/api/tasks/<task_id>
curl --silent -X DELETE -H "X-Project-Id: <id>" http://localhost:8456/api/tasks/<task_id>

# POST — body project_id MUST equal the header (header wins on conflict).
curl --silent -X POST -H "X-Project-Id: <id>" -H "Content-Type: application/json" \
  -d '{"project_id": <id>, "title": "..."}' http://localhost:8456/api/tasks
```

Project endpoints (`/api/projects/*`) do NOT need the header — the project IS
the resource. Only task endpoints are gated.

# Compact step
Before returning:
1. Update `context/projects/<active>/<role>/current-state.md`.
2. (Optional) write a session note for details worth keeping separately.
3. Return per the agent definition's report structure (Summary / Files modified / Proposed shared updates / Standards insights / Open questions).
```

## Sizing tips

- If a file is large, paste only the relevant section and tell the subagent to read the full file at the path given.
- Standards must include every framework in the lane — the subagent can't decide which framework applies; Lead has already decided.
- Parallel spawns: only when independent. Same artifact with an unstable contract → sequential, with the producing role first (e.g., `dev-backend` before `dev-frontend`; `novel-writer` before `novel-editor`).

## Cross-project task filing

When Lead (or a subagent at Lead's direction) files a Kanban task on a DIFFERENT project than the one this session is bound to, the task title **MUST** begin with a `[<purpose>]` prefix indicating WHY it's filed from outside.

Examples (good):
- `[platform-rule] Cross-project tasks must use [purpose] prefix in title`
- `[content-team] Draft .claude/teams/content.md playbook`
- `[methodology] Promote Thai translatese taxonomy`
- `[novel-drift] Audit Chapter 12 voice drift`

Examples (bad — missing prefix, blends with project-internal work):
- `Draft content team playbook`
- `Audit Chapter 12 voice drift`

Reasoning: the receiving project's future Lead needs instant context about WHY this task was filed from outside. Without the prefix, cross-project asks lose audit signal at batch-triage. Full rule + storage architecture rationale lives in root `CLAUDE.md` golden rules — "Cross-project edits to agent-teams platform files" bullet.
