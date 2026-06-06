"""Google Calendar READ client (Kanban #1942).

Read-only Calendar tools for the secretary's calendar-prep / conflict-detection
workflow — list-events + freebusy — so conflict detection happens server-side
(pure data) instead of via a Chrome browser session.

Reuses the EXISTING Google OAuth principal: the stored `google.oauth2.
credentials.Credentials` object (token_store provider key "gmail") drives the
Calendar API exactly as gmail_client drives the Gmail API. The only extra
requirement is the calendar.readonly scope — added to gmail_client.SCOPES — which
takes effect on the next operator RE-CONSENT (include_granted_scopes=true keeps
mail access). Until re-consent, a stored token lacks calendar.readonly and the
Calendar API raises an insufficient-permission 403; this module catches that and
raises `CalendarScopeError` so the route returns a clear "re-consent needed"
error (HTTP 412) WITHOUT leaking token details.

PRIVACY: event summaries, attendee emails, locations, and free/busy details are
returned in the response body but MUST NEVER be logged, written to any audit
trail, or echoed in error responses. Error paths surface only type(exc).__name__
(handled by the caller) or the fixed scope-error signal.
"""

from __future__ import annotations

import logging

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)


class CalendarScopeError(Exception):
    """Raised when the stored Google token lacks calendar.readonly.

    Signals the route to return HTTP 412 ("re-consent needed"). Carries NO token
    detail — its mere type is the signal; the route emits a fixed message.
    """


def _ensure_fresh(creds: Credentials) -> Credentials:
    """Refresh creds if expired and a refresh_token is available.

    Mirrors gmail_client._ensure_fresh so Calendar calls use the same
    fresh-credential discipline as the mail calls.
    """
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleAuthRequest())
    return creds


def _build_service(creds: Credentials):
    """Build a Calendar v3 API service client. Refreshes creds if needed."""
    _ensure_fresh(creds)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _is_scope_error(exc: HttpError) -> bool:
    """True iff an HttpError indicates the token lacks the calendar scope.

    Google returns 403 with an insufficient-permission reason when a valid token
    is missing the required scope (distinct from a 401 expired/invalid token).
    We match on status 403 AND an insufficient-scope marker in the reason/message
    so a genuine 403 (e.g. calendar not shared) is NOT mis-reported as a scope gap.

    PRIVACY: NEVER inspect str(exc) — it embeds the request URI which may carry
    calendarId (a user email). Only exc.reason (the HTTP reason phrase / Google
    error message, no URI) and exc.error_details are inspected; the raw text is
    not logged or surfaced.
    """
    status = getattr(getattr(exc, "resp", None), "status", None) or getattr(
        exc, "status_code", None
    )
    if status != 403:
        return False
    # exc.reason: the HTTP reason phrase / Google error message without URI.
    # Lower-case and match against tight scope-gap strings only, so an unrelated
    # 403 (e.g. "calendarNotFound", "forbidden") is not mis-classified.
    text = (getattr(exc, "reason", "") or "").lower()
    markers = (
        "insufficientpermissions",           # Google error reason enum
        "insufficient authentication scopes", # narrative message form
        "insufficient_scope",                # OAuth error_code form
        "access_token_scope_insufficient",   # Google OAuth 2.0 error
    )
    if any(m in text for m in markers):
        return True
    # Fallback: error_details list may carry the reason in some response shapes.
    # Never inspect str(exc)/the URI — only the structured details list.
    for detail in (getattr(exc, "error_details", None) or []):
        detail_text = (str(detail) if not isinstance(detail, str) else detail).lower()
        if any(m in detail_text for m in markers):
            return True
    return False


