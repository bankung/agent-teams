"""HTTP routes for push notification delivery (Kanban #1224).

Mounted at `/api/notifications` from main.py.

Single endpoint v1: POST /api/notifications/deliver. Used by:
  - HITL halt trigger (when an autorun task halts with halt_reason)
  - daily-digest cron (rolls up open work + posts summary)
  - kill-switch confirm (operator post-action acknowledgment)

The endpoint requires `X-Project-Id` (existing session-key convention) — the
header is validated to match the task's project_id, enforcing the "session
bound to project" invariant. AP1 anti-pattern from #1220: platform-kind is
metadata ON a NotificationTarget, NOT part of the session key. Session
remains `project_id`-bound.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_session
from src.models.task import Task
from src.schemas.notification import NotificationKind
from src.services.notification_router import deliver
from src.services.session_project import (
    assert_task_belongs_to_session,
    require_project_id_header,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])


class DeliveryRequest(BaseModel):
    """Request body for POST /api/notifications/deliver (Kanban #1224).

    `kind` constrains the adapter dispatch — only NotificationTarget entries
    matching this kind are attempted. v1 = telegram only; widens as adapters
    land. `payload` is a free-form dict the adapter serializes to text
    (Telegram: `<key>: <value>` lines).

    `extra='forbid'` mirrors the kill/grant-consent deliberate-action posture
    — typo'd keys fail 422 instead of silently dropping.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: int = Field(ge=1)
    payload: dict = Field(default_factory=dict)
    kind: NotificationKind


class DeliveryAttempt(BaseModel):
    """One element in the response `attempts` list. Value-tolerant on the
    target/path fields so the wire contract is stable as adapters add extras."""

    model_config = ConfigDict(extra="allow")

    target: dict | None = None
    ok: bool
    detail: str
    priority: int | None = None


class DeliveryResponse(BaseModel):
    """Response for POST /api/notifications/deliver.

    `attempts` is ordered first-to-last by attempt-time. The FIRST entry with
    `ok=True` (if any) is the successful delivery — subsequent entries are
    omitted (we break the loop on first success). If no target succeeds, the
    LAST entry is the local-file fallback row.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: int
    attempts: list[DeliveryAttempt]


@router.post("/deliver", response_model=DeliveryResponse)
async def deliver_notification(
    body: DeliveryRequest,
    session: AsyncSession = Depends(get_session),
    session_project_id: int = Depends(require_project_id_header),
) -> DeliveryResponse:
    """Resolve target list + attempt push delivery + write audit rows.

    Resolution priority (per AC3):
      task override > project default > local-file fallback.

    Audit (per AC7): every attempt (including fallback) appends a row to
    `tasks_history` with operation='N'.
    """
    task = (
        await session.execute(select(Task).where(Task.id == body.task_id))
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail=f"task {body.task_id} not found")

    # Enforce the X-Project-Id session-key convention — same as the tasks
    # router. A mismatch surfaces the compaction-induced project-context-loss
    # gate per the session_project module's contract.
    assert_task_belongs_to_session(
        task_id=body.task_id,
        task_project_id=task.project_id,
        session_project_id=session_project_id,
    )

    result = await deliver(
        task_id=body.task_id,
        payload=body.payload,
        kind=body.kind,
        session=session,
    )
    return DeliveryResponse(**result)
