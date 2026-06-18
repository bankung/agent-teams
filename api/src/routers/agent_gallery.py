"""HTTP routes for the agent gallery (Kanban #1017).

Mounted at ``/api/agents``. Platform-level resource (like ``/api/agents/validate``
in ``routers/agent_validation.py``) — NO ``X-Project-Id`` header. The agent
files belong to the agent-teams platform, not to any one bound project.

Endpoints (both GET-only):

  GET /api/agents
    Flat array of agent summaries, sorted by name. Built on the #1016 validator
    so an invalid file still appears (``valid=false`` + its diagnostics). Pure
    filesystem read — no DB touch.

  GET /api/agents/{name}
    Everything in the summary PLUS ``raw_frontmatter``, ``full_description``,
    and recent cross-project ``spawns`` (the ONE DB read in this feature).
    ``name`` is validated against ``AGENT_NAME_RE`` (404 on a bad shape — this
    blocks path-traversal-shaped input like ``..%2fetc`` before any lookup),
    then resolved by matching the SCANNED listing (client input is NEVER joined
    onto a filesystem path). Unknown name → 404.

The filesystem scan is synchronous; it is dispatched to the anyio thread pool so
the event loop is not blocked by directory I/O over the bind mount (same
discipline as ``routers/agent_validation.py`` / ``routers/task_outputs.py``).
"""

from __future__ import annotations

from pathlib import Path

import anyio
import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi import Path as PathParam
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_session
from src.schemas.agent_metadata import (
    AGENT_NAME_RE,
    AgentDetail,
    AgentSummary,
    AgentWrite,
)
from src.services.agent_spawns import fetch_agent_spawns
from src.services.agent_validation import (
    AgentPathError,
    assemble_agent_file,
    confine_agent_path,
    default_agents_dir,
    get_agent_summary,
    list_agents,
    validate_candidate_agent_file,
    validate_hooks_structure,
    write_agent_file_atomic,
)
from src.services.operator_auth import OperatorDecision, require_operator_proof
from src.settings import get_settings

# Platform-level (no X-Project-Id) router. Mounted at /api/agents in main.py,
# alongside the #1016 validator router. The two share the /agents prefix; route
# paths do not collide (`/validate` vs `/` and `/{name}`).
router = APIRouter(prefix="/agents", tags=["agent-gallery"])


@router.get("", response_model=list[AgentSummary])
async def list_agents_endpoint() -> list[dict[str, object]]:
    """List every ``.claude/agents/*.md`` agent as a gallery summary row.

    Returns a flat array sorted by ``name``. Underscore-prefixed includes are
    skipped (same rule as the validator). Invalid files appear with
    ``valid=false`` and their diagnostics in ``validation_errors``.
    """
    repo_root = Path(get_settings().repo_root)
    agents_dir = default_agents_dir(repo_root)
    return await anyio.to_thread.run_sync(lambda: list_agents(agents_dir))