def list_events(
    creds: Credentials,
    time_min: str,
    time_max: str,
    calendar_id: str = "primary",
    max_results: int = 50,
) -> list[dict]:
    """List calendar events in [time_min, time_max). READ-only.

    Uses `events().list(singleEvents=True, orderBy="startTime")` so recurring
    events are expanded to individual instances ordered by start time.

    time_min / time_max are RFC3339 timestamps (e.g. "2026-06-06T00:00:00Z").
    The caller (route + schema) validates/bounds them; this fetches what's asked.

    Returns a list of:
      {id, summary, start, end, attendees, location, all_day}
    where:
      - all_day is True when the event uses a date (not dateTime) — i.e. a
        whole-day event with no specific time.
      - start / end are the raw RFC3339 dateTime (timed) OR date (all-day) string.
      - attendees is a list of {email, display_name} dicts (may be empty).

    Raises CalendarScopeError if the stored token lacks calendar.readonly
    (caller maps to HTTP 412). Other upstream failures propagate as HttpError /
    Exception for the caller's generic 502 path.

    PRIVACY: the returned summaries / attendees / locations MUST NOT be logged.
    Caller is responsible for cap enforcement BEFORE invoking.
    """
    service = _build_service(creds)
    try:
        resp = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                maxResults=max_results,
            )
            .execute()
        )
    except HttpError as exc:
        if _is_scope_error(exc):
            # Do NOT log the upstream detail — only the scope-gap signal.
            logger.warning("calendar list_events: token lacks calendar scope")
            raise CalendarScopeError("calendar scope not granted") from exc
        raise

    events: list[dict] = []
    for ev in resp.get("items", []) or []:
        start_obj = ev.get("start", {}) or {}
        end_obj = ev.get("end", {}) or {}
        # all-day events carry `date`; timed events carry `dateTime`.
        all_day = "date" in start_obj and "dateTime" not in start_obj
        start = start_obj.get("dateTime") or start_obj.get("date")
        end = end_obj.get("dateTime") or end_obj.get("date")
        attendees = [
            {"email": a.get("email"), "display_name": a.get("displayName")}
            for a in (ev.get("attendees", []) or [])
        ]
        events.append(
            {
                "id": ev.get("id"),
                "summary": ev.get("summary"),
                "start": start,
                "end": end,
                "attendees": attendees,
                "location": ev.get("location"),
                "all_day": all_day,
            }
        )
    return events


def freebusy(
    creds: Credentials,
    time_min: str,
    time_max: str,
    calendars: list[str] | None = None,
) -> dict:
    """Query free/busy intervals for one or more calendars. READ-only.

    Uses `freebusy().query(body={timeMin, timeMax, items:[{id}...]})`.

    time_min / time_max are RFC3339 timestamps. `calendars` is a list of
    calendar ids (defaults to ["primary"]).

    Returns:
      {
        "busy":   {calendar_id: [{start, end}, ...]},
        "errors": {calendar_id: [<reason_string>, ...]}  # omitted if empty
      }
    where each busy interval is a busy block (RFC3339 start/end).  A calendar
    with no busy blocks maps to an empty list in "busy".  Per-calendar errors
    (e.g. notFound on a secondary calendar) are surfaced in "errors" rather than
    silently appearing as "no busy blocks" (false "free").

    PRIVACY: "errors" carries only the reason string (e.g. "notFound") — never
    the raw upstream body. Busy intervals are timing data — MUST NOT be logged.
    Caller is responsible for cap enforcement BEFORE invoking.

    Raises CalendarScopeError if the stored token lacks calendar.readonly
    (caller maps to HTTP 412). Other upstream failures propagate for the
    caller's generic 502 path.
    """
    cal_ids = calendars if calendars else ["primary"]
    service = _build_service(creds)
    body = {
        "timeMin": time_min,
        "timeMax": time_max,
        "items": [{"id": cid} for cid in cal_ids],
    }
    try:
        resp = service.freebusy().query(body=body).execute()
    except HttpError as exc:
        if _is_scope_error(exc):
            logger.warning("calendar freebusy: token lacks calendar scope")
            raise CalendarScopeError("calendar scope not granted") from exc
        raise

    busy_out: dict[str, list[dict]] = {}
    errors_out: dict[str, list[str]] = {}
    calendars_resp = resp.get("calendars", {}) or {}
    for cid in cal_ids:
        cal_entry = calendars_resp.get(cid, {}) or {}
        busy_out[cid] = [
            {"start": b.get("start"), "end": b.get("end")}
            for b in (cal_entry.get("busy", []) or [])
        ]
        # Per-calendar errors (e.g. notFound): surface reason strings only.
        # PRIVACY: extract only "reason" fields — never echo raw upstream bodies.
        cal_errors = cal_entry.get("errors", []) or []
        if cal_errors:
            errors_out[cid] = [
                str(e.get("reason", "unknown")) for e in cal_errors if isinstance(e, dict)
            ]

    result: dict = {"busy": busy_out}
    if errors_out:
        result["errors"] = errors_out
    return result
