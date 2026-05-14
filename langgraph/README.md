# langgraph — Phase 4 engine container

Headless LangGraph runtime for the agent-teams supervisor + specialist subagents.
Runs as the `langgraph` service in `docker-compose.yml`, published on host
port `${LANGGRAPH_PORT:-8465}` (container always listens on 8000).

> Status (2026-05-14, Kanban #851): scaffold only. `graph.py` is a FastAPI
> stub exposing `GET /ok`. #850 will replace it with the supervisor
> StateGraph + `AsyncPostgresSaver` checkpoint wiring.

## Layout

| File | Purpose |
|---|---|
| `Dockerfile` | `python:3.12-slim` base; installs deps from `pyproject.toml`. CMD `uvicorn graph:app` (stub) — will change when #850 swaps to a LangGraph server. |
| `pyproject.toml` | Pinned deps: langgraph, langgraph-checkpoint-postgres, langgraph-cli, langchain-anthropic, langchain-openai, fastapi, uvicorn, psycopg[binary]. |
| `langgraph.json` | LangGraph CLI config — points the future `supervisor` graph at `./graph.py:graph`. Unused by the stub CMD but in place for #850's `langgraph build` workflow. |
| `graph.py` | FastAPI app + compiled StateGraph + lifespan. Stub replaced by #850; #852 adds the Kanban worker task to the lifespan. |
| `worker.py` | (#852) Background asyncio task — polls `GET /api/tasks/next-autorun` and feeds picked tasks through the compiled graph, then PATCHes the result back. Started/stopped by `graph.py`'s lifespan. |
| `__init__.py` | Package marker (empty). |
| `.gitignore` | `__pycache__/`, `.venv/`, `*.egg-info/`. |

## Postgres `langgraph` schema

The container shares the existing `agent_teams` database on the `db` service.
Checkpoint tables are isolated in a separate `langgraph` schema.

**Bootstrap (Option B — chosen):** the schema is created at app startup by the
graph code (lands in #850) via `CREATE SCHEMA IF NOT EXISTS langgraph;` issued
right before `AsyncPostgresSaver.setup()`. This is portable across existing
DB volumes — no `init-langgraph-schema.sql` mount required, and existing
developers don't need to re-bootstrap their volume.

The `DATABASE_URI` env-var carries `?options=-c%20search_path=langgraph` so the
connection lands in the schema by default once it exists.

> Why not Option A (initdb script under `/docker-entrypoint-initdb.d/`)?
> Initdb scripts only run on a fresh DB volume, so existing developers would
> have to wipe `agent-teams-pgdata` to pick the schema up. Option B is a tiny
> one-liner in app startup and works on any DB state.

## Usage

```sh
# Build the image (first time or after pyproject changes)
docker compose build langgraph

# Start the service detached
docker compose up -d langgraph

# Liveness check
curl http://localhost:8465/ok
# -> {"ok":true,"note":"stub; #850 pending"}

# Logs (the stub emits a single startup warning)
docker compose logs langgraph

# Stop
docker compose stop langgraph
```

## Switching LLM providers

Two env-vars control the provider; defaults make the Anthropic path work
without configuration once `ANTHROPIC_API_KEY` is set in `.env`. Switching
providers is a `.env` change + container restart — no code edits.

```env
LANGGRAPH_LLM_PROVIDER=anthropic   # or: openai
ANTHROPIC_API_KEY=sk-ant-...        # required when provider=anthropic
OPENAI_API_KEY=sk-...               # required when provider=openai
ANTHROPIC_MODEL=claude-sonnet-4-6   # optional override (default shown)
OPENAI_MODEL=gpt-4o                 # optional override (default shown)
```

### Anthropic → OpenAI

```env
LANGGRAPH_LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
# ANTHROPIC_API_KEY may stay set; it's only consulted when provider=anthropic.
```

```sh
docker compose restart langgraph
```

### OpenAI → Anthropic

```env
LANGGRAPH_LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

```sh
docker compose restart langgraph
```

### Model overrides

`ANTHROPIC_MODEL` / `OPENAI_MODEL` override the default model per provider —
e.g., set `ANTHROPIC_MODEL=claude-opus-4-7` to swap the model without touching
the SDK selector. Names are validated at startup: lowercase letters, digits,
dot, hyphen only. The common copy-paste gotcha — `claude_sonnet_4_6`
(underscores) vs `claude-sonnet-4-6` (hyphens) — is caught with an explicit
error message.

### Why a restart is required

The factory reads `os.getenv(...)` once during the FastAPI lifespan probe
(`make_chat_model().invoke("ping")`); subsequent `/invoke` calls reuse the
same model instance. Changing `.env` mid-run has no effect until the lifespan
re-runs. `docker compose restart langgraph` is the cheapest way to force it.

### Fail-fast behaviour

The container refuses to start (lifespan raises `RuntimeError`) if:

- the configured provider's API key is unset or whitespace-only;
- `LANGGRAPH_LLM_PROVIDER` is anything other than `anthropic` / `openai`;
- the chosen model name fails the shape regex; or
- the `invoke("ping")` probe to the provider itself fails.

Better to refuse `docker compose up` than to look healthy and crash on the
first `/invoke`. Logs name exactly which env-var to set.

## Kanban poll worker (Kanban #852)

The lifespan starts a background asyncio task — `worker.py:run_worker_loop` —
right after the graph compiles and the LLM probe succeeds. It polls
`GET /api/tasks/next-autorun` on the compose-internal api hostname every
`LANGGRAPH_POLL_INTERVAL_SEC` seconds (default 30) using the `X-Project-Id`
header from `LANGGRAPH_PROJECT_ID`.

Per task: PATCH `process_status=2` (IN_PROGRESS) → `graph.ainvoke(...)` →
PATCH `process_status=5` (DONE) on success, or `process_status=4` (BLOCKED)
plus `is_pending=true` if the graph returned `halt_reason`, or
`process_status=4` with `halt_reason="langgraph error: ..."` if `ainvoke`
raised.

HITL resume (consuming `resume_tasks` from next-autorun and re-running halted
tasks from their checkpoint) is **deferred to #852b** — the worker logs a
single line per poll when resume_tasks is non-empty so the gap is visible
in container logs.

Required env-var: `LANGGRAPH_PROJECT_ID` (positive integer). Optional:
`LANGGRAPH_POLL_INTERVAL_SEC`, `LANGGRAPH_KANBAN_API_BASE`.

The worker stops within ~5 seconds of `docker compose stop langgraph` —
the lifespan cancels the asyncio task and waits with a 5s grace.

## Rebuilding after a pyproject edit

```sh
docker compose build --no-cache langgraph
docker compose up -d langgraph
```

The bind-mount `.:/repo` covers source edits (no rebuild needed for `graph.py`
changes once #850 wires `--reload`), but dependency changes in
`pyproject.toml` require a rebuild because deps are installed at image-build
time.

## Pinned versions (2026-05-14)

| Package | Version |
|---|---|
| langgraph | 1.2.0 |
| langgraph-checkpoint-postgres | 3.1.0 |
| langgraph-cli | 0.4.26 |
| langchain-anthropic | 1.4.3 |
| langchain-openai | 1.2.1 |
| langchain-core | 1.4.0 |
| fastapi | 0.136.1 |
| uvicorn[standard] | 0.46.0 |
| psycopg[binary] | >=3.2,<4 |

LangGraph is perishable — re-validate pins on major bumps (langgraph 2.x,
langchain-* 2.x) or when langgraph-cli changes its `langgraph.json` schema.
