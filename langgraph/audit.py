"""Specialist-tool audit-trail wiring — langgraph-side (Kanban #980).

Thin HTTP wrapper that the specialist tool-use loop calls AFTER every
tool invocation to record one row in the `tool_calls` audit table.
The wrapper hits the FastAPI service synchronously via httpx; the
langgraph container does NOT share a source tree with the api
container (separate pyproject + container — see worker.py preamble),
so direct DB writes from here are intentionally avoided.

This module is the langgraph-side surface. Wiring it into the
specialist-node tool-use loop is the job of **Kanban #981**; #980 just
ships the callable + the audit table.

Note: as of #980 the FastAPI side exposes `record_tool_call` as a
Python service (no public POST endpoint — clients cannot insert audit
rows). For #981's wiring choice see the design lock in Lead's spawn
brief: the cleaner option is to run the writer inside the api
container's process (the api and langgraph containers share a DB and
the langgraph container's tool-use loop posts heartbeats via the api
already — adding a private POST endpoint for audit rows is the natural
next slice). Until that endpoint lands, `record_tool_invocation` is a
no-op stub that logs the call (so #981 has a clear seam to fill).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("langgraph.audit")

# Same env-var as the worker; same default. Kept as a module-level
# constant so #981 can import it directly when wiring the POST call.
DEFAULT_API_BASE = "http://api:8456"


def _api_base() -> str:
    """Resolve the kanban API base URL at call time (env-var honoured).

    Lazy lookup (not a module-level constant) so tests can monkeypatch
    LANGGRAPH_KANBAN_API_BASE per-invocation without re-importing.
    """
    return (
        os.getenv("LANGGRAPH_KANBAN_API_BASE", DEFAULT_API_BASE)
        .strip()
        .rstrip("/")
    )


async def record_tool_invocation(
    task_id: int,
    tool: Any,
    input_args: dict[str, Any],
    result: Any,
    decision: Any,
) -> None:
    """Record one specialist-tool invocation in the audit table.

    Args:
        task_id: Kanban task id that owns this invocation.
        tool: a `Tool` instance from `langgraph/tools/__init__.py`.
            Reads `tool.name` and `tool.tier` (a `Tier` enum value;
            `.value` is the wire string).
        input_args: the tool's validated input args dict.
        result: a `ToolResult` Pydantic model (from `tools/base.py`).
            Serialised via `.model_dump()` for the POST body.
        decision: a `PermissionDecision` enum value (from
            `tools/permission_gate.py`). `.value` is the wire string.

    Behaviour:
      - Builds the POST payload synchronously (raises on bad inputs so
        bugs surface loudly in dev — the loop's invariant is "every
        invocation has an audit row").
      - **Stub for #980:** logs the payload at INFO level and returns.
        The actual POST to FastAPI lands in **#981** alongside the
        sandbox-enforcement wiring; the writer service
        (`api/src/services/tool_call_writer.py`) is already in place
        and tested at the model layer.

    Failure isolation: this function MUST NOT raise on transport
    errors. The audit log is a forensic aid, not a hard dependency of
    the agent loop — losing one row is better than crashing a running
    task. (The synchronous-write invariant in Q9 lives on the api side,
    inside the `record_tool_call` service: the api commits before
    returning. On the langgraph side, "synchronous" means "we await the
    HTTP call before continuing the loop"; we still tolerate transport
    failure.)
    """
    payload = _build_payload(task_id, tool, input_args, result, decision)
    # #980 ships the stub. #981 replaces the body with the real POST.
    # The stub logs at INFO so dev can grep for "tool_call_audit" while
    # exercising the worker + specialist node end-to-end.
    logger.info(
        "tool_call_audit (stub): task=%d tool=%s tier=%s decision=%s success=%s duration_ms=%s",
        task_id,
        payload["tool_name"],
        payload["tier"],
        payload["permission_decision"],
        payload["result"]["success"],
        payload["result"]["duration_ms"],
    )


def _build_payload(
    task_id: int,
    tool: Any,
    input_args: dict[str, Any],
    result: Any,
    decision: Any,
) -> dict[str, Any]:
    """Pure helper — build the POST body. Extracted for unit testing.

    Tolerates a `result` that's already a dict (the loop may have
    serialised it before logging) OR a Pydantic v2 model (the typical
    case — `ToolResult`). Same for `decision` (enum value or str).
    """
    if hasattr(result, "model_dump"):
        result_dict = result.model_dump()
    else:
        result_dict = dict(result)

    tier_value = (
        tool.tier.value if hasattr(tool.tier, "value") else str(tool.tier)
    )
    decision_value = (
        decision.value if hasattr(decision, "value") else str(decision)
    )

    return {
        "task_id": task_id,
        "tool_name": tool.name,
        "tier": tier_value,
        "input_args": dict(input_args or {}),
        "result": result_dict,
        "permission_decision": decision_value,
    }


# ---------------------------------------------------------------------------
# Forward-compat slot for #981 — keep the call site stable so the only
# diff in #981 is uncommenting these lines + wiring them into
# `record_tool_invocation`. The endpoint URL + payload shape are pinned
# here to make the #981 spawn brief trivial.
# ---------------------------------------------------------------------------


async def _post_audit_row(payload: dict[str, Any]) -> None:  # pragma: no cover
    """#981 will route record_tool_invocation through this helper.

    Currently unused. The endpoint shape is provisional — the api side
    must add `POST /api/tasks/{task_id}/tool-calls` (internal only,
    same X-Project-Id discipline as the GET) before this can fire. See
    #981 spawn brief.
    """
    task_id = payload["task_id"]
    url = f"{_api_base()}/api/tasks/{task_id}/tool-calls"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(url, json=payload)
    except httpx.HTTPError as exc:
        logger.warning(
            "tool_call_audit POST failed (non-fatal): task=%d url=%s err=%r",
            task_id,
            url,
            exc,
        )
