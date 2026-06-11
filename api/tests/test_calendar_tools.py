"""Kanban #1963 — Calendar tools at the PROPER base /api/tools/calendar.

Contract-smoke + gate coverage for the relocated + extended Calendar surface
(Google + Outlook), READ (list-events/freebusy) + WRITE (create-event/respond):

  READ : Layer-0 grant → tier(READ, no-op) → creds → cap → client → gate.log_audit
  WRITE: Layer-0 grant → operator-proof(WRITE) → creds → cap → client
         → gate.log_audit + secretary-action audit row.

What these tests lock:
  - READ list-events/freebusy return 200 with the right shape on the happy path
    (POSITIVE: the client fn really ran), for BOTH providers.
  - 401 when no provider creds are stored.
  - Layer-0 grant DENY → 403 BEFORE any upstream work (NEGATIVE: client not run).
  - Insufficient scope → 412 with the fixed {error: calendar_scope_not_granted,
    hint: re-consent OAuth} detail (no token/event leakage).
  - WRITE create-event/respond return 200 with NO proof when the gate is INACTIVE
    (fail-open), but 403 (operator_proof_required) when the gate is ACTIVE + no
    token; the WRITE client fn is NOT called on the 403 (NEGATIVE lock).
  - The RELOCATED Google read routes live at the NEW base (and the OLD email-base
    routes are GONE — 404).
  - Drift guard: the policy file's calendar `write` set == CalendarTier proof set.
  - calendar_client client-level scope-error unit tests (carried from #1942's
    test_calendar_read.py, which this file supersedes).

Hermetic project ids; touches NO live DB rows (creds cache-seeded, upstream
monkeypatched) so the live agent_teams row-count invariant holds.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from src.routers import tools_calendar
from src.routers import tools_email
from src.routers.tools_calendar import CalendarTier, _PROOF_REQUIRED_TIERS

_BASE = "/api/tools/calendar"
_KEY_ENV = "OPERATOR_ACTION_KEY"
_TOKEN = "s3cret-operator-token"

# Hermetic project ids (disjoint from the email test blocks that use 999x).
_PROJ = 9985
_HDR = {"X-Project-Id": str(_PROJ)}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _fake_google_creds() -> object:
    from unittest.mock import MagicMock
    from google.oauth2.credentials import Credentials as RealCreds

    creds = MagicMock(spec=RealCreds)
    creds.expiry = datetime.datetime(2099, 1, 1, 0, 0, 0)
    creds._at_email_cache = "test@gmail.com"
    creds.scopes = [
        "https://mail.google.com/",
        "https://www.googleapis.com/auth/calendar.events",
    ]
    return creds


def _fake_outlook_creds() -> dict:
    # Outlook creds are a plain token dict (msal result shape).
    return {
        "access_token": "fake-access-token",
        "refresh_token": "fake-refresh-token",
        "expires_in": 3600,
        "_acquired_at": datetime.datetime.now(datetime.UTC).timestamp(),
    }


@pytest.fixture(autouse=True)
def _clean_stores():
    """Clear the in-memory creds + daily-units stores for _PROJ between tests."""
    from src.tools.email import gate, token_store

    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    for prov in ("gmail", "outlook"):
        token_store._CACHE.pop((prov, _PROJ), None)
    gate._DAILY_UNITS.pop((_PROJ, today), None)
    yield
    for prov in ("gmail", "outlook"):
        token_store._CACHE.pop((prov, _PROJ), None)
    gate._DAILY_UNITS.pop((_PROJ, today), None)


def _seed_google(monkeypatch):
    from src.tools.email import token_store

    token_store._CACHE[("gmail", _PROJ)] = _fake_google_creds()
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")


def _seed_outlook(monkeypatch):
    from src.tools.email import token_store

    token_store._CACHE[("outlook", _PROJ)] = _fake_outlook_creds()
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")


def _read_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ===========================================================================
# READ — list-events (both providers)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "outlook"])
async def test_list_events_success(client, monkeypatch, provider):
    """AC: /{provider}/list-events returns 200 with the event shape.

    POSITIVE: the provider's list_events client fn runs with the request args.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    if provider == "google":
        _seed_google(monkeypatch)
        from src.tools.email import calendar_client as cc
    else:
        _seed_outlook(monkeypatch)
        from src.tools.email import outlook_calendar_client as cc

    calls: list[tuple] = []
    fake_events = [
        {
            "id": "ev001",
            "summary": "Standup",
            "start": "2026-06-06T09:00:00Z",
            "end": "2026-06-06T09:15:00Z",
            "attendees": [{"email": "alice@x.com", "display_name": "Alice", "response_status": "accepted"}],
            "location": "Zoom",
            "all_day": False,
        },
    ]

    def _fake(creds, time_min, time_max, calendar_id, max_results):
        calls.append((time_min, time_max, calendar_id, max_results))
        return fake_events

    monkeypatch.setattr(cc, "list_events", _fake)

    resp = await client.post(
        f"{_BASE}/{provider}/list-events",
        headers=_HDR,
        json={
            "time_min": "2026-06-06T00:00:00Z",
            "time_max": "2026-06-08T00:00:00Z",
            "calendar_id": "primary",
            "max_results": 50,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 1
    assert body["events"][0]["id"] == "ev001"
    assert body["events"][0]["attendees"][0]["email"] == "alice@x.com"
    assert calls == [("2026-06-06T00:00:00Z", "2026-06-08T00:00:00Z", "primary", 50)]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "outlook"])
