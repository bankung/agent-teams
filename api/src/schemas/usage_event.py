"""Pydantic schemas for usage_events (Kanban #2354).

`UsageEventCreate` — POST body for `/api/usage/events` (the Mode-A ingest path).
The producer is the Mode-A cost hook/parser (P2, a later task); for P1 the
endpoint is exercised by tests + manual curl. Cost is NOT accepted from the
client — it is computed SERVER-SIDE by the router via `services/cost_tracker`.

`UsageEventRead` — wire shape for the stored row (mirrors the ORM).

`project_id` is NOT on the create body: the row's project is the canonical
`X-Project-Id` header value (the router sets it). This avoids the header/body
mismatch class entirely for this endpoint.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UsageEventCreate(BaseModel):
    """POST body for `/api/usage/events` (Kanban #2354).

    `model` is the only required field. Token counts default to 0. `occurred_at`
    defaults to now() server-side when omitted (left None here so the router can
    apply the DB/server default). `cost_usd` is intentionally absent — the
    server computes it; a client cannot assert a cost.
    """

    model_config = ConfigDict(extra="forbid")

    model: str = Field(
        ..., min_length=1, max_length=255, description="Model identifier (price-card key)."
    )

    input_tokens: int = Field(0, ge=0, le=1_000_000_000)
    output_tokens: int = Field(0, ge=0, le=1_000_000_000)
    cache_read_input_tokens: int = Field(0, ge=0, le=1_000_000_000)
    cache_creation_input_tokens: int = Field(0, ge=0, le=1_000_000_000)

    occurred_at: datetime | None = Field(
        None,
        description="Event's real time (UTC). Omitted → server defaults to now().",
    )

    task_id: int | None = Field(None, ge=1)
    agent_name: str | None = Field(
        None, min_length=1, max_length=128, description="Subagent name; None = Lead/main."
    )
    session_ext_id: str | None = Field(
        None,
        min_length=1,
        max_length=128,
        description="Claude Code session uuid string (not a FK).",
    )
    provider: str = Field("anthropic", min_length=1, max_length=255)

    dedup_key: str | None = Field(
        None,
        min_length=1,
        max_length=256,
        description="Idempotency key. Repeat with the same key (same project) collapses to the existing row.",
    )
    is_estimate: bool = Field(True)
    source: str = Field("mode_a", min_length=1, max_length=32)

    @field_validator("occurred_at")
    @classmethod
    def _clamp_occurred_at(cls, v: datetime | None) -> datetime | None:
        """Bound occurred_at to [now-30d, now+5min] (Kanban #2356, AC2).

        Rejects backdating into closed billing cycles and bounds clock skew.
        None passes through (server applies now() default). A tz-naive value is
        treated as UTC before comparison — never crash on naive input.
        """
        if v is None:
            return v
        # Treat naive datetimes as UTC; never compare naive vs aware.
        cmp = v if v.tzinfo is not None else v.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if cmp < now - timedelta(days=30) or cmp > now + timedelta(minutes=5):
            raise ValueError(
                "occurred_at outside the accepted window [now-30d, now+5min]"
            )
        return v


class UsageEventRead(BaseModel):
    """Wire shape for a stored usage_events row (mirrors the ORM)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    occurred_at: datetime
    project_id: int
    task_id: int | None
    session_ext_id: str | None
    agent_name: str | None
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    cost_usd: Decimal
    is_estimate: bool
    source: str
    dedup_key: str | None
    created_at: datetime
