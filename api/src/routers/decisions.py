"""Retro decisions feed — Kanban #1007 (AC5).

Mounted at `/api/decisions`. Returns past decisions (interaction_kind='decision'
tasks whose chosen_id is set) across a project, ordered by chosen_at DESC.

Project scoping follows the `X-Project-Id` header convention (same as `/api/tasks`).
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import RecordStatus, TaskInteractionKind
from src.db import get_session
from src.models.task import Task
from src.schemas.task import DecisionListItem, OptionItem
from src.services.session_project import require_project_id_header

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/decisions", tags=["decisions"])

# Kanban #1007 AC5: default pagination cap mirrors the
# `fastapi/routing.md` list-endpoint standard (default=50, max=500).
# The caller may override with ?limit=N (1..500). `chosen_at DESC`
# ensures the most-recent decision surfaces first.
_DECISIONS_DEFAULT_LIMIT = 100


@router.get("", response_model=list[DecisionListItem])
async def list_decisions(
    session_project_id: int = Depends(require_project_id_header),
    since: datetime | None = Query(
        default=None,
        description=(
            "Optional ISO-8601 datetime filter. When set, only decisions with "
            "chosen_at >= since are returned. Timezone-aware values are recommended; "
            "naive datetimes are treated as UTC by the DB."
        ),
    ),
    limit: int = Query(
        default=_DECISIONS_DEFAULT_LIMIT,
        ge=1,
        le=500,
        description="Maximum number of results (default 100, max 500).",
    ),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[DecisionListItem]:
    """Kanban #1007 (AC5) — retro decisions feed.

    Returns tasks where:
      - `interaction_kind = 'decision'`
      - `question_payload->>'chosen_id' IS NOT NULL` (i.e., a decision was made)
      - `project_id` matches the session-bound project (`X-Project-Id` header)
      - `status = 1` (not soft-deleted)

    Optional `?since=<datetime>` filters by `chosen_at >= since`. Results are
    ordered `chosen_at DESC` (most-recent decision first). Paginate with
    `?limit=N&offset=M`.

    The response flattens the JSONB `question_payload` into typed fields for
    easy consumption — callers do not need to unpack the raw JSONB blob.
    """
    stmt = (
        select(Task)
        .where(
            Task.project_id == session_project_id,
            Task.status == RecordStatus.ACTIVE,
            Task.interaction_kind == TaskInteractionKind.DECISION,
            # chosen_at IS NOT NULL — only tasks that have been decided.
            # The JSONB column stores chosen_at as an ISO string in the
            # question_payload dict. We filter at the application layer
            # (after fetching candidates) to avoid a JSONB-cast PG expression
            # that would be harder to index. Limit=100 default keeps the
            # candidate set small in practice.
        )
        .order_by(Task.id.desc())  # fallback stable order; refined below
        .limit(limit + offset)  # over-fetch so we can slice after app-filter
    )

    # Fetch the candidate rows; filter + sort at the application layer.
    # This is acceptable for AC5 because:
    #   (a) Decision tasks are rare (human-gated one-offs per task lifecycle).
    #   (b) limit cap is 500 — candidate set stays bounded.
    #   (c) JSONB-cast ORDER BY chosen_at is PG-side but needs a functional
    #       index; the measured-first index policy defers that to a follow-up.
    rows = list((await session.execute(stmt)).scalars().all())

    items: list[DecisionListItem] = []
    for row in rows:
        payload = row.question_payload or {}
        chosen_id = payload.get("chosen_id")
        if not chosen_id:
            # Skip tasks where no decision has been recorded yet.
            continue

        chosen_at_raw = payload.get("chosen_at")
        try:
            chosen_at: datetime | None = (
                datetime.fromisoformat(chosen_at_raw) if chosen_at_raw else None
            )
        except (ValueError, TypeError):
            chosen_at = None

        # Apply `since` filter at application layer.
        if since is not None and chosen_at is not None and chosen_at < since:
            continue

        # Build typed OptionItem list from the raw JSONB options array.
        raw_options = payload.get("options") or []
        typed_options: list[OptionItem] = []
        for opt in raw_options:
            if isinstance(opt, dict):
                try:
                    typed_options.append(OptionItem(**opt))
                except Exception:
                    # Gracefully skip malformed option entries rather than 500ing.
                    logger.warning("list_decisions: skipped malformed option entry %r", opt)
                    pass

        items.append(
            DecisionListItem(
                task_id=row.id,
                title=row.title,
                options=typed_options,
                chosen_id=chosen_id,
                rationale=payload.get("rationale"),
                chosen_at=chosen_at,
                chosen_by=payload.get("chosen_by"),
            )
        )

    # Sort by chosen_at DESC (most-recent first). None-chosen_at rows sort last.
    items.sort(
        key=lambda d: d.chosen_at or datetime.min.replace(tzinfo=None),
        reverse=True,
    )

    # Apply offset/limit after filtering (since we over-fetched above).
    return items[offset : offset + limit]
