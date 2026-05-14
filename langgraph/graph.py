"""Stub — replaced by #850. Boots a FastAPI shell with /ok so the container
can pass compose healthcheck before the LangGraph graph is implemented.

When #850 lands this module will export a compiled `graph` (StateGraph) plus
either keep the FastAPI shell or hand off to `langgraph dev`. Until then the
`graph` symbol is intentionally absent — `langgraph.json` references it for
future builds but the stub CMD (uvicorn graph:app) only needs `app`.
"""
import logging

from fastapi import FastAPI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.warning(
    "graph.py is a stub — pick up Kanban #850 to define the supervisor graph"
)

app = FastAPI(title="langgraph-stub")


@app.get("/ok")
async def health() -> dict:
    return {"ok": True, "note": "stub; #850 pending"}
