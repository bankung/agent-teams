"""Unit tests for src.schemas.task validators.

These exercise the Pydantic validator factory directly (no HTTP, no DB) — they
lock in the exact error message format the API surfaces so the FE error UX
won't silently drift if someone refactors the validator wiring again.

Error message contracts (must remain stable):
- process_status invalid: "process_status must be one of (1, 2, 3, 4, 5), got <repr>"
- process_status required (POST): "process_status is required"
- priority invalid:       "priority must be one of (1, 2, 3, 4), got <repr>"
- assigned_role invalid:  "assigned_role must be NULL or one of (1, 2, 3, 4, 5), got <repr>"

The 1..5 lifecycle code is now `process_status` (renamed by the 2026-05-08
soft-delete-and-lead migration); the bare `status` name is reserved for the
0/1 soft-delete flag and is NOT exposed in any public schema.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.constants import TaskPriority, TaskRole, TaskStatus
from src.schemas.task import TaskCreate, TaskUpdate


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _first_msg(exc: ValidationError) -> str:
    """Concatenate every error message into a single string for substring asserts.

    Pydantic v2 exposes per-field errors via `.errors()`; the validator's
    `ValueError(...)` message lands in `errors()[i]["msg"]`, prefixed by
    "Value error, " in the default rendering. We match against the raw `msg`
    rather than the prefixed string so the assertions stay tied to what
    `_make_code_validator` actually raises.
    """
    return " | ".join(e["msg"] for e in exc.errors())


# -----------------------------------------------------------------------------
# TaskCreate — defaults + each validator branch
# -----------------------------------------------------------------------------


def test_task_create_defaults_applied() -> None:
    task = TaskCreate(project_id=1, title="x")
    assert task.process_status == TaskStatus.TODO  # 1
    assert task.priority == TaskPriority.NORMAL  # 2
    assert task.assigned_role is None


def test_task_create_process_status_invalid_message() -> None:
    with pytest.raises(ValidationError) as ei:
        TaskCreate(project_id=1, title="x", process_status=99)
    assert "process_status must be one of (1, 2, 3, 4, 5), got 99" in _first_msg(ei.value)


def test_task_create_priority_invalid_message() -> None:
    with pytest.raises(ValidationError) as ei:
        TaskCreate(project_id=1, title="x", priority=99)
    assert "priority must be one of (1, 2, 3, 4), got 99" in _first_msg(ei.value)


def test_task_create_role_invalid_message() -> None:
    with pytest.raises(ValidationError) as ei:
        TaskCreate(project_id=1, title="x", assigned_role=99)
    assert (
        "assigned_role must be NULL or one of (1, 2, 3, 4, 5), got 99"
        in _first_msg(ei.value)
    )


def test_task_create_role_none_is_allowed() -> None:
    """assigned_role is the only nullable code on TaskCreate."""
    task = TaskCreate(project_id=1, title="x", assigned_role=None)
    assert task.assigned_role is None


def test_task_create_process_status_none_rejected_at_type_layer() -> None:
    """`TaskCreate.process_status` is typed `Annotated[int, ...]` (non-Optional),
    so Pydantic's int-coercion rejects None *before* the validator runs. The
    "<field> is required" branch in `_make_code_validator(required=True)` is
    therefore unreachable from `TaskCreate` for `process_status` / `priority` —
    flagged as a contract gap in the qa report. This test pins *current*
    behavior so a future re-typing (e.g. switching to `int | None`) is detected.
    """
    with pytest.raises(ValidationError) as ei:
        TaskCreate(project_id=1, title="x", process_status=None)
    msg = _first_msg(ei.value)
    # Pydantic v2 default int_type message — exact text is library-controlled,
    # so we assert the substring that signals "rejected before our validator".
    assert "valid integer" in msg or "int_type" in msg


def test_task_create_priority_none_rejected_at_type_layer() -> None:
    """Same gap as process_status — see test above. priority is typed
    `Annotated[int, ...]`, so None is rejected by Pydantic type coercion before
    the "is required" branch.
    """
    with pytest.raises(ValidationError) as ei:
        TaskCreate(project_id=1, title="x", priority=None)
    msg = _first_msg(ei.value)
    assert "valid integer" in msg or "int_type" in msg


# -----------------------------------------------------------------------------
# TaskUpdate — None is allowed (PATCH semantics) but bad codes still rejected
# -----------------------------------------------------------------------------


def test_task_update_process_status_none_passes() -> None:
    """PATCH semantics: None is the absence of an update, not an error."""
    upd = TaskUpdate(process_status=None)
    assert upd.process_status is None


def test_task_update_priority_none_passes() -> None:
    upd = TaskUpdate(priority=None)
    assert upd.priority is None


def test_task_update_role_none_passes() -> None:
    upd = TaskUpdate(assigned_role=None)
    assert upd.assigned_role is None


def test_task_update_process_status_invalid_message() -> None:
    with pytest.raises(ValidationError) as ei:
        TaskUpdate(process_status=99)
    assert "process_status must be one of (1, 2, 3, 4, 5), got 99" in _first_msg(ei.value)


def test_task_update_priority_invalid_message() -> None:
    with pytest.raises(ValidationError) as ei:
        TaskUpdate(priority=99)
    assert "priority must be one of (1, 2, 3, 4), got 99" in _first_msg(ei.value)


def test_task_update_role_invalid_message() -> None:
    with pytest.raises(ValidationError) as ei:
        TaskUpdate(assigned_role=99)
    assert (
        "assigned_role must be NULL or one of (1, 2, 3, 4, 5), got 99"
        in _first_msg(ei.value)
    )


# -----------------------------------------------------------------------------
# Sanity — every valid code in the ALL tuples passes the validator
# -----------------------------------------------------------------------------


def test_task_create_accepts_every_valid_process_status() -> None:
    for code in TaskStatus.ALL:
        task = TaskCreate(project_id=1, title="x", process_status=code)
        assert task.process_status == code


def test_task_create_accepts_every_valid_priority() -> None:
    for code in TaskPriority.ALL:
        task = TaskCreate(project_id=1, title="x", priority=code)
        assert task.priority == code


def test_task_create_accepts_every_valid_role() -> None:
    for code in TaskRole.ALL:
        task = TaskCreate(project_id=1, title="x", assigned_role=code)
        assert task.assigned_role == code
