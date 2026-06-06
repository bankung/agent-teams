"""Kanban #1942 — Google Calendar READ tools (list-events + freebusy).

Contract-smoke coverage for the two new READ-tier Calendar endpoints that reuse
the Google OAuth principal (token_store provider "gmail") + the existing gate
chain:
  Layer-0 (#1799) tool-grant → tier(READ, no-op) → auth → cap → calendar_client
  → gate.log_audit (units trail only). NO _write_action_audit (reads).

What these tests lock:
  - /calendar/events + /calendar/freebusy return 200 with the correct shape on
    the happy path (POSITIVE: the upstream client fn really ran).
  - 401 when no Google creds are stored.
  - Layer-0 grant DENY → 403 BEFORE any upstream work (NEGATIVE: client not run).
  - INSUFFICIENT SCOPE: calendar_client.CalendarScopeError → HTTP 412 with the
    fixed {error: calendar_scope_not_granted, hint: re-consent OAuth} detail and
    NO token/event leakage.
  - PRIVACY: event summary / attendee email never appears in the units audit row.

Mirrors test_email_tier1_actions.py READ-block fixtures (creds cache seeding,
store cleanup, gate-audit monkeypatch). Hermetic project ids; touches NO live DB
rows (creds cache-seeded, upstream monkeypatched) so the live agent_teams
row-count invariant holds.
"""

from __future__ import annotations

import datetime
import json

import pytest

from src.routers import tools_email

_BASE = "/api/tools/email"
_KEY_ENV = "OPERATOR_ACTION_KEY"
_TOKEN = "s3cret-operator-token"

# Hermetic project ids (disjoint from the email test blocks that use 999x).
_PROJ_CAL = 9990
_HDR_CAL = {"X-Project-Id": str(_PROJ_CAL)}


def _fake_google_creds() -> object:
    """Minimal Google Credentials mock with the calendar scope granted."""
    from unittest.mock import MagicMock
    from google.oauth2.credentials import Credentials as RealCreds

    creds = MagicMock(spec=RealCreds)
    creds.expiry = datetime.datetime(2099, 1, 1, 0, 0, 0)
    creds._at_email_cache = "test@gmail.com"
    creds.scopes = [
        "https://mail.google.com/",
        "https://www.googleapis.com/auth/calendar.readonly",
    ]
    return creds


@pytest.fixture(autouse=True)
def _clean_calendar_stores():
    """Clear the in-memory creds + daily-units stores for _PROJ_CAL between tests."""
    from src.tools.email import gate, token_store

    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    token_store._CACHE.pop(("gmail", _PROJ_CAL), None)
    gate._DAILY_UNITS.pop((_PROJ_CAL, today), None)
    yield
    token_store._CACHE.pop(("gmail", _PROJ_CAL), None)
    gate._DAILY_UNITS.pop((_PROJ_CAL, today), None)


def _seed_calendar_creds(monkeypatch):
    from src.tools.email import token_store

    token_store._CACHE[("gmail", _PROJ_CAL)] = _fake_google_creds()
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")


# ===========================================================================
# list-events
# ===========================================================================


