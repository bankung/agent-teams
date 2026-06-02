"""Email tools router (Kanban #1604 Gmail; #1608 appends Outlook below marker).

Mounts under `/api/tools/email/*`. All endpoints require the `X-Project-Id`
header (via `require_project_id_header`) so the daily-units cap, token store,
and audit log are scoped per project.

Endpoint surface (Gmail):
  POST /auth/gmail/start    → returns {auth_url}
  GET  /auth/gmail/callback → exchanges code, stores creds, returns JSON
  GET  /auth/gmail/status   → {authenticated, email, expires_at}
  POST /gmail/trash         → trash by query OR explicit message_ids
  GET  /gmail/usage         → daily-units counter snapshot

Karpathy cuts:
  - Router calls `gmail_client` directly — no service layer.
  - In-memory token store; lost on container restart.
  - Callback returns JSON, not a redirect.

Coordination with #1608 (Outlook): #1608 appends Outlook routes BELOW the
`# >>> #1608 OUTLOOK ROUTES BELOW` marker at the bottom of this file.
DO NOT touch their section from this router; the two namespaces share only
the `router` object + helpers above the marker.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from src.constants import RecordStatus
from src.db import get_session
from src.models.project import Project
from src.schemas.tools_email import (
    AuthStatusResponse,
    GmailAuthStartResponse,
    GmailCallbackResponse,
    GmailTrashRequest,
    GmailTrashResponse,
    OutlookAuthStartResponse,
    OutlookCallbackResponse,
    OutlookTrashRequest,
    OutlookTrashResponse,
    UsageResponse,
)
from src.services.session_project import (
    optional_agent_role_header,
    require_project_id_header,
)
from src.services.tool_grants import GrantDecision, check_grant
from src.tools.email import gate, gmail_client, outlook_client, token_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tools/email", tags=["tools-email"])

# Cost constants — sourced from Gmail API quota reference (research note
# 2026-05-27 in `_scratch/research-email-tools-2026-05-27.md`).
_TRASH_UNITS_PER_MESSAGE = 20
_LIST_UNITS_PER_CALL = 5  # rough; we charge once per list invocation.

# Hard cap on list-page expansion when resolving a `query`. The bulk-threshold
# gate then applies on the resolved count — this is a separate ceiling so a
# user with `?force=true` still can't accidentally enumerate a 50k-id inbox.
_MAX_LIST_RESULTS = 1000


# ---------------------------------------------------------------------------
# Tool-governance gate (Kanban #1799 P0)
# ---------------------------------------------------------------------------

# 403 detail for a tool-grant denial. Stable string (a future source-text-lock
# test can scan for it). The role + tool are interpolated so the agent's log
# shows exactly which (role, tool) pair was refused.
_DETAIL_TOOL_GRANT_DENIED_TEMPLATE = (
    "tool_grant_denied: role {role!r} is not granted tool {tool!r} for this "
    "project (config.tool_grants). Ask the Lead to add it to the role's "
    "allow-list, or call without an X-Agent-Role header if this role should "
    "be unrestricted."
)


async def _enforce_tool_grant_or_403(
    session: AsyncSession,
    session_project_id: int,
    role: str | None,
    tool_name: str,
) -> None:
    """Run the #1799 per-agent-name tool-governance gate; raise 403 on deny.

    Reads the project's `config` (the JSONB holding `tool_grants`) and calls
    the pure `services.tool_grants.check_grant`. Enforcement is OPT-IN and
    defaults UNRESTRICTED: `tool_grants` absent, or `role` unlisted, or no
    role header -> ALLOW. Only an explicitly-listed role missing the tool ->
    403. The gate writes its own audit row for BOTH allow and deny.

    Wired at the START of the trash handlers, AFTER role resolution and BEFORE
    the daily-cap / bulk / auth gates — a forbidden role should be turned away
    before any OAuth/quota work. A missing project row is treated as
    unrestricted (None config -> ALLOW) rather than 404: the existing handler
    flow never 404s on the project, and the cross-project guard already lives
    in `require_project_id_header`'s session binding.
    """
    config = (
        await session.execute(
            select(Project.config)
            .where(Project.id == session_project_id)
            .where(Project.status == RecordStatus.ACTIVE)
        )
    ).scalar_one_or_none()

    decision = check_grant(
        config, role, tool_name, project_id=session_project_id
    )
    if decision is GrantDecision.DENY:
        raise HTTPException(
            status_code=403,
            detail=_DETAIL_TOOL_GRANT_DENIED_TEMPLATE.format(
                role=role, tool=tool_name
            ),
        )


# ---------------------------------------------------------------------------
# Gmail — OAuth dance
# ---------------------------------------------------------------------------


@router.post("/auth/gmail/start", response_model=GmailAuthStartResponse)
async def gmail_auth_start(
    session_project_id: int = Depends(require_project_id_header),
) -> GmailAuthStartResponse:
    """Begin the Gmail OAuth flow. Returns the URL to open in a browser."""
    try:
        auth_url = gmail_client.auth_start(session_project_id)
    except RuntimeError as exc:
        # Missing env vars — surface as 503 (config issue, not client fault).
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return GmailAuthStartResponse(auth_url=auth_url)


@router.get("/auth/gmail/callback", response_model=GmailCallbackResponse)
async def gmail_auth_callback(
    code: str = Query(..., min_length=1, max_length=2048),
    state: str = Query(..., min_length=1, max_length=512),
    session: AsyncSession = Depends(get_session),
) -> GmailCallbackResponse:
    """Exchange the OAuth code for credentials and store them.

    Note: the callback endpoint does NOT require X-Project-Id — Google's
    redirect won't carry custom headers. The project binding is recovered
    from the `state` value (set by `auth_start` and validated server-side).
    """
    try:
        project_id, creds = await run_in_threadpool(gmail_client.auth_callback, code, state)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        # Token exchange failure — Google rejected the code, network blip, etc.
        # Don't leak the upstream error string (may contain client_secret in
        # certain transport-error paths); log internally, return 400.
        logger.warning("gmail oauth callback failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=400,
            detail="oauth_callback_failed; restart at POST /api/tools/email/auth/gmail/start",
        ) from exc

    await token_store.put("gmail", project_id, creds, session)
    summary = gmail_client.creds_summary(creds)
    return GmailCallbackResponse(
        project_id=project_id,
        authenticated=True,
        email=summary.get("email"),
    )


@router.get("/auth/gmail/status", response_model=AuthStatusResponse)
async def gmail_auth_status(
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> AuthStatusResponse:
    """Return current auth status for Gmail on this project."""
    st = await token_store.status("gmail", session_project_id, session)
    return AuthStatusResponse(**st)


# ---------------------------------------------------------------------------
# Gmail — trash
# ---------------------------------------------------------------------------


async def _require_creds(session_project_id: int, session: AsyncSession):
    """Fetch Gmail creds or raise 401. Local helper — keeps the trash route lean."""
    creds = await token_store.get("gmail", session_project_id, session)
    if creds is None:
        raise HTTPException(
            status_code=401,
            detail=(
                "gmail not authenticated; start the OAuth flow at "
                "POST /api/tools/email/auth/gmail/start"
            ),
        )
    return creds


def _cap_check_or_429(session_project_id: int, units: int, action: str) -> None:
    """Run gate.check_and_increment; raise 429 with info on refusal.

    Also writes an audit row for the refusal so the JSONL trail captures
    every blocked attempt (not just upstream-Gmail calls).
    """
    ok, info = gate.check_and_increment(session_project_id, units)
    if not ok:
        gate.log_audit(
            "gmail", session_project_id, action, units, success=False,
            error_code="daily_cap_reached",
        )
        raise HTTPException(
            status_code=429,
            detail={"error": "daily_cap_reached", **info},
        )


def _bulk_check_or_400(count: int, force: bool) -> None:
    """Run gate.check_bulk_threshold; raise 400 on refusal."""
    ok, info = gate.check_bulk_threshold(count, force=force)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "bulk_threshold",
                "count": info["count"],
                "threshold": info["threshold"],
                "hint": "add ?force=true if intentional",
            },
        )


@router.post("/gmail/trash", response_model=GmailTrashResponse)
async def gmail_trash(
    body: GmailTrashRequest,
    force: bool = Query(default=False, description="Bypass the bulk-threshold gate."),
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    session: AsyncSession = Depends(get_session),
) -> GmailTrashResponse:
    """Trash Gmail messages by query OR explicit ids.

    Layer 0 (#1799): per-agent-name tool-governance gate — `gmail.trash` must
    be granted to the `X-Agent-Role` (if the role is restricted in
    `config.tool_grants`). Fires FIRST so a forbidden role is turned away
    before any OAuth/quota work. Opt-in: unrestricted by default.

    Flow (FIX-6 #1609 — gate ordering):
      message_ids mode:
        1. Resolve ids from request body (no auth needed).
        2. Bulk-threshold gate (Layer 3) — fires before auth; payload-safety rail.
        3. Auth check — 401 if not authenticated.
        4. Daily-cap gate (Layer 1) for 20 * count units.
        5. Trash loop; audit row written after upstream call (Layer 2).

      query mode:
        1. Auth check — list call requires auth; gate fires after.
        2. Resolve query -> ids via Gmail messages.list (and pay list units).
        3. Bulk-threshold gate (Layer 3) — must fire after list; count unknown before.
        4. Daily-cap gate (Layer 1) for 20 * count units.
        5. Trash loop; audit row written after upstream call (Layer 2).
    """
    # Layer 0 (#1799) — tool-governance gate. 403 on a denied (role, gmail.trash).
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "gmail.trash"
    )

    # FIX-6 (#1609): message_ids mode — bulk-check before auth so the payload
    # safety rail is observable without OAuth setup. query mode still requires
    # auth first because the list call (which produces the count) requires auth.
    if body.message_ids is not None:
        ids = list(body.message_ids)

        # If no ids, exit early — no trash work to do.
        if not ids:
            return GmailTrashResponse(trashed_count=0, trashed_ids=[], errors=[])

        # Layer 3 — bulk threshold fires BEFORE auth in message_ids mode.
        _bulk_check_or_400(len(ids), force)

        # Auth check after bulk gate.
        creds = await _require_creds(session_project_id, session)
    else:
        # query mode: auth must come first because the list call requires creds.
        creds = await _require_creds(session_project_id, session)

        # Pay list units before we know the count — this is honest accounting.
        _cap_check_or_429(session_project_id, _LIST_UNITS_PER_CALL, "list")
        try:
            ids = await run_in_threadpool(
                gmail_client.list_message_ids,
                creds, body.query, _MAX_LIST_RESULTS,
            )
        except Exception as exc:
            gate.log_audit(
                "gmail", session_project_id, "list", _LIST_UNITS_PER_CALL,
                success=False, error_code=type(exc).__name__,
            )
            logger.warning("gmail list failed: %s", type(exc).__name__)
            raise HTTPException(
                status_code=502,
                detail={"error": "gmail_list_failed", "class": type(exc).__name__},
            ) from exc
        gate.log_audit(
            "gmail", session_project_id, "list", _LIST_UNITS_PER_CALL, success=True,
        )

        # If the query matched nothing, exit early — no trash work to do.
        if not ids:
            return GmailTrashResponse(trashed_count=0, trashed_ids=[], errors=[])

        # Layer 3 — bulk threshold fires AFTER list in query mode (count unknown before).
        _bulk_check_or_400(len(ids), force)

    # Layer 1 — daily-units cap for the trash workload.
    total_units = _TRASH_UNITS_PER_MESSAGE * len(ids)
    _cap_check_or_429(session_project_id, total_units, "trash")

    # Execute the trash loop.
    try:
        trashed, errors = await run_in_threadpool(gmail_client.trash_messages, creds, ids)
    except Exception as exc:
        gate.log_audit(
            "gmail", session_project_id, "trash", total_units,
            success=False, error_code=type(exc).__name__,
        )
        logger.warning("gmail trash batch failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail={"error": "gmail_trash_failed", "class": type(exc).__name__},
        ) from exc

    # Audit row — success = at least one message trashed; partial errors
    # surface in the response body for the operator to inspect.
    gate.log_audit(
        "gmail", session_project_id, "trash", total_units,
        success=len(trashed) > 0,
        error_code=None if not errors else "partial_failure",
    )

    return GmailTrashResponse(
        trashed_count=len(trashed),
        trashed_ids=trashed,
        errors=errors,
    )


@router.get("/gmail/usage", response_model=UsageResponse)
async def gmail_usage(
    session_project_id: int = Depends(require_project_id_header),
) -> UsageResponse:
    """Snapshot the daily-units counter for this project (UTC day)."""
    return UsageResponse(**gate.usage(session_project_id))


# >>> #1608 OUTLOOK ROUTES BELOW — append-only zone for parallel dev coordination


# Outlook unit-cost constants — Lead-frozen (research note: Graph publishes no
# per-operation cost; we mirror the same _DAILY_UNITS_CAP via scaled units).
_OUTLOOK_TRASH_UNITS_PER_MESSAGE = 10  # half of Gmail's 20 — see outlook_client docstring.
_OUTLOOK_LIST_UNITS_PER_CALL = 5  # mirrors Gmail's _LIST_UNITS_PER_CALL.
_OUTLOOK_CALLBACK_UNITS = 1  # one AAD token-exchange call.


# ---------------------------------------------------------------------------
# Outlook — OAuth dance
# ---------------------------------------------------------------------------


@router.post("/auth/outlook/start", response_model=OutlookAuthStartResponse)
async def outlook_auth_start(
    session_project_id: int = Depends(require_project_id_header),
) -> OutlookAuthStartResponse:
    """Begin the Outlook OAuth flow. Returns the URL to open in a browser."""
    try:
        auth_url = outlook_client.auth_start(session_project_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return OutlookAuthStartResponse(auth_url=auth_url)


@router.get("/auth/outlook/callback", response_model=OutlookCallbackResponse)
async def outlook_auth_callback(
    code: str = Query(..., min_length=1, max_length=2048),
    state: str = Query(..., min_length=1, max_length=512),
    session: AsyncSession = Depends(get_session),
) -> OutlookCallbackResponse:
    """Exchange the OAuth code for tokens and store them.

    Note: like the Gmail callback, this endpoint does NOT require X-Project-Id
    — Microsoft's redirect won't carry custom headers. The project binding is
    recovered from the `state` value (set by auth_start, validated server-side).

    Audit: we log 1 unit even though no cap-check is gated (callback is part of
    the OAuth dance, not user-issued bulk work) so the audit trail captures
    every token exchange.
    """
    try:
        project_id, creds = await run_in_threadpool(outlook_client.auth_callback, code, state)
    except ValueError as exc:
        # Don't leak the upstream error_description (msal may include hints
        # about the secret in some error paths).
        logger.warning("outlook oauth callback failed: %s", str(exc).split(":")[0])
        raise HTTPException(
            status_code=400,
            detail=(
                "oauth_callback_failed; restart at "
                "POST /api/tools/email/auth/outlook/start"
            ),
        ) from exc
    except Exception as exc:
        logger.warning("outlook oauth callback failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=400,
            detail=(
                "oauth_callback_failed; restart at "
                "POST /api/tools/email/auth/outlook/start"
            ),
        ) from exc

    await token_store.put("outlook", project_id, creds, session)
    gate.log_audit(
        "outlook", project_id, "auth_callback", _OUTLOOK_CALLBACK_UNITS, success=True,
    )
    summary = outlook_client.creds_summary(creds)
    return OutlookCallbackResponse(
        project_id=project_id,
        authenticated=True,
        email=summary.get("email"),
    )


@router.get("/auth/outlook/status", response_model=AuthStatusResponse)
async def outlook_auth_status(
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> AuthStatusResponse:
    """Return current auth status for Outlook on this project."""
    st = await token_store.status("outlook", session_project_id, session)
    return AuthStatusResponse(**st)


# ---------------------------------------------------------------------------
# Outlook — trash (move to Deleted Items)
# ---------------------------------------------------------------------------


async def _require_outlook_creds(session_project_id: int, session: AsyncSession):
    """Fetch Outlook creds or raise 401."""
    creds = await token_store.get("outlook", session_project_id, session)
    if creds is None:
        raise HTTPException(
            status_code=401,
            detail=(
                "outlook not authenticated; start the OAuth flow at "
                "POST /api/tools/email/auth/outlook/start"
            ),
        )
    return creds


def _outlook_cap_check_or_429(session_project_id: int, units: int, action: str) -> None:
    """Run gate.check_and_increment; raise 429 with info on refusal.

    Mirrors Gmail's `_cap_check_or_429` but writes 'outlook' as the audit provider.
    """
    ok, info = gate.check_and_increment(session_project_id, units)
    if not ok:
        gate.log_audit(
            "outlook", session_project_id, action, units, success=False,
            error_code="daily_cap_reached",
        )
        raise HTTPException(
            status_code=429,
            detail={"error": "daily_cap_reached", **info},
        )


@router.post("/outlook/trash", response_model=OutlookTrashResponse)
async def outlook_trash(
    body: OutlookTrashRequest,
    force: bool = Query(default=False, description="Bypass the bulk-threshold gate."),
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    session: AsyncSession = Depends(get_session),
) -> OutlookTrashResponse:
    """Move Outlook messages to Deleted Items by query OR explicit ids.

    Layer 0 (#1799): per-agent-name tool-governance gate — `outlook.trash` must
    be granted to the `X-Agent-Role` (if the role is restricted in
    `config.tool_grants`). Fires FIRST so a forbidden role is turned away
    before any OAuth/quota work. Opt-in: unrestricted by default.

    Flow (FIX-6 #1609 — gate ordering):
      message_ids mode:
        1. Resolve ids from request body (no auth needed).
        2. Bulk-threshold gate (Layer 3) — fires BEFORE auth; payload-safety rail.
        3. Auth check — 401 if not authenticated.
        4. Daily-cap gate (Layer 1) for 10 * count units.
        5. Move loop; audit row written after upstream call (Layer 2).

      query mode (Graph $search — mirrors Gmail query flow):
        1. Auth check — list call requires auth; gate fires after.
        2. Resolve query -> ids via Graph $search (and pay list units).
        3. Bulk-threshold gate (Layer 3) — must fire after list; count unknown before.
        4. Daily-cap gate (Layer 1) for 10 * count units.
        5. Move loop; audit row written after upstream call (Layer 2).
    """
    # Layer 0 (#1799) — tool-governance gate. 403 on a denied (role, outlook.trash).
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "outlook.trash"
    )

    if body.message_ids is not None:
        ids = list(body.message_ids)

        if not ids:
            return OutlookTrashResponse(trashed_count=0, trashed_ids=[], errors=[])

        # FIX-6 (#1609): Layer 3 — bulk threshold fires BEFORE auth in message_ids mode.
        _bulk_check_or_400(len(ids), force)

        # Auth check after bulk gate.
        creds = await _require_outlook_creds(session_project_id, session)
    else:
        # query mode: auth must come first because the list call requires creds.
        creds = await _require_outlook_creds(session_project_id, session)

        # Pay list units before we know the count — honest accounting.
        _outlook_cap_check_or_429(session_project_id, _OUTLOOK_LIST_UNITS_PER_CALL, "list")
        try:
            ids = await run_in_threadpool(
                outlook_client.list_message_ids,
                creds, body.query, _MAX_LIST_RESULTS,
            )
        except Exception as exc:
            gate.log_audit(
                "outlook", session_project_id, "list", _OUTLOOK_LIST_UNITS_PER_CALL,
                success=False, error_code=type(exc).__name__,
            )
            logger.warning("outlook list failed: %s", type(exc).__name__)
            raise HTTPException(
                status_code=502,
                detail={"error": "outlook_list_failed", "class": type(exc).__name__},
            ) from exc
        gate.log_audit(
            "outlook", session_project_id, "list", _OUTLOOK_LIST_UNITS_PER_CALL, success=True,
        )

        if not ids:
            return OutlookTrashResponse(trashed_count=0, trashed_ids=[], errors=[])

        # Layer 3 — bulk threshold fires AFTER list in query mode (count unknown before).
        _bulk_check_or_400(len(ids), force)

    # Layer 1 — daily-units cap for the trash workload.
    total_units = _OUTLOOK_TRASH_UNITS_PER_MESSAGE * len(ids)
    _outlook_cap_check_or_429(session_project_id, total_units, "trash")

    # Execute the move loop.
    # FIX-1 (#1609): outlook_client.trash_messages is sync and calls time.sleep
    # on Graph 429. Wrap in run_in_threadpool so the async event loop is not
    # blocked during retry sleeps.
    try:
        trashed, errors = await run_in_threadpool(outlook_client.trash_messages, creds, ids)
    except Exception as exc:
        gate.log_audit(
            "outlook", session_project_id, "trash", total_units,
            success=False, error_code=type(exc).__name__,
        )
        logger.warning("outlook trash batch failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail={"error": "outlook_trash_failed", "class": type(exc).__name__},
        ) from exc

    # Audit row — success = at least one message moved; partial errors surface
    # in the response body for the operator to inspect.
    gate.log_audit(
        "outlook", session_project_id, "trash", total_units,
        success=len(trashed) > 0,
        error_code=None if not errors else "partial_failure",
    )

    return OutlookTrashResponse(
        trashed_count=len(trashed),
        trashed_ids=trashed,
        errors=errors,
    )
