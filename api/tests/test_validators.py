"""Unit tests for src.schemas.task validators.

These exercise the Pydantic validator factory directly (no HTTP, no DB) — they
lock in the exact error message format the API surfaces so the FE error UX
won't silently drift if someone refactors the validator wiring again.

Error message contracts (must remain stable):
- process_status invalid: "process_status must be one of (1, 2, 3, 4, 5, 6, 8), got <repr>"
  (Kanban #1839, 2026-06-16: 8='halted-pending-user' added to TaskStatus.ALL;
  7 intentionally skipped/reserved. The message renders TaskStatus.ALL verbatim.)
- process_status required (POST): "process_status is required"
- priority invalid:       "priority must be one of (1, 2, 3, 4), got <repr>"
- assigned_role invalid:  "assigned_role must be NULL or in range 1..<RANGE_MAX>, got <repr>"
  (Kanban #926, 2026-05-15: widened from "one of (1, 2, 3, 4, 5)" to a range
  to admit novel team codes 11..20 — 1..10 = dev, 11..20 = novel, 21+ reserved.
  Kanban #1266/#1269/#1271, 2026-05-20: further widened 1..50 to admit SEO
  codes 21..30, SEM codes 31..40, data-analytics codes 41..50. Kanban #2812,
  2026-07-10: further widened to 1..60 for social codes 51..57. Kanban #2871,
  2026-08-24: further widened to 1..70 for mobile codes 61..62.)

  The assertions below interpolate `TaskRole.RANGE_MAX` rather than pinning the
  literal — required by `context/standards/pydantic/v2-conventions.md`, which
  Kanban #1355 already had to clean out of THIS file once after the 20->50 bump.
  A hardcoded bound re-breaks on every range change; a derived one does not.

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
    assert "process_status must be one of (1, 2, 3, 4, 5, 6, 8), got 99" in _first_msg(ei.value)


def test_task_create_priority_invalid_message() -> None:
    with pytest.raises(ValidationError) as ei:
        TaskCreate(project_id=1, title="x", priority=99)
    assert "priority must be one of (1, 2, 3, 4), got 99" in _first_msg(ei.value)


def test_task_create_role_invalid_message() -> None:
    with pytest.raises(ValidationError) as ei:
        TaskCreate(project_id=1, title="x", assigned_role=99)
    assert (
        f"assigned_role must be NULL or in range 1..{TaskRole.RANGE_MAX}, got 99"
        in _first_msg(ei.value)
    )


def test_task_create_role_none_is_allowed() -> None:
    """assigned_role is the only nullable code on TaskCreate."""
    task = TaskCreate(project_id=1, title="x", assigned_role=None)
    assert task.assigned_role is None


def test_task_create_role_accepts_novel_codes() -> None:
    """Kanban #926: assigned_role admits novel team codes 11/12/13."""
    for code in (TaskRole.NOVEL_WRITER, TaskRole.NOVEL_EDITOR, TaskRole.NOVEL_PROOFREADER):
        task = TaskCreate(project_id=1, title="x", assigned_role=code)
        assert task.assigned_role == code


def test_task_create_role_accepts_seo_codes() -> None:
    """Kanban #1266 (2026-05-20): SEO range 21..30 — codes 21-24 named."""
    for code in (
        TaskRole.SEO_STRATEGIST,
        TaskRole.TECHNICAL_SEO_SPECIALIST,
        TaskRole.CONTENT_SEO_OPTIMIZER,
        TaskRole.SEO_REPORTING_ANALYST,
    ):
        task = TaskCreate(project_id=1, title="x", assigned_role=code)
        assert task.assigned_role == code


def test_task_create_role_accepts_sem_codes() -> None:
    """Kanban #1269 (2026-05-20): SEM range 31..40 — codes 31-34 named."""
    for code in (
        TaskRole.SEM_CAMPAIGN_LEAD,
        TaskRole.GOOGLE_ADS_SPECIALIST,
        TaskRole.META_ADS_SPECIALIST,
        TaskRole.PLATFORM_ADS_COORDINATOR,
    ):
        task = TaskCreate(project_id=1, title="x", assigned_role=code)
        assert task.assigned_role == code


def test_task_create_role_accepts_data_analytics_codes() -> None:
    """Kanban #1271 (2026-05-20): data-analytics range 41..50 — codes 41-44 named."""
    for code in (
        TaskRole.BI_ANALYST,
        TaskRole.SQL_OPTIMIZER,
        TaskRole.DASHBOARD_DESIGNER,
        TaskRole.ANALYTICS_PLATFORM_INTEGRATOR,
    ):
        task = TaskCreate(project_id=1, title="x", assigned_role=code)
        assert task.assigned_role == code


