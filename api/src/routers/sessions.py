"""HTTP routes for sessions / session_runs / session_compacts (CTX-1).

Mounted at `/api/sessions` (and `/api/session_runs/{id}` for the run PATCH).
NO `X-Project-Id` header is required — sessions follow the project-endpoint
convention; the URL identifies the resource (session_id) and the session row
itself carries `project_id`. (Same pattern as `/api/projects/{id}`.)

CTX-1 surface (7 read/write endpoints + 1 read-only compacts list = 8):
- POST   /api/sessions                       create + create FS skeleton
- GET    /api/sessions                       list (filterable by project + status)
- GET    /api/sessions/{id}                  detail with runs/compacts counts
- PATCH  /api/sessions/{id}                  partial update (closed is terminal)
- POST   /api/sessions/{id}/runs             register a run (+ optional card file)
- PATCH  /api/session_runs/{id}              update a run's totals/status
- GET    /api/sessions/{id}/runs             list runs in a session
- GET    /api/sessions/{id}/compacts         list compact events (CTX-4 owns POST)

Source-text-locked detail strings (per #122 / #690 pattern, pinned by tests):

    "Session id={id} already closed"
    "task {task_id} belongs to project {task_project_id}, "
        "session belongs to project {session_project_id}"
    "Session id={id} not found"
    "Session run id={id} not found"
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func
from sqlalchemy.sql.elements import ClauseElement

logger = logging.getLogger(__name__)

from src.constants import RecordStatus
from src.db import get_or_404, get_session
from src.models.project import Project
from src.models.session import Session as SessionModel
from src.models.session import SessionCompact, SessionRun
from src.models.task import Task
from src.schemas.session import (
    SessionActivityCreate,
    SessionActivityRead,
    SessionCompactRead,
    SessionCreate,
    SessionPromptRead,
    SessionRead,
    SessionRunCreate,
    SessionRunHeartbeat,
    SessionRunHeartbeatRead,
    SessionRunRead,
    SessionRunStatusLiteral,
    SessionRunUpdate,
    SessionStatusLiteral,
    SessionUpdate,
)
from src.services.session_files import (
    create_card_log_skeleton,
    create_session_skeleton,
)
from src.services.cost_tracker import compute_cost
from src.services.session_store import (
    SECTION_RECENT_ACTIVITY,
    append_recent_activity,
    get_section_text,
    read_session_for_prompt,
    write_card_log,
)
from src.services.token_counter import count_tokens
from src.settings import get_settings

router = APIRouter(prefix="/sessions", tags=["sessions"])
runs_router = APIRouter(prefix="/session_runs", tags=["sessions"])

# Source-text-locked detail strings (#122 / #690 pattern).
_DETAIL_SESSION_CLOSED_TEMPLATE = "Session id={id} already closed"
# Shared by /runs (POST) and /activity (POST) — both reject when the supplied
# task_id belongs to a different project than the session.
_DETAIL_CROSS_PROJECT_TEMPLATE = (
    "task {task_id} belongs to project {task_project_id}, "
    "session belongs to project {session_project_id}"
)
_DETAIL_SESSION_NOT_FOUND_TEMPLATE = "Session id={id} not found"
_DETAIL_RUN_NOT_FOUND_TEMPLATE = "Session run id={id} not found"
_DETAIL_RUN_ON_CLOSED_SESSION_TEMPLATE = (
    "Session id={id} is closed; cannot create runs"
)
# CTX-2 (Kanban #717) — locked detail strings for the 3 new endpoints.
_DETAIL_ACTIVITY_ON_CLOSED_SESSION_TEMPLATE = (
    "Session id={id} is closed; cannot append activity"
)
_DETAIL_HEARTBEAT_ON_RUNLESS_TEMPLATE = (
    "Session run id={id} has no task_id; heartbeat requires a card log"
)
_DETAIL_HEARTBEAT_ON_CLOSED_SESSION_TEMPLATE = (
    "Session id={id} is closed; cannot write heartbeat"
)

_ACTIVITY_PREVIEW_CHARS = 2000


async def _runs_count(db: AsyncSession, session_id: int) -> int:
    n = await db.scalar(
        select(func.count())
        .select_from(SessionRun)
        .where(SessionRun.session_id == session_id)
    )
    return int(n or 0)


async def _compacts_count(db: AsyncSession, session_id: int) -> int:
    n = await db.scalar(
        select(func.count())
        .select_from(SessionCompact)
        .where(SessionCompact.session_id == session_id)
    )
    return int(n or 0)


def _to_session_read(
    row: SessionModel, *, runs_count: int = 0, compacts_count: int = 0
) -> SessionRead:
    base = SessionRead.model_validate(row)
    # `runs_count` / `compacts_count` are NOT ORM columns — set explicitly.
    return base.model_copy(
        update={"runs_count": runs_count, "compacts_count": compacts_count}
    )


# =============================================================================
# Sessions
# =============================================================================


@router.post(
    "",
    response_model=SessionRead,
    status_code=http_status.HTTP_201_CREATED,
)
async def create_session(
    payload: SessionCreate,
    db: AsyncSession = Depends(get_session),
) -> SessionRead:
    """Create a session row + filesystem skeleton.

    Server computes `session_root_path = "_sessions/<id>/"` post-INSERT (id is
    autogenerated; we INSERT a placeholder, refresh to get the id, then
    UPDATE the path — single COMMIT either way). The filesystem skeleton
    (`session.md` + `archive/` + `cards/`) is created via
    `services.session_files.create_session_skeleton`.
    """
    # Validate project FK BEFORE INSERT — friendlier 400 than waiting for the
    # IntegrityError. Mirror of how routers/tasks.py validates parent_task_id.
    proj_exists = await db.scalar(
        select(Project.id).where(
            Project.id == payload.project_id,
            Project.status == RecordStatus.ACTIVE,
        )
    )
    if proj_exists is None:
        raise HTTPException(
            status_code=400,
            detail=f"project_id {payload.project_id} does not exist",
        )

    # Placeholder root path — overwritten with the real `_sessions/<id>/`
    # after the row gets its identity-generated id. Ceilings: only forward
    # explicit overrides (None → let the DB `server_default` apply).
    optional_ceilings = {
        k: v
        for k, v in {
            "compacted_history_ceiling_tokens": payload.compacted_history_ceiling_tokens,
            "recent_activity_ceiling_tokens": payload.recent_activity_ceiling_tokens,
            "card_detail_ceiling_tokens": payload.card_detail_ceiling_tokens,
            "output_budget_tokens": payload.output_budget_tokens,
        }.items()
        if v is not None
    }
    row = SessionModel(
        project_id=payload.project_id,
        process_label=payload.process_label,
        token_budget_per_run=payload.token_budget_per_run,
        session_root_path="_sessions/pending/",
        **optional_ceilings,
    )
    db.add(row)
    await db.flush()  # populates row.id without committing

    row.session_root_path = f"_sessions/{row.id}/"
    await db.commit()
    await db.refresh(row)

    # Create the filesystem skeleton AFTER commit — if the FS write fails we
    # want the audit row to exist (the row's session_root_path is still
    # correct; CTX-2's writer will create the dir on first append if it's
    # missing). Idempotent.
    repo_root = get_settings().repo_root
    create_session_skeleton(row.id, repo_root)

    return _to_session_read(row, runs_count=0, compacts_count=0)


@router.get("", response_model=list[SessionRead])
async def list_sessions(
    project_id: int | None = Query(default=None, ge=1),
    session_status: SessionStatusLiteral | None = Query(
        default=None,
        alias="status",
        description="Filter by session status: active | compacting | closed",
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_session),
) -> list[SessionRead]:
    stmt = select(SessionModel)
    if project_id is not None:
        stmt = stmt.where(SessionModel.project_id == project_id)
    if session_status is not None:
        stmt = stmt.where(SessionModel.status == session_status)
    stmt = stmt.order_by(SessionModel.id.asc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    # CTX-1 list endpoint returns counts=0 to keep this cheap (one extra query
    # per row would be N+1). Detail GET fills the real counts.
    return [_to_session_read(r) for r in rows]


@router.get("/{session_id}", response_model=SessionRead)
async def get_session_detail(
    session_id: int,
    db: AsyncSession = Depends(get_session),
) -> SessionRead:
    row = await get_or_404(
        db,
        SessionModel,
        detail=_DETAIL_SESSION_NOT_FOUND_TEMPLATE.format(id=session_id),
        id=session_id,
    )
    rc = await _runs_count(db, session_id)
    cc = await _compacts_count(db, session_id)
    return _to_session_read(row, runs_count=rc, compacts_count=cc)


@router.patch("/{session_id}", response_model=SessionRead)
async def update_session(
    session_id: int,
    payload: SessionUpdate,
    db: AsyncSession = Depends(get_session),
) -> SessionRead:
    row = await get_or_404(
        db,
        SessionModel,
        detail=_DETAIL_SESSION_NOT_FOUND_TEMPLATE.format(id=session_id),
        id=session_id,
    )

    # Closed sessions are terminal — reject any subsequent mutation. Detail
    # source-text-locked.
    if row.status == "closed":
        raise HTTPException(
            status_code=400,
            detail=_DETAIL_SESSION_CLOSED_TEMPLATE.format(id=session_id),
        )

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        # Silent no-op (mirror of N7 in projects/tasks PATCHes).
        rc = await _runs_count(db, session_id)
        cc = await _compacts_count(db, session_id)
        return _to_session_read(row, runs_count=rc, compacts_count=cc)

    # If the caller is closing the session, server-stamp `closed_at`.
    if updates.get("status") == "closed" and row.closed_at is None:
        updates.setdefault("closed_at", func.now())

    changed = False
    for field, value in updates.items():
        if isinstance(value, ClauseElement) or getattr(row, field) != value:
            setattr(row, field, value)
            changed = True

    if changed:
        row.updated_at = func.now()

    await db.commit()
    await db.refresh(row)
    rc = await _runs_count(db, session_id)
    cc = await _compacts_count(db, session_id)
    return _to_session_read(row, runs_count=rc, compacts_count=cc)


# =============================================================================
# SessionRuns
# =============================================================================


@router.post(
    "/{session_id}/runs",
    response_model=SessionRunRead,
    status_code=http_status.HTTP_201_CREATED,
)
async def create_session_run(
    session_id: int,
    payload: SessionRunCreate,
    db: AsyncSession = Depends(get_session),
) -> SessionRun:
    sess = await get_or_404(
        db,
        SessionModel,
        detail=_DETAIL_SESSION_NOT_FOUND_TEMPLATE.format(id=session_id),
        id=session_id,
    )
    if sess.status == "closed":
        raise HTTPException(
            status_code=400,
            detail=_DETAIL_RUN_ON_CLOSED_SESSION_TEMPLATE.format(id=session_id),
        )

    card_log_path: str | None = None
    if payload.task_id is not None:
        # Cross-project rejection — the run's task must belong to the same
        # project as the session. Mirror of routers/tasks.py POST cross-project
        # parent rejection (Kanban #238).
        task = await db.get(Task, payload.task_id)
        if task is None:
            raise HTTPException(
                status_code=400,
                detail=f"task_id {payload.task_id} does not exist or is deleted",
            )
        if task.project_id != sess.project_id:
            raise HTTPException(
                status_code=400,
                detail=_DETAIL_CROSS_PROJECT_TEMPLATE.format(
                    task_id=payload.task_id,
                    task_project_id=task.project_id,
                    session_project_id=sess.project_id,
                ),
            )
        card_log_path = f"_sessions/{session_id}/cards/{payload.task_id}.md"

    run = SessionRun(
        session_id=session_id,
        task_id=payload.task_id,
        status=payload.status,
        card_log_path=card_log_path,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    # Filesystem skeleton AFTER commit — same pattern as session create.
    if payload.task_id is not None:
        repo_root = get_settings().repo_root
        create_card_log_skeleton(session_id, payload.task_id, repo_root)

    return run


@runs_router.patch("/{run_id}", response_model=SessionRunRead)
async def update_session_run(
    run_id: int,
    payload: SessionRunUpdate,
    db: AsyncSession = Depends(get_session),
) -> SessionRun:
    run = await get_or_404(
        db,
        SessionRun,
        detail=_DETAIL_RUN_NOT_FOUND_TEMPLATE.format(id=run_id),
        id=run_id,
    )

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return run

    # CTX-3 (#718): server-authoritative cost. Capture provider/model + drop
    # them (not persisted columns), and DROP any client-supplied
    # total_cost_usd (server overwrites). When all 4 cost-inputs present,
    # compute via cost_tracker and stamp the column.
    provider = updates.pop("provider", None)
    model = updates.pop("model", None)
    updates.pop("total_cost_usd", None)  # server-managed; client value ignored.

    input_tokens = updates.get("total_input_tokens")
    output_tokens = updates.get("total_output_tokens")
    if (
        provider is not None
        and model is not None
        and input_tokens is not None
        and output_tokens is not None
    ):
        try:
            updates["total_cost_usd"] = compute_cost(
                provider, model, input_tokens, output_tokens
            )
        except ValueError as exc:
            # Unknown (provider, model). Don't fail the PATCH — log + leave column.
            logger.warning(
                "session_runs cost lookup failed: run_id=%d provider=%r model=%r err=%s",
                run_id,
                provider,
                model,
                exc,
            )

    # Auto-stamp `finished_at` when status transitions to a terminal state.
    new_status = updates.get("status")
    terminal_states: set[str] = {"done", "error", "timeout"}
    if (
        new_status is not None
        and new_status in terminal_states
        and run.finished_at is None
        and updates.get("finished_at") is None
    ):
        updates["finished_at"] = func.now()

    changed = False
    for field, value in updates.items():
        if isinstance(value, ClauseElement) or getattr(run, field) != value:
            setattr(run, field, value)
            changed = True

    # CTX-3 soft-warn: if post-update input tokens exceed
    # sessions.token_budget_per_run, set budget_warning=true + log WARNING.
    # Never fails the PATCH (soft enforcement).
    if input_tokens is not None:
        sess = await db.get(SessionModel, run.session_id)
        if sess is not None and sess.token_budget_per_run is not None:
            if input_tokens > sess.token_budget_per_run:
                if not run.budget_warning:
                    run.budget_warning = True
                    changed = True
                logger.warning(
                    "session_runs.budget_warning fired: session_id=%d run_id=%d current=%d budget=%d over_by=%d",
                    sess.id,
                    run_id,
                    input_tokens,
                    sess.token_budget_per_run,
                    input_tokens - sess.token_budget_per_run,
                )

    if changed:
        run.updated_at = func.now()

    await db.commit()
    await db.refresh(run)
    return run


@router.get("/{session_id}/runs", response_model=list[SessionRunRead])
async def list_session_runs(
    session_id: int,
    run_status: SessionRunStatusLiteral | None = Query(
        default=None,
        alias="status",
        description="Filter by run status: running | done | error | timeout",
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_session),
) -> list[SessionRun]:
    # Validate session exists — surfaces 404 for a stale id rather than
    # returning an empty list (which would be ambiguous).
    await get_or_404(
        db,
        SessionModel,
        detail=_DETAIL_SESSION_NOT_FOUND_TEMPLATE.format(id=session_id),
        id=session_id,
    )
    stmt = select(SessionRun).where(SessionRun.session_id == session_id)
    if run_status is not None:
        stmt = stmt.where(SessionRun.status == run_status)
    stmt = stmt.order_by(SessionRun.id.asc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


# =============================================================================
# CTX-2 — activity append + prompt read + run heartbeat (Kanban #717)
# =============================================================================


@router.post(
    "/{session_id}/activity",
    response_model=SessionActivityRead,
    status_code=http_status.HTTP_201_CREATED,
)
async def append_session_activity(
    session_id: int,
    payload: SessionActivityCreate,
    db: AsyncSession = Depends(get_session),
) -> SessionActivityRead:
    """Append a Recent Activity entry to the session.md on disk."""
    sess = await get_or_404(
        db,
        SessionModel,
        detail=_DETAIL_SESSION_NOT_FOUND_TEMPLATE.format(id=session_id),
        id=session_id,
    )
    if sess.status == "closed":
        raise HTTPException(
            status_code=400,
            detail=_DETAIL_ACTIVITY_ON_CLOSED_SESSION_TEMPLATE.format(
                id=session_id
            ),
        )

    if payload.task_id is not None:
        task = await db.get(Task, payload.task_id)
        if task is None:
            raise HTTPException(
                status_code=400,
                detail=f"task_id {payload.task_id} does not exist or is deleted",
            )
        if task.project_id != sess.project_id:
            raise HTTPException(
                status_code=400,
                detail=_DETAIL_CROSS_PROJECT_TEMPLATE.format(
                    task_id=payload.task_id,
                    task_project_id=task.project_id,
                    session_project_id=sess.project_id,
                ),
            )

    repo_root = get_settings().repo_root
    block = append_recent_activity(
        session_id,
        summary=payload.summary,
        task_id=payload.task_id,
        role=payload.role,
        kind=payload.kind,
        repo_root=repo_root,
    )
    section_body = get_section_text(
        session_id, SECTION_RECENT_ACTIVITY, repo_root
    )
    preview = section_body[-_ACTIVITY_PREVIEW_CHARS:]

    # CTX-3 (#718): advisory token-budget signal. Recent Activity vs ceiling.
    recent_tokens = count_tokens(section_body)
    recent_ceiling = int(sess.recent_activity_ceiling_tokens)
    compact_recommended = recent_tokens > recent_ceiling

    return SessionActivityRead(
        appended_block=block,
        section_preview=preview,
        section_chars=len(section_body),
        compact_recommended=compact_recommended,
        current_recent_tokens=recent_tokens,
        recent_ceiling_tokens=recent_ceiling,
    )


@router.get("/{session_id}/prompt", response_model=SessionPromptRead)
async def get_session_prompt(
    session_id: int,
    include_card_id: int | None = Query(default=None, ge=1),
    db: AsyncSession = Depends(get_session),
) -> SessionPromptRead:
    """Return the prompt-ready markdown + char count for LLM injection."""
    await get_or_404(
        db,
        SessionModel,
        detail=_DETAIL_SESSION_NOT_FOUND_TEMPLATE.format(id=session_id),
        id=session_id,
    )
    repo_root = get_settings().repo_root
    markdown, chars = read_session_for_prompt(
        session_id, repo_root, include_card_id=include_card_id
    )
    return SessionPromptRead(markdown=markdown, char_count=chars)


@runs_router.post(
    "/{run_id}/heartbeat",
    response_model=SessionRunHeartbeatRead,
    status_code=http_status.HTTP_201_CREATED,
)
async def write_run_heartbeat(
    run_id: int,
    payload: SessionRunHeartbeat,
    db: AsyncSession = Depends(get_session),
) -> SessionRunHeartbeatRead:
    """Append (or replace) a heartbeat block in the run's card log file."""
    run = await get_or_404(
        db,
        SessionRun,
        detail=_DETAIL_RUN_NOT_FOUND_TEMPLATE.format(id=run_id),
        id=run_id,
    )
    if run.task_id is None:
        raise HTTPException(
            status_code=400,
            detail=_DETAIL_HEARTBEAT_ON_RUNLESS_TEMPLATE.format(id=run_id),
        )
    sess = await db.get(SessionModel, run.session_id)
    if sess is None:
        # Defensive — session_runs.session_id has ON DELETE CASCADE so this
        # path is unreachable in practice; surface a 404 if it ever happens.
        raise HTTPException(
            status_code=404,
            detail=_DETAIL_SESSION_NOT_FOUND_TEMPLATE.format(id=run.session_id),
        )
    if sess.status == "closed":
        raise HTTPException(
            status_code=400,
            detail=_DETAIL_HEARTBEAT_ON_CLOSED_SESSION_TEMPLATE.format(
                id=sess.id
            ),
        )

    repo_root = get_settings().repo_root
    card_path = write_card_log(
        run.session_id,
        run.task_id,
        payload.content,
        mode=payload.mode,
        repo_root=repo_root,
    )
    return SessionRunHeartbeatRead(
        card_log_path=str(card_path.relative_to(repo_root)).replace("\\", "/"),
        total_bytes=card_path.stat().st_size,
    )


@router.get("/{session_id}/compacts", response_model=list[SessionCompactRead])
async def list_session_compacts(
    session_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_session),
) -> list[SessionCompact]:
    await get_or_404(
        db,
        SessionModel,
        detail=_DETAIL_SESSION_NOT_FOUND_TEMPLATE.format(id=session_id),
        id=session_id,
    )
    stmt = (
        select(SessionCompact)
        .where(SessionCompact.session_id == session_id)
        .order_by(SessionCompact.id.asc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())
