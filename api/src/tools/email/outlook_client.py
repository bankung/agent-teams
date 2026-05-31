"""Outlook OAuth + trash client (Kanban #1608, Phase 3 Karpathy cut).

Mirrors `gmail_client.py` 1:1 but speaks Microsoft Graph instead of Gmail API.

Scope: `Mail.ReadWrite` — minimal sensitive scope covering the move-to-Deleted-
Items operation we ship in this phase. Future label/draft/send actions stay
under this same scope (per Graph docs 2026-05-27 research).

OAuth dance (test-mode, operator-only):
  1. `POST /api/tools/email/auth/outlook/start` → returns auth_url; user opens
     in browser, signs in with personal MSA (outlook.com / hotmail.com), consents.
  2. Microsoft redirects to `AZURE_OAUTH_REDIRECT_URI` with ?code=&state=
     → `GET /api/tools/email/auth/outlook/callback` exchanges code for tokens
     via msal and stores via `token_store.put("outlook", project_id, creds)`.
  3. `GET /api/tools/email/auth/outlook/status` confirms.

Unit-cost convention (Lead-frozen, see spawn brief):
  Microsoft Graph publishes NO per-operation point cost (Gmail-style), so we
  pick conservative unit equivalents to share the SAME daily cap
  (EMAIL_TOOLS_DAILY_UNITS_CAP=5000) across both providers:
    - move-to-Deleted-Items: 10 units / message (half of Gmail's 20; Graph
      throttling is gentler in practice but we still want headroom under cap).
    - auth/start, auth/status: 0 units (no upstream call).
    - auth/callback: 1 unit (one token-exchange call to AAD).

Karpathy cuts (matches Gmail):
  - In-memory `_PENDING_FLOWS` keyed by `state`. 10-min TTL prune on each start.
  - Callback returns JSON, not 302 — no front-end this phase.
  - No `msgraph-sdk` dep — `msal` for tokens + `httpx` (already installed) for
    REST. Sticks to the smallest viable surface.
  - Per-mailbox throttling limits are NOT publicly documented; we honor the
    Graph `Retry-After` header on every 429 + fall back to exponential backoff
    capped at 60s.
"""

from __future__ import annotations

import datetime
import logging
import os
import secrets
import time
from typing import Any

import httpx
import msal

logger = logging.getLogger(__name__)

# Mail.ReadWrite — the minimal Graph scope that covers reading folders and
# moving messages to Deleted Items. Form expected by msal: bare permission
# name; the library prefixes the resource URL automatically for Graph.
SCOPES = ["Mail.ReadWrite"]

# Microsoft Graph base URL for the v1.0 REST surface.
_GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Pending OAuth flows — state -> (msal_app, project_id, created_at_utc).
# 10-min TTL prune on each new start.
_PENDING_FLOWS: dict[str, tuple[msal.ConfidentialClientApplication, int, datetime.datetime]] = {}
_PENDING_TTL = datetime.timedelta(minutes=10)

# Retry-After cap — protect against an upstream telling us to wait an hour.
_RETRY_AFTER_CAP_SECONDS = 60
_MAX_RETRY_ATTEMPTS = 3


def _prune_pending() -> None:
    """Drop pending OAuth states older than _PENDING_TTL.

    Called on each `auth_start` to bound memory. Cheap O(n) walk; n stays in
    single digits in operator-only usage.
    """
    now = datetime.datetime.now(datetime.UTC)
    expired = [s for s, (_, _, ts) in _PENDING_FLOWS.items() if now - ts > _PENDING_TTL]
    for s in expired:
        _PENDING_FLOWS.pop(s, None)