def test_task_create_role_accepts_social_codes() -> None:
    """Kanban #2812 (2026-07-10): social range 51..60 — codes 51-57 named."""
    for code in (
        TaskRole.CONTENT_WRITER,
        TaskRole.CONTENT_HOOK_DOCTOR,
        TaskRole.CONTENT_EDITOR,
        TaskRole.CONTENT_VERACITY_CHECKER,
        TaskRole.THAI_PROOFREADER,
        TaskRole.SOCIAL_BI_ANALYST,
        TaskRole.SOCIAL_GENERAL_RESEARCHER,
    ):
        task = TaskCreate(project_id=1, title="x", assigned_role=code)
        assert task.assigned_role == code


def test_task_role_security_reviewer_code_is_six() -> None:
    """Kanban #7 Section B (2026-05-16): SECURITY_REVIEWER pins to integer
    code 6 in the dev range (1..10). Numbers are stable forever; this test
    is the cross-stack tripwire if anyone renumbers.
    """
    assert TaskRole.SECURITY_REVIEWER == 6


def test_task_create_accepts_security_reviewer_role() -> None:
    """Kanban #7 Section B: the wire-layer accepts assigned_role=6 for a
    dev-team task. Mirrors the per-role accept tests for FRONTEND..REVIEWER.
    """
    task = TaskCreate(
        project_id=1, title="x", assigned_role=TaskRole.SECURITY_REVIEWER
    )
    assert task.assigned_role == 6


def test_task_create_role_accepts_unnamed_reserved_codes() -> None:
    """Kanban #926: range gate admits unnamed codes inside the partition.
    Kanban #1266/#1269/#1271: expanded to 1..50 — reserved sub-ranges:
    7..10 (dev), 14..20 (novel), 25..30 (seo), 35..40 (sem), 45..50 (data).
    Kanban #2812: expanded to 1..60 — reserved sub-range 58..60 (social).
    Kanban #2871: expanded to 1..70 — reserved sub-range 63..70 (mobile)."""
    for code in (7, 10, 14, 20, 25, 30, 35, 40, 45, 50, 58, 60, 63, 70):
        task = TaskCreate(project_id=1, title="x", assigned_role=code)
        assert task.assigned_role == code


def test_task_create_role_rejects_above_range() -> None:
    """Kanban #2871: 71+ is out of range — reserved for future team domains
    beyond mobile. Range is now 1..70 (61..70 = mobile, Kanban #2871; 61 was
    the ceiling probe until this bump)."""
    with pytest.raises(ValidationError) as ei:
        TaskCreate(project_id=1, title="x", assigned_role=71)
    assert (
        f"assigned_role must be NULL or in range 1..{TaskRole.RANGE_MAX}, got 71"
        in _first_msg(ei.value)
    )


def test_task_create_role_rejects_zero() -> None:
    with pytest.raises(ValidationError) as ei:
        TaskCreate(project_id=1, title="x", assigned_role=0)
    assert (
        f"assigned_role must be NULL or in range 1..{TaskRole.RANGE_MAX}, got 0"
        in _first_msg(ei.value)
    )


def test_task_create_role_rejects_negative() -> None:
    with pytest.raises(ValidationError) as ei:
        TaskCreate(project_id=1, title="x", assigned_role=-1)
    assert (
        f"assigned_role must be NULL or in range 1..{TaskRole.RANGE_MAX}, got -1"
        in _first_msg(ei.value)
    )


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
    assert "process_status must be one of (1, 2, 3, 4, 5, 6, 8), got 99" in _first_msg(ei.value)


def test_task_update_priority_invalid_message() -> None:
    with pytest.raises(ValidationError) as ei:
        TaskUpdate(priority=99)
    assert "priority must be one of (1, 2, 3, 4), got 99" in _first_msg(ei.value)


def test_task_update_role_invalid_message() -> None:
    with pytest.raises(ValidationError) as ei:
        TaskUpdate(assigned_role=99)
    assert (
        f"assigned_role must be NULL or in range 1..{TaskRole.RANGE_MAX}, got 99"
        in _first_msg(ei.value)
    )


def test_task_update_role_accepts_novel_codes() -> None:
    """Kanban #926: PATCH path admits novel team codes 11/12/13."""
    for code in (TaskRole.NOVEL_WRITER, TaskRole.NOVEL_EDITOR, TaskRole.NOVEL_PROOFREADER):
        upd = TaskUpdate(assigned_role=code)
        assert upd.assigned_role == code


