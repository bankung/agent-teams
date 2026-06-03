"""Pydantic schemas for the `milestones` table (Kanban #1868).

Wire-enum for `milestone_status` is a Literal kept in lockstep with
`src.constants.MilestoneStatus.ALL` (guard at module bottom — mirrors the
TaskRunModeLiteral pattern in schemas/task.py).

Column-naming convention (#1868): `milestone_status` is the LIFECYCLE field;
the 0/1 soft-delete `status` flag is intentionally NOT exposed on any public
schema (clients call DELETE /api/milestones/{id} to soft-delete) — parity with
how `tasks` hides `status` while exposing `process_status`.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.constants import MilestoneStatus

# Wire enum for milestones.milestone_status; lockstep guard at module bottom.
MilestoneStatusLiteral = Literal["planned", "active", "released", "cancelled"]


def _check_start_before_target(
    start_date: date | None, target_date: date | None
) -> None:
    """Raise ValueError if both dates are set and start_date > target_date.

    None on either side = no constraint (a half-specified window is legal).
    Shared by MilestoneCreate + MilestoneUpdate so the wire contract is one
    string for both. Error message is part of the wire contract.
    """
    if start_date is not None and target_date is not None and start_date > target_date:
        raise ValueError(
            "start_date must be on or before target_date"
        )


class MilestoneCreate(BaseModel):
    """Request body for POST /api/milestones.

    `project_id` is defense-in-depth — the X-Project-Id header is canonical and
    the router asserts the body matches the session (mirrors TaskCreate).
    """

    model_config = ConfigDict(extra="forbid")

    project_id: int
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20_000)
    # Default 'planned' matches the DB DEFAULT; the Literal 422s any other value.
    milestone_status: MilestoneStatusLiteral = MilestoneStatus.PLANNED
    start_date: date | None = None
    target_date: date | None = None
    # Sparse-float manual ordering (mirror of tasks.sort_order). NULL = unset.
    sort_order: float | None = Field(default=None)

    @model_validator(mode="after")
    def _validate_dates(self) -> "MilestoneCreate":
        _check_start_before_target(self.start_date, self.target_date)
        return self


class MilestoneUpdate(BaseModel):
    """Request body for PATCH /api/milestones/{id} — all fields optional.

    Soft-delete `status` is intentionally absent — DELETE /api/milestones/{id}
    is the public soft-delete path. Missing-key vs explicit-null is enforced at
    the router via `model_dump(exclude_unset=True)`.
    """

    model_config = ConfigDict(extra="ignore")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20_000)
    milestone_status: MilestoneStatusLiteral | None = None
    start_date: date | None = None
    target_date: date | None = None
    sort_order: float | None = Field(default=None)
    # released_at is operator-settable (e.g. stamp on release, or clear on
    # re-open). PATCH semantics: key-absent = unchanged; explicit-null = clear.
    released_at: datetime | None = None

    @model_validator(mode="after")
    def _validate_dates(self) -> "MilestoneUpdate":
        # Only validate when BOTH are present in this PATCH — a one-field PATCH
        # cannot see the existing row's other date, so the cross-field rule is
        # checked here only for the both-in-payload case (the router does the
        # resolved-final check against the stored row). Mirrors the
        # scheduled_at/is_template XOR resolved-final pattern in schemas/task.py.
        if (
            "start_date" in self.model_fields_set
            and "target_date" in self.model_fields_set
        ):
            _check_start_before_target(self.start_date, self.target_date)
        return self


class MilestoneRollup(BaseModel):
    """Task-rollup stats for a milestone (Kanban #1868).

    - `total` — count of active (status=1) tasks pointing at this milestone,
      INCLUDING cancelled (process_status=6).
    - `by_process_status` — count per process_status bucket (string keys '1'..'6'),
      always all six keys (zero-filled) so the FE has a stable shape.
    - `done` — count of process_status=5 (DONE) tasks.
    - `progress_pct` — done / (total excluding cancelled), 0..100, rounded to
      one decimal. 0.0 when the non-cancelled denominator is zero (div-by-zero
      guard).
    """

    model_config = ConfigDict(extra="forbid")

    total: int
    by_process_status: dict[str, int]
    done: int
    progress_pct: float


class MilestoneRead(BaseModel):
    """Full milestone row as returned by the API (no rollup)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    title: str
    description: str | None
    milestone_status: MilestoneStatusLiteral
    start_date: date | None
    target_date: date | None
    sort_order: float | None
    created_at: datetime
    updated_at: datetime
    released_at: datetime | None


class MilestoneDetail(MilestoneRead):
    """MilestoneRead + the task rollup — returned by GET /api/milestones/{id}."""

    rollup: MilestoneRollup


# Sanity: the Literal stays in lockstep with src.constants.MilestoneStatus.ALL.
# Use a real exception (not `assert`) so the guard survives `python -O`.
# Mirrors the TaskRunModeLiteral <-> TaskRunMode.ALL guard in schemas/task.py.
if set(MilestoneStatusLiteral.__args__) != set(MilestoneStatus.ALL):  # type: ignore[attr-defined]
    raise RuntimeError(
        f"MilestoneStatusLiteral {MilestoneStatusLiteral.__args__!r} drifted "  # type: ignore[attr-defined]
        f"from MilestoneStatus.ALL {MilestoneStatus.ALL!r}"
    )
