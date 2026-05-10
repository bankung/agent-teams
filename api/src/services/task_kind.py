"""Cross-table invariant for tasks.task_kind ↔ tasks.run_mode (Kanban #706).

The rule `task_kind = 'human' AND run_mode != 'manual'` is forbidden — human
work is user-driven and cannot be auto-picked or auto-headless'd. This spans
two columns of the same row but lives at the app layer (NOT a DB CHECK)
because it pairs with `services/run_mode.py`'s consent check; both are
"resolved final value" cross-validators called from POST + PATCH /api/tasks
on the values that would land AFTER the write.

Why app-layer over DB CHECK: the cross-validator in services/run_mode.py
spans the projects table (auto_run_consent_at) and so MUST be app-layer
(no DB CHECK can reach across rows in another table). Keeping task_kind ↔
run_mode in the same service-layer slot makes the invariant set easy to
audit — every cross-table rule is a `services/<rule>.py` file fired from the
two routers. The DB layer holds only single-row constraints
(ck_tasks_task_kind_valid + ck_tasks_template_recurrence_complete).

Stable wire detail strings (pinned by source-text-lock test in
`tests/test_task_kind_recurrence.py`):

    "task_kind 'human' is incompatible with run_mode '{run_mode}'"

The detail string interpolates the RESOLVED run_mode (the value AFTER the
PATCH lands) — never the existing value, never the payload value alone.
Mirrors the resolved-final pattern from services/run_mode.py.
"""

from __future__ import annotations

from fastapi import HTTPException

from src.constants import TaskKind, TaskRunMode


def assert_run_mode_for_kind(task_kind: str, run_mode: str) -> None:
    """Raise 400 if task_kind='human' is paired with run_mode != 'manual'.

    No-op for any other combination. Pure-function (no DB I/O) — cheap to
    fire BEFORE the consent check on the POST/PATCH hot path.

    Caller contract:
    - POST /api/tasks: pass `payload.task_kind` and `payload.run_mode`.
    - PATCH /api/tasks/{id}: pass the RESOLVED final values for both fields
      (PATCH-supplied if present, else the existing row's value). Validator
      fires on the post-PATCH state, so e.g. flipping `task_kind='human'`
      while `run_mode='auto_pickup'` already exists fails — the resolved
      final state is incompatible.
    """
    if task_kind == TaskKind.HUMAN and run_mode != TaskRunMode.MANUAL:
        raise HTTPException(
            status_code=400,
            detail=f"task_kind 'human' is incompatible with run_mode '{run_mode}'",
        )