@pytest.mark.asyncio
async def test_calendar_events_success_returns_event_shape(client, monkeypatch):
    """AC: /calendar/events returns 200 with the [{id, summary, start, end,
    attendees, location, all_day}] shape.

    POSITIVE: list_events is called with the request args; response carries the
    mapped fields including an all-day event flag and attendees.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_calendar_creds(monkeypatch)
    from src.tools.email import calendar_client

    calls: list[tuple] = []
    fake_events = [
        {
            "id": "ev001",
            "summary": "Standup",
            "start": "2026-06-06T09:00:00Z",
            "end": "2026-06-06T09:15:00Z",
            "attendees": [{"email": "alice@x.com", "display_name": "Alice"}],
            "location": "Zoom",
            "all_day": False,
        },
        {
            "id": "ev002",
            "summary": "Holiday",
            "start": "2026-06-07",
            "end": "2026-06-08",
            "attendees": [],
            "location": None,
            "all_day": True,
        },
    ]

    def _fake_list_events(creds, time_min, time_max, calendar_id, max_results):
        calls.append((time_min, time_max, calendar_id, max_results))
        return fake_events

    monkeypatch.setattr(calendar_client, "list_events", _fake_list_events)

    resp = await client.post(
        f"{_BASE}/calendar/events",
        headers=_HDR_CAL,
        json={
            "time_min": "2026-06-06T00:00:00Z",
            "time_max": "2026-06-08T00:00:00Z",
            "calendar_id": "primary",
            "max_results": 50,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 2
    ev = body["events"][0]
    assert ev["id"] == "ev001"
    assert ev["summary"] == "Standup"
    assert ev["start"] == "2026-06-06T09:00:00Z"
    assert ev["end"] == "2026-06-06T09:15:00Z"
    assert ev["all_day"] is False
    assert ev["attendees"][0]["email"] == "alice@x.com"
    assert ev["location"] == "Zoom"
    # all-day event mapping.
    assert body["events"][1]["all_day"] is True
    # POSITIVE — list_events ran with the supplied window + calendar + cap.
    assert calls == [("2026-06-06T00:00:00Z", "2026-06-08T00:00:00Z", "primary", 50)]


@pytest.mark.asyncio
async def test_calendar_events_401_no_auth(client, monkeypatch):
    """/calendar/events returns 401 when no Google creds are stored."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")
    # Deliberately do NOT seed creds for _PROJ_CAL.
    resp = await client.post(
        f"{_BASE}/calendar/events",
        headers=_HDR_CAL,
        json={"time_min": "2026-06-06T00:00:00Z", "time_max": "2026-06-07T00:00:00Z"},
    )
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_calendar_events_layer0_denial_403(client, monkeypatch):
    """AC: /calendar/events 403s on Layer-0 grant denial; list_events NOT called.

    NEGATIVE lock: Layer-0 turns the role away before any auth/cap/upstream work.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_calendar_creds(monkeypatch)
    from src.tools.email import calendar_client
    from src.services import tool_grants as tg

    real_check = tg.check_grant

    def _deny_check(config, role, tool_name, *, project_id=None):
        if role == "locked-role":
            return tg.GrantDecision.DENY
        return real_check(config, role, tool_name, project_id=project_id)

    monkeypatch.setattr(tools_email, "check_grant", _deny_check)
    called: list = []
    monkeypatch.setattr(
        calendar_client, "list_events",
        lambda *a, **k: called.append(1) or [],
    )

    resp = await client.post(
        f"{_BASE}/calendar/events",
        headers={**_HDR_CAL, "X-Agent-Role": "locked-role"},
        json={"time_min": "2026-06-06T00:00:00Z", "time_max": "2026-06-07T00:00:00Z"},
    )
    assert resp.status_code == 403, resp.text
    assert "tool_grant_denied" in resp.json()["detail"]
    assert called == [], "list_events must NOT run when Layer-0 denies"


@pytest.mark.asyncio
async def test_calendar_events_insufficient_scope_412(client, monkeypatch):
    """AC: /calendar/events returns 412 when the token lacks calendar.readonly.

    POSITIVE: the fixed re-consent detail is returned.
    NEGATIVE/PRIVACY: the 412 detail must NOT contain token or event data — only
    {error: calendar_scope_not_granted, hint: re-consent OAuth}.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_calendar_creds(monkeypatch)
    from src.tools.email import calendar_client

    def _raise_scope(creds, time_min, time_max, calendar_id, max_results):
        raise calendar_client.CalendarScopeError("calendar scope not granted")

    monkeypatch.setattr(calendar_client, "list_events", _raise_scope)

    resp = await client.post(
        f"{_BASE}/calendar/events",
        headers=_HDR_CAL,
        json={"time_min": "2026-06-06T00:00:00Z", "time_max": "2026-06-07T00:00:00Z"},
    )
    assert resp.status_code == 412, f"expected 412, got {resp.status_code}: {resp.text}"
    detail = resp.json()["detail"]
    assert detail == {"error": "calendar_scope_not_granted", "hint": "re-consent OAuth"}
    # NEGATIVE: must NOT be 502 (generic) or 401 (no-auth).
    assert resp.status_code != 502
    assert resp.status_code != 401


