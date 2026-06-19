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
import html
import logging
import os
import re
import secrets
import time
from typing import Any

import httpx
import msal

logger = logging.getLogger(__name__)

# FIX-2 (#1939): defense-in-depth id validation inside the client.
# Same charset as Gmail _ID_RE plus the 512-char Outlook length bound.
# Defined independently here to avoid a router/schema import cycle.
_ID_RE = re.compile(r"^[A-Za-z0-9_\-=+]+$")

# Mail.ReadWrite — the minimal Graph scope that covers reading folders and
# moving messages to Deleted Items. Form expected by msal: bare permission
# name; the library prefixes the resource URL automatically for Graph.
#
# Kanban #1963: Calendars.ReadWrite ADDED for the Calendar tools on the PROPER
# `/api/tools/calendar` base — covers list (calendarView), getSchedule (freebusy),
# POST /me/events (create), and the accept/decline/tentativelyAccept RSVP verbs.
# RE-CONSENT PREREQUISITE: a token granted under the old Mail.ReadWrite-only list
# does NOT carry Calendars.ReadWrite — the operator must re-run the OAuth dance
# (POST /api/tools/email/auth/outlook/start, which uses prompt=consent) to grant
# it. Until re-consent, a Graph calendar call returns an insufficient-scope error
# which outlook_calendar_client maps to CalendarScopeError → HTTP 412. LIVE
# create/respond verification is OUT OF SCOPE for #1963 (build + mocked tests).
SCOPES = ["Mail.ReadWrite", "Calendars.ReadWrite"]

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
    params: dict[str, Any] | None = None,
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
      - `params`: if provided, passed to httpx.Client.request so httpx handles
        percent-encoding. Used for the FIRST page only; @odata.nextLink pages
        pass a fully-formed URL with no extra params.
    """
    last_response: httpx.Response | None = None
    for attempt in range(_MAX_RETRY_ATTEMPTS):
        with httpx.Client(timeout=30.0) as client:
            resp = client.request(method, url, headers=headers, json=json_body, params=params)
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


def list_message_ids(creds: dict[str, Any], query: str, max_results: int = 500) -> list[str]:
    """Run Graph GET /me/messages with $search and return up to max_results ids.

    Uses KQL via the `$search` query parameter. Graph REQUIRES the
    `ConsistencyLevel: eventual` header for $search — omitting it yields 400.

    Caller is responsible for cap enforcement BEFORE invoking. Cost: one Graph
    list call, regardless of page count (we charge once per invocation).

    Returns a flat list of message ids.
    """
    access_token = _acquire_silent(creds)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        # Required by Graph when using $search — without this header Graph
        # returns HTTP 400 "request requires ConsistencyLevel header".
        "ConsistencyLevel": "eventual",
    }
    ids: list[str] = []
    # Graph accepts $top up to 1000 per page; we use min(max_results, 1000)
    # as the page size and follow @odata.nextLink to paginate.
    page_size = min(max_results, 1000)
    # KQL phrase-escape: internal double-quotes are doubled per KQL spec.
    # httpx `params=` handles percent-encoding of the full value so spaces,
    # `&`, `#`, and other special chars don't break the URL structure.
    escaped_query = query.replace('"', '""')
    first_page_params: dict[str, Any] = {
        "$search": f'"{escaped_query}"',
        "$select": "id",
        "$top": page_size,
    }
    first_page_url = f"{_GRAPH_BASE}/me/messages"
    # url=None after first page means we use @odata.nextLink (already fully formed).
    next_link: str | None = None
    is_first_page = True
    while len(ids) < max_results:
        if is_first_page:
            resp = _graph_request_with_retry(
                "GET", first_page_url, headers=headers, params=first_page_params
            )
            is_first_page = False
        elif next_link:
            # SSRF guard (OWASP A10): nextLink must stay on Graph — reject any
            # tampered URL before it could redirect the Bearer token off-host.
            if not next_link.startswith("https://graph.microsoft.com/"):
                logger.error(
                    "list_message_ids: unexpected nextLink host, aborting pagination: %s",
                    next_link[:120],
                )
                raise ValueError(
                    f"nextLink host is not graph.microsoft.com — aborting: {next_link[:120]}"
                )
            # @odata.nextLink is a fully-formed URL — pass as-is, no extra params.
            resp = _graph_request_with_retry("GET", next_link, headers=headers)
        else:
            break
        resp.raise_for_status()
        data = resp.json()
        for msg in data.get("value", []) or []:
            ids.append(msg["id"])
            if len(ids) >= max_results:
                break
        next_link = data.get("@odata.nextLink")
    return ids


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
        if not _ID_RE.fullmatch(mid) or len(mid) > 512:
            raise ValueError("invalid message_id")
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


def mark_read(creds: dict[str, Any], message_ids: list[str], read: bool) -> tuple[list[str], list[dict]]:
    """Set isRead on each message. Returns (modified_ids, errors).

    read=True  -> isRead=true  (mark read).
    read=False -> isRead=false (mark unread).

    Graph has no label model; read/unread is the `isRead` boolean property —
    equivalent of Gmail's UNREAD label add/remove via modify_labels.

    Per-message failures do NOT abort the loop (mirrors `trash_messages`).
    Errors entry shape: {message_id, error_class, status}.
    """
    access_token = _acquire_silent(creds)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    modified: list[str] = []
    errors: list[dict] = []
    for mid in message_ids:
        if not _ID_RE.fullmatch(mid) or len(mid) > 512:
            raise ValueError("invalid message_id")
        url = f"{_GRAPH_BASE}/me/messages/{mid}"
        body = {"isRead": read}
        try:
            resp = _graph_request_with_retry("PATCH", url, headers=headers, json_body=body)
            if 200 <= resp.status_code < 300:
                modified.append(mid)
            else:
                errors.append(
                    {
                        "message_id": mid,
                        "error_class": "HTTPError",
                        "status": resp.status_code,
                    }
                )
        except Exception as exc:
            errors.append(
                {
                    "message_id": mid,
                    "error_class": type(exc).__name__,
                    "status": None,
                }
            )
    return modified, errors


def archive(creds: dict[str, Any], message_ids: list[str]) -> tuple[list[str], list[dict]]:
    """Move each message to the Archive folder. Returns (modified_ids, errors).

    Uses the Graph well-known folder name "archive" as the destinationId.
    Equivalent of Gmail removing the INBOX label (archiving without deleting).

    Per-message failures do NOT abort the loop (mirrors `trash_messages`).
    Errors entry shape: {message_id, error_class, status}.
    """
    access_token = _acquire_silent(creds)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    modified: list[str] = []
    errors: list[dict] = []
    for mid in message_ids:
        if not _ID_RE.fullmatch(mid) or len(mid) > 512:
            raise ValueError("invalid message_id")
        url = f"{_GRAPH_BASE}/me/messages/{mid}/move"
        body = {"destinationId": "archive"}
        try:
            resp = _graph_request_with_retry("POST", url, headers=headers, json_body=body)
            if 200 <= resp.status_code < 300:
                modified.append(mid)
            else:
                errors.append(
                    {
                        "message_id": mid,
                        "error_class": "HTTPError",
                        "status": resp.status_code,
                    }
                )
        except Exception as exc:
            errors.append(
                {
                    "message_id": mid,
                    "error_class": type(exc).__name__,
                    "status": None,
                }
            )
    return modified, errors


# ---------------------------------------------------------------------------
# Kanban #1939 — READ endpoints (search + get)
# ---------------------------------------------------------------------------


def search_messages(
    creds: dict[str, Any], query: str, max_results: int = 10
) -> list[dict]:
    """Search the mailbox using Graph $search and return metadata for up to max_results messages.

    Extends the `list_message_ids` pattern: requests $select=id,conversationId,
    from,subject,receivedDateTime,bodyPreview so the FIRST call already carries
    all metadata (no second per-message round-trip unlike Gmail's metadata mode).

    Returns a list of:
      {id, thread_id, from, subject, date, snippet}

    ConsistencyLevel: eventual is REQUIRED by Graph for $search — omitting it
    yields 400. The nextLink SSRF guard from list_message_ids is applied here too.

    PRIVACY: bodyPreview is a short system-generated preview (not the full body).
    It is surfaced in the response only, never written to any log or audit trail.

    Caller is responsible for cap enforcement BEFORE invoking.
    """
    # FIX-6 (#1939): schema caps max_results at 50; $top already covers it.
    # The while/nextLink pagination loop never iterated a second time (50 < 1000).
    # Replace with a single Graph request — simpler, no nextLink SSRF surface here.
    access_token = _acquire_silent(creds)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "ConsistencyLevel": "eventual",
    }
    # KQL phrase-escape: internal double-quotes doubled per KQL spec.
    escaped_query = query.replace('"', '""')
    params: dict[str, Any] = {
        "$search": f'"{escaped_query}"',
        "$select": "id,conversationId,from,subject,receivedDateTime,bodyPreview,isRead,parentFolderId",
        "$top": max_results,
    }
    url = f"{_GRAPH_BASE}/me/messages"
    resp = _graph_request_with_retry("GET", url, headers=headers, params=params)
    resp.raise_for_status()
    data = resp.json()

    results: list[dict] = []
    for msg in (data.get("value", []) or [])[:max_results]:
        # Graph `from` field shape: {"emailAddress": {"name": ..., "address": ...}}
        from_obj = msg.get("from") or {}
        from_addr = (from_obj.get("emailAddress") or {})
        from_str = from_addr.get("address") or from_addr.get("name")
        results.append(
            {
                "id": msg.get("id"),
                "thread_id": msg.get("conversationId"),
                "from": from_str,
                "subject": msg.get("subject"),
                "date": msg.get("receivedDateTime"),
                "snippet": msg.get("bodyPreview"),
                "is_read": msg.get("isRead", False),
                "parent_folder_id": msg.get("parentFolderId"),
            }
        )
    return results


def get_message(creds: dict[str, Any], message_id: str) -> dict:
    """Fetch a single Outlook message and return its content.

    Uses Graph GET /me/messages/{id} with $select for relevant fields plus the
    `Prefer: outlook.body-content-type="text"` header so Graph returns the body
    as plain text (not HTML). Falls back to stripping HTML tags if the response
    comes back as HTML despite the Prefer header.

    Returns:
      {id, thread_id, from, to, subject, date, body_text}

    PRIVACY: body_text MUST NOT be logged, written to any audit trail, or echoed
    in error responses. Error paths use only type(exc).__name__.

    Caller is responsible for cap enforcement BEFORE invoking.
    """
    # FIX-2 (#1939): defense-in-depth — validate id before interpolating into URL.
    # Outlook ids are up to 512 chars; same charset as Gmail.
    if not (1 <= len(message_id) <= 512) or not _ID_RE.fullmatch(message_id):
        raise ValueError("invalid message_id")

    access_token = _acquire_silent(creds)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        # Ask Graph to return the body as plain text; some accounts still return
        # HTML depending on the message — the caller gets whatever is available.
        "Prefer": 'outlook.body-content-type="text"',
    }
    params: dict[str, Any] = {
        "$select": "id,conversationId,from,toRecipients,subject,receivedDateTime,body,isRead,parentFolderId",
    }
    url = f"{_GRAPH_BASE}/me/messages/{message_id}"
    resp = _graph_request_with_retry("GET", url, headers=headers, params=params)
    resp.raise_for_status()
    data = resp.json()

    # from field: {"emailAddress": {"address": ..., "name": ...}}
    from_obj = data.get("from") or {}
    from_addr_obj = from_obj.get("emailAddress") or {}
    from_str = from_addr_obj.get("address") or from_addr_obj.get("name")

    # toRecipients: list of {"emailAddress": {"address": ..., "name": ...}}
    to_parts = []
    for rec in data.get("toRecipients", []) or []:
        addr_obj = (rec.get("emailAddress") or {})
        addr = addr_obj.get("address") or addr_obj.get("name")
        if addr:
            to_parts.append(addr)
    to_str = ", ".join(to_parts) if to_parts else None

    body_obj = data.get("body") or {}
    body_text = body_obj.get("content", "") or ""
    # If Graph returned HTML despite the Prefer header, strip tags minimally.
    if body_obj.get("contentType", "").lower() == "html" and "<" in body_text:
        body_text = _strip_html(body_text)

    return {
        "id": data.get("id"),
        "thread_id": data.get("conversationId"),
        "from": from_str,
        "to": to_str,
        "subject": data.get("subject"),
        "date": data.get("receivedDateTime"),
        "body_text": body_text,
        "is_read": data.get("isRead", False),
        "parent_folder_id": data.get("parentFolderId"),
    }


def _strip_html(raw_html: str) -> str:
    """Minimal HTML tag stripper for fallback body-content-type conversion.

    Removes <...> tags then decodes HTML entities with stdlib html.unescape
    (covers &quot;, &apos;, numeric entities, etc.). Not a full sanitiser —
    just enough to present readable text when Graph ignores the Prefer header.

    FIX-3 (#1939): cap input to 500_000 chars to guard against pathologically
    large bodies; use html.unescape instead of manual entity replacement.
    PRIVACY: return value must never be logged.
    """
    # FIX-3 (#1939): cap input size before processing.
    if len(raw_html) > 500_000:
        raw_html = raw_html[:500_000]
    text = re.sub(r"<[^>]+>", "", raw_html)
    text = html.unescape(text)
    return text


def save_draft(creds: dict[str, Any], *, to: str, subject: str, body: str) -> dict:
    """Create a draft message (no send) via Graph POST /me/messages.

    Returns {"draft_id": <id>, "message_id": <id>}. The Graph message id
    serves as both draft_id and message_id (unlike Gmail which has a separate
    Draft envelope id). Equivalent of Gmail's save_draft.

    The created message is in the Drafts folder and will NOT be sent until
    the operator explicitly moves it through the send flow (a higher-tier action).
    """
    access_token = _acquire_silent(creds)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "subject": subject,
        "body": {"contentType": "Text", "content": body},
        "toRecipients": [{"emailAddress": {"address": to}}],
    }
    url = f"{_GRAPH_BASE}/me/messages"
    resp = _graph_request_with_retry("POST", url, headers=headers, json_body=payload)
    resp.raise_for_status()
    data = resp.json()
    msg_id = data.get("id")
    return {"draft_id": msg_id, "message_id": msg_id}


# ---------------------------------------------------------------------------
# Kanban #2100 — Tier-3 SEND functions (reply / forward / send)
# ---------------------------------------------------------------------------
#
# Graph actions:
#   reply   -> POST /me/messages/{id}/reply    {comment}            -> 202 (no body)
#   forward -> POST /me/messages/{id}/forward  {comment, toRecipients} -> 202
#   send    -> POST /me/sendMail               {message, saveToSentItems} -> 202
# All three return 202 Accepted with an EMPTY body (the message is queued, no id
# returned), so these functions return {message_id: None, thread_id: None}. The
# router gates them behind the operator-proof tier gate; caller pays cap first.


def _to_recipients(line: str) -> list[dict]:
    """Split a comma-separated recipient line into Graph toRecipients objects."""
    return [
        {"emailAddress": {"address": addr.strip()}}
        for addr in line.split(",")
        if addr.strip()
    ]


def _post_send_action(creds: dict[str, Any], path: str, payload: dict) -> dict:
    """POST a Graph send-class action (reply/forward/sendMail) and assert 202.

    Returns {message_id: None, thread_id: None} — Graph's send actions return
    202 with no body. raise_for_status surfaces non-2xx to the router (502).
    """
    access_token = _acquire_silent(creds)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    url = f"{_GRAPH_BASE}{path}"
    resp = _graph_request_with_retry("POST", url, headers=headers, json_body=payload)
    resp.raise_for_status()
    return {"message_id": None, "thread_id": None}


def send_reply(creds: dict[str, Any], *, message_id: str, body: str) -> dict:
    """Reply to `message_id` in-conversation via Graph /reply. Returns {message_id, thread_id}.

    Graph keeps the conversation and adds our `comment` as the reply body.
    """
    return _post_send_action(
        creds, f"/me/messages/{message_id}/reply", {"comment": body}
    )


def send_forward(creds: dict[str, Any], *, message_id: str, to: str, body: str = "") -> dict:
    """Forward `message_id` to `to` via Graph /forward. Returns {message_id, thread_id}."""
    return _post_send_action(
        creds,
        f"/me/messages/{message_id}/forward",
        {"comment": body, "toRecipients": _to_recipients(to)},
    )


def send_message(
    creds: dict[str, Any],
    *,
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    bcc: str | None = None,
) -> dict:
    """Compose + send a NEW message via Graph /sendMail. Returns {message_id, thread_id}.

    Used by both send-internal and external-send (the router decides the tier).
    `saveToSentItems` defaults true so the operator sees the sent copy.
    """
    message: dict[str, Any] = {
        "subject": subject,
        "body": {"contentType": "Text", "content": body},
        "toRecipients": _to_recipients(to),
    }
    if cc and cc.strip():
        message["ccRecipients"] = _to_recipients(cc)
    if bcc and bcc.strip():
        message["bccRecipients"] = _to_recipients(bcc)
    return _post_send_action(
        creds, "/me/sendMail", {"message": message, "saveToSentItems": True}
    )
