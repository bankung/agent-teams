"""Calendar tools router (Kanban #1963) — base `/api/tools/calendar`.

The PROPER home for the secretary's Calendar surface (Google + Outlook). Relocates
the Google READ routes that #1942 shipped under the email router
(`/api/tools/email/calendar/{events,freebusy}`) to this base, and adds the Outlook
equivalents + the WRITE tools (create-event + respond/RSVP).

Endpoint surface (provider in the path):
  POST /api/tools/calendar/{google|outlook}/list-events   READ  (auto)
  POST /api/tools/calendar/{google|outlook}/freebusy      READ  (auto)
  POST /api/tools/calendar/{google|outlook}/create-event  WRITE (operator-proof)
  POST /api/tools/calendar/{google|outlook}/respond       WRITE (operator-proof)

GATE COMPOSITION — mirrors the email tool gate (Kanban #1799 Layer-0 + #1859
operator-proof tier), reusing the email router's helpers so there is ONE gate
implementation:
  READ : Layer-0 grant → tier(READ, no-op) → creds → cap → exec → gate.log_audit
  WRITE: Layer-0 grant → operator-proof(WRITE) → creds → cap → exec
         → gate.log_audit + secretary-action audit row.

Calendar has its OWN two-tier vocabulary (`CalendarTier.READ|WRITE`) rather than
reusing `EmailTier` — the two surfaces are distinct and the email policy drift
tests pin `EmailTier` to the email policy. `_PROOF_REQUIRED_TIERS = {WRITE}`.

OAuth scopes (re-consent required before WRITE works live — see clients):
  Google : list/freebusy = calendar.readonly (#1942); create/respond need
           calendar.events (#1963, added to gmail_client.SCOPES).
  Outlook: all calendar ops need Calendars.ReadWrite (#1963, added to
           outlook_client.SCOPES).
Until re-consent, a WRITE (or a stale READ token) hits insufficient-scope →
CalendarScopeError → HTTP 412 {error: calendar_scope_not_granted, hint:
re-consent OAuth}. LIVE create/respond verification is OUT OF SCOPE (#1963 build +
mocked tests only); a go-live followup confirms re-consent.

PRIVACY: event summaries, attendee emails, locations, descriptions, and busy
intervals MUST NOT appear in gate.log_audit, any logger call, or HTTP error
detail. Only type(exc).__name__ (or the fixed scope-error signal) is used in
error paths.
"""

from __future__ import annotations

