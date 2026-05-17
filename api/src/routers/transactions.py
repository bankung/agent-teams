"""HTTP routes for the per-project transactions ledger (Kanban #953).

Mounted at `/api/transactions` from main.py. All endpoints require an
`X-Project-Id` header (parity with /api/tasks — Kanban #695). Cross-project
reads / writes / patches return 400 (POST body mismatch) or 404 (GET / PATCH
on a row from a different project).

DELETE is intentionally NOT implemented in this slice — ledger entries are
immutable history; the accountant flow expects audit trails, not soft-delete.
Add later if a corrective-reversal workflow is needed (a `refund` row that
points at the original via `source_ref` is the current workaround).
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_or_404, get_session
from src.models.transaction import Transaction
from src.schemas.transaction import (
    TransactionCreate,
    TransactionRead,
    TransactionUpdate,
)
from src.services.session_project import (
    assert_body_matches_session,
    require_project_id_header,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transactions", tags=["transactions"])


# Mirrors the locked detail for X-Project-Id row-mismatch — but for
# transactions we return 404 (not 400) on PATCH/GET cross-project. Rationale:
# from the wire perspective, a transaction belonging to another project is
# INVISIBLE to this session (parity with the soft-delete "invisible" model).
# Locked by source-text-lock test in test_transactions.py.
_DETAIL_TRANSACTION_NOT_FOUND_TEMPLATE = "Transaction id={txn_id} not found"


@router.get("", response_model=list[TransactionRead])
async def list_transactions(
    session_project_id: int = Depends(require_project_id_header),
    kind: str | None = Query(
        default=None,
        description="Filter by transactions.kind (revenue/cost/expense/refund/transfer).",
    ),
    category: str | None = Query(
        default=None,
        description="Exact-match filter by transactions.category (free-form).",
    ),
    since: datetime | None = Query(
        default=None,
        description="Inclusive lower bound on occurred_at (ISO-8601).",
    ),
    until: datetime | None = Query(
        default=None,
        description="Exclusive upper bound on occurred_at (ISO-8601).",
    ),
    task_id: int | None = Query(
        default=None,
        ge=1,
        description="Filter to transactions linked to this task_id.",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[Transaction]:
    """List transactions scoped to the session-bound project.

    Sort: `occurred_at DESC` (newest first — matches the ledger UI). The
    `(project_id, occurred_at DESC)` index covers this directly.
    """
    stmt = select(Transaction).where(Transaction.project_id == session_project_id)
    if kind is not None:
        stmt = stmt.where(Transaction.kind == kind)
    if category is not None:
        stmt = stmt.where(Transaction.category == category)
    if since is not None:
        stmt = stmt.where(Transaction.occurred_at >= since)
    if until is not None:
        stmt = stmt.where(Transaction.occurred_at < until)
    if task_id is not None:
        stmt = stmt.where(Transaction.task_id == task_id)
    stmt = stmt.order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
    stmt = stmt.limit(limit).offset(offset)
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.post("", response_model=TransactionRead, status_code=201)
async def create_transaction(
    payload: TransactionCreate,
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> Transaction:
    """Create a ledger entry. `project_id` MUST match the X-Project-Id header
    (mirroring TaskCreate). Header is the canonical channel; body is
    defense-in-depth.
    """
    # Header-body cross-check — mirrors POST /api/tasks (Kanban #695).
    assert_body_matches_session(payload.project_id, session_project_id)

    txn = Transaction(
        project_id=payload.project_id,
        amount_minor=payload.amount_minor,
        currency=payload.currency,
        kind=payload.kind,
        category=payload.category,
        occurred_at=payload.occurred_at,
        source=payload.source,
        source_ref=payload.source_ref,
        task_id=payload.task_id,
        notes=payload.notes,
    )
    session.add(txn)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        orig_text = str(exc.orig)
        # Source-text-locked detail strings — pinned by
        # test_post_transaction_detail_strings_pinned_in_router_source.
        if "transactions_project_id_fkey" in orig_text:
            detail = f"project_id {payload.project_id} does not exist"
        elif "transactions_task_id_fkey" in orig_text:
            detail = f"task_id {payload.task_id} does not exist"
        elif "ck_transactions_kind_valid" in orig_text:
            detail = "kind violates ck_transactions_kind_valid"
        else:
            detail = "Transaction creation violates a database constraint"
        raise HTTPException(status_code=400, detail=detail) from exc
    await session.refresh(txn)
    return txn


@router.patch("/{txn_id}", response_model=TransactionRead)
async def update_transaction(
    txn_id: int,
    payload: TransactionUpdate,
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> Transaction:
    """Partial update. `project_id` / `task_id` NOT modifiable post-creation
    (re-assigning a ledger entry across projects would corrupt the audit trail).

    Cross-project PATCH: a transaction belonging to another project surfaces
    as 404 (parity with soft-delete "invisible" semantics). Locked detail
    string pinned by source-text-lock test.
    """
    txn = await get_or_404(
        session,
        Transaction,
        detail=_DETAIL_TRANSACTION_NOT_FOUND_TEMPLATE.format(txn_id=txn_id),
        id=txn_id,
    )
    # Cross-project row → 404 (not 400 — different semantics from tasks; see
    # router docstring).
    if txn.project_id != session_project_id:
        raise HTTPException(
            status_code=404,
            detail=_DETAIL_TRANSACTION_NOT_FOUND_TEMPLATE.format(txn_id=txn_id),
        )

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        # No-op PATCH — return current row without bumping anything.
        return txn

    for field, value in updates.items():
        setattr(txn, field, value)

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        orig_text = str(exc.orig)
        if "ck_transactions_kind_valid" in orig_text:
            detail = "kind violates ck_transactions_kind_valid"
        else:
            detail = "Transaction update violates a database constraint"
        raise HTTPException(status_code=400, detail=detail) from exc

    await session.refresh(txn)
    return txn
