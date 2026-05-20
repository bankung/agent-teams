"""Web Push subscription CRUD router (Kanban #955.A).

Mounted at `/api/push` from main.py. Three endpoints:

  - POST /api/push/subscribe — register (or resurrect) a browser subscription.
    Idempotent on `endpoint` per D5: re-subscribing the same endpoint UPDATES
    the existing row (refresh p256dh + auth + kinds_enabled + flip status to
    active). A soft-deleted row re-emerges via the same path.
  - DELETE /api/push/subscribe/{id} — soft-delete (status=0). Idempotent —
    re-DELETE returns 204 without bumping updated_at.
  - GET /api/push/subscriptions — list active subscriptions. `?include_deleted=true`
    opts in to soft-deleted rows (debug surface, parity with handoff_templates).

X-Project-Id header is NOT required — subscriptions are operator-scoped, not
project-scoped (a single browser receives notifications across all the
projects it's interested in; per-subscription `project_id` is a filter
hint, not a session key). The resolver in 955.B's event hooks consumes
`project_id` to fan out a specific task's notifications.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from src.constants import RecordStatus
from src.db import get_or_404, get_session
from src.models.push_subscription import PushSubscription
from src.schemas.push_subscription import (
    KindsEnabled,
    PushSubscribeRequest,
    PushSubscriptionRead,
    PushSubscriptionUpdate,
)

router = APIRouter(prefix="/push", tags=["push"])

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# POST /api/push/subscribe — register or resurrect
# ---------------------------------------------------------------------------


@router.post(
    "/subscribe",
    response_model=PushSubscriptionRead,
    status_code=http_status.HTTP_201_CREATED,
)
async def subscribe_push(
    payload: PushSubscribeRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> PushSubscription:
    """Register a new browser subscription, OR refresh an existing one by
    matching on `endpoint` (D5 — idempotent re-subscribe).

    The Web Push `endpoint` URL is the unique identity of a browser+device
    pair. Browsers re-emit the same endpoint across reloads; reissuing
    p256dh/auth (e.g. on a key rotation) is allowed and updates the row.

    Status semantics:
      - New endpoint → INSERT row with status=1, return 201.
      - Existing endpoint, status=1 → UPDATE row in place, return 200.
      - Existing endpoint, status=0 → UPDATE row, flip status=1 (resurrect),
        return 200.

    Errors:
      - 400 — `project_id` references a missing/deleted project (FK violation).
      - 422 — Pydantic validation (default).
    """
    kinds_dict = (
        payload.kinds_enabled.model_dump()
        if payload.kinds_enabled is not None
        else KindsEnabled().model_dump()
    )

    # Lookup by endpoint (the natural unique key). If found, UPDATE; else
    # INSERT. Wrapping the lookup + write in the same session keeps the
    # whole thing atomic — a concurrent INSERT collision is rare in practice
    # (operator manually clicking "subscribe" twice in <1s) but we still
    # catch IntegrityError below as defense.
    existing = (
        await session.execute(
            select(PushSubscription).where(
                PushSubscription.endpoint == payload.endpoint
            )
        )
    ).scalar_one_or_none()

    is_new = existing is None

    if existing is not None:
        # Refresh path — bump p256dh/auth/user_agent/project_id/kinds_enabled
        # + resurrect if soft-deleted.
        existing.p256dh = payload.keys.p256dh
        existing.auth = payload.keys.auth
        existing.kinds_enabled = kinds_dict
        existing.user_agent = payload.user_agent
        existing.project_id = payload.project_id
        existing.status = RecordStatus.ACTIVE
        existing.updated_at = func.now()
        sub = existing
    else:
        sub = PushSubscription(
            endpoint=payload.endpoint,
            p256dh=payload.keys.p256dh,
            auth=payload.keys.auth,
            kinds_enabled=kinds_dict,
            user_agent=payload.user_agent,
            project_id=payload.project_id,
            status=RecordStatus.ACTIVE,
        )
        session.add(sub)

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        orig_text = str(exc.orig)
        if "push_subscriptions_project_id_fkey" in orig_text:
            raise HTTPException(
                status_code=400,
                detail=f"project_id {payload.project_id} does not exist",
            ) from exc
        if "ux_push_subscriptions_endpoint" in orig_text:
            # Concurrent-INSERT race — retry the UPDATE branch. This is a
            # safety net; the upfront SELECT should have caught it.
            raise HTTPException(
                status_code=409,
                detail="endpoint conflict — retry",
            ) from exc
        raise HTTPException(
            status_code=400,
            detail="PushSubscription write violates a database constraint",
        ) from exc

    await session.refresh(sub)

    # Idempotent re-subscribe returns 200 (per D5 — "same endpoint re-subscribed
    # → update existing row"). A net-new row gets 201.
    if not is_new:
        response.status_code = http_status.HTTP_200_OK
    return sub


# ---------------------------------------------------------------------------
# DELETE /api/push/subscribe/{id} — soft-delete
# ---------------------------------------------------------------------------


@router.delete(
    "/subscribe/{subscription_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
async def unsubscribe_push(
    subscription_id: int,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Soft-delete a subscription. Idempotent: re-DELETE returns 204 without
    bumping `updated_at`. Returns 404 only if the id was never created.

    Mirrors the soft-delete pattern of `handoff_templates` / `tasks`.
    """
    sub = await get_or_404(
        session,
        PushSubscription,
        detail=f"PushSubscription id={subscription_id} not found",
        id=subscription_id,
    )

    if sub.status == RecordStatus.ACTIVE:
        sub.status = RecordStatus.DELETED
        sub.updated_at = func.now()
        await session.commit()

    return Response(status_code=http_status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# PATCH /api/push/subscribe/{id} — partial update (Kanban #955.B)
# ---------------------------------------------------------------------------


@router.patch(
    "/subscribe/{subscription_id}",
    response_model=PushSubscriptionRead,
    status_code=http_status.HTTP_200_OK,
)
async def update_push_subscription(
    subscription_id: int,
    payload: PushSubscriptionUpdate,
    session: AsyncSession = Depends(get_session),
) -> PushSubscription:
    """Partially update a push subscription.

    Typical use: the FE settings UI toggles individual `kinds_enabled` flags
    (e.g. turn off task_done push notifications for this browser). The caller
    sends only the fields it wants to change; omitted fields are unchanged
    (`exclude_unset=True` PATCH semantics).

    Returns the full updated row on 200. Returns 404 when the subscription_id
    was never created (soft-deleted rows are still patchable — the FE may want
    to re-enable a previously-deleted subscription's flags before re-subscribing
    via POST).

    Errors:
      - 404 — subscription_id not found at all.
      - 422 — Pydantic validation (invalid KindsEnabled shape, etc.).
    """
    sub = await get_or_404(
        session,
        PushSubscription,
        detail=f"PushSubscription id={subscription_id} not found",
        id=subscription_id,
    )

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        # No-op PATCH — return the current row unchanged (mirrors tasks PATCH
        # no-op behaviour: no write, no updated_at bump, 200 with current data).
        await session.refresh(sub)
        return sub

    # Apply updates. `kinds_enabled` is a KindsEnabled instance when supplied;
    # dump to plain dict for the JSONB column (parity with the POST handler).
    if "kinds_enabled" in updates and payload.kinds_enabled is not None:
        updates["kinds_enabled"] = payload.kinds_enabled.model_dump()

    for field, value in updates.items():
        setattr(sub, field, value)

    sub.updated_at = func.now()

    try:
        await session.commit()
    except Exception:  # pragma: no cover — only FK violation is realistic
        await session.rollback()
        raise

    await session.refresh(sub)
    return sub


# ---------------------------------------------------------------------------
# GET /api/push/subscriptions — list
# ---------------------------------------------------------------------------


@router.get(
    "/subscriptions",
    response_model=list[PushSubscriptionRead],
)
async def list_push_subscriptions(
    include_deleted: bool = Query(
        default=False,
        description="If true, include soft-deleted (status=0) rows. Debug-only.",
    ),
    project_id: int | None = Query(
        default=None,
        ge=1,
        description=(
            "Filter to subscriptions matching this project_id OR project_id "
            "IS NULL (NULL = all-projects subscriptions). Omit to list ALL "
            "subscriptions regardless of scope."
        ),
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[PushSubscription]:
    """List subscriptions.

    Default filter: status=1 (active). `?include_deleted=true` opts in to
    soft-deleted rows (debug surface).

    Ordering: `id ASC` for stable pagination (parity with handoff_templates).
    """
    stmt = select(PushSubscription)
    if not include_deleted:
        stmt = stmt.where(PushSubscription.status == RecordStatus.ACTIVE)
    if project_id is not None:
        from sqlalchemy import or_

        stmt = stmt.where(
            or_(
                PushSubscription.project_id.is_(None),
                PushSubscription.project_id == project_id,
            )
        )
    stmt = stmt.order_by(PushSubscription.id.asc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return list(result.scalars().all())
