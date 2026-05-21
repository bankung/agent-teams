"""Pydantic schemas for the email-ingest webhook (Kanban #1327 M4a).

Mailgun-shape inbound JSON — the email forwarding service (Cloudflare Email
Routing + Worker by default; Mailgun Routes / SendGrid Inbound Parse as
alternates documented in ``context/standards/integrations/email-ingest-setup.md``)
transforms the raw RFC822 + DKIM verdict into this shape and POSTs it to
``/api/ingest/email`` with the shared-secret header.

``extra='allow'`` on both classes — the Cloudflare Worker template may add
DKIM verdict, SPF result, ARC chain, custom headers, etc. We don't bind on
them today but we want forward-compat without a 422.

Note the ``from`` Pydantic alias: the JSON wire key is the Python keyword
``from``, so the field is exposed as ``from_address`` via ``alias='from'``
+ ``populate_by_name=True`` (matches the credentials schema pattern).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EmailAttachment(BaseModel):
    """One attachment in a Mailgun-shape email payload.

    The ``content_base64`` field carries the base64-encoded file body. The
    router re-decodes + RE-COMPUTES size from the decoded bytes (the
    ``size_bytes`` field is informational; never trusted — see #1327 section 4
    "NEVER trust the size_bytes field").
    """

    model_config = ConfigDict(extra="allow")

    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=0)
    content_base64: str = Field(min_length=1)


class EmailIngestRequest(BaseModel):
    """Mailgun-shape inbound email payload (Kanban #1327).

    Required: ``from`` (mapped to ``from_address``), ``to``, ``subject``.
    Optional: body_text / body_html / timestamp / message_id / attachments /
    cc / bcc — plus arbitrary extra fields the forwarder may add.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    # Wire field is "from" (Python keyword) → ``from_address`` on the model.
    # ``populate_by_name=True`` keeps either name accepted for ergonomics.
    from_address: str = Field(alias="from", min_length=1, max_length=500)
    to: str = Field(min_length=1, max_length=500)
    # Subject capped at 5000 (Mailgun's limit) so attacker-controlled fluff is
    # bounded at the API boundary. The router truncates to 200 chars when
    # building the task title.
    subject: str = Field(min_length=0, max_length=5_000)
    body_text: str | None = Field(default=None, max_length=200_000)
    body_html: str | None = Field(default=None, max_length=400_000)
    timestamp: datetime | int | None = None
    message_id: str | None = Field(default=None, max_length=998)  # RFC 5322 line limit
    attachments: list[EmailAttachment] = Field(default_factory=list)
    cc: str | None = Field(default=None, max_length=2_000)
    bcc: str | None = Field(default=None, max_length=2_000)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _coerce_timestamp(cls, v: Any) -> Any:
        """Accept int (unix seconds) OR ISO-8601 string OR datetime.

        Mailgun emits a unix-seconds integer; SendGrid / forwarders may emit
        an ISO string; the Pydantic-native path is datetime. We normalize all
        three so the field is ``datetime | None`` downstream.
        """
        if v is None or isinstance(v, datetime):
            return v
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(int(v), tz=timezone.utc)
        if isinstance(v, str):
            # Cloudflare Worker may emit ISO with 'Z' suffix; fromisoformat in
            # 3.11+ accepts that, but defensively swap 'Z' -> '+00:00' first.
            try:
                return datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                # Stringified unix seconds is a legal Mailgun variant.
                try:
                    return datetime.fromtimestamp(int(v), tz=timezone.utc)
                except (ValueError, OverflowError):
                    return None
        return v


class EmailIngestResponse(BaseModel):
    """Response shape for POST /api/ingest/email.

    ``attachment_count`` reports how many attachments were ACCEPTED (decoded +
    written). Oversized attachments are skipped (not in this count) but flagged
    in the task description for follow-up triage.
    """

    received: bool
    task_id: int
    project_id: int
    attachment_count: int
