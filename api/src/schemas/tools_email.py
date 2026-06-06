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
# Gmail — Tier-1 modify actions (Kanban #1585: mark read/unread, archive, draft)
# ---------------------------------------------------------------------------
#
# These map to the `modify` EmailTier (OPEN — Layer-0 role-gated + audited, no
# operator-proof). Tier-1 label mutations are recoverable; only `trash`/delete
# (Tier-2) and the send/reply tiers carry an operator-proof.


def _validate_message_ids(message_ids: list[str]) -> list[str]:
    """Shared bound + allowlist check for an explicit Gmail message-id list.

    Mirrors `GmailTrashRequest._exactly_one`'s id rules (<=1000 entries, each a
    non-empty ASCII id <=64 chars, character-allowlisted) so every id-bearing
    Gmail endpoint applies the SAME boundary guard. Raises ValueError on any
    violation (Pydantic surfaces it as a 422).
    """
    if not isinstance(message_ids, list) or len(message_ids) == 0:
        raise ValueError("message_ids must be a non-empty list.")
    if len(message_ids) > 1000:
        raise ValueError("message_ids list cannot exceed 1000 entries per call.")
    for mid in message_ids:
        if not isinstance(mid, str) or not (1 <= len(mid) <= 64):
            raise ValueError("each message_id must be a non-empty string <=64 chars.")
        if not _MID_ALLOWED.fullmatch(mid):
            raise ValueError(
                "message_ids contain disallowed characters; allowed: A-Z a-z 0-9 _ - = +"
            )
    return message_ids


class GmailMarkRequest(BaseModel):
    """Mark Gmail messages read/unread (`modify` tier).

    `read=True`  -> remove the UNREAD label (mark read).
    `read=False` -> add the UNREAD label (mark unread).
    """

    model_config = ConfigDict(extra="forbid")

    message_ids: list[str] = Field(
        ..., description="Explicit Gmail message id list to mark."
    )
    read: bool = Field(
        ..., description="True = mark read (remove UNREAD); False = mark unread (add UNREAD)."
    )

    @model_validator(mode="after")
    def _check_ids(self) -> "GmailMarkRequest":
        _validate_message_ids(self.message_ids)
        return self


class GmailArchiveRequest(BaseModel):
    """Archive Gmail messages — remove the INBOX label (`modify` tier)."""

    model_config = ConfigDict(extra="forbid")

    message_ids: list[str] = Field(
        ..., description="Explicit Gmail message id list to archive (remove INBOX)."
    )

    @model_validator(mode="after")
    def _check_ids(self) -> "GmailArchiveRequest":
        _validate_message_ids(self.message_ids)
        return self


class GmailModifyResponse(BaseModel):
    """Result of a mark/archive (label-modify) call. Reports modified ids + per-id errors."""

    modified_count: int
    modified_ids: list[str]
    errors: list[dict[str, Any]] = Field(default_factory=list)


class GmailDraftRequest(BaseModel):
    """Create a Gmail DRAFT (no send) — `modify` tier.

    A draft is a recoverable Tier-1 mutation: it lives in the Drafts folder
    until the operator explicitly sends it (a `send_internal`/`external_send`
    action, which carry operator-proof). Creating the draft itself does NOT.
    """

    model_config = ConfigDict(extra="forbid")

    to: str = Field(
        ..., min_length=1, max_length=998,
        description="Recipient address line (RFC-2822 'To'). Operator-supplied; not validated as a strict addr-spec.",
    )
    subject: str = Field(
        default="", max_length=998, description="Draft subject line."
    )
    body: str = Field(
        default="", max_length=100_000, description="Draft plain-text body."
    )


class GmailDraftResponse(BaseModel):
    """Result of a save-draft call. Reports the created Gmail draft id + message id."""

    draft_id: str
    message_id: str | None = None


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


# ---------------------------------------------------------------------------
# Outlook — Tier-1 modify actions (Kanban #1917: mark read/unread, archive, draft)
# ---------------------------------------------------------------------------
#
# Mirrors the Gmail Tier-1 schema block above. Gate composition is identical:
# Layer-0 role grant + modify tier (OPEN) + daily-cap. Provider = "outlook".


def _validate_outlook_message_ids(message_ids: list[str]) -> list[str]:
    """Bound + allowlist check for an explicit Outlook/Graph message-id list.

    Mirrors `_validate_message_ids` for Gmail but uses the Outlook 512-char
    bound (Graph ids are longer than Gmail hex ids, ~150 chars empirically).
    Raises ValueError on any violation (Pydantic surfaces it as a 422).

    NOTE: _MID_ALLOWED already excludes / ? # \\ . whitespace AND CRLF — so
    path-traversal, URL-segment injection, and header injection are blocked
    here. Do NOT widen the regex without a security review.
    """
    if not isinstance(message_ids, list) or len(message_ids) == 0:
        raise ValueError("message_ids must be a non-empty list.")
    if len(message_ids) > 1000:
        raise ValueError("message_ids list cannot exceed 1000 entries per call.")
    for mid in message_ids:
        if not isinstance(mid, str) or not (1 <= len(mid) <= 512):
            raise ValueError("each message_id must be a non-empty string <=512 chars.")
        if not _MID_ALLOWED.fullmatch(mid):
            raise ValueError(
                "message_ids contain disallowed characters; allowed: A-Z a-z 0-9 _ - = +"
            )
    return message_ids


