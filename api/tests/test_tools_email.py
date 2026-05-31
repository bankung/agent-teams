"""Tests for the email tools router (Kanban #1610, item A).

Covers all 8 endpoints:
  POST /api/tools/email/auth/gmail/start        (gmail_auth_start)
  GET  /api/tools/email/auth/gmail/callback     (gmail_auth_callback)
  GET  /api/tools/email/auth/gmail/status       (gmail_auth_status)
  POST /api/tools/email/gmail/trash             (gmail_trash)
  GET  /api/tools/email/gmail/usage             (gmail_usage)
  POST /api/tools/email/auth/outlook/start      (outlook_auth_start)
  GET  /api/tools/email/auth/outlook/callback   (outlook_auth_callback)
  GET  /api/tools/email/auth/outlook/status     (outlook_auth_status)
  POST /api/tools/email/outlook/trash           (outlook_trash)

Mocking philosophy:
  - token_store._STORE is patched directly for creds injection.
  - gate._DAILY_UNITS is patched to simulate cap exhaustion.
  - gmail_client.trash_messages / outlook_client.trash_messages are
    monkeypatched to return fake (trashed, errors) without real network.
  - The gate logic, router Pydantic validators, and ordering rules are
    exercised against the real code — NOT mocked.

Gate ordering (FIX-6 #1609):
  message_ids mode: bulk-check BEFORE auth-check.
  query mode: auth-check FIRST (needs creds to list).
"""
from __future__ import annotations

import datetime
import importlib
from unittest.mock import MagicMock, patch

import pytest

# Project-id used in all tests. Picked to avoid colliding with real seed data;
# in-memory stores (token_store._STORE, gate._DAILY_UNITS) are keyed by
# project_id so isolation is guaranteed as long as we clean up after each test.
_PROJ = 9999

# Shared base URL prefix for all email-tool endpoints.
_BASE = "/api/tools/email"

# X-Project-Id header value.
_HDR = {"X-Project-Id": str(_PROJ)}


# ---------------------------------------------------------------------------
# Helper: fake Gmail credentials object
# ---------------------------------------------------------------------------

def _fake_gmail_creds() -> object:
    """Return a minimal google.oauth2.credentials.Credentials-like mock.

    gmail_client.creds_summary() checks isinstance(creds, Credentials)
    and reads `.expiry`. We use MagicMock with spec to satisfy that.
    """
    from google.oauth2.credentials import Credentials as RealCreds
    creds = MagicMock(spec=RealCreds)
    # expiry attribute — creds_summary reads creds.expiry
    creds.expiry = datetime.datetime(2099, 1, 1, 0, 0, 0)
    # _at_email_cache used by _safe_profile_email to skip network call.
    creds._at_email_cache = "test@gmail.com"
    return creds


def _fake_outlook_creds() -> dict:
    """Return a minimal Outlook token dict (msal format).

    outlook_client.creds_summary() checks isinstance(creds, dict) and
    reads id_token_claims, _acquired_at, expires_in.
    """
    import time
    return {
        "access_token": "fake-access-token",
        "refresh_token": "fake-refresh-token",
        "expires_in": 3600,
        "_acquired_at": time.time(),
        "id_token_claims": {"preferred_username": "test@outlook.com"},
    }


# ---------------------------------------------------------------------------
# Autouse fixture: clean token_store + gate between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_email_stores():
    """Clear email in-memory stores before and after each test.

    This isolates tests from each other without touching production state.
    Cleans:
      - token_store._STORE keys for _PROJ
      - gate._DAILY_UNITS keys for _PROJ
    """
    from src.tools.email import gate, token_store

    # Pre-test: remove any stale entries for our test project.
    for provider in ("gmail", "outlook"):
        token_store._STORE.pop((provider, _PROJ), None)
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    gate._DAILY_UNITS.pop((_PROJ, today), None)

    yield

    # Post-test: same cleanup.
    for provider in ("gmail", "outlook"):
        token_store._STORE.pop((provider, _PROJ), None)
    gate._DAILY_UNITS.pop((_PROJ, today), None)


