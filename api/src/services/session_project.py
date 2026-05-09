"""X-Project-Id header gate (Kanban #695, Phase 3 of session-scoped active).

Forces Lead's session-bound project_id onto the wire so a compaction-induced
context loss surfaces as a 400 at the next task-API call instead of a silent
write to the wrong project.

Stable wire detail strings (pinned by source-text-lock tests in
`tests/test_session_project_header.py`):

    "X-Project-Id header is required for task endpoints"
    "task {task_id} does not belong to project_id {session_project_id}"
    "X-Project-Id header {header} does not match request body project_id {body}"

Phase 3 rationale + scope: `context/teams/dev/decisions.md` 2026-05-09
'Session-scoped active project: bootstrap asks user, supports parallel terminals'.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Header, HTTPException


# Source-text-locked detail strings (#122 / #690 pattern). Templates kept as
# module-level constants so source-text-lock tests can scan for them precisely.
_DETAIL_HEADER_MISSING = "X-Project-Id header is required for task endpoints"
_DETAIL_TASK_MISMATCH_TEMPLATE = (
    "task {task_id} does not belong to project_id {session_project_id}"
)
_DETAIL_BODY_MISMATCH_TEMPLATE = (
    "X-Project-Id header {header} does not match request body project_id {body}"
)


async def require_project_id_header(
    x_project_id: Annotated[int | None, Header(alias="X-Project-Id")] = None,
) -> int:
    """FastAPI dependency: extract X-Project-Id header. 400 if missing.

    Pydantic Header() handles non-int with 422 automatically; we 400 on
    `None` so the wire contract is uniform for the missing case.
    """
    if x_project_id is None:
        raise HTTPException(status_code=400, detail=_DETAIL_HEADER_MISSING)
    return x_project_id


def assert_task_belongs_to_session(
    task_id: int, task_project_id: int, session_project_id: int
) -> None:
    """Raise 400 if a fetched task's project_id != the session header value."""
    if task_project_id != session_project_id:
        raise HTTPException(
            status_code=400,
            detail=_DETAIL_TASK_MISMATCH_TEMPLATE.format(
                task_id=task_id, session_project_id=session_project_id
            ),
        )


def assert_body_matches_session(
    body_project_id: int, session_project_id: int
) -> None:
    """Raise 400 on POST when the request body's project_id doesn't match the header."""
    if body_project_id != session_project_id:
        raise HTTPException(
            status_code=400,
            detail=_DETAIL_BODY_MISMATCH_TEMPLATE.format(
                header=session_project_id, body=body_project_id
            ),
        )
