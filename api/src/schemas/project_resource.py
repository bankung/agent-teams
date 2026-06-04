"""Pydantic schemas for the `project_resources` table (Kanban #1302).

Wire-enum for `kind` is a Literal kept in lockstep with
`src.constants.ResourceKind.ALL` (guard at module bottom — mirrors the
MilestoneStatusLiteral / TaskRunModeLiteral pattern).

SCHEMA-ONLY this slice (#1302 / X.1): these models define the wire contract for
the upcoming upload endpoint (#1309 / X.2). No router consumes them yet.

Column-naming convention, parity with `tasks` / `milestones`: `kind` is the
DISCRIMINATOR; the 0/1 soft-delete `status` flag is intentionally NOT exposed on
any public schema (clients will DELETE /api/.../resources/{id} to soft-delete) —
parity with how `tasks` hides `status` while exposing `process_status`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.constants import ResourceKind

# Wire enum for project_resources.kind; lockstep guard at module bottom.
ResourceKindLiteral = Literal["file", "link"]


def _check_kind_fields(
    kind: str, filename: str | None, url: str | None
) -> None:
    """Mirror the DB CHECK `ck_project_resources_kind_fields` at the API boundary.

    'file' rows MUST carry a `filename`; 'link' rows MUST carry a `url`. Raises
    ValueError (-> 422) so the friendlier API error fires BEFORE the DB
    IntegrityError. Shared by ResourceCreate (and any future full-body PATCH).
    Error messages are part of the wire contract.
    """
    if kind == ResourceKind.FILE and not filename:
        raise ValueError("kind='file' requires a non-empty filename")
    if kind == ResourceKind.LINK and not url:
        raise ValueError("kind='link' requires a non-empty url")


class ResourceCreate(BaseModel):
    """Request body for the (forthcoming) POST upload/attach endpoint (#1309).

    `project_id` is defense-in-depth — the X-Project-Id header is canonical and
    the router will assert the body matches the session (mirrors TaskCreate /
    MilestoneCreate). `task_id` optionally pins the resource to one task in the
    same project (same-project rule is app-layer, like milestone_id).
    """

    model_config = ConfigDict(extra="forbid")

    project_id: int
    task_id: int | None = None
    # The Literal 422s any value outside the enum.
    kind: ResourceKindLiteral
    filename: str | None = Field(default=None, max_length=500)
    url: str | None = Field(default=None, max_length=2_000)
    content_type: str | None = Field(default=None, max_length=255)
    size_bytes: int | None = Field(default=None, ge=0)
    label: str | None = Field(default=None, max_length=500)
    # Tag-bearing (#1302) — a list of non-empty strings. Element shape validated
    # here (mirrors projects.sources / required_binaries). DEFAULT [] matches the
    # DB DEFAULT '[]'.
    tags: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _validate_kind_fields(self) -> "ResourceCreate":
        _check_kind_fields(self.kind, self.filename, self.url)
        return self

    @model_validator(mode="after")
    def _validate_tags(self) -> "ResourceCreate":
        for t in self.tags:
            if not isinstance(t, str) or not t.strip():
                raise ValueError("tags must be non-empty strings")
            if len(t) > 100:
                raise ValueError("each tag must be <= 100 chars")
        return self


class ResourceUpdate(BaseModel):
    """Request body for the (forthcoming) PATCH endpoint — all fields optional.

    Soft-delete `status` is intentionally absent — DELETE is the public
    soft-delete path. `kind` is NOT editable post-create (changing the
    discriminator would re-interpret filename/url semantics); the upcoming
    router exposes only the metadata fields below. Missing-key vs explicit-null
    is enforced at the router via `model_dump(exclude_unset=True)`.
    """

    model_config = ConfigDict(extra="ignore")

    task_id: int | None = None
    label: str | None = Field(default=None, max_length=500)
    tags: list[str] | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def _validate_tags(self) -> "ResourceUpdate":
        if self.tags is not None:
            for t in self.tags:
                if not isinstance(t, str) or not t.strip():
                    raise ValueError("tags must be non-empty strings")
                if len(t) > 100:
                    raise ValueError("each tag must be <= 100 chars")
        return self


class ResourceRead(BaseModel):
    """Full project_resource row as returned by the API."""

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
    tags: list[str]
    created_at: datetime
    updated_at: datetime


# Sanity: the Literal stays in lockstep with src.constants.ResourceKind.ALL.
# Use a real exception (not `assert`) so the guard survives `python -O`.
# Mirrors the MilestoneStatusLiteral <-> MilestoneStatus.ALL guard.
if set(ResourceKindLiteral.__args__) != set(ResourceKind.ALL):  # type: ignore[attr-defined]
    raise RuntimeError(
        f"ResourceKindLiteral {ResourceKindLiteral.__args__!r} drifted "  # type: ignore[attr-defined]
        f"from ResourceKind.ALL {ResourceKind.ALL!r}"
    )
