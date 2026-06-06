"""Outlook (Microsoft Graph) Calendar client (Kanban #1963).

Mirrors `calendar_client.py` (Google) but speaks Microsoft Graph. Reuses the
EXISTING Outlook OAuth principal (token_store provider key "outlook") + the Graph
request/auth helpers in `outlook_client.py` (`_acquire_silent`,
`_graph_request_with_retry`, `_GRAPH_BASE`, the nextLink SSRF guard pattern,
`_strip_html`) — it does NOT re-implement the token dance.

Surface (parity with Google):
  - list_events  → GET /me/calendarView   (expands recurrences in a window)
  - freebusy     → POST /me/calendar/getSchedule
  - create_event → POST /me/events
  - respond      → POST /me/events/{id}/{accept|decline|tentativelyAccept}

SCOPE: needs `Calendars.ReadWrite` (added to outlook_client.SCOPES #1963). A token
granted before that scope was added gets an insufficient-scope error from Graph,
which this module maps to `CalendarScopeError` (imported from calendar_client so
the route's single 412 mapping covers BOTH providers). Graph signals an
insufficient-scope failure with HTTP 403 + an ErrorAccessDenied / insufficient
marker (distinct from a 401 expired/invalid token).

PRIVACY: event subjects, attendee emails, locations, bodies, and busy intervals
are returned in the response body but MUST NEVER be logged, written to any audit
trail, or echoed in error responses. Error paths surface only type(exc).__name__
(handled by the caller) or the fixed scope-error signal.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from src.tools.email.calendar_client import CalendarScopeError
from src.tools.email.outlook_client import (
    _GRAPH_BASE,
    _acquire_silent,
    _graph_request_with_retry,
)

logger = logging.getLogger(__name__)

# Provider-neutral RSVP vocabulary → Graph RSVP action segment.
_RESPONSE_ACTION_MAP = {
    "accept": "accept",
    "decline": "decline",
    "tentative": "tentativelyAccept",
}

def _is_scope_error(resp: httpx.Response) -> bool:
    """True iff a Graph response indicates the token lacks the calendar scope.

    A 403 whose body carries an access-denied / insufficient-permission marker is
    treated as a scope gap (→ 412 re-consent). A 401 (expired/invalid token) is
    NOT a scope gap — it surfaces as the normal auth path. Other statuses are not
    scope errors.

    Code-based signals (matched against the Graph error CODE field, NOT the message):
      - "erroraccessdenied" — exact code match
      - "authorization_requestdenied" — OAuth scope-gap variant (recovered: Round-1 dropped)
      - "insufficient" in code — matches InsufficientScope / insufficient_scope codes

    Message-based signal (tight phrase, to avoid false-positives from quota messages):
      - "does not have the required privilege" — only this phrase, not broader "permission" phrases

    PRIVACY: only the error code / message text is inspected, never logged.
    """
    if resp.status_code != 403:
        return False
    try:
        body = resp.json()
    except (ValueError, httpx.DecodingError):
        return False
    err = body.get("error", {}) if isinstance(body, dict) else {}
    code = ((err.get("code", "") or "") if isinstance(err, dict) else "").lower()
    message = ((err.get("message", "") or "") if isinstance(err, dict) else "").lower()
    # Code-based checks: inspect the Graph error CODE field only.
    if code in ("erroraccessdenied", "authorization_requestdenied"):
        return True
    if "insufficient" in code:
        return True
    # Message-based check: only the tight privilege phrase (not generic "permission").
    if "does not have the required privilege" in message:
        return True
    return False


def _headers(creds: dict[str, Any], *, write: bool = False) -> dict[str, str]:
    """Build the auth headers for a Graph call, refreshing the token if needed."""
    access_token = _acquire_silent(creds)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    if write:
        headers["Content-Type"] = "application/json"
    return headers


def _raise_for_scope_or_status(resp: httpx.Response) -> None:
    """Map a Graph scope-gap 403 → CalendarScopeError; else raise_for_status."""
    if _is_scope_error(resp):
        logger.warning("outlook calendar: token lacks Calendars scope")
        raise CalendarScopeError("calendar scope not granted")
    resp.raise_for_status()


def list_events(
    creds: dict[str, Any],
    time_min: str,
    time_max: str,
    calendar_id: str = "primary",  # accepted for parity; Graph uses the default calendar.
    max_results: int = 50,
) -> list[dict]:
    """List events in [time_min, time_max) via GET /me/calendarView. READ.

    calendarView expands recurring events to instances within the window, the
    Graph equivalent of Google's singleEvents=True. time_min / time_max are
    RFC3339 timestamps.

    Returns the SAME mapped shape as calendar_client.list_events:
      {id, summary, start, end, attendees, location, all_day}

    Raises CalendarScopeError on an insufficient-scope 403 (caller → 412). Other
    upstream failures propagate for the caller's 502 path.

    PRIVACY: summaries / attendees / locations MUST NOT be logged.
    """
    headers = _headers(creds)
    params: dict[str, Any] = {
        "startDateTime": time_min,
        "endDateTime": time_max,
        "$select": "id,subject,start,end,attendees,location,isAllDay",
        "$orderby": "start/dateTime",
        "$top": min(max_results, 1000),
    }
    url = f"{_GRAPH_BASE}/me/calendarView"
    resp = _graph_request_with_retry("GET", url, headers=headers, params=params)
    _raise_for_scope_or_status(resp)
    data = resp.json()

    events: list[dict] = []
    for ev in (data.get("value", []) or [])[:max_results]:
        start_obj = ev.get("start", {}) or {}
        end_obj = ev.get("end", {}) or {}
        all_day = bool(ev.get("isAllDay"))
        attendees = []
        for a in (ev.get("attendees", []) or []):
            email_obj = (a.get("emailAddress") or {})
            status_obj = (a.get("status") or {})
            attendees.append(
                {
                    "email": email_obj.get("address"),
                    "display_name": email_obj.get("name"),
                    "response_status": status_obj.get("response"),
                }
            )
        location_obj = ev.get("location") or {}
        events.append(
            {
                "id": ev.get("id"),
                "summary": ev.get("subject"),
                "start": start_obj.get("dateTime"),
                "end": end_obj.get("dateTime"),
                "attendees": attendees,
                "location": location_obj.get("displayName"),
                "all_day": all_day,
            }
        )
    return events


def freebusy(
    creds: dict[str, Any],
    time_min: str,
    time_max: str,
    calendars: list[str] | None = None,
) -> dict:
    """Query free/busy via POST /me/calendar/getSchedule. READ.

    `calendars` is a list of mailbox addresses (Graph schedules). Defaults to the
    authenticated user's own schedule — but getSchedule requires explicit
    addresses, so a caller that passes ['primary'] gets it normalised to the
    authenticated mailbox is NOT possible without an extra /me lookup; callers are
    expected to pass real mailbox addresses for cross-calendar checks. When only
    'primary' is supplied we still send it through; Graph returns an error entry
    for an unknown schedule which we surface in `errors` rather than as a 502.

    Returns the SAME shape as calendar_client.freebusy:
      {"busy": {schedule: [{start, end}]}, "errors": {schedule: [reason]}}

    Raises CalendarScopeError on an insufficient-scope 403 (caller → 412).

    PRIVACY: busy intervals are timing data — MUST NOT be logged.
    """
    schedules = calendars if calendars else ["primary"]
    headers = _headers(creds, write=True)
    # FIX-6 (#1963): UTC-normalize time_min/time_max before sending to Graph.
    # If the caller passes an offset-aware timestamp (e.g. +07:00), Graph
    # getSchedule mis-interprets it when we also declare timeZone:"UTC".
    # Normalise to UTC so the declared timeZone and the dateTime value agree.
    def _to_utc_str(ts: str) -> str:
        # Handle trailing Z (Python < 3.11 fromisoformat doesn't accept Z).
        ts_clean = ts.rstrip("Z") if ts.endswith("Z") else ts
        try:
            dt = datetime.fromisoformat(ts_clean)
        except ValueError:
            return ts  # pass through unparseable strings unchanged.
        if dt.tzinfo is None:
            # Assume UTC for naive timestamps (mirrors Graph expectation).
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        # Convert to UTC and strip the offset.
        dt_utc = dt.astimezone(timezone.utc)
        return dt_utc.strftime("%Y-%m-%dT%H:%M:%S")

    body = {
        "schedules": schedules,
        "startTime": {"dateTime": _to_utc_str(time_min), "timeZone": "UTC"},
        "endTime": {"dateTime": _to_utc_str(time_max), "timeZone": "UTC"},
    }
    url = f"{_GRAPH_BASE}/me/calendar/getSchedule"
    resp = _graph_request_with_retry("POST", url, headers=headers, json_body=body)
    _raise_for_scope_or_status(resp)
    data = resp.json()

    busy_out: dict[str, list[dict]] = {}
    errors_out: dict[str, list[str]] = {}
    # Graph returns scheduleInformation entries in the SAME order as the request
    # `schedules` array; pair them positionally.
    items = data.get("value", []) or []
    for idx, schedule in enumerate(schedules):
        entry = items[idx] if idx < len(items) else {}
        if not isinstance(entry, dict):
            entry = {}
        # An entry with an `error` field means the schedule was inaccessible.
        err = entry.get("error")
        if err:
            # FIX-3 (#1963): use the Graph error CODE enum (not the message
            # narrative) to avoid leaking raw Graph text that may carry emails
            # or internal path info.
            reason = (
                err.get("code", "schedule_inaccessible") if isinstance(err, dict) else "schedule_inaccessible"
            ) or "schedule_inaccessible"
            errors_out[schedule] = [str(reason)]
            busy_out[schedule] = []
            continue
        intervals = []
        for item in (entry.get("scheduleItems", []) or []):
            start_obj = item.get("start") or {}
            end_obj = item.get("end") or {}
            intervals.append(
                {"start": start_obj.get("dateTime"), "end": end_obj.get("dateTime")}
            )
        busy_out[schedule] = intervals

    result: dict = {"busy": busy_out}
    if errors_out:
        result["errors"] = errors_out
    return result


def create_event(
    creds: dict[str, Any],
    *,
    title: str,
    start: str,
    end: str,
    timezone: str,
    calendar_id: str = "primary",  # accepted for parity; Graph uses the default calendar.
    location: str | None = None,
    description: str | None = None,
    attendees: list[str] | None = None,
) -> dict:
    """Create an event via POST /me/events. WRITE.

    start / end are RFC3339 timestamps; `timezone` is an IANA tz name applied to
    both via the Graph dateTimeTimeZone shape. Returns
    {"event_id": <id>, "html_link": <webLink|None>}.

    Raises CalendarScopeError on an insufficient-scope 403 (caller → 412). Other
    upstream failures propagate for the caller's 502 path.

    PRIVACY: title / location / description / attendees MUST NOT be logged.
    """
    headers = _headers(creds, write=True)
    payload: dict[str, Any] = {
        "subject": title,
        "start": {"dateTime": start, "timeZone": timezone},
        "end": {"dateTime": end, "timeZone": timezone},
    }
    if location is not None:
        payload["location"] = {"displayName": location}
    if description is not None:
        payload["body"] = {"contentType": "Text", "content": description}
    if attendees:
        payload["attendees"] = [
            {"emailAddress": {"address": a}, "type": "required"} for a in attendees
        ]
    url = f"{_GRAPH_BASE}/me/events"
    resp = _graph_request_with_retry("POST", url, headers=headers, json_body=payload)
    _raise_for_scope_or_status(resp)
    data = resp.json()
    return {"event_id": data.get("id"), "html_link": data.get("webLink")}


def respond(
    creds: dict[str, Any],
    event_id: str,
    response: str,
    calendar_id: str = "primary",  # accepted for parity; Graph addresses by event id.
) -> dict:
    """RSVP to an event via POST /me/events/{id}/{accept|decline|tentativelyAccept}.

    `response` is one of accept|decline|tentative (provider-neutral). Returns
    {"event_id": <id>, "response": <response>}. Graph returns 202 Accepted on
    success.

    Raises CalendarScopeError on an insufficient-scope 403 (caller → 412). Other
    upstream failures propagate for the caller's 502 path.
    """
    # Defense-in-depth — validate id before interpolating into the URL path.
    # FIX-1 (#1963): Graph calendar event IDs are base64url-ish and routinely
    # contain '/', so a charset regex copied from the message-id path (#1939)
    # was too restrictive. Use a length-only bound; Graph rejects a truly-bad
    # id itself, and the schema already enforces max_length=1024.
    if not (1 <= len(event_id) <= 1024):
        raise ValueError("invalid event_id")
    action = _RESPONSE_ACTION_MAP.get(response)
    if action is None:
        raise ValueError(f"invalid response: {response!r}")

    headers = _headers(creds, write=True)
    # sendResponse=True so the organizer is notified of the RSVP (default Outlook
    # behavior); no comment is sent (privacy — no free-text leaves the system).
    body = {"sendResponse": True}
    # FIX-1 (#1963): percent-encode the event_id before interpolating into the URL
    # path. Graph event IDs are base64url-ish and routinely contain '/', which would
    # break the path segment. A crafted id could also redirect the Graph call.
    safe_id = quote(event_id, safe="")
    url = f"{_GRAPH_BASE}/me/events/{safe_id}/{action}"
    resp = _graph_request_with_retry("POST", url, headers=headers, json_body=body)
    _raise_for_scope_or_status(resp)
    # Graph returns 202 with an empty body on success — no JSON to parse.
    return {"event_id": event_id, "response": response}
