"""Gmail OAuth + trash client (Kanban #1604, Phase 0 Karpathy cut).

Full mail scope (`https://mail.google.com/`) — sufficient for the trash
endpoint AND every downstream label/draft/send action we may add later
without re-consenting.

OAuth dance (test-mode, operator-only):
  1. `POST /api/tools/email/auth/gmail/start` → returns auth_url; user opens
     in browser, logs in, consents.
  2. Google redirects to `GOOGLE_OAUTH_REDIRECT_URI` with ?code=&state=
     → `GET /api/tools/email/auth/gmail/callback` exchanges code for creds
     and stores via `token_store.put("gmail", project_id, creds)`.
  3. `GET /api/tools/email/auth/gmail/status` confirms.

Karpathy cuts:
  - In-memory `_PENDING_FLOWS` keyed by `state` — state is the only safe
    cross-redirect handle (no cookies on the api). Pending entries are
    auto-pruned after 10 min to bound memory.
  - The `redirect_uri` on the callback responds with JSON, not a 302 — no
    front-end this phase.

Project-id binding:
  - `auth_start(project_id)` stamps state with the project_id so the
    callback knows which token_store slot to populate (multi-project
    operator could run two parallel OAuth flows; state-based binding
    prevents cross-talk).
"""

from __future__ import annotations

import datetime
import logging
import os
import secrets
from typing import Any

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

# Full mail scope — covers read, modify, trash, send, labels, drafts. One
# consent prompt covers every future endpoint we'd add for the operator.
SCOPES = ["https://mail.google.com/"]

# Pending OAuth flows — state -> (flow_obj, project_id, created_at_utc).
# Bounded by a 10-min TTL prune on each new start.
_PENDING_FLOWS: dict[str, tuple[Flow, int, datetime.datetime]] = {}
_PENDING_TTL = datetime.timedelta(minutes=10)


def _prune_pending() -> None:
    """Drop pending OAuth states older than _PENDING_TTL.

    Called on each `auth_start` to bound memory. Cheap O(n) walk; n stays in
    single digits in operator-only usage.
    """
    now = datetime.datetime.now(datetime.UTC)
    expired = [s for s, (_, _, ts) in _PENDING_FLOWS.items() if now - ts > _PENDING_TTL]
    for s in expired:
        _PENDING_FLOWS.pop(s, None)


def _client_config() -> dict[str, Any]:
    """Build the desktop-app OAuth client config dict from env vars.

    Returns the shape `google_auth_oauthlib.flow.Flow.from_client_config`
    expects. Raises RuntimeError if required env vars missing.
    """
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    redirect_uri = os.environ.get(
        "GOOGLE_OAUTH_REDIRECT_URI",
        "http://localhost:8456/api/tools/email/auth/gmail/callback",
    ).strip()
    if not client_id or not client_secret:
        raise RuntimeError(
            "Gmail OAuth not configured: set GOOGLE_OAUTH_CLIENT_ID and "
            "GOOGLE_OAUTH_CLIENT_SECRET in the repo-root .env (dockerized stack) "
            "or api/.env (local uvicorn) — see .env.example Kanban #1604 block for setup steps."
        )
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }, redirect_uri


def auth_start(project_id: int) -> str:
    """Begin OAuth flow for `project_id`. Returns the auth_url to open.

    Side effect: registers state -> (flow, project_id, created_at) in
    `_PENDING_FLOWS` so the callback can recover the project binding.
    """
    _prune_pending()
    config, redirect_uri = _client_config()
    flow = Flow.from_client_config(config, scopes=SCOPES, redirect_uri=redirect_uri)
    # PKCE (C): code_verifier makes the code exchange binding to this client
    # instance; google-auth-oauthlib 1.x picks it up automatically in fetch_token.
    flow.code_verifier = secrets.token_urlsafe(96)
    # Explicit state (E): generate state ourselves so the value stored in
    # _PENDING_FLOWS is identical to what Google echoes back in the callback.
    # access_type=offline → refresh token; prompt=consent → force re-consent so
    # we always receive a refresh_token (Google omits it on re-grant otherwise).
    state = secrets.token_urlsafe(32)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
        state=state,
    )
    _PENDING_FLOWS[state] = (flow, project_id, datetime.datetime.now(datetime.UTC))
    return auth_url


def auth_callback(code: str, state: str) -> tuple[int, Credentials]:
    """Exchange `code` for credentials. Returns (project_id, creds).

    Raises ValueError if state is unknown / expired (replay protection +
    cross-project safety).
    """
    entry = _PENDING_FLOWS.pop(state, None)
    if entry is None:
        raise ValueError(
            "unknown or expired OAuth state; restart the flow at "
            "POST /api/tools/email/auth/gmail/start"
        )
    flow, project_id, _created_at = entry
    # fetch_token exchanges the auth code for an access_token + refresh_token.
    flow.fetch_token(code=code)
    return project_id, flow.credentials


def creds_summary(creds: object) -> dict:
    """Project the stored Credentials into a status dict for token_store.

    Returns {email, expires_at}. Best-effort — Google's Credentials object
    doesn't carry email by default (it's encoded in the id_token if requested,
    which we don't). We fetch the user's email via the Gmail profile endpoint
    on first lookup if needed; for the Karpathy cut we just return None for
    email and expose expiry.
    """
    if not isinstance(creds, Credentials):
        return {"email": None, "expires_at": None}
    expires_at = creds.expiry.isoformat() + "Z" if creds.expiry else None
    return {"email": _safe_profile_email(creds), "expires_at": expires_at}