def test_task_update_role_rejects_above_range() -> None:
    """Kanban #2871: PATCH path rejects 71+ — symmetric with POST."""
    with pytest.raises(ValidationError) as ei:
        TaskUpdate(assigned_role=71)
    assert (
        f"assigned_role must be NULL or in range 1..{TaskRole.RANGE_MAX}, got 71"
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


# -----------------------------------------------------------------------------
# Bool-coercion guard (Kanban #2829) — Python `bool` is an `int` subclass
# (`True == 1`, `False == 0`) and Pydantic's lax `int` coercion accepts it.
# Without an explicit guard, `process_status=True` silently became `1` (TODO)
# and `priority=True` became `1` (LOW) instead of 422ing. The error format is
# the SAME "must be one of (...), got <repr>" contract pinned above — these
# tests just lock that a bool `<repr>` (`True`/`False`) now reaches it too.
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [True, False])
def test_task_create_process_status_rejects_bool(bad: bool) -> None:
    with pytest.raises(ValidationError) as ei:
        TaskCreate(project_id=1, title="x", process_status=bad)
    assert (
        f"process_status must be one of (1, 2, 3, 4, 5, 6, 8), got {bad!r}"
        in _first_msg(ei.value)
    )


@pytest.mark.parametrize("bad", [True, False])
def test_task_create_priority_rejects_bool(bad: bool) -> None:
    with pytest.raises(ValidationError) as ei:
        TaskCreate(project_id=1, title="x", priority=bad)
    assert f"priority must be one of (1, 2, 3, 4), got {bad!r}" in _first_msg(ei.value)


@pytest.mark.parametrize("bad", [True, False])
def test_task_update_process_status_rejects_bool(bad: bool) -> None:
    with pytest.raises(ValidationError) as ei:
        TaskUpdate(process_status=bad)
    assert (
        f"process_status must be one of (1, 2, 3, 4, 5, 6, 8), got {bad!r}"
        in _first_msg(ei.value)
    )


@pytest.mark.parametrize("bad", [True, False])
def test_task_update_priority_rejects_bool(bad: bool) -> None:
    with pytest.raises(ValidationError) as ei:
        TaskUpdate(priority=bad)
    assert f"priority must be one of (1, 2, 3, 4), got {bad!r}" in _first_msg(ei.value)


def test_task_create_process_status_int_not_confused_with_bool() -> None:
    """Regression lock paired with the bool-rejection tests above: the guard
    must not start rejecting plain ints. `1 == True` under `==`, so the type
    check (not just `==`) is what proves the guard didn't overreach."""
    task1 = TaskCreate(project_id=1, title="x", process_status=1)
    assert task1.process_status == 1
    assert type(task1.process_status) is int
    task5 = TaskCreate(project_id=1, title="x", process_status=5)
    assert task5.process_status == 5


def test_task_update_process_status_int_one_still_accepted() -> None:
    upd = TaskUpdate(process_status=1)
    assert upd.process_status == 1
    assert type(upd.process_status) is int


# -----------------------------------------------------------------------------
# Bool-coercion guard for assigned_role (Kanban #2844) — sibling fix to the
# #2829 guard above. `_make_role_range_validator`'s `isinstance(v, bool)`
# check (an "after"-mode guard) had the identical dead-code defect: Pydantic's
# lax `int` coercion rebuilds `True`/`False` into `1`/`0` before an "after"
# validator ever sees it. `assigned_role=True` silently became `1` instead of
# 422ing. Fixed by `_reject_bool_for_role_field`, a `mode="before"` companion
# wired onto `assigned_role` on both TaskCreate and TaskUpdate. Error format
# is the same range-style "must be NULL or in range X..Y, got <repr>" contract
# pinned above — these tests lock that a bool `<repr>` now reaches it too.
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [True, False])
def test_task_create_role_rejects_bool(bad: bool) -> None:
    with pytest.raises(ValidationError) as ei:
        TaskCreate(project_id=1, title="x", assigned_role=bad)
    assert (
        f"assigned_role must be NULL or in range 1..{TaskRole.RANGE_MAX}, got {bad!r}"
        in _first_msg(ei.value)
    )


@pytest.mark.parametrize("bad", [True, False])
def test_task_update_role_rejects_bool(bad: bool) -> None:
    with pytest.raises(ValidationError) as ei:
        TaskUpdate(assigned_role=bad)
    assert (
        f"assigned_role must be NULL or in range 1..{TaskRole.RANGE_MAX}, got {bad!r}"
        in _first_msg(ei.value)
    )


def test_task_create_role_int_not_confused_with_bool() -> None:
    """Regression lock paired with the bool-rejection tests above: the guard
    must not start rejecting plain ints. `1 == True` under `==`, so the type
    check (not just `==`) is what proves the guard didn't overreach."""
    task5 = TaskCreate(project_id=1, title="x", assigned_role=5)
    assert task5.assigned_role == 5
    assert type(task5.assigned_role) is int
    task51 = TaskCreate(project_id=1, title="x", assigned_role=51)
    assert task51.assigned_role == 51
    assert type(task51.assigned_role) is int


def test_task_update_role_int_still_accepted() -> None:
    upd = TaskUpdate(assigned_role=5)
    assert upd.assigned_role == 5
    assert type(upd.assigned_role) is int
