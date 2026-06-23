"""Zero-LLM 3-mode search over a project's `shared/` memory corpus — Kanban #1678.

Mounts `GET /api/projects/{project_id}/shared/search` with a `mode` query param
(discovery | scroll | browse). Recall a prior decision/incident from the
project's `shared/*.md` corpus in well under a second with NO LLM, NO token
cost. The ranking + chunking + path-guard live in
`services/shared_search.py` (pure, unit-tested); this router is I/O glue:
fetch the project (404), resolve the corpus root, dispatch on `mode`, and map
the service's exceptions to HTTP status.

Corpus-root resolution (per context-layout.md "Path resolution — working_path"):
  * `projects.working_path` set  -> `{working_path}/shared`
  * `projects.working_path` null -> `{REPO_ROOT}/context/projects/{name}/shared`
404 if the project is missing/soft-deleted OR its resolved corpus root doesn't
exist on disk.

Read-only: every mode reads files; nothing here writes or mutates `shared/`.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_active_project_or_404, get_session
from src.services.session_project import require_project_id_header
from src.schemas.shared_search import (
    BrowseResponse,
    DiscoveryResponse,
    ScrollResponse,
)
from src.services import shared_search as svc
from src.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_id}/shared", tags=["shared-search"])

_VALID_MODES = ("discovery", "scroll", "browse")


def resolve_corpus_root(working_path: str | None, project_name: str, repo_root: Path) -> Path:
    """Resolve the per-project `shared/` corpus root.

    Mirrors `context-layout.md` "Path resolution — projects.working_path":
      * working_path set  -> Path(working_path) / "shared"
      * working_path null -> repo_root / context/projects/<name> / shared

    Pure (no I/O) so it's unit-testable. Existence is checked by the caller.
    The agent-teams project itself has working_path=null, so it takes the
    in-repo fallback branch.
    """
    if working_path and working_path.strip():
        # security: working_path is operator-set (PATCH /api/projects). Cross-project
        # corpus isolation on this branch is DB-granted operator-trust, NOT HTTP-enforced
        # (the project_id==session check guards the HTTP surface, not the DB value).
        wp = Path(working_path.strip())
        if not wp.is_absolute():
            raise ValueError("shared_search: working_path must be absolute")
        return wp / "shared"
    # Second-wall guard: project_name must be a single safe path component.
    if (
        project_name in ("", ".", "..")
        or "/" in project_name
        or "\\" in project_name
        or Path(project_name).parts != (project_name,)
    ):
        raise ValueError("shared_search: unsafe project name")
    return Path(repo_root) / "context" / "projects" / project_name / "shared"


@router.get(
    "/search",
    response_model=None,  # union of 3 typed models; we return the right one per mode
)
async def search_shared_corpus(
    project_id: int,
    mode: str = Query(
        default="discovery",
        description="One of: discovery (default) | scroll | browse.",
    ),
    q: str | None = Query(
        default=None,
        max_length=1000,
        description="discovery: the search query (required for discovery).",
    ),
    limit: int = Query(
        default=10,
        ge=1,
        le=100,
        description="discovery: max results (default 10, max 100).",
    ),
    file: str | None = Query(
        default=None,
        max_length=4096,
        description=(
            "scroll: the file (relative path within the corpus, required). "
            "browse: optional file to scope the heading tree to."
        ),
    ),
    line: int = Query(
        default=1,
        ge=1,
        description="scroll: 1-based start line of the window (default 1).",
    ),
    window: int = Query(
        default=40,
        ge=1,
        le=1000,
        description="scroll: number of lines in the window (default 40, max 1000).",
    ),
    session: AsyncSession = Depends(get_session),
    session_project_id: int = Depends(require_project_id_header),
) -> DiscoveryResponse | ScrollResponse | BrowseResponse:
    """Search a project's shared/ corpus in one of three modes.

    * **discovery** (default) — BM25-rank chunks for `q`; top-`limit`.
    * **scroll** — return a `window` of lines around `line` in `file`.
    * **browse** — heading tree for `file`, or the whole corpus when `file` omitted.

    Status codes:
      * 404 — project missing/soft-deleted, OR its corpus root doesn't exist,
              OR (scroll/browse) the requested `file` doesn't exist in the corpus.
      * 422 — unknown `mode`, or `mode=discovery` with no `q`, or out-of-range
              query params (handled by FastAPI for ge/le).
      * 400 — `file` escapes the corpus root (path-traversal attempt).
    """
    if mode not in _VALID_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"unknown mode {mode!r}; expected one of {', '.join(_VALID_MODES)}",
        )

    # Session-project binding: url project_id must match X-Project-Id header (400).
    # MUST run BEFORE the DB lookup to avoid a project-existence oracle: a 404 on
    # an unknown id vs 400 on a known-but-mismatched id would let callers enumerate
    # project ids by observing which status code they receive.
    if project_id != session_project_id:
        raise HTTPException(
            status_code=400,
            detail=f"project_id {project_id} does not match session project_id {session_project_id}",
        )

    # Project must exist + be active (404).
    project = await get_active_project_or_404(session, project_id)

    settings = get_settings()
    try:
        root = resolve_corpus_root(project.working_path, project.name, settings.repo_root)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not root.is_dir():
        raise HTTPException(
            status_code=404,
            detail=(
                f"shared/ corpus not found for project id={project_id} "
                f"(resolved root does not exist)"
            ),
        )

    loop = asyncio.get_running_loop()

    if mode == "discovery":
        if q is None or not q.strip():
            raise HTTPException(
                status_code=422,
                detail="mode=discovery requires a non-empty `q` query parameter",
            )
        payload = await loop.run_in_executor(None, lambda: svc.run_discovery(root, q, limit=limit))
        return DiscoveryResponse(**payload)

    if mode == "scroll":
        if file is None or not file.strip():
            raise HTTPException(
                status_code=422,
                detail="mode=scroll requires a `file` query parameter",
            )
        try:
            payload = await loop.run_in_executor(
                None, lambda: svc.run_scroll(root, file, line=line, window=window)
            )
        except ValueError as exc:
            # Path-traversal guard tripped -> 400.
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail=f"file not found in corpus: {file!r}"
            ) from exc
        return ScrollResponse(**payload)

    # mode == "browse"
    try:
        payload = await loop.run_in_executor(
            None, lambda: svc.run_browse(root, file)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail=f"file not found in corpus: {file!r}"
        ) from exc
    return BrowseResponse(**payload)
