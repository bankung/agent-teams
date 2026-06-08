"""Wire schemas for the Calendar tools (Kanban #1963).

Base `/api/tools/calendar` — the PROPER home for the secretary's calendar surface
(relocated from the email router's `/calendar/*` block shipped in #1942). Covers
both Google and Outlook:

  list-events / freebusy  → READ tier (auto)
  create-event / respond  → WRITE tier (operator-proof)

Naming convention mirrors `schemas/tools_email.py`: a provider-agnostic base for
shared shapes, provider-neutral request/response bodies where the contract is the
same across Google + Outlook (the route injects the provider via the path).

The list-events / freebusy READ schemas are the SAME shapes that lived in
`schemas/tools_email.py` as `CalendarEventsRequest` / `CalendarEvent` /
`CalendarEventsResponse` / `FreeBusyRequest` / `FreeBusyInterval` /
`FreeBusyResponse` (#1942) — re-homed here so the calendar surface owns its own
contract. The email schemas keep their copies ONLY for the now-removed email
calendar routes' import compatibility; nothing imports them after the relocation,
but they are left untouched to avoid touching the email contract.

PRIVACY: event summaries, attendee emails, locations, descriptions, and busy
intervals are returned in response bodies but MUST NEVER be logged, written to an
audit trail, or echoed in error responses.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

# RFC3339 timestamp pattern — accepts both Z and numeric-offset forms. Mirrors the
# regex used in schemas/tools_email.py for the #1942 calendar schemas.
_RFC3339_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}([.\d]+)?(Z|[+\-]\d{2}:?\d{2})$"
_RFC3339_MAX = 64
_CAL_ID_MAX = 1024


def _parse_rfc3339(ts: str) -> datetime:
    """Parse an RFC3339 timestamp to a tz-aware datetime.

    Normalises trailing 'Z' to '+00:00' so `datetime.fromisoformat` accepts it.
    """
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


# ===========================================================================
# READ — list-events
# ===========================================================================


class CalendarEventsRequest(BaseModel):
    """List calendar events in a time window. READ tier — auto-approve."""

    model_config = ConfigDict(extra="forbid")

    time_min: str = Field(
        ...,
        min_length=1,
        max_length=_RFC3339_MAX,
        pattern=_RFC3339_PATTERN,
        description="RFC3339 lower bound (inclusive), e.g. '2026-06-06T00:00:00Z'.",
    )
    time_max: str = Field(
        ...,
        min_length=1,
        max_length=_RFC3339_MAX,
        pattern=_RFC3339_PATTERN,
        description="RFC3339 upper bound (exclusive), e.g. '2026-06-07T00:00:00Z'.",
    )
    calendar_id: str = Field(
        default="primary",
        min_length=1,
        max_length=_CAL_ID_MAX,
        description="Calendar id — 'primary', an email, or a calendar-id string.",
    )
    max_results: int = Field(
        default=50,
        ge=1,
        le=250,
        description="Maximum number of events to return (1–250).",
    )

    @model_validator(mode="after")
    def _check_time_ordering(self) -> "CalendarEventsRequest":
        try:
            t_min = _parse_rfc3339(self.time_min)
            t_max = _parse_rfc3339(self.time_max)
        except ValueError as exc:
            raise ValueError(
                f"time_min/time_max could not be parsed as RFC3339: {exc}"
            ) from exc
        if t_min >= t_max:
            raise ValueError("time_min must be strictly before time_max.")
        return self


class CalendarAttendee(BaseModel):
    """A single event attendee. email/display_name MUST NOT be logged."""

    email: str | None = None
    display_name: str | None = None
    response_status: str | None = None


class CalendarEvent(BaseModel):
    """A single calendar event. summary/attendees/location MUST NOT be logged."""

    id: str | None = None
    summary: str | None = None
    start: str | None = None
    end: str | None = None
    attendees: list[CalendarAttendee] = Field(default_factory=list)
    location: str | None = None
    all_day: bool = False


class CalendarEventsResponse(BaseModel):
    """Result of a list-events call. Event content MUST NOT be logged."""

    events: list[CalendarEvent]
    count: int


# ===========================================================================
# READ — freebusy
# ===========================================================================


class FreeBusyRequest(BaseModel):
    """Query free/busy intervals for one or more calendars. READ tier."""

    model_config = ConfigDict(extra="forbid")

    time_min: str = Field(
        ...,
        min_length=1,
        max_length=_RFC3339_MAX,
        pattern=_RFC3339_PATTERN,
        description="RFC3339 lower bound (inclusive).",
    )
    time_max: str = Field(
        ...,
        min_length=1,
        max_length=_RFC3339_MAX,
        pattern=_RFC3339_PATTERN,
        description="RFC3339 upper bound (exclusive).",
    )
    calendars: list[str] = Field(
        default_factory=lambda: ["primary"],
        description="Calendar ids / mailbox addresses to query (defaults to ['primary']).",
    )

    @model_validator(mode="after")
    def _check_calendars_and_ordering(self) -> "FreeBusyRequest":
        try:
            t_min = _parse_rfc3339(self.time_min)
            t_max = _parse_rfc3339(self.time_max)
        except ValueError as exc:
            raise ValueError(
                f"time_min/time_max could not be parsed as RFC3339: {exc}"
            ) from exc
        if t_min >= t_max:
            raise ValueError("time_min must be strictly before time_max.")
        if not isinstance(self.calendars, list) or len(self.calendars) == 0:
            raise ValueError("calendars must be a non-empty list.")
        if len(self.calendars) > 50:
            raise ValueError("calendars list cannot exceed 50 entries per call.")
        for cid in self.calendars:
            if not isinstance(cid, str) or not (1 <= len(cid) <= _CAL_ID_MAX):
                raise ValueError(
                    f"each calendar id must be a non-empty string <={_CAL_ID_MAX} chars."
                )
        return self


class FreeBusyInterval(BaseModel):
    """A single busy interval. Timing data MUST NOT be logged."""

    start: str | None = None
    end: str | None = None


class FreeBusyResponse(BaseModel):
    """Busy intervals + per-calendar errors. MUST NOT be logged.

    busy:   {calendar_id: [busy intervals]}  — always present; empty list = no
            busy blocks (but check errors first — an error means the calendar was
            inaccessible, NOT that it was genuinely free).
    errors: {calendar_id: [reason strings]}  — present only when one or more
            calendars returned an error. Reason strings only; no raw upstream bodies.
    """

    busy: dict[str, list[FreeBusyInterval]]
    errors: dict[str, list[str]] | None = None


# ===========================================================================
# WRITE — create-event
# ===========================================================================

# Bounds — refuse obviously-garbage payloads early (422) before any upstream call.
_TITLE_MAX = 1024
_LOCATION_MAX = 1024
_DESCRIPTION_MAX = 8192
_TIMEZONE_MAX = 64
_ATTENDEE_EMAIL_MAX = 320  # RFC 5321 max addr length.
_MAX_ATTENDEES = 100


class CreateEventRequest(BaseModel):
    """Create a calendar event. WRITE tier — operator-proof.

    Provider-neutral body; the route maps it to Google `events.insert` or Graph
    `POST /me/events`. `start`/`end` are RFC3339 timestamps; `timezone` is an IANA
    tz name (e.g. 'Asia/Bangkok') the provider applies to the start/end.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(
        ...,
        min_length=1,
        max_length=_TITLE_MAX,
        description="Event title / summary.",
    )
    start: str = Field(
        ...,
        min_length=1,
        max_length=_RFC3339_MAX,
        pattern=_RFC3339_PATTERN,
        description="RFC3339 start time, e.g. '2026-06-06T09:00:00Z'.",
    )
    end: str = Field(
        ...,
        min_length=1,
        max_length=_RFC3339_MAX,
        pattern=_RFC3339_PATTERN,
        description="RFC3339 end time, e.g. '2026-06-06T10:00:00Z'.",
    )
    timezone: str = Field(
        ...,
        min_length=1,
        max_length=_TIMEZONE_MAX,
        description="IANA timezone name applied to start/end (e.g. 'Asia/Bangkok', 'UTC').",
    )
    location: str | None = Field(
        default=None,
        max_length=_LOCATION_MAX,
        description="Optional event location.",
    )
    description: str | None = Field(
        default=None,
        max_length=_DESCRIPTION_MAX,
        description="Optional event description / body.",
    )
    attendees: list[str] | None = Field(
        default=None,
        description="Optional list of attendee email addresses to invite.",
    )
    calendar_id: str = Field(
        default="primary",
        min_length=1,
        max_length=_CAL_ID_MAX,
        description="Calendar to create the event on (Google only; ignored by Outlook).",
    )

    @model_validator(mode="after")
    def _check(self) -> "CreateEventRequest":
        try:
            t_start = _parse_rfc3339(self.start)
            t_end = _parse_rfc3339(self.end)
        except ValueError as exc:
            raise ValueError(
                f"start/end could not be parsed as RFC3339: {exc}"
            ) from exc
        if t_start >= t_end:
            raise ValueError("start must be strictly before end.")
        if self.attendees is not None:
            if len(self.attendees) > _MAX_ATTENDEES:
                raise ValueError(
                    f"attendees list cannot exceed {_MAX_ATTENDEES} entries per call."
                )
            for addr in self.attendees:
                if not isinstance(addr, str) or not (3 <= len(addr) <= _ATTENDEE_EMAIL_MAX):
                    raise ValueError(
                        f"each attendee must be a string between 3 and {_ATTENDEE_EMAIL_MAX} chars."
                    )
                if "@" not in addr:
                    raise ValueError("each attendee must look like an email address (contain '@').")
        return self