class OutlookMarkRequest(BaseModel):
    """Mark Outlook messages read/unread (`modify` tier).

    `read=True`  -> isRead=true  (mark read).
    `read=False` -> isRead=false (mark unread).

    Graph has no label model; isRead is the boolean property equivalent of
    Gmail's UNREAD label add/remove.
    """

    model_config = ConfigDict(extra="forbid")

    message_ids: list[str] = Field(
        ..., description="Explicit Outlook/Graph message id list to mark."
    )
    read: bool = Field(
        ..., description="True = mark read (isRead=true); False = mark unread (isRead=false)."
    )

    @model_validator(mode="after")
    def _check_ids(self) -> "OutlookMarkRequest":
        _validate_outlook_message_ids(self.message_ids)
        return self


class OutlookArchiveRequest(BaseModel):
    """Archive Outlook messages — move to well-known folder 'archive' (`modify` tier)."""

    model_config = ConfigDict(extra="forbid")

    message_ids: list[str] = Field(
        ..., description="Explicit Outlook/Graph message id list to archive."
    )

    @model_validator(mode="after")
    def _check_ids(self) -> "OutlookArchiveRequest":
        _validate_outlook_message_ids(self.message_ids)
        return self


class OutlookModifyResponse(BaseModel):
    """Result of a mark/archive (modify) call. Reports modified ids + per-id errors."""

    modified_count: int
    modified_ids: list[str]
    errors: list[dict[str, Any]] = Field(default_factory=list)


class OutlookDraftRequest(BaseModel):
    """Create an Outlook DRAFT (no send) — `modify` tier.

    Mirrors `GmailDraftRequest`. A draft lives in the Drafts folder until the
    operator explicitly sends it (a higher-tier action carrying operator-proof).
    """

    model_config = ConfigDict(extra="forbid")

    to: str = Field(
        ..., min_length=1, max_length=998,
        description="Recipient address line. Operator-supplied; not validated as a strict addr-spec.",
    )
    subject: str = Field(
        default="", max_length=998, description="Draft subject line."
    )
    body: str = Field(
        default="", max_length=100_000, description="Draft plain-text body."
    )


class OutlookDraftResponse(BaseModel):
    """Result of a save-draft call. Reports the created Graph message id."""

    draft_id: str
    message_id: str | None = None


# ---------------------------------------------------------------------------
# Kanban #1939 — READ schemas (search + get) — Gmail + Outlook
# ---------------------------------------------------------------------------
#
# READ tier: auto-approve (no operator-proof). Body content is returned in the
# response but MUST NOT appear in any log, audit trail, or error detail.


class GmailSearchRequest(BaseModel):
    """Search Gmail and return metadata (no body). READ tier — auto-approve."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        ...,
        min_length=1,
        max_length=1024,
        description="Gmail search query — e.g. 'from:foo@bar.com is:unread'.",
    )
    max_results: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of messages to return (1–50).",
    )


class GmailSearchItem(BaseModel):
    """Metadata for a single Gmail message returned by search."""

    id: str
    thread_id: str | None = None
    from_: str | None = Field(default=None, alias="from")
    subject: str | None = None
    date: str | None = None
    snippet: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class GmailSearchResponse(BaseModel):
    """Result of a Gmail search call — metadata only (no body)."""

    results: list[GmailSearchItem]
    count: int


class GmailGetRequest(BaseModel):
    """Fetch the full content of a single Gmail message by id. READ tier."""

    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(
        ...,
        description="Gmail message id (hex, e.g. '18b3f1a2c9d4e5f6').",
    )

    @model_validator(mode="after")
    def _check_id(self) -> "GmailGetRequest":
        # FIX-7 (#1939): Pydantic coerces to str before mode="after" validators;
        # no need for isinstance check.
        mid = self.message_id
        if not (1 <= len(mid) <= 64):
            raise ValueError("message_id must be a non-empty string <=64 chars.")
        if not _MID_ALLOWED.fullmatch(mid):
            raise ValueError(
                "message_id contains disallowed characters; allowed: A-Z a-z 0-9 _ - = +"
            )
        return self


class GmailGetResponse(BaseModel):
    """Full content of a single Gmail message. body_text MUST NOT be logged."""

    id: str
    thread_id: str | None = None
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    subject: str | None = None
    date: str | None = None
    body_text: str

    model_config = ConfigDict(populate_by_name=True)


class OutlookSearchRequest(BaseModel):
    """Search Outlook/Graph and return metadata (no body). READ tier — auto-approve."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        ...,
        min_length=1,
        max_length=1024,
        description=(
            "Microsoft Graph $search KQL query — "
            "e.g. 'from:foo@bar.com AND subject:invoice'. "
            "Syntax is NOT identical to Gmail."
        ),
    )
    max_results: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of messages to return (1–50).",
    )