async def test_list_events_401_no_auth(client, monkeypatch, provider):
    """/{provider}/list-events returns 401 when no creds are stored."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")
    resp = await client.post(
        f"{_BASE}/{provider}/list-events",
        headers=_HDR,
        json={"time_min": "2026-06-06T00:00:00Z", "time_max": "2026-06-07T00:00:00Z"},
    )
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "outlook"])
async def test_list_events_layer0_denial_403(client, monkeypatch, provider):
    """AC: list-events 403s on Layer-0 grant denial; client NOT called."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    if provider == "google":
        _seed_google(monkeypatch)
        from src.tools.email import calendar_client as cc
    else:
        _seed_outlook(monkeypatch)
        from src.tools.email import outlook_calendar_client as cc
    from src.services import tool_grants as tg

    real_check = tg.check_grant

    def _deny_check(config, role, tool_name, *, project_id=None):
        if role == "locked-role":
            return tg.GrantDecision.DENY
        return real_check(config, role, tool_name, project_id=project_id)

    # Layer-0 gate lives in tools_email (imported by tools_calendar) — patch the
    # name the gate actually calls.
    monkeypatch.setattr(tools_email, "check_grant", _deny_check)
    called: list = []
    monkeypatch.setattr(cc, "list_events", lambda *a, **k: called.append(1) or [])

    resp = await client.post(
        f"{_BASE}/{provider}/list-events",
        headers={**_HDR, "X-Agent-Role": "locked-role"},
        json={"time_min": "2026-06-06T00:00:00Z", "time_max": "2026-06-07T00:00:00Z"},
    )
    assert resp.status_code == 403, resp.text
    assert "tool_grant_denied" in resp.json()["detail"]
    assert called == [], "list_events must NOT run when Layer-0 denies"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "outlook"])
async def test_list_events_insufficient_scope_412(client, monkeypatch, provider):
    """AC: list-events returns 412 when the token lacks the calendar scope."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    if provider == "google":
        _seed_google(monkeypatch)
        from src.tools.email import calendar_client as cc
    else:
        _seed_outlook(monkeypatch)
        from src.tools.email import outlook_calendar_client as cc
    from src.tools.email import calendar_client

    def _raise_scope(creds, time_min, time_max, calendar_id, max_results):
        raise calendar_client.CalendarScopeError("calendar scope not granted")

    monkeypatch.setattr(cc, "list_events", _raise_scope)

    resp = await client.post(
        f"{_BASE}/{provider}/list-events",
        headers=_HDR,
        json={"time_min": "2026-06-06T00:00:00Z", "time_max": "2026-06-07T00:00:00Z"},
    )
    assert resp.status_code == 412, resp.text
    assert resp.json()["detail"] == {
        "error": "calendar_scope_not_granted", "hint": "re-consent OAuth"
    }


# ===========================================================================
# READ — freebusy (both providers)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "outlook"])
async def test_freebusy_success(client, monkeypatch, provider):
    """AC: /{provider}/freebusy returns 200 with {busy: {cal: [intervals]}}."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    if provider == "google":
        _seed_google(monkeypatch)
        from src.tools.email import calendar_client as cc
    else:
        _seed_outlook(monkeypatch)
        from src.tools.email import outlook_calendar_client as cc

    calls: list[tuple] = []
    fake_fb = {
        "busy": {
            "primary": [{"start": "2026-06-06T09:00:00Z", "end": "2026-06-06T10:00:00Z"}],
        },
    }

    def _fake(creds, time_min, time_max, calendars):
        calls.append((time_min, time_max, list(calendars)))
        return fake_fb

    monkeypatch.setattr(cc, "freebusy", _fake)

    resp = await client.post(
        f"{_BASE}/{provider}/freebusy",
        headers=_HDR,
        json={
            "time_min": "2026-06-06T00:00:00Z",
            "time_max": "2026-06-07T00:00:00Z",
            "calendars": ["primary"],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["busy"]["primary"]) == 1
    assert body.get("errors") is None
    assert calls == [("2026-06-06T00:00:00Z", "2026-06-07T00:00:00Z", ["primary"])]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "outlook"])
async def test_freebusy_401_no_auth(client, monkeypatch, provider):
    """/{provider}/freebusy returns 401 when no creds are stored."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")
    resp = await client.post(
        f"{_BASE}/{provider}/freebusy",
        headers=_HDR,
        json={"time_min": "2026-06-06T00:00:00Z", "time_max": "2026-06-07T00:00:00Z"},
    )
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_freebusy_insufficient_scope_412(client, monkeypatch):
    """AC: freebusy returns 412 when the token lacks the calendar scope (google)."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_google(monkeypatch)
    from src.tools.email import calendar_client

    def _raise_scope(creds, time_min, time_max, calendars):
        raise calendar_client.CalendarScopeError("calendar scope not granted")

    monkeypatch.setattr(calendar_client, "freebusy", _raise_scope)

    resp = await client.post(
        f"{_BASE}/google/freebusy",
        headers=_HDR,
        json={"time_min": "2026-06-06T00:00:00Z", "time_max": "2026-06-07T00:00:00Z"},
    )
    assert resp.status_code == 412, resp.text
    assert resp.json()["detail"] == {
        "error": "calendar_scope_not_granted", "hint": "re-consent OAuth"
    }


