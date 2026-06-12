"""Pydantic schemas for the `task_templates` table (Kanban #1303).

A per-team reusable Kanban-task starting point. `TaskTemplateCreate` /
`TaskTemplateUpdate` / `TaskTemplateRead` — all `extra="forbid"` (the spec
requires the Update schema to forbid extras too, unlike the lenient
ResourceUpdate precedent).

TEAM / DEFAULT-ENUM VALIDATION (#1620 doctrine): `team`, `default_task_type`,
and `default_task_kind` are PLAIN `str` here — they are NOT modeled as Pydantic
Literals. They are validated APP-SIDE by the router against the single-source
constants (`ProjectTeam.ALL` / `TaskType.ALL` / `TaskKind.ALL`), exactly as
`projects.team` is validated since the per-team DB CHECK was dropped (#1620).
This keeps "adding a team / task_type" a constants.py-only edit with no schema
change. The router returns a precise 422 listing the valid values (mirror of
`routers/projects.py::create_project`). The soft-delete `status` flag is
intentionally absent from the public schemas — DELETE is the soft-delete path
(parity with milestones / project_resources), and `status` toggle on PATCH is
exposed via the explicit `status` field below per the #1303 spec
("toggle status, edit text").
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_PLACEHOLDER_RE = re.compile(r"^[\w.-]+$", re.ASCII)


def _validate_str_list(values: list[Any], label: str) -> list[str]:
    """Shared element-shape guard for the JSONB string-list fields.

    Mirrors the project_resources.tags validator: every element must be a
    non-empty string, each <= 100 chars. Error messages are part of the wire
    contract. Returns the (unchanged) list so it can be used as a validator
    return value.
    """
    for v in values:
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"{label} must be non-empty strings")
        if len(v) > 100:
            raise ValueError(f"each {label} entry must be <= 100 chars")
    return values


def _validate_ac_template(items: list[Any]) -> list[dict]:
    """Validate the AC template array — a list of {text, ...} objects.

    Each element must be a dict carrying a non-empty `text` string (the field
    the renderer substitutes). Other keys (e.g. status, notes) are passed
    through untouched — this mirrors the loose `acceptance_criteria` shape the
    tasks surface already accepts. Element-shape validated here, NOT at the DB.
    """
    for obj in items:
        if not isinstance(obj, dict):
            raise ValueError("acceptance_criteria_template items must be objects")
        text_val = obj.get("text")
        if not isinstance(text_val, str) or not text_val.strip():
            raise ValueError(
                "each acceptance_criteria_template item needs a non-empty 'text'"
            )
    return items


class TaskTemplateCreate(BaseModel):
    """Request body for POST /api/task-templates.

    `team` / `default_task_type` / `default_task_kind` are plain strings;
    the router validates them against the constants registry (422 on unknown).
    """

    model_config = ConfigDict(extra="forbid")

    team: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    icon: str | None = Field(default=None, max_length=100)
    description_template: str = Field(min_length=1, max_length=20_000)
    acceptance_criteria_template: list[dict] = Field(
        default_factory=list, max_length=100
    )
    default_task_type: str = Field(default="feature", max_length=50)
    default_priority: int = Field(default=2, ge=1, le=4)
    default_task_kind: str = Field(default="ai", max_length=50)
    placeholders: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("placeholders")
    @classmethod
    def _check_placeholders(cls, v: list[Any]) -> list[str]:
        validated = _validate_str_list(v, "placeholders")
        for i, key in enumerate(validated):
            if not _PLACEHOLDER_RE.fullmatch(key):
                raise ValueError(
                    f"placeholders[{i}]={key!r} must match ^[\\w.-]+$"
                )
        return validated

    @field_validator("acceptance_criteria_template")
    @classmethod
    def _check_ac_template(cls, v: list[Any]) -> list[dict]:
        return _validate_ac_template(v)


class TaskTemplateUpdate(BaseModel):
    """Request body for PATCH /api/task-templates/{id} — all fields optional.

    `extra="forbid"` (spec). Missing-key vs explicit-null is enforced at the
    router via `model_dump(exclude_unset=True)`. `status` IS editable here (the
    #1303 spec calls out "toggle status, edit text") — distinct from
    project_resources, where status is DELETE-only. `team` re-assignment IS
    allowed (the router re-validates it against the registry); set it to move a
    template between teams.
    """

    model_config = ConfigDict(extra="forbid")

    team: str | None = Field(default=None, min_length=1, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    icon: str | None = Field(default=None, max_length=100)
    description_template: str | None = Field(
        default=None, min_length=1, max_length=20_000
    )
    acceptance_criteria_template: list[dict] | None = Field(
        default=None, max_length=100
    )
    default_task_type: str | None = Field(default=None, max_length=50)
    default_priority: int | None = Field(default=None, ge=1, le=4)
    default_task_kind: str | None = Field(default=None, max_length=50)
    placeholders: list[str] | None = Field(default=None, max_length=100)
    # 0=disabled/soft-deleted, 1=active. Exposed for the spec's "toggle status".
    status: int | None = Field(default=None, ge=0, le=1)

    @field_validator("placeholders")
    @classmethod
    def _check_placeholders(cls, v: list[Any] | None) -> list[str] | None:
        if v is None:
            return None
        validated = _validate_str_list(v, "placeholders")
        for i, key in enumerate(validated):
            if not _PLACEHOLDER_RE.fullmatch(key):
                raise ValueError(
                    f"placeholders[{i}]={key!r} must match ^[\\w.-]+$"
                )
        return validated

    @field_validator("acceptance_criteria_template")
    @classmethod
    def _check_ac_template(cls, v: list[Any] | None) -> list[dict] | None:
        if v is None:
            return None
        return _validate_ac_template(v)


class TaskTemplateRead(BaseModel):
    """Full task_template row as returned by the API (incl. raw templates)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    team: str
    name: str
    icon: str | None
    description_template: str
    acceptance_criteria_template: list[dict]
    default_task_type: str
    default_priority: int
    default_task_kind: str
    placeholders: list[str]
    status: int
    created_at: datetime
    updated_at: datetime | None