def _client_config() -> tuple[str, str, str, str]:
    """Read AZURE_OAUTH_* env vars. Returns (client_id, client_secret, tenant, redirect_uri).

    Raises RuntimeError if required vars missing. `tenant` defaults to "consumers"
    (personal MSA accounts only — the typical operator setup for outlook.com /
    hotmail.com). Use "common" for both personal + work/school.
    """
    client_id = os.environ.get("AZURE_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.environ.get("AZURE_OAUTH_CLIENT_SECRET", "").strip()
    tenant = os.environ.get("AZURE_OAUTH_TENANT", "consumers").strip() or "consumers"
    redirect_uri = os.environ.get(
        "AZURE_OAUTH_REDIRECT_URI",
        "http://localhost:8456/api/tools/email/auth/outlook/callback",
    ).strip()
    if not client_id or not client_secret:
        raise RuntimeError(
            "Outlook OAuth not configured: set AZURE_OAUTH_CLIENT_ID and "
            "AZURE_OAUTH_CLIENT_SECRET in the repo-root .env (dockerized stack) "
            "or api/.env (local uvicorn) — see .env.example Kanban #1608 block for setup steps."
        )
    return client_id, client_secret, tenant, redirect_uri


def _build_msal_app() -> tuple[msal.ConfidentialClientApplication, str]:
    """Construct a fresh ConfidentialClientApplication. Returns (app, redirect_uri).

    Each pending OAuth flow gets its own app instance so the in-memory token
    cache is isolated per-flow until the callback persists creds to token_store.
    """
    client_id, client_secret, tenant, redirect_uri = _client_config()
    authority = f"https://login.microsoftonline.com/{tenant}"
    app = msal.ConfidentialClientApplication(
        client_id=client_id,
        client_credential=client_secret,
        authority=authority,
    )
    return app, redirect_uri


def auth_start(project_id: int) -> str:
    """Begin OAuth flow for `project_id`. Returns the auth_url to open.

    Side effect: registers state -> (msal_app, project_id, created_at) in
    `_PENDING_FLOWS` so the callback can recover the project binding + token
    cache.
    """
    _prune_pending()
    app, redirect_uri = _build_msal_app()
    state = secrets.token_urlsafe(32)
    auth_url = app.get_authorization_request_url(
        scopes=SCOPES,
        state=state,
        redirect_uri=redirect_uri,
        # prompt=consent forces re-consent so we reliably receive a refresh
        # token (msal/AAD behaves like Google here).
        prompt="consent",
    )
    _PENDING_FLOWS[state] = (app, project_id, datetime.datetime.now(datetime.UTC))
    return auth_url


def auth_callback(code: str, state: str) -> tuple[int, dict[str, Any]]:
    """Exchange `code` for a token result. Returns (project_id, token_result).

    `token_result` is the dict returned by msal's `acquire_token_by_authorization_code`
    — includes access_token, refresh_token (if granted), id_token_claims (with
    email/upn), expires_in. We store this raw dict in token_store; refresh
    happens via a fresh msal app on demand inside `_acquire_silent`.

    Raises ValueError on unknown state or upstream rejection.
    """
    entry = _PENDING_FLOWS.pop(state, None)
    if entry is None:
        raise ValueError(
            "unknown or expired OAuth state; restart the flow at "
            "POST /api/tools/email/auth/outlook/start"
        )
    app, project_id, _created_at = entry
    _, _, _, redirect_uri = _client_config()
    result = app.acquire_token_by_authorization_code(
        code=code,
        scopes=SCOPES,
        redirect_uri=redirect_uri,
    )
    if "access_token" not in result:
        # msal returns {error, error_description, ...} on failure.
        err = result.get("error", "unknown")
        # Don't echo full description (may contain hints about the secret).
        logger.warning("outlook oauth callback failed: %s", err)
        raise ValueError(f"oauth_callback_failed: {err}")
    # Stamp issuance time so we can compute expires_at deterministically.
    # msal returns expires_in (seconds); we convert to absolute epoch for the
    # status summary.
    result["_acquired_at"] = time.time()
    return project_id, result


def creds_summary(creds: object) -> dict:
    """Project the stored token dict into a status dict for token_store.

    Returns {email, expires_at}. Best-effort — pulls email from id_token_claims
    (preferred) or `account` claims; expires_at computed from `_acquired_at` +
    `expires_in`.
    """
    if not isinstance(creds, dict):
        return {"email": None, "expires_at": None}
    # Email candidates in order of preference: id_token_claims.preferred_username,
    # id_token_claims.email, account.username.
    email = None
    id_claims = creds.get("id_token_claims") or {}
    if isinstance(id_claims, dict):
        email = id_claims.get("preferred_username") or id_claims.get("email")
    if email is None:
        acct = creds.get("account") or {}
        if isinstance(acct, dict):
            email = acct.get("username")
    # expires_at — best effort.
    expires_at = None
    acquired = creds.get("_acquired_at")
    expires_in = creds.get("expires_in")
    if acquired is not None and expires_in is not None:
        try:
            ts = datetime.datetime.fromtimestamp(float(acquired) + float(expires_in), datetime.UTC).replace(tzinfo=None)
            expires_at = ts.isoformat() + "Z"
        except (TypeError, ValueError):
            expires_at = None
    return {"email": email, "expires_at": expires_at}


def _acquire_silent(creds: dict[str, Any]) -> str:
    """Return a fresh access_token, refreshing via msal if needed.

    The stored token dict may include a refresh_token (granted because we asked
    for prompt=consent at auth_start). If access_token still valid, return it
    as-is; otherwise call msal `acquire_token_by_refresh_token` and mutate the
    creds dict so subsequent calls see the new token.
    """
    acquired = creds.get("_acquired_at", 0)
    expires_in = creds.get("expires_in", 0)
    # Refresh 60s before expiry to avoid mid-call expiration.
    if time.time() < (float(acquired) + float(expires_in) - 60):
        token = creds.get("access_token")
        if isinstance(token, str) and token:
            return token
    # Need a refresh.
    refresh_token = creds.get("refresh_token")
    if not refresh_token:
        # No refresh token — caller will hit a 401 on the next Graph call; let
        # them re-OAuth. Returning the stale token surfaces the failure clearly.
        return creds.get("access_token", "")
    app, _ = _build_msal_app()
    result = app.acquire_token_by_refresh_token(refresh_token, scopes=SCOPES)
    if "access_token" not in result:
        logger.warning("outlook token refresh failed: %s", result.get("error", "unknown"))
        return creds.get("access_token", "")
    # Merge new token + new expiry into existing creds dict.
    creds["access_token"] = result["access_token"]
    creds["expires_in"] = result.get("expires_in", expires_in)
    creds["_acquired_at"] = time.time()
    # AAD may rotate the refresh token — preserve the rotation.
    if result.get("refresh_token"):
        creds["refresh_token"] = result["refresh_token"]
    return creds["access_token"]


def _graph_request_with_retry(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict[str, Any] | None = None,
) -> httpx.Response:
    """Issue a Graph REST call with 429-aware retry.

    Behavior:
      - On HTTP 429: read `Retry-After` header (seconds); if absent, fall back
        to exponential backoff (1s, 2s, 4s).
      - Sleep is capped at `_RETRY_AFTER_CAP_SECONDS` (60s) regardless of source.
      - Retry up to `_MAX_RETRY_ATTEMPTS` (3) times. After that, surface the
        last 429 response to the caller — the route handler audits + raises 502.
      - Non-429 responses (including 5xx) are returned directly; let the caller
        decide. Graph's 5xx are infrequent and usually transient but we don't
        auto-retry them here (Karpathy cut — add later if observed).
    """
    last_response: httpx.Response | None = None
    for attempt in range(_MAX_RETRY_ATTEMPTS):
        with httpx.Client(timeout=30.0) as client:
            resp = client.request(method, url, headers=headers, json=json_body)
        if resp.status_code != 429:
            return resp
        last_response = resp
        # Decide sleep duration.
        retry_after_raw = resp.headers.get("Retry-After")
        sleep_s: float
        if retry_after_raw is not None:
            try:
                sleep_s = float(retry_after_raw)
            except ValueError:
                # Some servers emit HTTP-date; we don't parse those — fall through
                # to backoff. (Graph emits seconds in practice.)
                sleep_s = 2.0 ** attempt
        else:
            sleep_s = 2.0 ** attempt  # 1, 2, 4
        sleep_s = min(sleep_s, float(_RETRY_AFTER_CAP_SECONDS))
        logger.info(
            "graph 429 (attempt %d/%d), sleeping %.1fs",
            attempt + 1, _MAX_RETRY_ATTEMPTS, sleep_s,
        )
        time.sleep(sleep_s)
    # All retries exhausted — return last 429 response so caller can surface it.
    if last_response is None:
        raise RuntimeError("no response after retries")
    return last_response


def trash_messages(creds: dict[str, Any], message_ids: list[str]) -> tuple[list[str], list[dict]]:
    """Move each message to Deleted Items. Returns (trashed_ids, errors).

    Microsoft Graph distinguishes "move to Deleted Items" (soft delete, recoverable)
    from "permanent delete" (DELETE on the message). We do the SOFT version
    here — mirrors Gmail's `trash` (which also keeps messages recoverable).

    Per-message failures do NOT abort the loop — caller decides whether to
    surface partial success.

    Errors entry shape: {message_id, error_class, status}. `error_class` is
    'HTTPError' (Graph returned non-2xx) or the exception class name (network /
    parsing fail).
    """
    access_token = _acquire_silent(creds)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    trashed: list[str] = []
    errors: list[dict] = []
    for mid in message_ids:
        url = f"{_GRAPH_BASE}/me/messages/{mid}/move"
        # destinationId="deletedItems" is the well-known folder id Graph
        # recognises for the Deleted Items folder.
        body = {"destinationId": "deletedItems"}
        try:
            resp = _graph_request_with_retry("POST", url, headers=headers, json_body=body)
            if 200 <= resp.status_code < 300:
                trashed.append(mid)
            else:
                errors.append(
                    {
                        "message_id": mid,
                        "error_class": "HTTPError",
                        "status": resp.status_code,
                    }
                )
        except Exception as e:
            errors.append(
                {
                    "message_id": mid,
                    "error_class": type(e).__name__,
                    "status": None,
                }
            )
    return trashed, errors