# ===========================================================================
# WRITE — create-event (operator-proof tier)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "outlook"])
async def test_create_event_success_gate_inactive(client, monkeypatch, provider, tmp_path):
    """AC: create-event 200 with NO proof when the gate is INACTIVE (fail-open).

    POSITIVE: the provider's create_event client fn runs; an action-audit row is
    written with operator_proof approval mode + the created event id.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)  # gate INACTIVE.
    audit = tmp_path / "email-actions.jsonl"
    monkeypatch.setattr(tools_email, "_EMAIL_ACTIONS_PATH", audit)
    if provider == "google":
        _seed_google(monkeypatch)
        from src.tools.email import calendar_client as cc
    else:
        _seed_outlook(monkeypatch)
        from src.tools.email import outlook_calendar_client as cc

    calls: list[dict] = []

    def _fake(creds, **kwargs):
        calls.append(kwargs)
        return {"event_id": "new-ev-123", "html_link": "https://cal/x"}

    monkeypatch.setattr(cc, "create_event", _fake)

    resp = await client.post(
        f"{_BASE}/{provider}/create-event",
        headers=_HDR,
        json={
            "title": "Sprint review",
            "start": "2026-06-10T09:00:00Z",
            "end": "2026-06-10T10:00:00Z",
            "timezone": "UTC",
            "attendees": ["bob@x.com"],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["event_id"] == "new-ev-123"
    assert body["html_link"] == "https://cal/x"
    # POSITIVE — create_event ran with the supplied args.
    assert calls and calls[0]["title"] == "Sprint review"
    assert calls[0]["attendees"] == ["bob@x.com"]
    # Action-audit row written (operator_proof mode, event id referenced).
    lines = _read_lines(audit)
    assert len(lines) == 1, lines
    assert lines[0]["action"] == "calendar_create_event"
    assert lines[0]["approval_mode"] == "operator_proof"
    assert lines[0]["message_ids"] == ["new-ev-123"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "outlook"])
async def test_create_event_proof_required_when_gate_active(client, monkeypatch, provider):
    """AC: create-event 403s (operator_proof_required) when gate ACTIVE + no token.

    NEGATIVE lock: the create_event client fn is NOT called.
    """
    monkeypatch.setenv(_KEY_ENV, _TOKEN)  # gate ACTIVE.
    if provider == "google":
        _seed_google(monkeypatch)
        from src.tools.email import calendar_client as cc
    else:
        _seed_outlook(monkeypatch)
        from src.tools.email import outlook_calendar_client as cc

    called: list = []
    monkeypatch.setattr(
        cc, "create_event",
        lambda creds, **k: called.append(1) or {"event_id": "x"},
    )

    resp = await client.post(
        f"{_BASE}/{provider}/create-event",
        headers=_HDR,  # NO X-Operator-Token.
        json={
            "title": "Sprint review",
            "start": "2026-06-10T09:00:00Z",
            "end": "2026-06-10T10:00:00Z",
            "timezone": "UTC",
        },
    )
    assert resp.status_code == 403, resp.text
    assert "operator_proof_required" in resp.json()["detail"]
    assert CalendarTier.WRITE.value in resp.json()["detail"]
    assert called == [], "create_event must NOT run without operator-proof when gate ACTIVE"


@pytest.mark.asyncio
async def test_create_event_proof_passes_with_token_when_gate_active(client, monkeypatch):
    """create-event 200 when gate ACTIVE + a valid X-Operator-Token is presented."""
    monkeypatch.setenv(_KEY_ENV, _TOKEN)  # gate ACTIVE.
    _seed_google(monkeypatch)
    from src.tools.email import calendar_client as cc

    monkeypatch.setattr(
        cc, "create_event",
        lambda creds, **k: {"event_id": "ok-ev", "html_link": None},
    )

    resp = await client.post(
        f"{_BASE}/google/create-event",
        headers={**_HDR, "X-Operator-Token": _TOKEN},
        json={
            "title": "ok",
            "start": "2026-06-10T09:00:00Z",
            "end": "2026-06-10T10:00:00Z",
            "timezone": "UTC",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["event_id"] == "ok-ev"


@pytest.mark.asyncio
async def test_create_event_insufficient_scope_412(client, monkeypatch):
    """create-event returns 412 when the token lacks the WRITE calendar scope."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_google(monkeypatch)
    from src.tools.email import calendar_client

    def _raise_scope(creds, **kwargs):
        raise calendar_client.CalendarScopeError("calendar scope not granted")

    monkeypatch.setattr(calendar_client, "create_event", _raise_scope)

    resp = await client.post(
        f"{_BASE}/google/create-event",
        headers=_HDR,
        json={
            "title": "x",
            "start": "2026-06-10T09:00:00Z",
            "end": "2026-06-10T10:00:00Z",
            "timezone": "UTC",
        },
    )
    assert resp.status_code == 412, resp.text
    assert resp.json()["detail"] == {
        "error": "calendar_scope_not_granted", "hint": "re-consent OAuth"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "outlook"])