@router.get("/{name}", response_model=AgentDetail)
async def get_agent_detail_endpoint(
    name: str = PathParam(...),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Return one agent's full detail + recent cross-project spawn history.

    404 when ``name`` fails the agent-name regex (blocks traversal-shaped /
    malformed input before any filesystem or DB work) or when no agent file in
    the scanned directory carries that name. The spawn history is a single
    read-only query against ``tasks.subagent_models``.
    """
    # Regex gate FIRST — a name that cannot be a valid agent name cannot match
    # any scanned file, and rejecting it here keeps traversal-shaped input
    # (``..%2fetc``, ``foo/bar``) from ever reaching the lookup.
    if not AGENT_NAME_RE.fullmatch(name):
        raise HTTPException(status_code=404, detail=f"Unknown agent {name!r}")

    repo_root = Path(get_settings().repo_root)
    agents_dir = default_agents_dir(repo_root)
    summary = await anyio.to_thread.run_sync(
        lambda: get_agent_summary(agents_dir, name)
    )
    if summary is None:
        raise HTTPException(status_code=404, detail=f"Unknown agent {name!r}")

    spawns = await fetch_agent_spawns(session, name)
    # `summary` already carries raw_frontmatter + full_description (computed in
    # the service); attach the spawn history and let AgentDetail serialize.
    return {**summary, "spawns": spawns}


# ===========================================================================
# Gated WRITE endpoints (Kanban #2481) — operator-proof create/edit.
#
# These are SENSITIVE: writing `.claude/agents/*.md` from the UI mutates what
# Claude Code loads as agents at session start. Every write is behind the
# #1857 operator-proof gate (mirrors `routers/tools_email.py`). The gate
# fail-OPENS when OPERATOR_ACTION_KEY is unset (dormant in dev; the operator
# activates it by setting the key) — that is intended.
#
# Pipeline (both endpoints): operator-proof 403 → AgentWrite (Pydantic 422 on a
# bad/unknown field) → confine the path (422 on a bad name / traversal) →
# POST: 409 if exists / PUT: 404 if absent → assemble + validate the candidate
# (422 with diagnostics on any error) → atomic write → return the fresh summary.
# NO partial/invalid file is ever written.
# ===========================================================================

# Stable 403 detail (a future source-text-lock test can scan for it). Mirrors
# the `operator_proof_required:` convention of routers/tools_email.py.
_DETAIL_OPERATOR_PROOF_REQUIRED = (
    "operator_proof_required: writing an agent definition requires operator "
    "proof (X-Operator-Token). This surface mutates what Claude Code loads as "
    "agents; an AI agent cannot self-authorize it."
)


def _require_operator_or_403(operator_proof: OperatorDecision) -> None:
    """Raise 403 unless this request carries a valid operator proof.

    ``require_operator_proof`` fail-OPENS (returns OPERATOR for any request)
    when OPERATOR_ACTION_KEY is unset, so on the dormant dev deployment this is
    a no-op; once the operator activates the gate, a write with no/invalid
    token raises 403 here BEFORE any filesystem touch.
    """
    if operator_proof is not OperatorDecision.OPERATOR:
        raise HTTPException(status_code=403, detail=_DETAIL_OPERATOR_PROOF_REQUIRED)


def _agents_dir() -> Path:
    """Resolve the canonical agents dir (single place the write routes anchor)."""
    return default_agents_dir(Path(get_settings().repo_root))


def _build_validate_write(agents_dir: Path, name: str, payload: AgentWrite) -> None:
    """Confine → assemble → validate → atomically write the candidate file.

    Synchronous (filesystem-bound); the route offloads it via
    ``anyio.to_thread.run_sync``. Raises:
      * ``AgentPathError`` — bad name / traversal (router maps to 422).
      * a list of error diagnostics surfaced as ``_CandidateInvalid`` (422).
    The existence pre-check (409/404) is done by the CALLER before this runs, so
    a confirmed-OK create/edit lands here.
    """
    target = confine_agent_path(agents_dir, name)

    # Only emit frontmatter keys the caller actually set (model/tools/hooks/scope
    # absent = inherit/omit, NOT written as null). name + description always.
    fields: dict[str, object] = {
        "name": name,
        "description": payload.description,
    }
    if payload.model is not None:
        fields["model"] = payload.model
    if payload.tools is not None:
        fields["tools"] = payload.tools
    if payload.hooks is not None:
        fields["hooks"] = payload.hooks
    if payload.scope is not None:
        fields["scope"] = payload.scope

    # Structural hooks validation (write-path ONLY — not in validate_agents_dir).
    # A well-formed hooks block can still carry any shell command; the
    # operator-proof gate is the authorization control for that.
    hooks_errors = validate_hooks_structure(payload.hooks)
    if hooks_errors:
        raise _CandidateInvalid(
            [
                {
                    "file": f"{name}.md",
                    "line": 1,
                    "field": "hooks",
                    "message": msg,
                    "severity": "error",
                }
                for msg in hooks_errors
            ]
        )

    file_text = assemble_agent_file(fields, payload.body)

    errors = validate_candidate_agent_file(name, file_text)
    if errors:
        raise _CandidateInvalid(errors)

    write_agent_file_atomic(target, file_text)


class _CandidateInvalid(Exception):
    """Internal: the assembled candidate failed the file validator.

    Carries the error-severity diagnostics so the route can 422 with the
    validator's own ``{file, line, field, message, severity}`` shape — the same
    diagnostics surface as ``GET /api/agents/validate``.
    """

    def __init__(self, diagnostics: list[dict[str, object]]) -> None:
        super().__init__("candidate agent file failed validation")
        self.diagnostics = diagnostics


@router.post("", response_model=AgentSummary, status_code=status.HTTP_201_CREATED)
async def create_agent_endpoint(
    payload: AgentWrite,
    operator_proof: OperatorDecision = Depends(require_operator_proof),
) -> dict[str, object]:
    """Create a new ``.claude/agents/{name}.md`` (operator-gated).

    409 if an agent with that name already exists (use PUT to edit). 422 on a
    bad/unknown body field, a bad name, or a candidate that fails the frontmatter
    validator. 201 + the created agent's summary on success.
    """
    _require_operator_or_403(operator_proof)

    agents_dir = _agents_dir()
    name = payload.name

    # 409 — name already taken. Resolve by the SCANNED listing (same posture as
    # the gallery: client `name` is matched against scanned files, never joined
    # blindly). A reserved name (`validate`) returns None here and would also be
    # caught by the candidate validator (reserved-name ERROR) → 422; the 409
    # check simply does not fire for it.
    existing = await anyio.to_thread.run_sync(
        lambda: get_agent_summary(agents_dir, name)
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"agent {name!r} already exists; use PUT to edit it",
        )

    try:
        await anyio.to_thread.run_sync(
            lambda: _build_validate_write(agents_dir, name, payload)
        )
    except AgentPathError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except _CandidateInvalid as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "agent frontmatter is invalid; nothing was written",
                "diagnostics": exc.diagnostics,
            },
        ) from exc

    created = await anyio.to_thread.run_sync(
        lambda: get_agent_summary(agents_dir, name)
    )
    if created is None:  # pragma: no cover — write succeeded but scan missed it
        raise HTTPException(
            status_code=500,
            detail="agent written but could not be re-read from the scan",
        )
    return created


@router.put("/{name}", response_model=AgentSummary)
async def update_agent_endpoint(
    payload: AgentWrite,
    name: str = PathParam(...),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
) -> dict[str, object]:
    """Edit an existing ``.claude/agents/{name}.md`` (operator-gated).

    The PATH ``{name}`` is authoritative; the body ``name`` MUST equal it (else
    422) so the filename and the on-disk frontmatter name can never diverge.
    404 if no agent with that name exists (use POST to create). 422 on a bad
    name/body or a candidate that fails the validator. 200 + the updated summary
    on success.
    """
    _require_operator_or_403(operator_proof)

    # Path is the source of truth for the name. Reject a mismatching body name
    # rather than silently ignoring it (least-surprise; documented in AgentWrite).
    if payload.name != name:
        raise HTTPException(
            status_code=422,
            detail=(
                f"body name {payload.name!r} does not match path name {name!r}; "
                f"the path is authoritative for PUT"
            ),
        )

    agents_dir = _agents_dir()

    # Regex-gate the PATH name FIRST (mirrors the GET detail route): a name that
    # cannot be a valid agent name cannot match any scanned file → 404, and this
    # blocks traversal-shaped path input before any fs work.
    if not AGENT_NAME_RE.fullmatch(name):
        raise HTTPException(status_code=404, detail=f"Unknown agent {name!r}")

    existing = await anyio.to_thread.run_sync(
        lambda: get_agent_summary(agents_dir, name)
    )
    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"agent {name!r} does not exist; use POST to create it",
        )

    try:
        await anyio.to_thread.run_sync(
            lambda: _build_validate_write(agents_dir, name, payload)
        )
    except AgentPathError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except _CandidateInvalid as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "agent frontmatter is invalid; nothing was written",
                "diagnostics": exc.diagnostics,
            },
        ) from exc

    updated = await anyio.to_thread.run_sync(
        lambda: get_agent_summary(agents_dir, name)
    )
    if updated is None:  # pragma: no cover — write succeeded but scan missed it
        raise HTTPException(
            status_code=500,
            detail="agent written but could not be re-read from the scan",
        )
    return updated
