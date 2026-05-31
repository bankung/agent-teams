"""Wire schemas for email tools (Kanban #1604 Gmail; #1608 will append Outlook).

Naming convention: each provider's schemas are prefixed with the provider name
(`Gmail*`, `Outlook*`) so the two parallel-developed namespaces stay disjoint.

Shared base classes live above the provider-specific blocks.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

# FIX-3 (#1609): path-injection guard for message_ids. Graph IDs are base64url;
# Gmail IDs are hex. Both fit within [A-Za-z0-9_\-=+]. The slash character is
# the primary injection vector (rewriting the Graph URL path).
_MID_ALLOWED = re.compile(r"^[A-Za-z0-9_\-=+]+$")


# ---------------------------------------------------------------------------
# Shared base classes (used by Gmail + Outlook)
# ---------------------------------------------------------------------------


class AuthStatusResponse(BaseModel):
    """Provider-agnostic auth status. Mirrors `token_store.status()` output."""

    authenticated: bool
    email: str | None = None
    expires_at: str | None = None


class UsageResponse(BaseModel):
    """Daily usage counter — same shape for every provider since the cap is
    shared (`EMAIL_TOOLS_DAILY_UNITS_CAP`).
    """

    date: str
    units_consumed: int
    cap: int
    remaining: int


# ---------------------------------------------------------------------------
# Gmail-specific (Kanban #1604)
# ---------------------------------------------------------------------------


class GmailAuthStartResponse(BaseModel):
    auth_url: str


class GmailCallbackResponse(BaseModel):
    """Returned by the OAuth callback (JSON, not a redirect, per Karpathy cut)."""

    project_id: int
    authenticated: bool
    email: str | None = None


class GmailTrashRequest(BaseModel):
    """Trash by Gmail search `query` OR explicit `message_ids` list (XOR).

    Exactly one of (query, message_ids) must be set — enforced by a model
    validator so the wire contract is unambiguous.
    """

    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(
        default=None,
        min_length=1,
        max_length=2000,
        description="Gmail search query — e.g. 'from:foo@bar.com older_than:30d'.",
    )
    message_ids: list[str] | None = Field(
        default=None,
        description="Explicit Gmail message id list (XOR with `query`).",
    )

    @model_validator(mode="after")
    def _exactly_one(self) -> "GmailTrashRequest":
        has_q = self.query is not None and self.query.strip() != ""
        has_ids = self.message_ids is not None and len(self.message_ids) > 0
        if has_q == has_ids:
            raise ValueError(
                "Provide exactly one of `query` or `message_ids` (not both, not neither)."
            )
        if has_ids:
            # Bound message_ids length so a 10k-id payload can't slip past the
            # bulk-threshold gate via length-game. 1000 is a hard ceiling; the
            # bulk threshold check (default 100) is the soft refusal.
            if self.message_ids is None:
                raise ValueError("message_ids required")
            if len(self.message_ids) > 1000:
                raise ValueError("message_ids list cannot exceed 1000 entries per call.")
            # Each id is a Gmail message id — short ASCII; bound length to
            # refuse obvious garbage early.
            for mid in self.message_ids:
                if not isinstance(mid, str) or not (1 <= len(mid) <= 64):
                    raise ValueError("each message_id must be a non-empty string <=64 chars.")
            # FIX-3 (#1609): character allowlist — Gmail ids are hex; disallow
            # slash and other path-traversal characters.
            for mid in self.message_ids:
                if not _MID_ALLOWED.fullmatch(mid):
                    raise ValueError(
                        "message_ids contain disallowed characters; allowed: A-Z a-z 0-9 _ - = +"
                    )
        return self


class GmailTrashResponse(BaseModel):
    """Result of a trash call. Reports trashed ids + any per-id errors."""

    trashed_count: int
    trashed_ids: list[str]
    errors: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# #1608 OUTLOOK SCHEMAS BELOW — append-only zone for parallel dev coordination
# ---------------------------------------------------------------------------


class OutlookAuthStartResponse(BaseModel):
    """Auth-start response — mirrors Gmail. Returned by POST /auth/outlook/start."""

    auth_url: str


class OutlookCallbackResponse(BaseModel):
    """Returned by the OAuth callback (JSON, not a redirect, per Karpathy cut)."""

    project_id: int
    authenticated: bool
    email: str | None = None


class OutlookTrashRequest(BaseModel):
    """Trash by Outlook search `query` OR explicit `message_ids` list (XOR).

    Outlook query is a Microsoft Graph `$search` clause (a KQL-ish string) —
    NOT identical to Gmail's syntax. Operator is expected to know the format
    (we don't translate). Example: `from:foo@bar.com AND received>=2025-01-01`.

    NOTE: `query` mode IS implemented via Microsoft Graph `$search` (KQL) —
    shipped in #1711 (mirrors the Gmail query flow). Before the query string
    hits Graph it is KQL-quote-escaped and URL-encoded (#1721). The `$search`
    KQL syntax is NOT identical to Gmail's; the operator supplies the correct
    format (we do not translate between the two).
    """

    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(
        default=None,
        min_length=1,
        max_length=2000,
        description=(
            "Microsoft Graph $search KQL query — implemented (#1711). "
            "Value is KQL-quote-escaped + URL-encoded before hitting Graph (#1721). "
            "Syntax is NOT identical to Gmail. XOR with message_ids."
        ),
    )
    message_ids: list[str] | None = Field(
        default=None,
        description="Explicit Outlook/Graph message id list (XOR with `query`).",
    )

    @model_validator(mode="after")
    def _exactly_one(self) -> "OutlookTrashRequest":
        has_q = self.query is not None and self.query.strip() != ""
        has_ids = self.message_ids is not None and len(self.message_ids) > 0
        if has_q == has_ids:
            raise ValueError(
                "Provide exactly one of `query` or `message_ids` (not both, not neither)."
            )
        if has_ids:
            # Match Gmail's hard ceiling on list length; bulk threshold gate
            # (default 100) is the soft refusal.
            if self.message_ids is None:
                raise ValueError("message_ids required")
            if len(self.message_ids) > 1000:
                raise ValueError("message_ids list cannot exceed 1000 entries per call.")
            # Graph message ids are long base64-ish strings — bound to 512 chars
            # to refuse obvious garbage early. (Empirical Graph ids are ~150 chars.)
            for mid in self.message_ids:
                if not isinstance(mid, str) or not (1 <= len(mid) <= 512):
                    raise ValueError("each message_id must be a non-empty string <=512 chars.")
            # FIX-3 (#1609): character allowlist — Graph ids are base64url; disallow
            # slash and other path-traversal characters (same alphabet as Gmail).
            for mid in self.message_ids:
                if not _MID_ALLOWED.fullmatch(mid):
                    raise ValueError(
                        "message_ids contain disallowed characters; allowed: A-Z a-z 0-9 _ - = +"
                    )
        return self


class OutlookTrashResponse(BaseModel):
    """Result of a move-to-Deleted-Items call. Reports trashed ids + per-id errors."""

    trashed_count: int
    trashed_ids: list[str]
    errors: list[dict[str, Any]] = Field(default_factory=list)
