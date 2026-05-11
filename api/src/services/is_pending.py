"""Cross-state invariant for tasks.is_pending ↔ tasks.process_status (Kanban #750).

Why app-layer over DB CHECK: this slice keeps cross-state validation
lockstep with the other `services/<rule>.py` cross-validators
(task_kind ↔ run_mode, run_mode ↔ consent). A DB CHECK would lock the
two columns into a single-mutation interlock; the resolved-final PATCH
pattern (where the validator runs over `PATCH-supplied if present, else
existing row's value` for BOTH fields) is the right enforcement shape.

Semantics (locked 2026-05-11 with user on amendment to #748):
- `is_pending=true` means "in-flight work that hit a problem and is stuck"
- Only meaningful when `process_status=2` (IN_PROGRESS).
- Backwards / forwards transitions on `process_status` while `is_pending`
  stays true are REJECTED — the validator catches the invalid pair at
  request time. Auto-clearing on backwards transition would hide intent.

Detail string is source-text-locked by `tests/test_task_is_pending.py`.

Validator ordering: this check is PURE (no DB I/O) — cheaper than the
consent gate (which SELECTs the project row). Router calls this AFTER the
task_kind ↔ run_mode check (also pure) and BEFORE the consent gate.
"""

from __future__ import annotations

from fastapi import HTTPException

from src.constants import TaskStatus


def assert_is_pending_with_process_status(
    is_pending: bool, process_status: int
) -> None:
    """Raise 400 if is_pending=True paired with process_status != IN_PROGRESS.

    Caller contract:
    - POST /api/tasks: pass `payload.is_pending` and `payload.process_status`.
    - PATCH /api/tasks/{id}: pass the RESOLVED final values — PATCH-supplied
      if the key is in `model_fields_set`, else the existing row's value.
      Mirrors task_kind / run_mode resolved-final pattern.
    """
    if is_pending and process_status != TaskStatus.IN_PROGRESS:
        raise HTTPException(
            status_code=400,
            detail="is_pending=true requires process_status=2 (in_progress)",
        )
