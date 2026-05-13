"""Cross-table invariant for tasks.task_kind ↔ tasks.run_mode (Kanban #706)
and the interaction_kind → task_kind coercion (Kanban #858).

Why app-layer over DB CHECK: the cross-validator in `services/run_mode.py`
spans the projects table (auto_run_consent_at) and so MUST be app-layer
(no DB CHECK can reach across rows in another table). Keeping task_kind ↔
run_mode in the same service-layer slot makes the invariant set easy to
audit — every cross-table rule is a `services/<rule>.py` file fired from
the two routers.

Kanban #858 (2026-05-13) extends this module with
`coerce_task_kind_for_interaction(...)` — a silent server-side coercion:
when `interaction_kind IN ('question','decision')`, `task_kind` is forced
to 'human' AND `run_mode` is forced to 'manual' (Option A — atomic coerce
to keep the HUMAN ↔ MANUAL invariant in `assert_run_mode_for_kind` from
firing on the same call). The reverse flip (back to 'work') does NOT
auto-revert task_kind / run_mode — once a row has been 'human-ized' the
caller is responsible for explicitly re-classifying it if desired.

Detail strings are source-text-locked by `tests/test_task_kind_recurrence.py`.
"""

from __future__ import annotations

from fastapi import HTTPException

from src.constants import TaskInteractionKind, TaskKind, TaskRunMode
from src.schemas.task import TaskKindLiteral, TaskRunModeLiteral


# Kanban #858: interaction_kind values that force task_kind='human'. Module
# constant so the test pin can import it directly (no string drift).
_HUMAN_INTERACTION_KINDS: frozenset[str] = frozenset(
    (TaskInteractionKind.QUESTION, TaskInteractionKind.DECISION)
)


def coerce_task_kind_for_interaction(
    interaction_kind: str | None,
    task_kind: TaskKindLiteral,
    run_mode: TaskRunModeLiteral,
) -> tuple[TaskKindLiteral, TaskRunModeLiteral]:
    """Kanban #858: server-side coerce on the RESOLVED final fields.

    If `interaction_kind` is 'question' or 'decision', force
    `task_kind='human'` AND `run_mode='manual'` regardless of caller input —
    these tasks gate auto-run on an external answer and cannot run headless
    by definition. The atomic run_mode flip (Option A in the spawn brief)
    keeps the HUMAN ↔ MANUAL invariant in `assert_run_mode_for_kind` from
    firing on the same call.

    `interaction_kind=None` is treated as 'work' (no coerce) — covers the
    PATCH "leave unchanged" case where the resolved value lookup returned
    the existing column value, which is non-null in practice but tolerated
    here as a defensive belt.

    No-op when interaction_kind == 'work'. Reverse PATCH (question → work)
    is NOT this function's concern; the caller decides whether to leave
    task_kind alone (current contract — be conservative) or pass an
    explicit `task_kind=ai`.
    """
    if interaction_kind in _HUMAN_INTERACTION_KINDS:
        return TaskKind.HUMAN, TaskRunMode.MANUAL
    return task_kind, run_mode


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