@pytest.mark.asyncio
async def test_calendar_events_privacy_summary_not_in_audit(client, monkeypatch):
    """PRIVACY: event summary + attendee email never reach the units audit row.

    gate.log_audit records only {provider, action, units, success} — assert the
    secret summary/attendee strings never appear in any logged row.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_calendar_creds(monkeypatch)
    from src.tools.email import calendar_client, gate

    secret_summary = "Confidential board meeting agenda"
    secret_attendee = "ceo-private@x.com"
    fake_events = [
        {
            "id": "ev001",
            "summary": secret_summary,
            "start": "2026-06-06T09:00:00Z",
            "end": "2026-06-06T10:00:00Z",
            "attendees": [{"email": secret_attendee, "display_name": "CEO"}],
            "location": "Private room",
            "all_day": False,
        },
    ]
    monkeypatch.setattr(
        calendar_client, "list_events",
        lambda creds, time_min, time_max, calendar_id, max_results: fake_events,
    )

    audit_rows: list[dict] = []

    def _fake_log_audit(provider, pid, action, units, success, error_code=None):
        audit_rows.append({"provider": provider, "action": action,
                           "units": units, "success": success})

    monkeypatch.setattr(gate, "log_audit", _fake_log_audit)

    resp = await client.post(
        f"{_BASE}/calendar/events",
        headers=_HDR_CAL,
        json={"time_min": "2026-06-06T00:00:00Z", "time_max": "2026-06-07T00:00:00Z"},
    )
    assert resp.status_code == 200, resp.text
    # POSITIVE — the secret content DID flow to the response body.
    assert resp.json()["events"][0]["summary"] == secret_summary
    # PRIVACY — but NOT into any audit row.
    for row in audit_rows:
        row_str = json.dumps(row)
        assert secret_summary not in row_str, f"summary leaked into audit: {row_str}"
        assert secret_attendee not in row_str, f"attendee leaked into audit: {row_str}"


# ===========================================================================
# freebusy
# ===========================================================================


@pytest.mark.asyncio
async def test_calendar_freebusy_success_returns_busy_intervals(client, monkeypatch):
    """AC: /calendar/freebusy returns 200 with {busy: {calendar_id: [intervals]}}.

    POSITIVE: freebusy is called with the request args; response maps the busy
    intervals per calendar.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_calendar_creds(monkeypatch)
    from src.tools.email import calendar_client

    calls: list[tuple] = []
    # FIX-2: freebusy now returns {"busy": ..., "errors": ...}
    fake_fb_result = {
        "busy": {
            "primary": [
                {"start": "2026-06-06T09:00:00Z", "end": "2026-06-06T10:00:00Z"},
                {"start": "2026-06-06T14:00:00Z", "end": "2026-06-06T15:00:00Z"},
            ],
            "team@x.com": [],
        },
    }

    def _fake_freebusy(creds, time_min, time_max, calendars):
        calls.append((time_min, time_max, list(calendars)))
        return fake_fb_result

    monkeypatch.setattr(calendar_client, "freebusy", _fake_freebusy)

    resp = await client.post(
        f"{_BASE}/calendar/freebusy",
        headers=_HDR_CAL,
        json={
            "time_min": "2026-06-06T00:00:00Z",
            "time_max": "2026-06-07T00:00:00Z",
            "calendars": ["primary", "team@x.com"],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body["busy"].keys()) == {"primary", "team@x.com"}
    assert len(body["busy"]["primary"]) == 2
    assert body["busy"]["primary"][0]["start"] == "2026-06-06T09:00:00Z"
    assert body["busy"]["primary"][0]["end"] == "2026-06-06T10:00:00Z"
    assert body["busy"]["team@x.com"] == []
    # FIX-2: no errors in the happy-path response.
    assert body.get("errors") is None
    # POSITIVE — freebusy ran with the supplied window + calendars.
    assert calls == [
        ("2026-06-06T00:00:00Z", "2026-06-07T00:00:00Z", ["primary", "team@x.com"])
    ]


@pytest.mark.asyncio
async def test_calendar_freebusy_default_calendars_primary(client, monkeypatch):
    """freebusy with no `calendars` defaults to ['primary']."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_calendar_creds(monkeypatch)
    from src.tools.email import calendar_client

    calls: list[list] = []
    # FIX-2: freebusy returns {"busy": ..., "errors": ...} shape.
    monkeypatch.setattr(
        calendar_client, "freebusy",
        lambda c, tmin, tmax, calendars: (calls.append(list(calendars)), {"busy": {"primary": []}})[1],
    )

    resp = await client.post(
        f"{_BASE}/calendar/freebusy",
        headers=_HDR_CAL,
        json={"time_min": "2026-06-06T00:00:00Z", "time_max": "2026-06-07T00:00:00Z"},
    )
    assert resp.status_code == 200, resp.text
    assert calls == [["primary"]]


@pytest.mark.asyncio
async def test_calendar_freebusy_401_no_auth(client, monkeypatch):
    """/calendar/freebusy returns 401 when no Google creds are stored."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")
    resp = await client.post(
        f"{_BASE}/calendar/freebusy",
        headers=_HDR_CAL,
        json={"time_min": "2026-06-06T00:00:00Z", "time_max": "2026-06-07T00:00:00Z"},
    )
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_calendar_freebusy_layer0_denial_403(client, monkeypatch):
    """AC: /calendar/freebusy 403s on Layer-0 grant denial; freebusy NOT called."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_calendar_creds(monkeypatch)
    from src.tools.email import calendar_client
    from src.services import tool_grants as tg

    real_check = tg.check_grant

    def _deny_check(config, role, tool_name, *, project_id=None):
        if role == "locked-role":
            return tg.GrantDecision.DENY
        return real_check(config, role, tool_name, project_id=project_id)

    monkeypatch.setattr(tools_email, "check_grant", _deny_check)
    called: list = []
    monkeypatch.setattr(
        calendar_client, "freebusy",
        lambda *a, **k: called.append(1) or {"busy": {}},
    )

    resp = await client.post(
        f"{_BASE}/calendar/freebusy",
        headers={**_HDR_CAL, "X-Agent-Role": "locked-role"},
        json={"time_min": "2026-06-06T00:00:00Z", "time_max": "2026-06-07T00:00:00Z"},
    )
    assert resp.status_code == 403, resp.text
    assert "tool_grant_denied" in resp.json()["detail"]
    assert called == [], "freebusy must NOT run when Layer-0 denies"


@pytest.mark.asyncio
async def test_calendar_freebusy_insufficient_scope_412(client, monkeypatch):
    """AC: /calendar/freebusy returns 412 when the token lacks calendar.readonly."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_calendar_creds(monkeypatch)
    from src.tools.email import calendar_client

    def _raise_scope(creds, time_min, time_max, calendars):
        raise calendar_client.CalendarScopeError("calendar scope not granted")

    monkeypatch.setattr(calendar_client, "freebusy", _raise_scope)

    resp = await client.post(
        f"{_BASE}/calendar/freebusy",
        headers=_HDR_CAL,
        json={"time_min": "2026-06-06T00:00:00Z", "time_max": "2026-06-07T00:00:00Z"},
    )
    assert resp.status_code == 412, f"expected 412, got {resp.status_code}: {resp.text}"
    assert resp.json()["detail"] == {
        "error": "calendar_scope_not_granted", "hint": "re-consent OAuth"
    }