import logging
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from src.db import get_session
from src.schemas.tools_calendar import (
    CalendarEvent,
    CalendarEventsRequest,
    CalendarEventsResponse,
    CreateEventRequest,
    CreateEventResponse,
    FreeBusyRequest,
    FreeBusyResponse,
    RespondRequest,
    RespondResponse,
)
from src.services.operator_auth import OperatorDecision, require_operator_proof
from src.services.session_project import (
    optional_agent_role_header,
    require_project_id_header,
)
from src.tools.email import (
    calendar_client,
    gate,
    outlook_calendar_client,
)
# Reuse the email router's gate machinery so there is ONE implementation of the
# Layer-0 grant gate, the creds fetchers, the cap check, and the action-audit
# sink. These are module-level helpers in tools_email; importing them keeps the
# calendar router a thin orchestration layer over the SAME gates.
from src.routers.tools_email import (
    _enforce_tool_grant_or_403,
    _require_creds,
    _require_outlook_creds,
    _write_action_audit,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tools/calendar", tags=["tools-calendar"])


# ---------------------------------------------------------------------------
# Calendar tier vocabulary (#1963)
# ---------------------------------------------------------------------------


class CalendarTier(str, Enum):
    """Operator-proof requirement tier for a calendar action.

    READ  — list-events / freebusy: OPEN (Layer-0 grant only, no operator-proof).
    WRITE — create-event / respond: PROOF (operator-proof required; HITL enforced
            at the skill layer above this API per the #1963 AC).
    """

    READ = "read"
    WRITE = "write"


# Tiers requiring an operator-proof. Only WRITE. Frozenset for O(1) membership.
_PROOF_REQUIRED_TIERS = frozenset({CalendarTier.WRITE})

# Source-text-locked detail for the operator-proof denial (calendar variant).
# Mirrors the email router's _DETAIL_OPERATOR_PROOF_REQUIRED_TEMPLATE convention.
_DETAIL_OPERATOR_PROOF_REQUIRED_TEMPLATE = (
    "operator_proof_required: calendar tier {tier!r} is a WRITE action and "
    "requires an operator-proof (X-Operator-Token). The #1799 role grant answers "
    "WHICH role; this gate answers OPERATOR-PRESENT. Present the operator token "
    "out-of-band, or call a read-tier endpoint (list-events / freebusy) if no "
    "mutation is intended."
)


def _enforce_calendar_tier_or_403(
    tier: CalendarTier, operator_proof: OperatorDecision
) -> None:
    """Run the operator-proof gate for a calendar action; 403 on a missing proof.

    READ is OPEN (no-op). WRITE requires operator_proof is OPERATOR — otherwise
    403 with a stable detail. The proof is resolved by `require_operator_proof`,
    which fail-OPENS (returns OPERATOR) when OPERATOR_ACTION_KEY is unset, so this
    gate is DORMANT on the live deployment until the operator activates it (same
    discipline as the email tier gate).
    """
    if tier not in _PROOF_REQUIRED_TIERS:
        return
    if operator_proof is not OperatorDecision.OPERATOR:
        raise HTTPException(
            status_code=403,
            detail=_DETAIL_OPERATOR_PROOF_REQUIRED_TEMPLATE.format(tier=tier.value),
        )


# ---------------------------------------------------------------------------
# Unit costs + provider tags
# ---------------------------------------------------------------------------

_LIST_UNITS_PER_CALL = 5      # one calendarView / events.list call.
_FREEBUSY_UNITS_PER_CALL = 5  # one getSchedule / freebusy.query call.
_CREATE_UNITS_PER_CALL = 10   # one events.insert / POST /me/events (write).
_RESPOND_UNITS_PER_CALL = 10  # get+patch / RSVP POST (write).

_PROVIDER_GOOGLE = "google_calendar"
_PROVIDER_OUTLOOK = "outlook_calendar"

# Source-text-locked fixed detail for the insufficient-scope path. No token
# detail — only the actionable re-consent hint. Identical shape to the email
# router's calendar-scope detail so a consumer sees one stable contract.
_DETAIL_CALENDAR_SCOPE_NOT_GRANTED = {
    "error": "calendar_scope_not_granted",
    "hint": "re-consent OAuth",
}


# ---------------------------------------------------------------------------
# Provider dispatch helpers
# ---------------------------------------------------------------------------


class Provider(str, Enum):
    GOOGLE = "google"
    OUTLOOK = "outlook"


def _client_for(provider: Provider):
    """Return the calendar client module for a provider."""
    return calendar_client if provider is Provider.GOOGLE else outlook_calendar_client


def _provider_tag(provider: Provider) -> str:
    return _PROVIDER_GOOGLE if provider is Provider.GOOGLE else _PROVIDER_OUTLOOK


async def _creds_for(provider: Provider, session_project_id: int, session: AsyncSession):
    """Fetch the right provider creds (reuses the email router fetchers)."""
    if provider is Provider.GOOGLE:
        return await _require_creds(session_project_id, session)
    return await _require_outlook_creds(session_project_id, session)


def _cap_or_429(session_project_id: int, units: int, action: str, provider_tag: str) -> None:
    """Daily-units cap; raise 429 + audit on refusal (mirrors email _cap_check_or_429)."""
    ok, info = gate.check_and_increment(session_project_id, units)
    if not ok:
        gate.log_audit(
            provider_tag, session_project_id, action, units, success=False,
            error_code="daily_cap_reached",
        )
        raise HTTPException(status_code=429, detail={"error": "daily_cap_reached", **info})


# ===========================================================================
# READ — list-events
# ===========================================================================


@router.post("/{provider}/list-events", response_model=CalendarEventsResponse)
async def list_events(
    provider: Provider,
    body: CalendarEventsRequest,
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
    session: AsyncSession = Depends(get_session),
) -> CalendarEventsResponse:
    """List calendar events in a time window. READ tier — auto-approve.

    Layer 0 (#1799): `calendar.list_events` must be granted to the X-Agent-Role
    (if the role is restricted). Tier gate: READ is OPEN. Insufficient scope →
    412. PRIVACY: summaries / attendees / locations never logged.
    """
    tag = _provider_tag(provider)
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "calendar.list_events"
    )
    _enforce_calendar_tier_or_403(CalendarTier.READ, operator_proof)

    creds = await _creds_for(provider, session_project_id, session)
    _cap_or_429(session_project_id, _LIST_UNITS_PER_CALL, "list_events", tag)

    client = _client_for(provider)
    try:
        items = await run_in_threadpool(
            client.list_events,
            creds,
            body.time_min,
            body.time_max,
            body.calendar_id,
            body.max_results,
        )
    except calendar_client.CalendarScopeError as exc:
        gate.log_audit(
            tag, session_project_id, "list_events", _LIST_UNITS_PER_CALL,
            success=False, error_code="CalendarScopeError",
        )
        raise HTTPException(status_code=412, detail=_DETAIL_CALENDAR_SCOPE_NOT_GRANTED) from exc
    except Exception as exc:
        gate.log_audit(
            tag, session_project_id, "list_events", _LIST_UNITS_PER_CALL,
            success=False, error_code=type(exc).__name__,
        )
        logger.warning("calendar list_events failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail={"error": "calendar_list_events_failed", "class": type(exc).__name__},
        ) from exc

    gate.log_audit(tag, session_project_id, "list_events", _LIST_UNITS_PER_CALL, success=True)
    events = [CalendarEvent.model_validate(ev) for ev in items]
    return CalendarEventsResponse(events=events, count=len(events))


