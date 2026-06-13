"""Mode-A usage-event ingest endpoint (Kanban #2354).

Mounted at `POST /api/usage/events`. Appends one row to the append-only
`usage_events` ledger. Project-scoped via the `X-Project-Id` header (same gate
as the tasks endpoints) — the stored row's `project_id` is the header value,
which is canonical.

COST IS COMPUTED SERVER-SIDE. The client supplies token totals only; the
endpoint resolves the price-card key via `cost_tracker.resolve_pricing_key`
then `cost_tracker.compute_cost`. An unknown model is NOT a 422 — the row is
stored with `cost_usd = 0` and the tokens preserved (partial signal beats no
signal); a warning is logged. A missing `model` IS a 422 (Pydantic).

IDEMPOTENT on `dedup_key` within the same project. A non-NULL `dedup_key` that
already exists for this project returns the EXISTING row with 200 (no duplicate
insert). A NULL `dedup_key` always inserts (Postgres treats NULLs as distinct in
the UNIQUE index). The UNIQUE constraint is composite (project_id, dedup_key), so
the same key string used in a different project inserts cleanly. The collision is
handled both ways: a SELECT-first fast path AND an IntegrityError fallback that
re-reads the winning row (covers a concurrent same-project insert race).

Status codes: 201 on a fresh insert, 200 on an idempotent hit.

NOTE: this is a SEPARATE router from `routers/usage.py` (the cross-project,
header-free `GET /usage/daily` rollup). Both share the `/usage` prefix; the
paths differ (`/daily` vs `/events`) so there is no collision. Keeping the
project-scoped write path in its own module avoids mixing the two header
postures in one file. P2 (hooks/parser) and P3 (rollup/UI) are later tasks.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_session
from src.models.task import Task
from src.models.usage_event import UsageEvent
from src.schemas.usage_event import UsageEventCreate, UsageEventRead
from src.services.cost_tracker import compute_cost, resolve_pricing_key
from src.services.session_project import require_project_id_header

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/usage", tags=["usage-events"])


def _compute_cost_usd(body: UsageEventCreate):
    """Resolve the price-card key + compute cost. Unknown model → 0 (logged)."""
    try:
        _provider, key_model = resolve_pricing_key(body.provider, body.model)
    except ValueError:
        logger.warning(
            "usage_events: no pricing entry for provider=%r model=%r — "
            "storing row with cost_usd=0 (tokens preserved)",
            body.provider,
            body.model,
        )
        return Decimal("0")
    return compute_cost(
        body.provider,
        key_model,
        body.input_tokens,
        body.output_tokens,
        body.cache_read_input_tokens,
        body.cache_creation_input_tokens,
    )


async def _existing_by_dedup_key(
    session: AsyncSession, project_id: int, dedup_key: str
) -> UsageEvent | None:
    """Return the row already stored under (project_id, dedup_key), or None.

    The UNIQUE constraint is composite on (project_id, dedup_key), so this
    SELECT is naturally scoped — a hit always belongs to this project. A genuine
    same-project race falls through to the IntegrityError path, which re-runs
    this SELECT and finds the winning row.
    """
    stmt = select(UsageEvent).where(
        UsageEvent.dedup_key == dedup_key,
        UsageEvent.project_id == project_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


@router.post(
    "/events",
    response_model=UsageEventRead,
    status_code=http_status.HTTP_201_CREATED,
)
async def create_usage_event(
    body: UsageEventCreate,
    response: Response,
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> UsageEvent:
    """Append one usage event; compute cost server-side; idempotent on dedup_key.

    See the module docstring for the full contract. project_id = the
    X-Project-Id header value (canonical).
    """
    # Idempotent fast path: a known dedup_key returns the existing row (200).
    if body.dedup_key is not None:
        existing = await _existing_by_dedup_key(
            session, session_project_id, body.dedup_key
        )
        if existing is not None:
            response.status_code = http_status.HTTP_200_OK
            return existing

    # Validate task_id belongs to this project (review fix m1, 2026-06-13).
    # Protects per-task cost attribution: a task_id from another project would
    # silently attribute cost to the wrong task.
    if body.task_id is not None:
        task = await session.get(Task, body.task_id)
        if task is None or task.project_id != session_project_id:
            raise HTTPException(
                status_code=400,
                detail="task_id does not belong to this project",
            )

    cost_usd = _compute_cost_usd(body)

    event = UsageEvent(
        project_id=session_project_id,
        task_id=body.task_id,
        session_ext_id=body.session_ext_id,
        agent_name=body.agent_name,
        provider=body.provider,
        model=body.model,
        input_tokens=body.input_tokens,
        output_tokens=body.output_tokens,
        cache_read_input_tokens=body.cache_read_input_tokens,
        cache_creation_input_tokens=body.cache_creation_input_tokens,
        cost_usd=cost_usd,
        is_estimate=body.is_estimate,
        source=body.source,
        dedup_key=body.dedup_key,
    )
    # occurred_at omitted → the server_default (now()) applies on flush.
    if body.occurred_at is not None:
        event.occurred_at = body.occurred_at

    session.add(event)
    try:
        await session.commit()
    except IntegrityError:
        # Concurrent same-project insert won the (project_id, dedup_key) race.
        # Roll back and return the row that landed.
        await session.rollback()
        if body.dedup_key is not None:
            existing = await _existing_by_dedup_key(
                session, session_project_id, body.dedup_key
            )
            if existing is not None:
                response.status_code = http_status.HTTP_200_OK
                return existing
        raise

    await session.refresh(event)
    return event