async def test_create_event_layer0_denial_403(client, monkeypatch, provider):
    """FIX-7: create-event 403s on Layer-0 grant denial BEFORE the proof gate;
    client NOT called — for BOTH providers.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    if provider == "google":
        _seed_google(monkeypatch)
        from src.tools.email import calendar_client as cc
    else:
        _seed_outlook(monkeypatch)
        from src.tools.email import outlook_calendar_client as cc
    from src.services import tool_grants as tg

    real_check = tg.check_grant

    def _deny_check(config, role, tool_name, *, project_id=None):
        if role == "locked-role":
            return tg.GrantDecision.DENY
        return real_check(config, role, tool_name, project_id=project_id)

    monkeypatch.setattr(tools_email, "check_grant", _deny_check)
    called: list = []
    monkeypatch.setattr(cc, "create_event", lambda creds, **k: called.append(1) or {"event_id": "x"})

    resp = await client.post(
        f"{_BASE}/{provider}/create-event",
        headers={**_HDR, "X-Agent-Role": "locked-role"},
        json={
            "title": "x",
            "start": "2026-06-10T09:00:00Z",
            "end": "2026-06-10T10:00:00Z",
            "timezone": "UTC",
        },
    )
    assert resp.status_code == 403, resp.text
    assert "tool_grant_denied" in resp.json()["detail"]
    assert called == [], "create_event must NOT run when Layer-0 denies"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "outlook"])
async def test_respond_layer0_denial_403(client, monkeypatch, provider):
    """FIX-7: respond 403s on Layer-0 grant denial BEFORE the proof gate;
    client NOT called — for BOTH providers.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    if provider == "google":
        _seed_google(monkeypatch)
        from src.tools.email import calendar_client as cc
    else:
        _seed_outlook(monkeypatch)
        from src.tools.email import outlook_calendar_client as cc
    from src.services import tool_grants as tg

    real_check = tg.check_grant

    def _deny_check(config, role, tool_name, *, project_id=None):
        if role == "locked-role":
            return tg.GrantDecision.DENY
        return real_check(config, role, tool_name, project_id=project_id)

    monkeypatch.setattr(tools_email, "check_grant", _deny_check)
    called: list = []
    monkeypatch.setattr(cc, "respond", lambda *a, **k: called.append(1) or {"event_id": "x", "response": "accept"})

    resp = await client.post(
        f"{_BASE}/{provider}/respond",
        headers={**_HDR, "X-Agent-Role": "locked-role"},
        json={"event_id": "ev-xyz", "response": "accept"},
    )
    assert resp.status_code == 403, resp.text
    assert "tool_grant_denied" in resp.json()["detail"]
    assert called == [], "respond must NOT run when Layer-0 denies"


