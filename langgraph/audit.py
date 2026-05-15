"""Specialist-tool audit-trail wiring — langgraph-side (Kanban #980 + #981).

Thin HTTP wrapper that the specialist tool-use loop calls AFTER every
tool invocation to record one row in the `tool_calls` audit table.
The wrapper hits the FastAPI service synchronously via httpx; the
langgraph container does NOT share a source tree with the api
container (separate pyproject + container — see worker.py preamble),
so direct DB writes from here are intentionally avoided.

Endpoint contract (matches `api/src/routers/tool_calls.py::create_tool_call`):

    POST {API_BASE}/api/tasks/{task_id}/tool-calls
    Headers: X-Project-Id: <project_id> (required)
             Content-Type: application/json
    Body:    {tool_name, tier, input_args, result, permission_decision}
    Success: 201 Created + ToolCallRead body (we discard the body —
             the langgraph side already knows what it wrote)

Failure isolation (Kanban #949 Q9 lock): the audit log is a forensic
aid, not a hard dependency. A transport-layer failure (connection
refused, 5xx, timeout) is LOGGED but does NOT raise — the loop
continues. The DB-side commit happens BEFORE the api returns 201
(synchronous-write invariant on the api side), so a 2xx response is
proof of durability. Only on the langgraph side do we tolerate
transport flakiness.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("langgraph.audit")

# Same env-var as the worker; same default. The compose-internal hostname
# resolves to the api service from inside the langgraph container.
DEFAULT_API_BASE = "http://api:8456"

# Module-level httpx timeout for audit POSTs. Smaller than the worker's 30s
# because audit calls are tiny + frequent — we want them snappy or failed.
_AUDIT_TIMEOUT_S: float = 5.0

# ToolResult fields the api accepts on the wire. The api side's
# `ToolCallResult` uses `extra='forbid'` so any forward-compat extras on
# ToolResult (e.g. `retry_safe`, which is a langgraph-side LLM hint and
# is intentionally not persisted) would 422 without this filter. Frozen
# so a typo on the producer side raises rather than silently mutating.
_KNOWN_RESULT_KEYS: frozenset[str] = frozenset(
    {"success", "error_code", "error_msg", "output", "duration_ms"}
)


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


def _project_id_header() -> dict[str, str]:
    """Build the X-Project-Id header from the worker's env-var.

    Mirrors `worker.WorkerConfig`: `LANGGRAPH_PROJECT_ID` is the source
    of truth for which project this engine is bound to. Missing / empty
    → empty dict (the api endpoint returns 400, which the caller logs).
    """
    pid = os.getenv("LANGGRAPH_PROJECT_ID", "").strip()
    if not pid:
        return {}
    return {"X-Project-Id": pid}


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
      - Issues a synchronous POST via httpx.AsyncClient (5s timeout).
      - Logs a WARNING on transport failure or non-201 response and
        returns without raising — see module docstring.
    """
    payload = _build_payload(task_id, tool, input_args, result, decision)
    await _post_audit_row(task_id, payload)


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

    NOTE: `task_id` is in the URL path, NOT the body — the api endpoint
    derives it from `/api/tasks/{task_id}/tool-calls`. We keep the
    `task_id` kwarg here for signature stability and as a sanity field
    for log lines, but it's not serialised into the POST body. Per the
    `ToolCallCreate` Pydantic schema (extra='forbid'), including
    `task_id` would 422.
    """
    if hasattr(result, "model_dump"):
        result_dict = result.model_dump()
    else:
        result_dict = dict(result)

    # ToolResult has 6 fields (success/error_code/error_msg/output/
    # retry_safe/duration_ms). The api-side ToolCallResult drops
    # `retry_safe` (langgraph-side LLM hint, not persisted) and uses
    # the rest. We filter to the api's known set so `extra='forbid'`
    # doesn't 422 us. See `_KNOWN_RESULT_KEYS` at module scope.
    filtered_result = {
        k: v for k, v in result_dict.items() if k in _KNOWN_RESULT_KEYS
    }

    tier_value = (
        tool.tier.value if hasattr(tool.tier, "value") else str(tool.tier)
    )
    decision_value = (
        decision.value if hasattr(decision, "value") else str(decision)
    )

    return {
        "tool_name": tool.name,
        "tier": tier_value,
        "input_args": dict(input_args or {}),
        "result": filtered_result,
        "permission_decision": decision_value,
    }


async def _post_audit_row(task_id: int, payload: dict[str, Any]) -> None:
    """POST the audit payload synchronously; log + swallow transport errors.

    The api endpoint commits BEFORE returning 201 — a 201 response means
    the row is durable. Anything else (transport error, non-2xx) is
    logged at WARNING and the function returns; the loop continues.
    """
    url = f"{_api_base()}/api/tasks/{task_id}/tool-calls"
    headers = {
        **_project_id_header(),
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=_AUDIT_TIMEOUT_S) as client:
            resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 201:
            logger.warning(
                "tool_call_audit POST returned %d (expected 201): "
                "task=%d tool=%s body=%r",
                resp.status_code,
                task_id,
                payload.get("tool_name"),
                resp.text[:200],
            )
        else:
            logger.info(
                "tool_call_audit: task=%d tool=%s tier=%s decision=%s success=%s",
                task_id,
                payload["tool_name"],
                payload["tier"],
                payload["permission_decision"],
                payload["result"].get("success"),
            )
    except httpx.HTTPError as exc:
        logger.warning(
            "tool_call_audit POST failed (non-fatal): task=%d url=%s err=%r",
            task_id,
            url,
            exc,
        )
