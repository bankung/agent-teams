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

from fastapi import APIRouter, Depends, HTTPException, Request
from itsdangerous import BadData
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_session
from src.middleware.rate_limit import limiter
from src.models.project import Project
from src.models.task import Task
from src.schemas.notification import NotificationKind
from src.services.digest_template import verify_optout_token
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

    `event_kind` (Kanban #1937) is the push-subscription filter key — when set,
    the router additionally queries `push_subscriptions` for active rows with
    kinds_enabled[event_kind]=true and appends them as web_push targets.
    Callers that pre-date #1937 omit this field; the router skips push-
    subscription resolution (backwards-compatible, same as event_kind=None
    in the service layer).  Only meaningful when `kind="web_push"`.

    `extra='forbid'` mirrors the kill/grant-consent deliberate-action posture
    — typo'd keys fail 422 instead of silently dropping.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: int = Field(ge=1)
    payload: dict = Field(default_factory=dict)
    kind: NotificationKind
    event_kind: str | None = Field(
        default=None,
        description=(
            "Optional push-subscription filter key (Kanban #1937). "
            "When set and kind='web_push', the router queries push_subscriptions "
            "for rows with kinds_enabled[event_kind]=true and appends them as targets. "
            "Valid values mirror EventKind in notification_router.py: "
            "hitl_needed, task_done, task_failed, budget_warn, session_waiting."
        ),
    )


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


@router.get("/digest-optout")
@limiter.limit("6/minute")
async def digest_optout(
    request: Request,  # required by slowapi key_func
    token: str = "",
    session: AsyncSession = Depends(get_session),
) -> str:
    """Verify a signed opt-out token and flip digest_email_enabled=False for the project.

    Kanban #1437. Token is a URLSafeTimedSerializer payload produced by
    `make_optout_token` in digest_template.py. Expiry: 90 days.

    Returns plain-text 200 on success (idempotent — already-false is OK).
    Returns 400 with reason on invalid/expired token.
    """
    if not token:
        raise HTTPException(status_code=400, detail="missing_token")

    try:
        data = verify_optout_token(token)
    except BadData as exc:
        # SignatureExpired and BadSignature are both BadData subclasses.
        reason = "expired_token" if "expired" in type(exc).__name__.lower() else "invalid_token"
        logger.warning("digest_optout: bad token reason=%s exc=%r", reason, exc)
        raise HTTPException(status_code=400, detail=reason) from exc

    project_id: int = int(data.get("pid", 0))
    if not project_id:
        raise HTTPException(status_code=400, detail="invalid_token_payload")

    project = (
        await session.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail=f"project {project_id} not found")

    # Merge into project.config — preserve existing keys; flip digest_email_enabled.
    # Stored in projects.config (free JSONB dict) rather than notification_targets
    # (a typed list of push delivery targets — incompatible shape).
    existing_config: dict = project.config or {}
    if existing_config.get("digest_email_enabled") is not False:
        project.config = {**existing_config, "digest_email_enabled": False}
        await session.commit()
        logger.info(
            "digest_optout: project %d opted out of digest emails", project_id
        )
    else:
        logger.info(
            "digest_optout: project %d already opted out (idempotent)", project_id
        )

    return (
        "You've been opted out of agent-teams digest emails. "
        f"To re-enable: PATCH /api/projects/{project_id} with "
        'config={"digest_email_enabled": true}, or contact admin.'
    )


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
        event_kind=body.event_kind,
    )
    return DeliveryResponse(**result)