# ===========================================================================
# WRITE — respond (operator-proof tier)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "outlook"])
async def test_respond_success_gate_inactive(client, monkeypatch, provider, tmp_path):
    """AC: respond 200 with NO proof when the gate is INACTIVE; client runs."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    audit = tmp_path / "email-actions.jsonl"
    monkeypatch.setattr(tools_email, "_EMAIL_ACTIONS_PATH", audit)
    if provider == "google":
        _seed_google(monkeypatch)
        from src.tools.email import calendar_client as cc
    else:
        _seed_outlook(monkeypatch)
        from src.tools.email import outlook_calendar_client as cc

    calls: list[tuple] = []

    def _fake(creds, event_id, response, calendar_id):
        calls.append((event_id, response, calendar_id))
        return {"event_id": event_id, "response": response}

    monkeypatch.setattr(cc, "respond", _fake)

    resp = await client.post(
        f"{_BASE}/{provider}/respond",
        headers=_HDR,
        json={"event_id": "ev-xyz", "response": "accept"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["event_id"] == "ev-xyz"
    assert body["response"] == "accept"
    assert calls == [("ev-xyz", "accept", "primary")]
    lines = _read_lines(audit)
    assert len(lines) == 1
    assert lines[0]["action"] == "calendar_respond_accept"
    assert lines[0]["approval_mode"] == "operator_proof"


@pytest.mark.asyncio
async def test_respond_proof_required_when_gate_active(client, monkeypatch):
    """respond 403s (operator_proof_required) when gate ACTIVE + no token; client not run."""
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    _seed_google(monkeypatch)
    from src.tools.email import calendar_client as cc

    called: list = []
    monkeypatch.setattr(cc, "respond", lambda *a, **k: called.append(1) or {"event_id": "x", "response": "accept"})

    resp = await client.post(
        f"{_BASE}/google/respond",
        headers=_HDR,
        json={"event_id": "ev-xyz", "response": "decline"},
    )
    assert resp.status_code == 403, resp.text
    assert "operator_proof_required" in resp.json()["detail"]
    assert called == []


@pytest.mark.asyncio
async def test_respond_bad_response_value_422(client, monkeypatch):
    """respond rejects an out-of-vocabulary response with 422 (schema guard)."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_google(monkeypatch)
    resp = await client.post(
        f"{_BASE}/google/respond",
        headers=_HDR,
        json={"event_id": "ev-xyz", "response": "maybe"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_respond_not_an_attendee_409(client, monkeypatch):
    """respond maps a ValueError (not_an_attendee) to 409, NOT 502."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_google(monkeypatch)
    from src.tools.email import calendar_client as cc

    def _raise(creds, event_id, response, calendar_id):
        raise ValueError("not_an_attendee")

    monkeypatch.setattr(cc, "respond", _raise)

    resp = await client.post(
        f"{_BASE}/google/respond",
        headers=_HDR,
        json={"event_id": "ev-xyz", "response": "accept"},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "cannot_respond"


# ===========================================================================
# Relocation — new base wired; OLD email-base calendar routes are GONE
# ===========================================================================


@pytest.mark.asyncio
async def test_old_email_base_calendar_routes_removed(client, monkeypatch):
    """The #1942 routes under /api/tools/email/calendar/* no longer exist (404).

    Proves the relocation REMOVED the old surface (nothing consumes it yet).
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_google(monkeypatch)
    for old in ("/api/tools/email/calendar/events", "/api/tools/email/calendar/freebusy"):
        resp = await client.post(
            old,
            headers=_HDR,
            json={"time_min": "2026-06-06T00:00:00Z", "time_max": "2026-06-07T00:00:00Z"},
        )
        assert resp.status_code == 404, f"{old} should be gone, got {resp.status_code}"


def test_new_base_routes_registered():
    """The new calendar routes are registered on the app at /api/tools/calendar."""
    from src.main import app

    paths = {r.path for r in app.routes}
    for p in (
        "/api/tools/calendar/{provider}/list-events",
        "/api/tools/calendar/{provider}/freebusy",
        "/api/tools/calendar/{provider}/create-event",
        "/api/tools/calendar/{provider}/respond",
    ):
        assert p in paths, f"missing route {p}; have {sorted(x for x in paths if 'calendar' in x)}"
    # And the OLD email-base calendar routes are gone from the route table.
    assert "/api/tools/email/calendar/events" not in paths
    assert "/api/tools/email/calendar/freebusy" not in paths


# ===========================================================================
# Policy drift guard
# ===========================================================================


def _policy_path() -> Path:
    # this file: /repo/api/tests/test_calendar_tools.py -> repo root = parents[2]
    return Path(__file__).resolve().parents[2] / "_runtime" / "secretary-email-policy.json"


def test_calendar_policy_section_exists_and_matches_tier_vocab():
    """The policy has a `calendar` section keyed by the CalendarTier vocabulary."""
    policy = json.loads(_policy_path().read_text(encoding="utf-8"))
    assert "calendar" in policy, "policy missing the #1963 calendar section"
    cal_tiers = set(policy["calendar"]["tiers"].keys())
    code_tiers = {t.value for t in CalendarTier}
    assert cal_tiers == code_tiers, (
        f"policy calendar tiers {cal_tiers} must EQUAL CalendarTier {code_tiers}"
    )


def test_calendar_policy_proof_tiers_match_code():
    """DRIFT GUARD: the policy's calendar operator_proof set == _PROOF_REQUIRED_TIERS."""
    policy = json.loads(_policy_path().read_text(encoding="utf-8"))
    policy_proof = {
        name for name, spec in policy["calendar"]["tiers"].items()
        if spec["approval_mode"] == "operator_proof"
    }
    code_proof = {t.value for t in _PROOF_REQUIRED_TIERS}
    assert policy_proof == code_proof, (
        f"policy calendar operator_proof tiers {policy_proof} diverge from code "
        f"_PROOF_REQUIRED_TIERS {code_proof}"
    )


# ===========================================================================
# calendar_client client-level scope-error units (carried from #1942 test_calendar_read.py)
# ===========================================================================


def test_is_scope_error_maps_403_insufficient_only():
    """_is_scope_error: 403+insufficient → True; plain 403 → False; 401 → False."""
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


def test_create_event_raises_scope_error_on_403_insufficient(monkeypatch):
    """create_event maps a 403-insufficient HttpError to CalendarScopeError."""
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
    fake_service.events().insert().execute.side_effect = scope_err
    monkeypatch.setattr(calendar_client, "_build_service", lambda creds: fake_service)

    with pytest.raises(calendar_client.CalendarScopeError):
        calendar_client.create_event(
            object(),
            title="x",
            start="2026-06-10T09:00:00Z",
            end="2026-06-10T10:00:00Z",
            timezone="UTC",
        )


def test_respond_not_attendee_raises_value_error(monkeypatch):
    """respond raises ValueError('not_an_attendee') when the user is not a guest."""
    from unittest.mock import MagicMock
    from src.tools.email import calendar_client

    fake_service = MagicMock()
    # events.get returns an event with attendees but none flagged `self`.
    fake_service.events().get().execute.return_value = {
        "id": "ev1",
        "attendees": [{"email": "other@x.com", "self": False}],
    }
    monkeypatch.setattr(calendar_client, "_build_service", lambda creds: fake_service)

    with pytest.raises(ValueError, match="not_an_attendee"):
        calendar_client.respond(object(), "ev1", "accept")


def test_outlook_is_scope_error_maps_403_access_denied():
    """outlook_calendar_client._is_scope_error: 403 ErrorAccessDenied → True; 401 → False."""
    from unittest.mock import MagicMock
    from src.tools.email import outlook_calendar_client as occ

    def _resp(status, body):
        r = MagicMock()
        r.status_code = status
        r.json.return_value = body
        return r

    scope = _resp(403, {"error": {"code": "ErrorAccessDenied", "message": "insufficient privileges"}})
    assert occ._is_scope_error(scope) is True

    plain = _resp(403, {"error": {"code": "ErrorItemNotFound", "message": "not found"}})
    assert occ._is_scope_error(plain) is False

    unauth = _resp(401, {"error": {"code": "InvalidAuthenticationToken", "message": "scope"}})
    assert occ._is_scope_error(unauth) is False


def test_outlook_scope_error_authorization_request_denied():
    """Round-2: Authorization_RequestDenied 403 (code) IS a scope error → route 412.

    Round-1 dropped this variant; it must be recovered. This is the real Graph
    OAuth scope-gap response when delegated permission is missing entirely.
    """
    from unittest.mock import MagicMock
    from src.tools.email import outlook_calendar_client as occ

    def _resp(status, body):
        r = MagicMock()
        r.status_code = status
        r.json.return_value = body
        return r

    # POSITIVE: Authorization_RequestDenied code → IS scope error.
    assert occ._is_scope_error(
        _resp(403, {"error": {"code": "Authorization_RequestDenied", "message": "no scope"}})
    ) is True

    # Also works case-insensitively.
    assert occ._is_scope_error(
        _resp(403, {"error": {"code": "authorization_requestdenied", "message": "no scope"}})
    ) is True


def test_outlook_scope_error_no_false_positive_insufficient_quota_in_message():
    """Round-2: 'insufficient quota' in the MESSAGE must NOT trigger scope error.

    Before Round-2, 'insufficient' was matched against the concatenated
    code+message string, so an unrelated 403 whose message contained 'insufficient
    quota' could be mis-classified as a scope gap → wrong 412. The fix restricts
    'insufficient' to the CODE field only.
    """
    from unittest.mock import MagicMock
    from src.tools.email import outlook_calendar_client as occ

    def _resp(status, body):
        r = MagicMock()
        r.status_code = status
        r.json.return_value = body
        return r

    # NEGATIVE: non-scope code + "insufficient quota" in message → NOT scope error.
    assert occ._is_scope_error(
        _resp(403, {"error": {"code": "ErrorFolderNotFound",
                              "message": "insufficient quota for this operation"}})
    ) is False


def test_outlook_scope_error_no_false_positive_calendar_not_shared():
    """Round-2: 'does not have permission to access this calendar' → NOT scope error.

    This is the calendar-not-shared message. Only the tighter phrase
    'does not have the required privilege' must trigger a scope match.
    """
    from unittest.mock import MagicMock
    from src.tools.email import outlook_calendar_client as occ

    def _resp(status, body):
        r = MagicMock()
        r.status_code = status
        r.json.return_value = body
        return r

    # NEGATIVE: calendar-not-shared message → NOT scope error.
    assert occ._is_scope_error(
        _resp(403, {"error": {"code": "ErrorCalendarSharingOperationFailed",
                              "message": "does not have permission to access this calendar"}})
    ) is False


def test_outlook_scope_error_insufficient_scope_code():
    """Round-2: InsufficientScope / insufficient_scope CODE → IS scope error.

    'insufficient' matched against the code field catches both Graph variants
    without false-positiving on quota messages that carry 'insufficient' in
    the message text only.
    """
    from unittest.mock import MagicMock
    from src.tools.email import outlook_calendar_client as occ

    def _resp(status, body):
        r = MagicMock()
        r.status_code = status
        r.json.return_value = body
        return r

    assert occ._is_scope_error(
        _resp(403, {"error": {"code": "InsufficientScope", "message": "token lacks permission"}})
    ) is True

    assert occ._is_scope_error(
        _resp(403, {"error": {"code": "insufficient_scope", "message": "token lacks permission"}})
    ) is True


def test_outlook_scope_error_required_privilege_message():
    """Round-2: 'does not have the required privilege' in the message → IS scope error.

    This tight phrase is the one message-level signal kept. Confirm it still works.
    """
    from unittest.mock import MagicMock
    from src.tools.email import outlook_calendar_client as occ

    def _resp(status, body):
        r = MagicMock()
        r.status_code = status
        r.json.return_value = body
        return r

    assert occ._is_scope_error(
        _resp(403, {"error": {"code": "Authorization_RequestDenied",
                              "message": "user does not have the required privilege"}})
    ) is True


# ===========================================================================
# FIX-1 (#1963) — Outlook event_id with '/' is accepted (length-only check)
# ===========================================================================


def test_outlook_respond_accepts_event_id_with_slash(monkeypatch):
    """FIX-1 + FIX-2: respond() must accept a Graph calendar event_id containing '/'.

    Previously _ID_RE (message charset) excluded '/', so every real Graph
    calendar event id raised ValueError → 409. The fix drops the charset
    regex and uses a length-only bound; Graph rejects a truly bad id itself.
    POSITIVE: the upstream _graph_request_with_retry call is reached (not raised).
    FIX-2: assert the URL passed to _graph_request_with_retry contains %2F
    (encoded slash), NOT a raw slash in the id segment, proving the id was encoded.
    """
    from unittest.mock import MagicMock, patch
    from src.tools.email import outlook_calendar_client as occ

    # A real Graph calendar id shape: long base64url with embedded slashes.
    slash_id = "AAMkAGRlYjliYmY4/LTRkYmMtNGY4NS1hMmZmLWQ5ZWQ3ZmYwZmM5YQBGAAAAAADp"

    fake_resp = MagicMock()
    fake_resp.status_code = 202
    fake_resp.raise_for_status.return_value = None

    with patch.object(occ, "_graph_request_with_retry", return_value=fake_resp) as mock_req, \
         patch.object(occ, "_acquire_silent", return_value="fake-token"):
        result = occ.respond({"access_token": "t"}, slash_id, "accept")

    assert result == {"event_id": slash_id, "response": "accept"}
    # POSITIVE: the upstream client call was reached (id was NOT rejected).
    assert mock_req.called, "upstream must be called — id must NOT raise ValueError"
    # FIX-2: assert the URL has the slash encoded as %2F, NOT raw.
    called_url = mock_req.call_args[0][1]  # positional arg[1] = url
    assert "%2F" in called_url, (
        f"event_id '/' must be percent-encoded as %2F in the URL; got: {called_url!r}"
    )
    assert called_url.endswith("/accept"), (
        f"URL must end with '/accept'; got: {called_url!r}"
    )
    # NEGATIVE: the raw slash from the id must NOT appear in the id path segment.
    # The URL shape is .../me/events/<safe_id>/accept — isolate the id portion.
    from src.tools.email.outlook_client import _GRAPH_BASE
    prefix = f"{_GRAPH_BASE}/me/events/"
    suffix = "/accept"
    inner = called_url[len(prefix):]
    if inner.endswith(suffix):
        inner = inner[: -len(suffix)]
    assert "/" not in inner, (
        f"raw '/' must not appear in the encoded id segment; got segment: {inner!r}"
    )


# ===========================================================================
# FIX-3 (#1963) — scope-marker false-positive: '403 with scope in message
#                  but NOT insufficient' must NOT be a scope error
# ===========================================================================


def test_outlook_scope_marker_no_false_positive_on_does_not_have_permission():
    """FIX-5: a 403 with 'does not have permission to access this calendar' must
    NOT be classified as a scope error (calendar-not-shared ≠ scope gap → must
    NOT become 412).

    Before FIX-5 _SCOPE_MARKERS included 'does not have permission', which caused
    calendar-not-shared 403s to be mis-classified as scope gaps → wrong 412.
    The marker was replaced with the narrower 'does not have the required privilege'.
    """
    from unittest.mock import MagicMock
    from src.tools.email import outlook_calendar_client as occ

    def _resp(status, body):
        r = MagicMock()
        r.status_code = status
        r.json.return_value = body
        return r

    # FIX-5: calendar-not-shared 403 — old broad marker matched; new one must NOT.
    not_scope = _resp(403, {"error": {"code": "ErrorCalendarSharingOperationFailed",
                                      "message": "user does not have permission to access this calendar"}})
    assert occ._is_scope_error(not_scope) is False, (
        "'does not have permission to access this calendar' must NOT trigger scope-error "
        "(FIX-5: calendar-not-shared != scope gap)"
    )

    # Confirm the tight OAuth scope-gap markers still trigger.
    is_scope_insufficient = _resp(403, {"error": {"code": "ErrorAccessDenied",
                                                   "message": "insufficient scope grants"}})
    assert occ._is_scope_error(is_scope_insufficient) is True

    is_scope_privilege = _resp(403, {"error": {"code": "Authorization_RequestDenied",
                                               "message": "user does not have the required privilege"}})
    assert occ._is_scope_error(is_scope_privilege) is True


# ===========================================================================
# FIX-6 (#1963) — freebusy timezone: non-UTC offset is normalized to UTC
# ===========================================================================


def test_outlook_freebusy_error_uses_code_not_message(monkeypatch):
    """FIX-3: freebusy errors_out must use the Graph error CODE enum, not the
    raw message narrative (which can carry emails or internal path info).

    NEGATIVE: the raw Graph message string must NOT appear in the errors dict.
    POSITIVE: the Graph error code string MUST appear.
    """
    from unittest.mock import MagicMock, patch
    from src.tools.email import outlook_calendar_client as occ

    raw_message = "The user alice@internal.example does not have access to this resource."
    error_code = "ErrorFolderNotFound"

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.raise_for_status.return_value = None
    fake_resp.json.return_value = {
        "value": [
            {
                "scheduleId": "alice@internal.example",
                "error": {"code": error_code, "message": raw_message},
            }
        ]
    }

    with patch.object(occ, "_graph_request_with_retry", return_value=fake_resp), \
         patch.object(occ, "_acquire_silent", return_value="fake-token"):
        result = occ.freebusy(
            {"access_token": "t"},
            "2026-06-06T00:00:00Z",
            "2026-06-07T00:00:00Z",
            calendars=["alice@internal.example"],
        )

    errors = result.get("errors", {})
    assert errors, "errors dict must be populated when Graph returns an error entry"

    reasons = errors.get("alice@internal.example", [])
    assert reasons, "error reasons must be non-empty for the inaccessible schedule"

    # POSITIVE: error code must appear.
    assert error_code in reasons, (
        f"Graph error code {error_code!r} must appear in errors; got {reasons!r}"
    )
    # NEGATIVE: raw message with PII must NOT appear.
    for r in reasons:
        assert raw_message not in r, (
            f"raw Graph message (may carry PII) must NOT appear in errors; got {r!r}"
        )


def test_outlook_freebusy_utc_normalizes_offset_timestamp(monkeypatch):
    """FIX-6: freebusy must send UTC-normalized dateTime even when caller passes
    a non-UTC offset (e.g. +07:00). Graph getSchedule mis-interprets an offset
    timestamp when timeZone is declared as UTC.

    Asserts: the body sent to _graph_request_with_retry uses a dateTime without
    any offset suffix, and timeZone is 'UTC'.
    """
    from unittest.mock import MagicMock, patch
    from src.tools.email import outlook_calendar_client as occ

    time_min_with_offset = "2026-06-06T09:00:00+07:00"  # 02:00 UTC
    time_max_with_offset = "2026-06-06T17:00:00+07:00"  # 10:00 UTC

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.raise_for_status.return_value = None
    fake_resp.json.return_value = {"value": []}

    captured: list[dict] = []

    def _capture(method, url, *, headers=None, json_body=None, **kwargs):
        captured.append(json_body or {})
        return fake_resp

    with patch.object(occ, "_graph_request_with_retry", side_effect=_capture), \
         patch.object(occ, "_acquire_silent", return_value="fake-token"):
        occ.freebusy({"access_token": "t"}, time_min_with_offset, time_max_with_offset)

    assert captured, "freebusy must call _graph_request_with_retry"
    body = captured[0]

    start_dt = body["startTime"]["dateTime"]
    end_dt = body["endTime"]["dateTime"]
    start_tz = body["startTime"]["timeZone"]
    end_tz = body["endTime"]["timeZone"]

    assert start_tz == "UTC", f"startTime.timeZone must be 'UTC'; got {start_tz!r}"
    assert end_tz == "UTC", f"endTime.timeZone must be 'UTC'; got {end_tz!r}"

    # The UTC-normalized values: +07:00 offset means subtract 7h for UTC.
    assert start_dt == "2026-06-06T02:00:00", (
        f"start should normalize +07:00 → UTC 02:00:00; got {start_dt!r}"
    )
    assert end_dt == "2026-06-06T10:00:00", (
        f"end should normalize +07:00 → UTC 10:00:00; got {end_dt!r}"
    )

    # Confirm no offset suffix leaks into the sent dateTime strings.
    assert "+" not in start_dt and "+" not in end_dt, (
        "UTC-normalized datetimes must not carry a '+' offset suffix"
    )


# ===========================================================================
# FIX-4 (#1963) — freebusy Layer-0 denial negative lock (both providers)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "outlook"])
async def test_freebusy_layer0_denial_403(client, monkeypatch, provider):
    """AC: freebusy 403s on Layer-0 grant denial; upstream freebusy NOT called.

    FIX-4: mirrors test_list_events_layer0_denial_403 for the freebusy endpoint.
    NEGATIVE lock: the provider freebusy client fn must NOT run when Layer-0 denies.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    if provider == "google":
        _seed_google(monkeypatch)
        from src.tools.email import calendar_client as cc
    else:
        _seed_outlook(monkeypatch)
        from src.tools.email import outlook_calendar_client as cc
    from src.services import tool_grants as tg

    real_check = tg.check_grant

    def _deny_check(config, role, tool_name, *, project_id=None):
        if role == "locked-role":
            return tg.GrantDecision.DENY
        return real_check(config, role, tool_name, project_id=project_id)

    monkeypatch.setattr(tools_email, "check_grant", _deny_check)
    called: list = []
    monkeypatch.setattr(cc, "freebusy", lambda *a, **k: called.append(1) or {})

    resp = await client.post(
        f"{_BASE}/{provider}/freebusy",
        headers={**_HDR, "X-Agent-Role": "locked-role"},
        json={"time_min": "2026-06-06T00:00:00Z", "time_max": "2026-06-07T00:00:00Z"},
    )
    assert resp.status_code == 403, resp.text
    assert "tool_grant_denied" in resp.json()["detail"]
    assert called == [], "freebusy must NOT run when Layer-0 denies"


# ===========================================================================
# FIX-5 (#1963) — privacy audit: event summary + attendee emails not in audit
# ===========================================================================


@pytest.mark.asyncio
async def test_calendar_events_privacy_summary_not_in_audit(
    client, monkeypatch, tmp_path
):
    """FIX-5: event summary and attendee emails must NEVER appear in gate audit rows.

    Ported from the deleted test_calendar_read.py. Tests the Google list-events
    path: seeds a fake event with a distinctive summary and attendee email, then
    asserts neither string appears in any gate.log_audit call.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_google(monkeypatch)
    from src.tools.email import calendar_client as cc, gate

    PRIVATE_SUMMARY = "TOP-SECRET-MEETING-AGENDA-XYZ"
    PRIVATE_EMAIL = "ceo-private@secret-corp.example"

    fake_events = [
        {
            "id": "ev-priv-001",
            "summary": PRIVATE_SUMMARY,
            "start": "2026-06-06T09:00:00Z",
            "end": "2026-06-06T10:00:00Z",
            "attendees": [{"email": PRIVATE_EMAIL, "display_name": "CEO", "response_status": "accepted"}],
            "location": "Secret HQ",
            "all_day": False,
        },
    ]
    monkeypatch.setattr(cc, "list_events", lambda *a, **k: fake_events)

    audit_calls: list[dict] = []
    real_log_audit = gate.log_audit

    def _capture_audit(*args, **kwargs):
        audit_calls.append({"args": args, "kwargs": kwargs})
        return real_log_audit(*args, **kwargs)

    monkeypatch.setattr(gate, "log_audit", _capture_audit)

    resp = await client.post(
        f"{_BASE}/google/list-events",
        headers=_HDR,
        json={"time_min": "2026-06-06T00:00:00Z", "time_max": "2026-06-08T00:00:00Z"},
    )
    assert resp.status_code == 200, resp.text

    # At least one audit call must have been made (success row).
    assert audit_calls, "gate.log_audit must be called on a successful list-events"

    # NEGATIVE: private data must NOT appear in any audit row.
    for call in audit_calls:
        row_str = str(call)
        assert PRIVATE_SUMMARY not in row_str, (
            f"event summary leaked into audit: {row_str!r}"
        )
        assert PRIVATE_EMAIL not in row_str, (
            f"attendee email leaked into audit: {row_str!r}"
        )