# ===========================================================================
# READ — freebusy
# ===========================================================================


@router.post("/{provider}/freebusy", response_model=FreeBusyResponse)
async def freebusy(
    provider: Provider,
    body: FreeBusyRequest,
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
    session: AsyncSession = Depends(get_session),
) -> FreeBusyResponse:
    """Query free/busy intervals per calendar. READ tier — auto-approve.

    Layer 0: `calendar.freebusy`. Tier gate: READ is OPEN. Insufficient scope →
    412. PRIVACY: busy intervals never logged.
    """
    tag = _provider_tag(provider)
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "calendar.freebusy"
    )
    _enforce_calendar_tier_or_403(CalendarTier.READ, operator_proof)

    creds = await _creds_for(provider, session_project_id, session)
    _cap_or_429(session_project_id, _FREEBUSY_UNITS_PER_CALL, "freebusy", tag)

    client = _client_for(provider)
    try:
        fb_result = await run_in_threadpool(
            client.freebusy, creds, body.time_min, body.time_max, body.calendars,
        )
    except calendar_client.CalendarScopeError as exc:
        gate.log_audit(
            tag, session_project_id, "freebusy", _FREEBUSY_UNITS_PER_CALL,
            success=False, error_code="CalendarScopeError",
        )
        raise HTTPException(status_code=412, detail=_DETAIL_CALENDAR_SCOPE_NOT_GRANTED) from exc
    except Exception as exc:
        gate.log_audit(
            tag, session_project_id, "freebusy", _FREEBUSY_UNITS_PER_CALL,
            success=False, error_code=type(exc).__name__,
        )
        logger.warning("calendar freebusy failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail={"error": "calendar_freebusy_failed", "class": type(exc).__name__},
        ) from exc

    gate.log_audit(tag, session_project_id, "freebusy", _FREEBUSY_UNITS_PER_CALL, success=True)
    return FreeBusyResponse(busy=fb_result["busy"], errors=fb_result.get("errors"))


# ===========================================================================
# WRITE — create-event
# ===========================================================================


@router.post("/{provider}/create-event", response_model=CreateEventResponse)
async def create_event(
    provider: Provider,
    body: CreateEventRequest,
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
    session: AsyncSession = Depends(get_session),
) -> CreateEventResponse:
    """Create a calendar event. WRITE tier — operator-proof.

    Layer 0: `calendar.create_event`. Tier gate (#1859): WRITE requires an
    operator-proof AFTER Layer-0; 403 if absent when the gate is ACTIVE (dormant
    until OPERATOR_ACTION_KEY is set). Insufficient scope → 412. On success,
    writes a secretary-action audit row.

    PRIVACY: title / location / description / attendees never logged.
    """
    tag = _provider_tag(provider)
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "calendar.create_event"
    )
    _enforce_calendar_tier_or_403(CalendarTier.WRITE, operator_proof)

    creds = await _creds_for(provider, session_project_id, session)
    _cap_or_429(session_project_id, _CREATE_UNITS_PER_CALL, "create_event", tag)

    client = _client_for(provider)
    try:
        created = await run_in_threadpool(
            lambda: client.create_event(
                creds,
                title=body.title,
                start=body.start,
                end=body.end,
                timezone=body.timezone,
                calendar_id=body.calendar_id,
                location=body.location,
                description=body.description,
                attendees=body.attendees,
            )
        )
    except calendar_client.CalendarScopeError as exc:
        gate.log_audit(
            tag, session_project_id, "create_event", _CREATE_UNITS_PER_CALL,
            success=False, error_code="CalendarScopeError",
        )
        raise HTTPException(status_code=412, detail=_DETAIL_CALENDAR_SCOPE_NOT_GRANTED) from exc
    except Exception as exc:
        gate.log_audit(
            tag, session_project_id, "create_event", _CREATE_UNITS_PER_CALL,
            success=False, error_code=type(exc).__name__,
        )
        logger.warning("calendar create_event failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail={"error": "calendar_create_event_failed", "class": type(exc).__name__},
        ) from exc

    event_id = created.get("event_id")
    if not event_id:
        gate.log_audit(
            tag, session_project_id, "create_event", _CREATE_UNITS_PER_CALL,
            success=False, error_code="empty_event_response",
        )
        raise HTTPException(
            status_code=502,
            detail={"error": "empty_event_response", "hint": "provider returned no event id"},
        )

    gate.log_audit(tag, session_project_id, "create_event", _CREATE_UNITS_PER_CALL, success=True)
    # Secretary-action audit (#1585 sink) — WRITE under operator_proof. The event
    # id is the artifact reference; NO title/attendee content is recorded.
    # FIX-6 (#1963): use CalendarTier.WRITE (value="write") so the JSONL audit
    # row carries a calendar-specific tier, not the email "send_internal" tag.
    _write_action_audit(
        agent_role=agent_role,
        action="calendar_create_event",
        tier=CalendarTier.WRITE,  # type: ignore[arg-type]  # TODO(#1963): widen _write_action_audit tier param
        message_ids=[event_id],
        approval_mode="operator_proof",
        result="success",
    )
    return CreateEventResponse(event_id=event_id, html_link=created.get("html_link"))


