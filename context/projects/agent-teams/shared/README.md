# shared/ — agent-teams (the Kanban app)

> **Lead is the only writer of this folder.** Subagents read it and propose changes in their final report.
>
> `working_path` is NULL for this project, so its shared zone is `context/projects/agent-teams/shared/`. Created 2026-09-24 (#3321).

## Project facts (compose + tests)

The single source of truth for this project's build/test facts (#3321). Nothing else duplicates them — not the repo's `CLAUDE.md`, not a project stub.

- compose project name: `agent-teams` (always pass `-p agent-teams`; a bare `docker compose` hits whichever stack the current folder belongs to)
- test command: `docker compose -p agent-teams exec -T api pytest -q` — **operator-run**, from the container's `/repo/api` workdir, so scoped paths are `tests/<file>.py`, never `api/tests/<file>.py`. An in-session invocation is denied by the L1.5 live-DB hook (`.claude/hooks/block-pytest-on-live-db.ps1`), which cannot read the container's env from outside; the operator attests with `DOCKER_PYTEST_VERIFIED=1` in the SAME shell (PowerShell: `$env:DOCKER_PYTEST_VERIFIED = '1'` on its own line — a bash-style `VAR=1 cmd` prefix is a parser error). **The container's `DATABASE_URL` points at the LIVE `agent_teams` by design — do not expect a `_test` suffix there** (the hooks/README.md wording suggests otherwise and is misleading for this container, #3321). The real isolation is `api/tests/conftest.py`: at module import it rewrites `DATABASE_URL` to `agent_teams_test`, drops/recreates/migrates/seeds it, and a session-scope guard pins the live `agent_teams` row totals and fails the run on drift. What the operator is attesting to is that this is the agent-teams api container, not that the URL ends in `_test`.
- test database: `agent_teams_test` — dropped, recreated, migrated and seeded per invocation by `api/tests/conftest.py`. The **live** DB is `agent_teams`; the conftest sentinel fails the run on any live-DB row-count drift, which is what the universal pytest-briefing AC ("report the live DB row count before and after") is checking.
- app/API URL (if any): API `http://localhost:8456` (bound to 127.0.0.1 via `API_PORT`); web and db are on the same compose project.

## Where else to look

- `decisions.md` — append-only decision log, newest at the top. Cite an entry by its task id, not a line number.
- `decisions-index.md` — the index over that log; read this at bootstrap rather than the full file.
- `api-contracts-core.md` / `api-contracts.md` — HTTP contract; core is the bootstrap-sized cut.
- `code-map.md` (+ `code-map-api.md`, `code-map-web.md`, `code-map-langgraph.md`) — module maps.
- `release-workflow.md` — the dev → main weekly release flow.
- `runbooks/`, `incidents/`, `stories/`, `design/` — operational procedures, incident records, live NOW-state docs, design plans.
