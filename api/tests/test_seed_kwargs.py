"""Regression for the soft-delete rename: seed.py must use the new column names.

Constructs the Task ORM instances without committing — catches the kwarg/column
mismatch that would otherwise fail at INSERT time on a fresh DB.

The pre-fix bug: seed.py passed `status=TaskStatus.DONE` (=5) etc., which the
ORM mapped to the new soft-delete `status` column (0/1) and would fail
`ck_tasks_status_valid` on a freshly-migrated DB. We assert here that:
- `process_status` is set explicitly to a valid 1..5 lifecycle code, AND
- `status` is NOT set on the constructed instance (so SQLAlchemy applies the
  model-level `default=RecordStatus.ACTIVE` at flush time, landing 1 in the DB).
"""
from __future__ import annotations

from scripts.seed import _sample_tasks
from src.constants import RecordStatus, TaskStatus
from src.models.task import Task


def test_sample_tasks_use_process_status_and_default_active() -> None:
    tasks = _sample_tasks(project_id=1)
    assert len(tasks) == 3
    for t in tasks:
        assert t.process_status in TaskStatus.ALL, (
            f"Task lifecycle code drifted: {t.title!r} has process_status={t.process_status!r}"
        )
        # `status` must NOT be passed by seed.py — it inherits from the model's
        # `default=RecordStatus.ACTIVE` at flush. If a future seed.py mistakenly
        # passes `status=<lifecycle code>` again, this assertion catches it.
        assert t.status is None, (
            f"seed.py must not pass `status=` (it would clobber the soft-delete "
            f"flag); {t.title!r} has status={t.status!r}"
        )

    # Lock the model-side default so the DB row lands at status=1 (ACTIVE) on flush.
    assert Task.__mapper__.columns["status"].default.arg == RecordStatus.ACTIVE
