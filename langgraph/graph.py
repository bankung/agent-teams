"""LangGraph supervisor — FastAPI app + compiled StateGraph (Kanban #850).

Boot sequence (FastAPI lifespan):
  1. Read DATABASE_URI from env (no fallback; fail fast).
  2. CREATE SCHEMA IF NOT EXISTS langgraph;  (Option B per #851 README)
  3. Open AsyncPostgresSaver against the same URI and `setup()` it. The
     ?options=-c%20search_path=langgraph in the URI ensures setup() lands
     tables under the langgraph schema (not public).
  4. Validate the LLM provider end-to-end by calling `model.invoke("ping")`.
     Missing API keys / bad model names raise; lifespan aborts. (Lead's
     fail-fast rule — surfaces config errors before traffic arrives.)
  5. Build the StateGraph, compile with the checkpointer, stash globals.

Topology (START -> supervisor -> conditional_edges to one of six specialist
nodes [frontend, backend, devops, tester, reviewer, general] -> END). Each
specialist returns directly to END (no loop back to supervisor for #850 — a
single specialist run per /invoke. #853 may add a self-critique loop).

Endpoints:
  GET  /ok       — healthcheck, returns {ok, graph_compiled, provider} or 503
  POST /invoke   — run the graph once for a given task_id + brief + role

The compiled graph is also exported as `graph` (module-level) for
`langgraph.json` so `langgraph build` / `langgraph dev` can discover it
without going through the FastAPI app.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

import worker as worker_module
from llm import make_chat_model
from nodes import (
    auditor_node,
    backend_specialist_node,
    devops_specialist_node,
    frontend_specialist_node,
    general_node,
    reviewer_specialist_node,
    route_from_auditor,
    route_from_supervisor,
    supervisor_node,
    tester_specialist_node,
)
from state import AgentState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("langgraph.graph")

# Module-level globals populated by the lifespan. `graph` is the public name
# referenced by langgraph.json — it's the compiled graph instance.
graph: Any = None
checkpointer: AsyncPostgresSaver | None = None
provider_name: str = "?"
graph_ready: bool = False
# Background poll loop (Kanban #852). Created in lifespan after the graph is
# compiled + LLM probe succeeds; cancelled on shutdown.
worker_task: asyncio.Task[None] | None = None
# Grace period to let the worker finish its current iteration after cancel.
# Short on purpose: a stuck httpx request will still be force-cancelled when
# this elapses, and the OS process exit will tear down the connection.
_WORKER_SHUTDOWN_GRACE_SEC = 5.0


# L7 prevention (Kanban #1112) — refuse to start if DATABASE_URI lacks the
# langgraph search_path hint or points at a db NOT in the allowlist. langgraph
# uses DATABASE_URI (its own env-var, separate from api's DATABASE_URL), so
# the L1/L2/L3 DATABASE_URL defenses don't cover this path. See the
# 2026-05-17 dev-DB-wipe incident for the runtime-pointer-drift class of bug.
ALLOWED_LANGGRAPH_DB_NAMES = set(
    os.environ.get(
        "LANGGRAPH_DB_NAME_ALLOWLIST", "agent_teams,agent_teams_test"
    ).split(",")
)


def _validate_database_uri(raw_uri: str) -> None:
    """Refuse to start if DATABASE_URI is misconfigured.

    Two checks, both must pass before any DB op (saver.setup, _ensure_schema):

    1. `search_path=langgraph` must appear literally in the URI (compose ships
       `?options=-c%20search_path=langgraph`). Without it, AsyncPostgresSaver's
       `setup()` lands checkpoint tables in `public`, where they collide with
       api's tasks/projects.
    2. The extracted db name (the path segment after host:port) must be in
       `ALLOWED_LANGGRAPH_DB_NAMES` — defaults to {agent_teams, agent_teams_test};
       override via `LANGGRAPH_DB_NAME_ALLOWLIST` env (comma-separated).

    Both failures raise RuntimeError so the lifespan aborts loudly before any
    write hits the DB. See 2026-05-17 incident and Kanban #1112.
    """
    if "search_path=langgraph" not in raw_uri:
        raise RuntimeError(
            "DATABASE_URI must include 'options=-c search_path=langgraph' to "
            "isolate langgraph writes from the public schema. See "
            "context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md"
        )
    # Extract db name from 'postgresql://...@host:port/<db>?...' — the path
    # segment between the netloc's trailing `/` and the `?` (if any).
    m = re.search(r"://[^/]+/([^?]+)", raw_uri)
    if not m:
        raise RuntimeError(
            f"DATABASE_URI does not look like a valid postgres URL: {raw_uri!r}"
        )
    db_name = m.group(1)
    if db_name not in ALLOWED_LANGGRAPH_DB_NAMES:
        raise RuntimeError(
            f"DATABASE_URI points at db {db_name!r} which is not in the "
            f"allowlist {ALLOWED_LANGGRAPH_DB_NAMES}. To add, set "
            "LANGGRAPH_DB_NAME_ALLOWLIST env (csv). See 2026-05-17 incident."
        )


def _normalize_pg_uri(uri: str) -> str:
    """Re-encode the URI's query so psycopg's libpq URI parser accepts it.

    The compose file ships DATABASE_URI as `...?options=-c%20search_path=langgraph`.
    libpq's URI grammar allows a literal `=` inside the value of a query
    parameter, but psycopg 3.x's `_parse_conninfo` rejects it
    ("extra key/value separator '=' in URI query parameter"). Re-encoding the
    value via `urlencode(quote_via=quote)` turns the inner `=` into `%3D`,
    which both psycopg AND libpq itself decode correctly.

    Lifting the fix into the app instead of editing docker-compose.yml keeps
    this a single-task change (Kanban #850) — and means we tolerate any URI
    source (compose env, .env override, deployed secret) without coupling to
    one specific encoder.
    """
    parts = urlsplit(uri)
    if not parts.query:
        return uri
    # parse_qsl decodes %20 / %3D etc.; urlencode then re-encodes with quote()
    # (which encodes `=` as %3D). keep_blank_values so an empty-value param
    # round-trips identically.
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    new_query = urlencode(pairs, quote_via=quote)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


def _build_graph(saver: AsyncPostgresSaver) -> Any:
    """Assemble the StateGraph and compile with the given checkpointer.

    Topology (Kanban #952 — auditor wired in):
      START → supervisor → {specialist} → auditor → END / supervisor (loop)

    The auditor sits between every specialist and END. It either:
      - lets the task complete (verdict=pass → END),
      - sends the task back to supervisor with an adjusted brief
        (verdict=auto_resolve under the retry cap), or
      - pauses the graph via HITL interrupt (verdict=escalate; on resume the
        operator's answer drives one of {accept→END, retry_with_X→supervisor,
        reject→END with halt_reason='operator_rejected'}).
    """
    builder = StateGraph(AgentState)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("backend", backend_specialist_node)
    builder.add_node("frontend", frontend_specialist_node)
    builder.add_node("devops", devops_specialist_node)
    builder.add_node("tester", tester_specialist_node)
    builder.add_node("reviewer", reviewer_specialist_node)
    builder.add_node("general", general_node)
    # Kanban #952: auditor sits between every specialist and END.
    builder.add_node("auditor", auditor_node)

    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        route_from_supervisor,
        {
            "frontend": "frontend",
            "backend": "backend",
            "devops": "devops",
            "tester": "tester",
            "reviewer": "reviewer",
            "general": "general",
        },
    )
    # Every specialist hands off to the auditor.
    for node in ("frontend", "backend", "devops", "tester", "reviewer", "general"):
        builder.add_edge(node, "auditor")

    # Auditor's conditional edge: PASS / halt → END; AUTO_RESOLVE → loop back
    # to supervisor (capped by audit_retry_count).
    builder.add_conditional_edges(
        "auditor",
        route_from_auditor,
        {
            "supervisor": "supervisor",
            "END": END,
        },
    )

    return builder.compile(checkpointer=saver)


async def _ensure_schema(uri: str) -> None:
    """Run `CREATE SCHEMA IF NOT EXISTS langgraph;` on a one-shot psycopg
    connection. The DATABASE_URI carries `options=-c search_path=langgraph`
    so the saver's subsequent `setup()` lands under that schema — but the
    schema itself has to exist first. This is the Option B contract from #851.
    """
    # psycopg accepts the libpq URI directly. autocommit so DDL is durable.
    async with await psycopg.AsyncConnection.connect(uri, autocommit=True) as conn:
        async with conn.cursor() as cur:
            await cur.execute("CREATE SCHEMA IF NOT EXISTS langgraph;")
    logger.info("langgraph schema ensured (CREATE SCHEMA IF NOT EXISTS langgraph)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global graph, checkpointer, provider_name, graph_ready, worker_task

    raw_uri = os.getenv("DATABASE_URI")
    if not raw_uri:
        raise RuntimeError("DATABASE_URI env-var is required (no fallback)")
    # L7 prevention (Kanban #1112) — MUST run BEFORE _normalize_pg_uri /
    # _ensure_schema / saver.setup() so a misconfigured URI never touches the DB.
    _validate_database_uri(raw_uri)
    uri = _normalize_pg_uri(raw_uri)

    # 1) Schema bootstrap (Option B). Must happen BEFORE AsyncPostgresSaver.setup().
    await _ensure_schema(uri)

    # 2) Validate LLM provider end-to-end. Fail fast on missing keys or bad
    # model names — better to refuse to start than to look healthy and crash
    # on first /invoke. Done BEFORE opening the saver so we don't leave
    # connections dangling if the LLM check fails.
    provider_name = os.getenv("LANGGRAPH_LLM_PROVIDER", "anthropic").lower()
    logger.info("validating LLM provider: %s", provider_name)
    try:
        probe_model = make_chat_model()
        probe_model.invoke([HumanMessage(content="ping")])
    except Exception as exc:
        logger.exception("LLM provider validation failed — aborting lifespan")
        raise RuntimeError(
            f"LLM provider {provider_name!r} validation failed: {exc}. "
            "Set the appropriate API key (ANTHROPIC_API_KEY / OPENAI_API_KEY) "
            "in .env and restart the container."
        ) from exc
    logger.info("LLM provider %s validated", provider_name)

    # 3) Open the AsyncPostgresSaver as an async context manager kept alive
    # for the lifetime of the app via AsyncExitStack. AsyncPostgresSaver
    # owns its connection pool; closing it on shutdown is important.
    async with AsyncExitStack() as stack:
        saver = await stack.enter_async_context(
            AsyncPostgresSaver.from_conn_string(uri)
        )
        await saver.setup()
        logger.info("AsyncPostgresSaver.setup() complete (tables live under langgraph schema)")

        # 4) Build + compile the graph.
        compiled = _build_graph(saver)
        graph = compiled
        checkpointer = saver
        graph_ready = True
        logger.info("graph compiled — supervisor + 6 specialist nodes wired")

        # 5) Start the Kanban poll worker (Kanban #852). MUST come after the
        # graph is compiled — the worker reads `graph_module.graph` on each
        # iteration and assumes it is non-None when graph_ready is set. We
        # pass the current module rather than the compiled graph object so a
        # future hot-reload can swap `graph` in place without restarting the
        # worker.
        graph_module = sys.modules[__name__]
        worker_task = asyncio.create_task(
            worker_module.run_worker_loop(graph_module), name="langgraph-kanban-worker"
        )
        logger.info("kanban worker started (asyncio task)")

        try:
            yield
        finally:
            graph_ready = False
            # Cancel + wait for the worker before tearing down the saver — the
            # worker's last iteration may still be issuing PATCH requests, and
            # we want to give it a brief window to land them before the
            # FastAPI app process exits.
            if worker_task is not None and not worker_task.done():
                logger.info("cancelling kanban worker")
                worker_task.cancel()
                try:
                    await asyncio.wait_for(
                        worker_task, timeout=_WORKER_SHUTDOWN_GRACE_SEC
                    )
                except asyncio.CancelledError:
                    pass
                except asyncio.TimeoutError:
                    logger.warning(
                        "kanban worker did not exit within %.1fs of cancel; continuing shutdown",
                        _WORKER_SHUTDOWN_GRACE_SEC,
                    )
                except Exception:
                    # Don't let a crash in the worker block lifespan shutdown.
                    logger.exception("kanban worker raised during shutdown")
            worker_task = None
            graph = None
            checkpointer = None
            logger.info("lifespan shutdown — saver context exiting")


app = FastAPI(title="langgraph", lifespan=lifespan)


# ---------------------------------------------------------------------------
# /ok — healthcheck
# ---------------------------------------------------------------------------


@app.get("/ok")
async def health() -> Any:
    if not graph_ready:
        return JSONResponse(
            {"ok": False, "graph_compiled": False, "reason": "graph not yet ready"},
            status_code=503,
        )
    return {"ok": True, "graph_compiled": True, "provider": provider_name}


# ---------------------------------------------------------------------------
# /invoke — one-shot run of the graph
# ---------------------------------------------------------------------------


class InvokeRequest(BaseModel):
    """Inbound payload for POST /invoke.

    `messages` is intentionally omitted from the API surface for now — the
    only entrypoint is a task brief. Multi-turn replay via thread_id is
    available implicitly through checkpointing (thread_id="task-{task_id}").
    """

    task_id: int = Field(..., description="Kanban task id; threads checkpoint state per task")
    brief: str = Field(..., description="Task description / spec passed to the specialist")
    assigned_role: int | None = Field(
        None, description="TaskRole code 1..5; null routes to the general node"
    )


@app.post("/invoke")
async def invoke(req: InvokeRequest) -> Any:
    if not graph_ready or graph is None:
        raise HTTPException(status_code=503, detail="graph not yet ready")

    initial_state: AgentState = {
        "task_id": req.task_id,
        "brief": req.brief,
        "assigned_role": req.assigned_role,
        "messages": [HumanMessage(content=req.brief)],
        "intermediate_results": {},
    }
    config = {"configurable": {"thread_id": f"task-{req.task_id}"}}
    result = await graph.ainvoke(initial_state, config=config)
    # Strip non-JSON-serializable message objects to a simple list of dicts so
    # the response is consumable by curl without further coercion. #852 may
    # want richer message echo; #850 keeps it minimal.
    serialized_messages = [
        {"type": m.__class__.__name__, "content": getattr(m, "content", "")}
        for m in result.get("messages", [])
    ]
    return {
        "task_id": result.get("task_id"),
        "assigned_role": result.get("assigned_role"),
        "final_result": result.get("final_result"),
        "halt_reason": result.get("halt_reason"),
        "messages": serialized_messages,
    }