class CreateEventResponse(BaseModel):
    """Result of a create-event call. The created event id + provider link."""

    event_id: str
    html_link: str | None = Field(
        default=None,
        description="Provider deep-link to the created event (Google htmlLink / Graph webLink).",
    )


# ===========================================================================
# WRITE — respond (RSVP)
# ===========================================================================

# Event-id bound — Google ids are short base32hex; Graph ids are long base64url.
_EVENT_ID_MAX = 1024


class RespondRequest(BaseModel):
    """RSVP to a calendar event. WRITE tier — operator-proof.

    `response` is one of accept|decline|tentative. The route maps it to the
    provider-specific RSVP mechanism (Google: patch the self-attendee's
    responseStatus; Outlook: POST /me/events/{id}/{accept|decline|tentativelyAccept}).
    """

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(
        ...,
        min_length=1,
        max_length=_EVENT_ID_MAX,
        description="The calendar event id to respond to.",
    )
    response: str = Field(
        ...,
        # FIX-7 (#1963): pattern mirrors the model_validator's allowlist so
        # the 422 fires at the schema boundary (before model_validator runs).
        pattern=r"^(accept|decline|tentative)$",
        description="RSVP response: 'accept', 'decline', or 'tentative'.",
    )
    calendar_id: str = Field(
        default="primary",
        min_length=1,
        max_length=_CAL_ID_MAX,
        description="Calendar the event lives on (Google only; ignored by Outlook).",
    )


class RespondResponse(BaseModel):
    """Result of an RSVP call."""

    event_id: str
    response: str
    status: str = Field(
        default="ok",
        description="'ok' on success.",
    )