# ===========================================================================
# WRITE — respond (RSVP)
# ===========================================================================


@router.post("/{provider}/respond", response_model=RespondResponse)
async def respond(
    provider: Provider,
    body: RespondRequest,
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
    session: AsyncSession = Depends(get_session),
) -> RespondResponse:
    """RSVP to a calendar event (accept|decline|tentative). WRITE — operator-proof.

    Layer 0: `calendar.respond`. Tier gate: WRITE requires operator-proof.
    Insufficient scope → 412. A non-attendee event (Google: no self-attendee) →
    409. On success, writes a secretary-action audit row.
    """
    tag = _provider_tag(provider)
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "calendar.respond"
    )
    _enforce_calendar_tier_or_403(CalendarTier.WRITE, operator_proof)

    creds = await _creds_for(provider, session_project_id, session)
    _cap_or_429(session_project_id, _RESPOND_UNITS_PER_CALL, "respond", tag)

    client = _client_for(provider)
    try:
        result = await run_in_threadpool(
            client.respond, creds, body.event_id, body.response, body.calendar_id,
        )
    except calendar_client.CalendarScopeError as exc:
        gate.log_audit(
            tag, session_project_id, "respond", _RESPOND_UNITS_PER_CALL,
            success=False, error_code="CalendarScopeError",
        )
        raise HTTPException(status_code=412, detail=_DETAIL_CALENDAR_SCOPE_NOT_GRANTED) from exc
    except ValueError as exc:
        # not_an_attendee / invalid input from the client — 409 (state conflict),
        # NOT a 502 (the upstream call succeeded; the user just can't RSVP).
        gate.log_audit(
            tag, session_project_id, "respond", _RESPOND_UNITS_PER_CALL,
            success=False, error_code="ValueError",
        )
        raise HTTPException(
            status_code=409,
            # FIX-2 (#1963): mirror the 502 path's privacy posture — no raw
            # exception message in the HTTP body; only the exception class name.
            detail={"error": "cannot_respond", "reason": type(exc).__name__},
        ) from exc
    except Exception as exc:
        gate.log_audit(
            tag, session_project_id, "respond", _RESPOND_UNITS_PER_CALL,
            success=False, error_code=type(exc).__name__,
        )
        logger.warning("calendar respond failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail={"error": "calendar_respond_failed", "class": type(exc).__name__},
        ) from exc

    gate.log_audit(tag, session_project_id, "respond", _RESPOND_UNITS_PER_CALL, success=True)
    # FIX-6 (#1963): use CalendarTier.WRITE so the audit row shows "write",
    # not the email "send_internal" tag.
    _write_action_audit(
        agent_role=agent_role,
        action=f"calendar_respond_{body.response}",
        tier=CalendarTier.WRITE,  # type: ignore[arg-type]  # TODO(#1963): widen _write_action_audit tier param
        message_ids=[body.event_id],
        approval_mode="operator_proof",
        result="success",
    )
    return RespondResponse(event_id=result["event_id"], response=result["response"])