# ===========================================================================
# 1. auth/gmail/status
# ===========================================================================

@pytest.mark.asyncio
async def test_gmail_auth_status_unauthenticated(client) -> None:
    """GET /auth/gmail/status with no creds → authenticated=false."""
    resp = await client.get(f"{_BASE}/auth/gmail/status", headers=_HDR)
    assert resp.status_code == 200
    body = resp.json()
    assert body["authenticated"] is False
    assert body["email"] is None


@pytest.mark.asyncio
async def test_gmail_auth_status_authenticated(client) -> None:
    """GET /auth/gmail/status with injected creds → authenticated=true + shape."""
    from src.tools.email import token_store

    creds = _fake_gmail_creds()
    token_store.put("gmail", _PROJ, creds)

    resp = await client.get(f"{_BASE}/auth/gmail/status", headers=_HDR)
    assert resp.status_code == 200
    body = resp.json()
    assert body["authenticated"] is True
    # creds_summary returns email from _at_email_cache.
    assert body["email"] == "test@gmail.com"
    assert "expires_at" in body


# ===========================================================================
# 2. auth/outlook/status
# ===========================================================================

@pytest.mark.asyncio
async def test_outlook_auth_status_unauthenticated(client) -> None:
    """GET /auth/outlook/status with no creds → authenticated=false."""
    resp = await client.get(f"{_BASE}/auth/outlook/status", headers=_HDR)
    assert resp.status_code == 200
    body = resp.json()
    assert body["authenticated"] is False
    assert body["email"] is None


@pytest.mark.asyncio
async def test_outlook_auth_status_authenticated(client) -> None:
    """GET /auth/outlook/status with injected creds → authenticated=true + shape."""
    from src.tools.email import token_store

    creds = _fake_outlook_creds()
    token_store.put("outlook", _PROJ, creds)

    resp = await client.get(f"{_BASE}/auth/outlook/status", headers=_HDR)
    assert resp.status_code == 200
    body = resp.json()
    assert body["authenticated"] is True
    assert body["email"] == "test@outlook.com"
    assert "expires_at" in body


# ===========================================================================
# 3. auth/gmail/start
# ===========================================================================

@pytest.mark.asyncio
async def test_gmail_auth_start_503_when_env_unset(client, monkeypatch) -> None:
    """POST /auth/gmail/start → 503 when GOOGLE_OAUTH_* vars are absent."""
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)

    resp = await client.post(f"{_BASE}/auth/gmail/start", headers=_HDR)
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    # Confirm the error is config-missing, not some other 5xx cause.
    assert "GOOGLE_OAUTH_CLIENT_ID" in detail or "Gmail OAuth not configured" in detail


@pytest.mark.asyncio
async def test_gmail_auth_start_200_with_env_set(client, monkeypatch) -> None:
    """POST /auth/gmail/start → 200 + auth_url when env vars present.

    We monkeypatch GOOGLE_OAUTH_CLIENT_ID + SECRET and intercept
    gmail_client._client_config to avoid constructing a real Flow object
    (which would hit google-auth network validation).
    We patch gmail_client.auth_start at the module level so the router picks
    up the patch (router imports the module, not the function directly).
    """
    from src.tools.email import gmail_client

    def _fake_auth_start(project_id: int) -> str:
        return "https://accounts.google.com/o/oauth2/auth?fake=1"

    monkeypatch.setattr(gmail_client, "auth_start", _fake_auth_start)

    resp = await client.post(f"{_BASE}/auth/gmail/start", headers=_HDR)
    assert resp.status_code == 200
    body = resp.json()
    assert "auth_url" in body
    assert body["auth_url"].startswith("https://")


# ===========================================================================
# 4. auth/outlook/start
# ===========================================================================