class OutlookSearchItem(BaseModel):
    """Metadata for a single Outlook message returned by search."""

    id: str
    thread_id: str | None = None
    from_: str | None = Field(default=None, alias="from")
    subject: str | None = None
    date: str | None = None
    snippet: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class OutlookSearchResponse(BaseModel):
    """Result of an Outlook search call — metadata only (no body)."""

    results: list[OutlookSearchItem]
    count: int


class OutlookGetRequest(BaseModel):
    """Fetch the full content of a single Outlook message by id. READ tier."""

    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(
        ...,
        description="Outlook/Graph message id (base64url, typically ~150 chars).",
    )

    @model_validator(mode="after")
    def _check_id(self) -> "OutlookGetRequest":
        # FIX-7 (#1939): Pydantic coerces to str before mode="after" validators;
        # no need for isinstance check.
        mid = self.message_id
        if not (1 <= len(mid) <= 512):
            raise ValueError("message_id must be a non-empty string <=512 chars.")
        if not _MID_ALLOWED.fullmatch(mid):
            raise ValueError(
                "message_id contains disallowed characters; allowed: A-Z a-z 0-9 _ - = +"
            )
        return self


class OutlookGetResponse(BaseModel):
    """Full content of a single Outlook message. body_text MUST NOT be logged."""

    id: str
    thread_id: str | None = None
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    subject: str | None = None
    date: str | None = None
    body_text: str

    model_config = ConfigDict(populate_by_name=True)


# ---------------------------------------------------------------------------
# Kanban #1940 — READ extras schemas (thread + labels + attachment) — Gmail
# ---------------------------------------------------------------------------
#
# READ tier: auto-approve (no operator-proof). Body content, filenames, and
# attachment data MUST NOT appear in any log, audit trail, or error detail.


class GmailThreadRequest(BaseModel):
    """Fetch all messages in a Gmail thread by thread id. READ tier."""

    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(
        ...,
        description="Gmail thread id (hex, same character class as message id).",
    )

    @model_validator(mode="after")
    def _check_id(self) -> "GmailThreadRequest":
        tid = self.thread_id
        if not (1 <= len(tid) <= 64):
            raise ValueError("thread_id must be a non-empty string <=64 chars.")
        if not _MID_ALLOWED.fullmatch(tid):
            raise ValueError(
                "thread_id contains disallowed characters; allowed: A-Z a-z 0-9 _ - = +"
            )
        return self


class GmailThreadMessage(BaseModel):
    """A single message within a Gmail thread. body_text MUST NOT be logged."""

    id: str
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    subject: str | None = None
    date: str | None = None
    body_text: str

    model_config = ConfigDict(populate_by_name=True)


class GmailThreadResponse(BaseModel):
    """All messages in a Gmail thread."""

    thread_id: str
    messages: list[GmailThreadMessage]
    count: int


class GmailLabelsRequest(BaseModel):
    """List all Gmail labels for the authenticated account. READ tier (empty body ok)."""

    model_config = ConfigDict(extra="forbid")


class GmailLabel(BaseModel):
    """A single Gmail label."""

    id: str
    name: str
    type: str | None = None


class GmailLabelsResponse(BaseModel):
    """All Gmail labels for the account."""

    labels: list[GmailLabel]
    count: int


class GmailAttachmentRequest(BaseModel):
    """Fetch a Gmail message attachment by message id + attachment id. READ tier."""

    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(
        ...,
        description="Gmail message id (hex, e.g. '18b3f1a2c9d4e5f6').",
    )
    attachment_id: str = Field(
        ...,
        description="Gmail attachment id (from payload.parts[].body.attachmentId).",
    )

    @model_validator(mode="after")
    def _check_ids(self) -> "GmailAttachmentRequest":
        mid = self.message_id
        if not (1 <= len(mid) <= 512):
            raise ValueError("message_id must be a non-empty string <=512 chars.")
        if not _MID_ALLOWED.fullmatch(mid):
            raise ValueError(
                "message_id contains disallowed characters; allowed: A-Z a-z 0-9 _ - = +"
            )
        att = self.attachment_id
        if not (1 <= len(att) <= 512):
            raise ValueError("attachment_id must be a non-empty string <=512 chars.")
        if not _MID_ALLOWED.fullmatch(att):
            raise ValueError(
                "attachment_id contains disallowed characters; allowed: A-Z a-z 0-9 _ - = +"
            )
        return self


class GmailAttachmentResponse(BaseModel):
    """Content of a Gmail attachment. filename + data_base64 MUST NOT be logged."""

    filename: str | None = None
    mime_type: str | None = None
    size: int
    data_base64: str