def _safe_profile_email(creds: Credentials) -> str | None:
    """Best-effort Gmail profile email lookup. Returns None on any failure.

    Cached on the Credentials object via a private attribute to avoid hammering
    the API on every status() call.
    """
    cached = getattr(creds, "_at_email_cache", None)
    if cached is not None:
        return cached
    try:
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        profile = service.users().getProfile(userId="me").execute()
        email = profile.get("emailAddress")
        # Stash on the object so subsequent status() calls don't refetch.
        try:
            creds._at_email_cache = email  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover — defensive
            pass
        return email
    except Exception as exc:
        # Sanitize log: drop credential bytes; surface upstream class+message only.
        logger.warning("gmail profile lookup failed: %s", type(exc).__name__)
        return None


def _ensure_fresh(creds: Credentials) -> Credentials:
    """Refresh creds if expired and a refresh_token is available."""
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleAuthRequest())
    return creds


def _build_service(creds: Credentials):
    """Build a Gmail API service client. Refreshes creds if needed."""
    _ensure_fresh(creds)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def list_message_ids(creds: Credentials, query: str, max_results: int = 500) -> list[str]:
    """Run Gmail `messages.list` with `q=` and return up to max_results ids.

    Caller is responsible for cap enforcement BEFORE invoking — this fetches
    only what's asked. Cost: 5 units per list call (small) + 5 per page.
    """
    service = _build_service(creds)
    ids: list[str] = []
    page_token: str | None = None
    while True:
        page_size = min(500, max_results - len(ids))
        if page_size <= 0:
            break
        kwargs = {
            "userId": "me",
            "q": query,
            "maxResults": page_size,
        }
        if page_token:
            kwargs["pageToken"] = page_token
        resp = service.users().messages().list(**kwargs).execute()
        for m in resp.get("messages", []) or []:
            ids.append(m["id"])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return ids


def trash_messages(creds: Credentials, message_ids: list[str]) -> tuple[list[str], list[dict]]:
    """Trash each id. Returns (trashed_ids, errors).

    Each errors entry: {message_id, error_class, status}. Per-message failures
    do NOT abort the loop — caller decides whether to surface partial success.
    """
    service = _build_service(creds)
    trashed: list[str] = []
    errors: list[dict] = []
    for mid in message_ids:
        try:
            service.users().messages().trash(userId="me", id=mid).execute()
            trashed.append(mid)
        except HttpError as e:
            errors.append(
                {
                    "message_id": mid,
                    "error_class": "HttpError",
                    "status": getattr(e, "status_code", None) or getattr(e.resp, "status", None),
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


def modify_labels(
    creds: Credentials,
    message_ids: list[str],
    add_label_ids: list[str] | None = None,
    remove_label_ids: list[str] | None = None,
) -> tuple[list[str], list[dict]]:
    """Add/remove labels on each id via `users.messages.modify`. Returns (modified_ids, errors).

    Covers the Tier-1 `modify` actions:
      - mark read    -> remove_label_ids=["UNREAD"]
      - mark unread  -> add_label_ids=["UNREAD"]
      - archive      -> remove_label_ids=["INBOX"]

    Per-message failures do NOT abort the loop (mirrors `trash_messages`) — the
    caller surfaces partial success. We use per-id `modify` rather than
    `batchModify` so a single bad id yields a per-id error entry instead of
    failing the whole batch (batchModify is all-or-nothing and returns no body).
    Each errors entry: {message_id, error_class, status}.
    """
    # Defense-in-depth: block system labels that bypass the operator-proof DELETE
    # tier (TRASH/SPAM manipulation would let a modify-tier call effectively delete
    # or spam-classify messages without operator-proof). Case-sensitive: Gmail
    # system label ids are uppercase ASCII.
    _DENIED_SYSTEM_LABELS = {"TRASH", "SPAM"}
    all_requested = list(add_label_ids or []) + list(remove_label_ids or [])
    for label in all_requested:
        if label in _DENIED_SYSTEM_LABELS:
            raise ValueError(f"system label not permitted via modify_labels: {label}")

    service = _build_service(creds)
    body = {
        "addLabelIds": list(add_label_ids or []),
        "removeLabelIds": list(remove_label_ids or []),
    }
    modified: list[str] = []
    errors: list[dict] = []
    for mid in message_ids:
        try:
            service.users().messages().modify(userId="me", id=mid, body=body).execute()
            modified.append(mid)
        except HttpError as e:
            errors.append(
                {
                    "message_id": mid,
                    "error_class": "HttpError",
                    "status": getattr(e, "status_code", None) or getattr(e.resp, "status", None),
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
    return modified, errors


def save_draft(creds: Credentials, *, to: str, subject: str, body: str) -> dict:
    """Create a Gmail DRAFT (no send) via `users.drafts.create`. Returns {draft_id, message_id}.

    The draft lives in the Drafts folder until the operator explicitly sends it.
    Creating it is a recoverable Tier-1 `modify` action; sending is a separate
    higher-tier action. The MIME message is built with stdlib `email.message`
    (UTF-8, base64url-encoded) — no extra dependency.
    """
    import base64
    from email.message import EmailMessage

    service = _build_service(creds)
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    created = (
        service.users()
        .drafts()
        .create(userId="me", body={"message": {"raw": raw}})
        .execute()
    )
    return {
        "draft_id": created.get("id"),
        "message_id": (created.get("message") or {}).get("id"),
    }


# TODO(#1585 follow-up): Outlook parity for modify_labels/save_draft (mark/archive/draft).