# ===========================================================================
# calendar_client unit — scope-error classification + event mapping
# ===========================================================================


def test_is_scope_error_maps_403_insufficient_only():
    """_is_scope_error: 403+insufficient → True; a plain 403 → False; 401 → False.

    NEGATIVE lock: a genuine non-scope 403 (e.g. calendar not shared) must NOT be
    mis-reported as a scope gap, and a 401 must NOT either.
    """
    from unittest.mock import MagicMock
    from googleapiclient.errors import HttpError
    from src.tools.email import calendar_client

    def _http_error(status: int, content: bytes) -> HttpError:
        resp = MagicMock()
        resp.status = status
        return HttpError(resp=resp, content=content)

    scope_err = _http_error(403, b'{"error":{"message":"Request had insufficient authentication scopes."}}')
    assert calendar_client._is_scope_error(scope_err) is True

    plain_403 = _http_error(403, b'{"error":{"message":"The requested calendar is not shared."}}')
    assert calendar_client._is_scope_error(plain_403) is False

    unauth_401 = _http_error(401, b'{"error":{"message":"insufficient ... ignored because 401"}}')
    assert calendar_client._is_scope_error(unauth_401) is False


def test_list_events_raises_scope_error_on_403_insufficient(monkeypatch):
    """list_events maps a 403-insufficient HttpError to CalendarScopeError.

    POSITIVE: a scope-gap 403 surfaces as the dedicated exception (route → 412).
    """
    from unittest.mock import MagicMock
    from googleapiclient.errors import HttpError
    from src.tools.email import calendar_client

    resp = MagicMock()
    resp.status = 403
    scope_err = HttpError(
        resp=resp,
        content=b'{"error":{"message":"Request had insufficient authentication scopes."}}',
    )

    fake_service = MagicMock()
    fake_service.events().list().execute.side_effect = scope_err

    monkeypatch.setattr(calendar_client, "_build_service", lambda creds: fake_service)

    import pytest as _pytest
    with _pytest.raises(calendar_client.CalendarScopeError):
        calendar_client.list_events(
            object(), "2026-06-06T00:00:00Z", "2026-06-07T00:00:00Z"
        )


def test_freebusy_raises_scope_error_on_403_insufficient(monkeypatch):
    """freebusy maps a 403-insufficient HttpError to CalendarScopeError.

    FIX-4 (#1942): mirrors the list_events scope-error unit test for the freebusy
    path. POSITIVE: a scope-gap 403 on freebusy.query surfaces as CalendarScopeError
    (route → 412), not a generic 502.
    """
    from unittest.mock import MagicMock
    from googleapiclient.errors import HttpError
    from src.tools.email import calendar_client

    resp = MagicMock()
    resp.status = 403
    scope_err = HttpError(
        resp=resp,
        content=b'{"error":{"message":"Request had insufficient authentication scopes."}}',
    )

    fake_service = MagicMock()
    fake_service.freebusy().query().execute.side_effect = scope_err

    monkeypatch.setattr(calendar_client, "_build_service", lambda creds: fake_service)

    import pytest as _pytest
    with _pytest.raises(calendar_client.CalendarScopeError):
        calendar_client.freebusy(
            object(), "2026-06-06T00:00:00Z", "2026-06-07T00:00:00Z", ["primary"]
        )
