"""Pydantic schemas for the `transactions` table (Kanban #953).

`TransactionCreate.project_id` is REQUIRED on the wire and cross-checked against
the X-Project-Id header in the router (parity with TaskCreate / Kanban #695).
Mismatch → 400.

`amount_minor` is BIGINT minor units (cents / satang) — NEVER use FLOAT for
money. The wire surface accepts the raw integer; the P&L summary converts
minor → major (Decimal) for display.

`kind` is a gated Literal matching the DB CHECK ck_transactions_kind_valid;
the lockstep guard at module bottom catches drift against `TRANSACTION_KINDS`.

Unknown keys are rejected (extra='forbid') on Create/Update — keeps the wire
tight; typo'd fields fail 422 instead of silently persisting.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.models.transaction import TRANSACTION_KINDS

# Wire enum mirrors the DB CHECK + TRANSACTION_KINDS module constant.
# Lockstep guard at module bottom catches drift.
TransactionKindLiteral = Literal["revenue", "cost", "expense", "refund", "transfer"]

# ISO 4217 shape — 3 uppercase letters. We don't validate against the full
# ISO list (changes over time + accountant flows occasionally see historical
# codes); the regex is enough to reject obvious garbage like "$" or "USDollar".
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def _validate_currency(v: str) -> str:
    """Normalize to uppercase + validate ISO 4217 shape."""
    if v is None:
        return v
    s = v.strip().upper()
    if not _CURRENCY_RE.fullmatch(s):
        raise ValueError(
            f"currency must be a 3-letter ISO 4217 code (got {v!r})"
        )
    return s


class TransactionCreate(BaseModel):
    """Request body for POST /api/transactions.

    `project_id` is REQUIRED on the wire — the router cross-checks it against
    the X-Project-Id header (mismatch → 400, mirroring TaskCreate / Kanban #695).
    """

    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(..., ge=1)
    amount_minor: int = Field(..., description="Amount in minor units (cents / satang)")
    currency: str = Field(default="USD", min_length=3, max_length=3)
    kind: TransactionKindLiteral
    category: str | None = Field(default=None, min_length=1, max_length=200)
    occurred_at: datetime
    source: str | None = Field(default=None, min_length=1, max_length=200)
    source_ref: str | None = Field(default=None, min_length=1, max_length=500)
    task_id: int | None = Field(default=None, ge=1)
    notes: str | None = Field(default=None, min_length=1)

    _check_currency = field_validator("currency")(_validate_currency)


class TransactionUpdate(BaseModel):
    """Request body for PATCH /api/transactions/{id} — all fields optional.

    `project_id` and `task_id` are NOT modifiable post-creation (re-assigning
    a ledger entry to a different project would silently corrupt the audit
    trail). Pass an explicit `project_id` key to the PATCH → 422.

    PATCH semantics: `model_dump(exclude_unset=True)` — key absent = no touch;
    explicit value writes. Mirrors TaskUpdate (Kanban #797 / #854).
    """

    model_config = ConfigDict(extra="forbid")

    amount_minor: int | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    kind: TransactionKindLiteral | None = None
    category: str | None = Field(default=None, min_length=1, max_length=200)
    occurred_at: datetime | None = None
    source: str | None = Field(default=None, min_length=1, max_length=200)
    source_ref: str | None = Field(default=None, min_length=1, max_length=500)
    notes: str | None = Field(default=None, min_length=1)

    _check_currency = field_validator("currency")(_validate_currency)


class TransactionRead(BaseModel):
    """Full transaction row as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    amount_minor: int
    currency: str
    kind: str
    category: str | None
    occurred_at: datetime
    source: str | None
    source_ref: str | None
    task_id: int | None
    notes: str | None
    created_at: datetime


# Sanity: TransactionKindLiteral stays in lockstep with TRANSACTION_KINDS.
# Mirrors the TeamCode / TaskRunModeLiteral guards elsewhere in the schemas.
if set(TransactionKindLiteral.__args__) != set(TRANSACTION_KINDS):  # type: ignore[attr-defined]
    raise RuntimeError(
        f"TransactionKindLiteral {TransactionKindLiteral.__args__!r} drifted from "  # type: ignore[attr-defined]
        f"TRANSACTION_KINDS {TRANSACTION_KINDS!r}"
    )
