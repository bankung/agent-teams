"""Cross-table invariant for tasks.task_kind ↔ tasks.run_mode (Kanban #706).

Why app-layer over DB CHECK: the cross-validator in `services/run_mode.py`
spans the projects table (auto_run_consent_at) and so MUST be app-layer
(no DB CHECK can reach across rows in another table). Keeping task_kind ↔
run_mode in the same service-layer slot makes the invariant set easy to
audit — every cross-table rule is a `services/<rule>.py` file fired from
the two routers.

Detail strings are source-text-locked by `tests/test_task_kind_recurrence.py`.
"""

from __future__ import annotations

from fastapi import HTTPException

from src.constants import TaskKind, TaskRunMode
from src.schemas.task import TaskKindLiteral, TaskRunModeLiteral


def assert_run_mode_for_kind(
    task_kind: TaskKindLiteral, run_mode: TaskRunModeLiteral
) -> None:
    """Raise 400 if task_kind='human' is paired with run_mode != 'manual'.

    Caller contract:
    - POST /api/tasks: pass `payload.task_kind` and `payload.run_mode`.
    - PATCH /api/tasks/{id}: pass the RESOLVED final values for both fields
      (PATCH-supplied if present, else the existing row's value).

    Kanban #714 MIN-2 (2026-05-11): params narrowed from `str` to the
    schema-level Literals so static-type tooling (mypy/pyright) catches
    drift at the call sites. The Literals stay in lockstep with the
    `*.ALL` tuples via the import-time guard in `src/schemas/task.py`.
    """
    if task_kind == TaskKind.HUMAN and run_mode != TaskRunMode.MANUAL:
        raise HTTPException(
            status_code=400,
            detail=f"task_kind 'human' is incompatible with run_mode '{run_mode}'",
        )
