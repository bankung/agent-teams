# Spawn prompt template

Use the `Agent` tool with `subagent_type` matching the role.

```
Agent({
  subagent_type: "<frontend | backend | devops | qa | reviewer>",
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

# Compact step
Before returning:
1. Update `context/projects/<active>/<role>/current-state.md`.
2. (Optional) write a session note for details worth keeping separately.
3. Return per the agent definition's report structure (Summary / Files modified / Proposed shared updates / Standards insights / Open questions).
```

## Sizing tips

- If a file is large, paste only the relevant section and tell the subagent to read the full file at the path given.
- Standards must include every framework in the lane — the subagent can't decide which framework applies; Lead has already decided.
- Parallel spawns: only when independent. Same feature with an unstable contract → sequential, backend first.
