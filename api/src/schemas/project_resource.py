"""Pydantic schemas for the `project_resources` table (Kanban #1302 / #1309).

Wire-enum for `kind` is a Literal kept in lockstep with
`src.constants.ResourceKind.ALL` (guard at module bottom — mirrors the
MilestoneStatusLiteral / TaskRunModeLiteral pattern).

#1309 — `tags` SHAPE CHANGE (list -> dict). The #1302 schema-only slice typed
`tags` as a `list[str]` (mirroring `projects.sources`). The #1309 verify-and-tag
pipeline needs to stash STRUCTURED metadata — `{row_count, col_count,
schema_detected, preview, est_cost_if_full, hash, format_detected, ...}` — which
is a JSON OBJECT, not a list. The table is brand-new with ZERO rows and NO
consumers (verified live 2026-06-04: `SELECT count(*) FROM project_resources` =
0), so widening the wire type from `list[str]` to `dict` is safe (no data to
migrate, no readers to break). The DB column is JSONB which holds either shape;
only the `server_default '[]'` differs from the value we now write (a dict),
which is fine — every #1309 INSERT supplies an explicit `tags` object. See the
report / decisions for the locked rationale.

Wire models used by the #1309 router:
  - LINK create  -> the router parses the JSON body directly (dual-contract by
    request content-type); `ResourceLinkCreate` documents that shape.
  - FILE create  -> multipart (UploadFile + Form) handled directly in the router
    (file metadata is server-derived, never client-supplied).
  - `ResourceRead` / `ResourcePreview` are the response models.

`ResourceCreate` / `ResourceUpdate` are RETAINED from the #1302 slice (still
exported via `schemas/__init__`) for backward-compat; their `tags` field is
widened to the dict shape to match the #1309 contract. They are not wired to the
router (kept for any future full-body PATCH + the #1302 contract tests).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.constants import ResourceKind

# Wire enum for project_resources.kind; lockstep guard at module bottom.
ResourceKindLiteral = Literal["file", "link"]

# Tags keys that are server-internal and must NEVER appear on the wire.
# stored_path leaks FS layout; keep it in the DB (DELETE needs it) but
# strip it from every response (#1309 fix #4).
_WIRE_HIDDEN_TAGS: frozenset[str] = frozenset({"stored_path"})


def _check_kind_fields(
    kind: str, filename: str | None, url: str | None
) -> None:
    """Mirror the DB CHECK `ck_project_resources_kind_fields` at the API boundary.

    'file' rows MUST carry a `filename`; 'link' rows MUST carry a `url`. Raises
    ValueError (-> 422) so the friendlier API error fires BEFORE the DB
    IntegrityError. Error messages are part of the wire contract (pinned by the
    #1302 schema contract tests).
    """
    if kind == ResourceKind.FILE and not filename:
        raise ValueError("kind='file' requires a non-empty filename")
    if kind == ResourceKind.LINK and not url:
        raise ValueError("kind='link' requires a non-empty url")


class ResourceLinkCreate(BaseModel):
    """JSON request body for attaching a LINK resource (#1309).

    The FILE path uses multipart (UploadFile + Form fields) handled in the
    router — file metadata (filename, content_type, size_bytes, tags) is
    SERVER-derived, never client-supplied. This body therefore models the link
    kind only: `kind` is fixed to 'link', `url` is required.

    `project_id` is NOT in the body — it is taken canonically from the URL path
    (`POST /api/projects/{project_id}/resources`). `task_id` optionally pins the
    resource to one task in the SAME project (same-project rule enforced in the
    router, mirroring tasks.milestone_id).
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["link"] = "link"
    url: str = Field(min_length=1, max_length=2_000)
    task_id: int | None = None
    label: str | None = Field(default=None, max_length=500)


class ResourceCreate(BaseModel):
    """Generic create body retained from #1302 (backward-compat; not router-wired).

    `tags` is the metadata OBJECT (#1309 dict shape; was list[str] in #1302).
    DEFAULT {} matches the verify-and-tag pipeline output container. The per-kind
    required-field validator + the kind Literal are preserved so the #1302
    contract tests (and any future full-body PATCH) keep their wire behavior.
    """

    model_config = ConfigDict(extra="forbid")

    project_id: int
    task_id: int | None = None
    kind: ResourceKindLiteral
    filename: str | None = Field(default=None, max_length=500)
    url: str | None = Field(default=None, max_length=2_000)
    content_type: str | None = Field(default=None, max_length=255)
    size_bytes: int | None = Field(default=None, ge=0)
    label: str | None = Field(default=None, max_length=500)
    tags: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_kind_fields(self) -> "ResourceCreate":
        _check_kind_fields(self.kind, self.filename, self.url)
        return self


class ResourceUpdate(BaseModel):
    """Partial update body retained from #1302 (backward-compat; not router-wired).

    `kind` is NOT editable post-create. `tags` (when present) is the metadata
    object. Missing-key vs explicit-null is the caller's concern via
    `model_dump(exclude_unset=True)`.
    """

    model_config = ConfigDict(extra="ignore")

    task_id: int | None = None
    label: str | None = Field(default=None, max_length=500)
    tags: dict[str, Any] | None = None


class ResourceRead(BaseModel):
    """Full project_resource row as returned by the API.

    `tags` is the verify-and-tag metadata OBJECT (#1309) — row_count, col_count,
    schema_detected, preview, est_cost_if_full, hash, format_detected, etc. for
    files; url_scheme / head_status / title for links. Shape is intentionally
    open (`dict[str, Any]`) so the pipeline can extend the metadata without a
    schema bump.

    Internal-only keys (stored_path) are stripped by the `_strip_internal_tags`
    validator before serialisation — they live in the DB but must never reach the
    wire (#1309 fix #4).
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    task_id: int | None
    kind: ResourceKindLiteral
    filename: str | None
    url: str | None
    content_type: str | None
    size_bytes: int | None
    label: str | None
    tags: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @field_validator("tags", mode="before")
    @classmethod
    def _strip_internal_tags(cls, v: Any) -> Any:
        """Drop server-internal keys (stored_path) before the value hits the wire."""
        if isinstance(v, dict):
            return {k: val for k, val in v.items() if k not in _WIRE_HIDDEN_TAGS}
        return v


class ResourcePreview(BaseModel):
    """Lightweight preview payload for GET /api/resources/{id}/preview (#1309).

    Read straight from the stored `tags` metadata — the endpoint NEVER re-reads
    the full file. `preview` is the first-N-rows sample (list of row-objects for
    CSV/TSV, parsed value for JSON, or None when no parser ran). The remaining
    fields are the at-a-glance stats also surfaced in the board UI.
    """

    model_config = ConfigDict(from_attributes=False)

    id: int
    kind: ResourceKindLiteral
    filename: str | None
    content_type: str | None
    format_detected: str | None = None
    row_count: int | None = None
    col_count: int | None = None
    schema_detected: list[str] | None = None
    preview: Any = None
    parser_unavailable: bool = False


# Sanity: the Literal stays in lockstep with src.constants.ResourceKind.ALL.
# Use a real exception (not `assert`) so the guard survives `python -O`.
# Mirrors the MilestoneStatusLiteral <-> MilestoneStatus.ALL guard.
if set(ResourceKindLiteral.__args__) != set(ResourceKind.ALL):  # type: ignore[attr-defined]
    raise RuntimeError(
        f"ResourceKindLiteral {ResourceKindLiteral.__args__!r} drifted "  # type: ignore[attr-defined]
        f"from ResourceKind.ALL {ResourceKind.ALL!r}"
    )