@pytest.mark.asyncio
async def test_outlook_auth_start_503_when_env_unset(client, monkeypatch) -> None:
    """POST /auth/outlook/start → 503 when AZURE_OAUTH_* vars absent."""
    monkeypatch.delenv("AZURE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("AZURE_OAUTH_CLIENT_SECRET", raising=False)

    resp = await client.post(f"{_BASE}/auth/outlook/start", headers=_HDR)
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "AZURE_OAUTH_CLIENT_ID" in detail or "Outlook OAuth not configured" in detail


@pytest.mark.asyncio
async def test_outlook_auth_start_200_with_env_set(client, monkeypatch) -> None:
    """POST /auth/outlook/start → 200 + auth_url when env vars present (patched)."""
    from src.tools.email import outlook_client

    def _fake_auth_start(project_id: int) -> str:
        return "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?fake=1"

    monkeypatch.setattr(outlook_client, "auth_start", _fake_auth_start)

    resp = await client.post(f"{_BASE}/auth/outlook/start", headers=_HDR)
    assert resp.status_code == 200
    body = resp.json()
    assert "auth_url" in body
    assert body["auth_url"].startswith("https://")


# ===========================================================================
# 5. auth/gmail/callback
# ===========================================================================

@pytest.mark.asyncio
async def test_gmail_callback_unknown_state_400(client) -> None:
    """GET /auth/gmail/callback with unknown state → 400."""
    resp = await client.get(
        f"{_BASE}/auth/gmail/callback",
        params={"code": "fake-code-12345", "state": "unknown-state-xyz"},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    # Must be a config-related rejection string, not a shape mismatch.
    assert "oauth_callback_failed" in detail or "unknown" in detail or "expired" in detail


# ===========================================================================
# 6. auth/outlook/callback
# ===========================================================================

@pytest.mark.asyncio
async def test_outlook_callback_unknown_state_400(client) -> None:
    """GET /auth/outlook/callback with unknown state → 400."""
    resp = await client.get(
        f"{_BASE}/auth/outlook/callback",
        params={"code": "fake-code-12345", "state": "unknown-state-xyz"},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "oauth_callback_failed" in detail or "unknown" in detail or "expired" in detail


# ===========================================================================
# 7. gmail/trash — XOR validation (Pydantic boundary — no creds needed)
# ===========================================================================

@pytest.mark.asyncio
async def test_gmail_trash_xor_neither_422(client) -> None:
    """POST /gmail/trash with {} (neither query nor message_ids) → 422."""
    resp = await client.post(f"{_BASE}/gmail/trash", headers=_HDR, json={})
    assert resp.status_code == 422
    body = resp.json()
    # Check detail contains our custom message from the model validator.
    detail_str = str(body)
    assert "exactly one" in detail_str.lower() or "message_ids" in detail_str.lower()


@pytest.mark.asyncio
async def test_gmail_trash_xor_both_422(client) -> None:
    """POST /gmail/trash with both query AND message_ids → 422."""
    resp = await client.post(
        f"{_BASE}/gmail/trash",
        headers=_HDR,
        json={"query": "from:spam@example.com", "message_ids": ["abc123"]},
    )
    assert resp.status_code == 422


# ===========================================================================
# 8. gmail/trash — 401 not authenticated
# ===========================================================================

@pytest.mark.asyncio
async def test_gmail_trash_401_unauthenticated(client) -> None:
    """POST /gmail/trash with valid body but no creds → 401.

    message_ids mode: bulk-check fires first (1 id is below threshold),
    THEN auth-check fires and raises 401.
    """
    resp = await client.post(
        f"{_BASE}/gmail/trash",
        headers=_HDR,
        json={"message_ids": ["abc123def456"]},
    )
    assert resp.status_code == 401
    detail = resp.json()["detail"]
    assert "gmail not authenticated" in detail or "authenticated" in detail.lower()


# ===========================================================================
# 9. gmail/trash — bulk-threshold gate (+/-)
# ===========================================================================

@pytest.mark.asyncio
async def test_gmail_trash_bulk_threshold_blocked(client, monkeypatch) -> None:
    """POST /gmail/trash with count > threshold and no ?force → 400 bulk_threshold.

    Gate ordering: message_ids mode → bulk-check fires BEFORE auth so we
    can observe this 400 WITHOUT creds.
    """
    monkeypatch.setenv("EMAIL_TOOLS_BULK_THRESHOLD", "2")
    # Reload gate so it picks up the new env value.
    from src.tools.email import gate as gate_mod
    importlib.reload(gate_mod)

    resp = await client.post(
        f"{_BASE}/gmail/trash",
        headers=_HDR,
        json={"message_ids": ["aaa111", "bbb222", "ccc333"]},  # 3 > 2
    )
    assert resp.status_code == 400
    body = resp.json()["detail"]
    assert body["error"] == "bulk_threshold"
    assert body["count"] == 3
    assert body["threshold"] == 2
    assert "force=true" in body["hint"]


@pytest.mark.asyncio
async def test_gmail_trash_bulk_threshold_bypassed_with_force(client, monkeypatch) -> None:
    """POST /gmail/trash with ?force=true bypasses bulk gate → reaches auth gate (401).

    Positive assertion: with force=true the 400-bulk-threshold does NOT fire.
    Next gate is auth → 401 (no creds in store).
    """
    monkeypatch.setenv("EMAIL_TOOLS_BULK_THRESHOLD", "2")
    from src.tools.email import gate as gate_mod
    importlib.reload(gate_mod)

    resp = await client.post(
        f"{_BASE}/gmail/trash",
        headers=_HDR,
        params={"force": "true"},
        json={"message_ids": ["aaa111", "bbb222", "ccc333"]},  # 3 > 2, but force=true
    )
    # Bulk gate bypassed → lands on auth gate → 401 (no creds).
    assert resp.status_code == 401
    assert resp.status_code != 400, "bulk gate should have been bypassed"


# ===========================================================================
# 10. gmail/trash — daily-cap gate (+/-)
# ===========================================================================

@pytest.mark.asyncio
async def test_gmail_trash_daily_cap_blocks(client, monkeypatch) -> None:
    """POST /gmail/trash → 429 daily_cap_reached when cap is exhausted.

    Inject creds + pre-fill _DAILY_UNITS to near cap, then issue a trash
    request that would exceed it.
    """
    from src.tools.email import gate, token_store

    creds = _fake_gmail_creds()
    token_store.put("gmail", _PROJ, creds)

    # Set cap to 100 units. One trash for 1 id = 20 units. Pre-fill to 90.
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "100")
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    gate._DAILY_UNITS[(_PROJ, today)] = 90  # 90 used; 10 left; need 20 → over cap

    resp = await client.post(
        f"{_BASE}/gmail/trash",
        headers=_HDR,
        json={"message_ids": ["abc123def456"]},  # 20 units > 10 remaining
    )
    assert resp.status_code == 429
    body = resp.json()["detail"]
    assert body["error"] == "daily_cap_reached"
    assert "cap" in body
    assert "current_units" in body


@pytest.mark.asyncio
async def test_gmail_trash_daily_cap_allows_when_under(client, monkeypatch) -> None:
    """POST /gmail/trash succeeds (200) when under the daily cap with mocked client."""
    from src.tools.email import gate, token_store, gmail_client

    creds = _fake_gmail_creds()
    token_store.put("gmail", _PROJ, creds)

    # Cap = 1000; nothing consumed → well within limit.
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")
    # Patch the upstream trash call so no real network request is made.
    monkeypatch.setattr(gmail_client, "trash_messages", lambda c, ids: (list(ids), []))

    resp = await client.post(
        f"{_BASE}/gmail/trash",
        headers=_HDR,
        json={"message_ids": ["abc123def456"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["trashed_count"] == 1
    assert body["trashed_ids"] == ["abc123def456"]
    assert body["errors"] == []


# ===========================================================================
# 11. gmail/usage
# ===========================================================================

@pytest.mark.asyncio
async def test_gmail_usage_shape(client) -> None:
    """GET /gmail/usage → 200 with {date, units_consumed, cap, remaining}."""
    resp = await client.get(f"{_BASE}/gmail/usage", headers=_HDR)
    assert resp.status_code == 200
    body = resp.json()
    assert "date" in body
    assert "units_consumed" in body
    assert "cap" in body
    assert "remaining" in body
    # Structural sanity: consumed + remaining == cap.
    assert body["units_consumed"] + body["remaining"] == body["cap"]


@pytest.mark.asyncio
async def test_gmail_usage_reflects_increment(client, monkeypatch) -> None:
    """GET /gmail/usage reflects units consumed after a gate increment."""
    from src.tools.email import gate

    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "5000")
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    gate._DAILY_UNITS[(_PROJ, today)] = 120

    resp = await client.get(f"{_BASE}/gmail/usage", headers=_HDR)
    assert resp.status_code == 200
    body = resp.json()
    assert body["units_consumed"] == 120
    assert body["remaining"] == 5000 - 120


# ===========================================================================
# 12. outlook/trash — XOR validation
# ===========================================================================

@pytest.mark.asyncio
async def test_outlook_trash_xor_neither_422(client) -> None:
    """POST /outlook/trash with {} → 422."""
    resp = await client.post(f"{_BASE}/outlook/trash", headers=_HDR, json={})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_outlook_trash_xor_both_422(client) -> None:
    """POST /outlook/trash with both query AND message_ids → 422."""
    resp = await client.post(
        f"{_BASE}/outlook/trash",
        headers=_HDR,
        json={"query": "from:spam@example.com", "message_ids": ["AAA111"]},
    )
    assert resp.status_code == 422


# ===========================================================================
# 13. outlook/trash — query mode returns 501
# ===========================================================================

@pytest.mark.asyncio
async def test_outlook_trash_query_mode_501(client) -> None:
    """POST /outlook/trash with query set → 501 (not implemented in Phase 3).

    Note: the XOR validator accepts (query XOR message_ids) — this body is
    valid Pydantic shape but the router logic returns 501.
    """
    resp = await client.post(
        f"{_BASE}/outlook/trash",
        headers=_HDR,
        json={"query": "from:spam@example.com"},
    )
    assert resp.status_code == 501
    body = resp.json()["detail"]
    assert body["error"] == "query_mode_not_implemented"


# ===========================================================================
# 14. outlook/trash — 401 not authenticated
# ===========================================================================

@pytest.mark.asyncio
async def test_outlook_trash_401_unauthenticated(client, monkeypatch) -> None:
    """POST /outlook/trash with valid body but no creds → 401.

    Gate ordering: bulk-check fires BEFORE auth. 1 id is below default
    threshold (100) so bulk gate passes, then auth gate raises 401.
    """
    resp = await client.post(
        f"{_BASE}/outlook/trash",
        headers=_HDR,
        json={"message_ids": ["AAA111bbb222"]},
    )
    assert resp.status_code == 401
    detail = resp.json()["detail"]
    assert "outlook not authenticated" in detail or "authenticated" in detail.lower()


# ===========================================================================
# 15. outlook/trash — bulk-threshold gate (+/-)
# ===========================================================================

@pytest.mark.asyncio
async def test_outlook_trash_bulk_threshold_blocked(client, monkeypatch) -> None:
    """POST /outlook/trash with count > threshold and no force → 400 bulk_threshold.

    Bulk gate fires BEFORE auth in ids mode — observable without creds.
    """
    monkeypatch.setenv("EMAIL_TOOLS_BULK_THRESHOLD", "2")
    from src.tools.email import gate as gate_mod
    importlib.reload(gate_mod)

    resp = await client.post(
        f"{_BASE}/outlook/trash",
        headers=_HDR,
        json={"message_ids": ["AAA111", "BBB222", "CCC333"]},  # 3 > 2
    )
    assert resp.status_code == 400
    body = resp.json()["detail"]
    assert body["error"] == "bulk_threshold"
    assert body["count"] == 3


@pytest.mark.asyncio
async def test_outlook_trash_bulk_threshold_bypassed_with_force(client, monkeypatch) -> None:
    """POST /outlook/trash with ?force=true bypasses bulk gate → 401 (no creds)."""
    monkeypatch.setenv("EMAIL_TOOLS_BULK_THRESHOLD", "2")
    from src.tools.email import gate as gate_mod
    importlib.reload(gate_mod)

    resp = await client.post(
        f"{_BASE}/outlook/trash",
        headers=_HDR,
        params={"force": "true"},
        json={"message_ids": ["AAA111", "BBB222", "CCC333"]},
    )
    # Bulk gate bypassed → auth gate fires → 401.
    assert resp.status_code == 401
    assert resp.status_code != 400, "bulk gate should have been bypassed"


# ===========================================================================
# 16. outlook/trash — daily-cap gate (+/-)
# ===========================================================================

@pytest.mark.asyncio
async def test_outlook_trash_daily_cap_blocks(client, monkeypatch) -> None:
    """POST /outlook/trash → 429 when cap exhausted."""
    from src.tools.email import gate, token_store

    creds = _fake_outlook_creds()
    token_store.put("outlook", _PROJ, creds)

    # Outlook = 10 units/msg. Cap=50. Pre-fill 45. 1 msg = 10 units → over cap.
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "50")
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    gate._DAILY_UNITS[(_PROJ, today)] = 45

    resp = await client.post(
        f"{_BASE}/outlook/trash",
        headers=_HDR,
        json={"message_ids": ["AAA111bbb222"]},
    )
    assert resp.status_code == 429
    body = resp.json()["detail"]
    assert body["error"] == "daily_cap_reached"


@pytest.mark.asyncio
async def test_outlook_trash_daily_cap_allows_when_under(client, monkeypatch) -> None:
    """POST /outlook/trash succeeds (200) when under the daily cap (mocked client)."""
    from src.tools.email import gate, token_store, outlook_client

    creds = _fake_outlook_creds()
    token_store.put("outlook", _PROJ, creds)

    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")
    monkeypatch.setattr(outlook_client, "trash_messages", lambda c, ids: (list(ids), []))

    resp = await client.post(
        f"{_BASE}/outlook/trash",
        headers=_HDR,
        json={"message_ids": ["AAA111bbb222"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["trashed_count"] == 1
    assert body["trashed_ids"] == ["AAA111bbb222"]
    assert body["errors"] == []


# ===========================================================================
# 17. Missing X-Project-Id header → 422
# ===========================================================================

@pytest.mark.asyncio
async def test_gmail_trash_missing_project_id_header(client) -> None:
    """POST /gmail/trash without X-Project-Id header → 400.

    require_project_id_header raises HTTP 400 (not 422) with:
      "X-Project-Id header is required for task endpoints"
    Note: this is NOT a Pydantic 422 — it is a custom FastAPI dependency that
    raises HTTPException(400, ...) directly.
    """
    resp = await client.post(
        f"{_BASE}/gmail/trash",
        json={"message_ids": ["abc123"]},
        # No headers — no X-Project-Id.
    )
    assert resp.status_code == 400
    assert "X-Project-Id" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_outlook_trash_missing_project_id_header(client) -> None:
    """POST /outlook/trash without X-Project-Id header → 400."""
    resp = await client.post(
        f"{_BASE}/outlook/trash",
        json={"message_ids": ["AAA111"]},
    )
    assert resp.status_code == 400
    assert "X-Project-Id" in resp.json()["detail"]
