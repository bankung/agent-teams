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

import base64
import datetime
import logging
import os
import re
import secrets
from typing import Any

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

# FIX-2 (#1939): defense-in-depth id validation inside the client.
# Mirror _MID_ALLOWED in schemas/tools_email.py — defined independently here
# to avoid a router/schema import cycle.
_ID_RE = re.compile(r"^[A-Za-z0-9_\-=+]+$")

# Header-injection rail (#2100 WARN-2 / NIT-1 / NIT-2). Values we re-inject into
# outbound MIME headers (In-Reply-To, References, the Re:/Fwd: Subject) are
# derived from the FETCHED inbound message — i.e. attacker-influenced (a crafted
# inbound email controls them). A bare CR/LF in EmailMessage.__setitem__ raises,
# which surfaces to the router as a 502 on the crafted-inbound path. Strip CR/LF
# (header-folding chars) and length-cap BEFORE assignment so the path is normal
# operation rather than a crash. Caps mirror the schema bounds: 998 for a single
# header field (_HEADER_MAX, RFC-5322 line limit), 4096 for the accumulating
# References chain (_REFS_MAX — many message-ids over a long thread).
_HEADER_MAX = 998
_REFS_MAX = 4096


def _strip_header_value(value: str, *, cap: int = _HEADER_MAX) -> str:
    """Strip CR/LF from a fetched header value + length-cap it before re-injection.

    Low-level primitive: removes every '\\r' and '\\n' (the chars EmailMessage
    rejects / that could fold or inject a new header) and truncates to `cap`
    chars. Pure + deterministic; safe on '' (returns ''). NOTE: this CONCATENATES
    the text on either side of a CR/LF ("a\\r\\nb" -> "ab"). That is enough to stop
    EmailMessage raising and to stop a *new header line* folding in, but it leaves
    the post-CRLF text glued onto the legitimate value (e.g. an inbound
    "<id>\\r\\nX-Injected: y" becomes "<id>X-Injected: y" — the smuggled token
    survives as a substring of the header VALUE). For re-injecting
    attacker-influenced inbound header values, prefer `_safe_header_value`, which
    DROPS everything after the first CR/LF entirely.
    """
    cleaned = value.replace("\r", "").replace("\n", "")
    return cleaned[:cap]


def _safe_header_value(value: str, *, cap: int = _HEADER_MAX) -> str:
    """Sanitize an attacker-influenced inbound header value before re-injection.

    Stronger than `_strip_header_value`: TRUNCATES at the first CR or LF (the
    smuggled continuation after a header-folding CRLF is an injection attempt — it
    is DROPPED, not concatenated onto the legitimate value), then length-caps to
    `cap`. So an inbound "<orig@corp.com>\\r\\nX-Injected: yes" yields
    "<orig@corp.com>" — the smuggled "X-Injected:" token does not survive anywhere
    in the outbound MIME. Pure + deterministic; safe on '' (returns '').

    Used on every inbound-derived header value re-injected by send_reply /
    send_forward (Subject, In-Reply-To, References) so a crafted inbound email can
    neither crash the send (502) nor smuggle a header through value-concatenation.
    """
    cr = value.find("\r")
    lf = value.find("\n")
    cut = min([i for i in (cr, lf) if i != -1], default=-1)
    truncated = value if cut == -1 else value[:cut]
    return truncated[:cap]

# Full mail scope — covers read, modify, trash, send, labels, drafts. One
# consent prompt covers every future endpoint we'd add for the operator.
#
# Kanban #1942: calendar.readonly added so the SAME Google OAuth principal can
# also drive the secretary's read-only Calendar tools (list-events + freebusy)
# for conflict detection.
#
# Kanban #1963: calendar.events ADDED for the Calendar WRITE tools (create-event
# + respond/RSVP) on the PROPER `/api/tools/calendar` base. calendar.events is a
# read-write scope on events; it is a STRICT superset of calendar.readonly for
# the operations we perform (insert + patch + get), so a token carrying
# calendar.events can satisfy BOTH the READ and the WRITE calendar tools. We keep
# calendar.readonly in the list too so the auth-status `calendar_readonly`
# projection (which checks for the readonly scope string) keeps working and a
# READ-only re-consent path stays available.
#
# RE-CONSENT PREREQUISITE: a token granted under an OLDER scope list does NOT
# carry the new scopes — the operator must re-run the OAuth dance
# (POST /api/tools/email/auth/gmail/start) to grant them. include_granted_scopes=
# "true" in auth_start preserves existing access across that re-consent, so
# re-consenting is additive (no Gmail / earlier Calendar capability is lost).
# Until re-consent, the Calendar API raises an insufficient-permission error which
# calendar_client maps to CalendarScopeError → HTTP 412. LIVE create/respond
# verification is OUT OF SCOPE for #1963 (build + mocked tests only) — a go-live
# followup must confirm re-consent grants calendar.events before WRITE tools work.
SCOPES = [
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]
# The Calendar scope values, exposed so the auth-status projection can report
# whether the stored token actually carries them (i.e. whether re-consent is done).
CALENDAR_READONLY_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
CALENDAR_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"

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

    Returns {email, expires_at, calendar_readonly}. Best-effort — Google's
    Credentials object doesn't carry email by default (it's encoded in the
    id_token if requested, which we don't). We fetch the user's email via the
    Gmail profile endpoint on first lookup if needed; for the Karpathy cut we
    just return None for email and expose expiry.

    `calendar_readonly` (#1942): True iff the stored token's granted scopes
    include calendar.readonly — i.e. the operator has re-consented and the
    Calendar tools are available. Best-effort: reads `creds.scopes` (no upstream
    call). Returns False when scopes are absent/unknown so a caller never assumes
    calendar access it doesn't have.
    """
    if not isinstance(creds, Credentials):
        return {"email": None, "expires_at": None, "calendar_readonly": False}
    expires_at = creds.expiry.isoformat() + "Z" if creds.expiry else None
    return {
        "email": _safe_profile_email(creds),
        "expires_at": expires_at,
        "calendar_readonly": _has_calendar_scope(creds),
    }


def _has_calendar_scope(creds: Credentials) -> bool:
    """True iff the stored token's granted scopes include calendar.readonly.

    Best-effort, no upstream call: reads the `scopes` attribute Google stores on
    the Credentials object. Returns False on any absence/uncertainty.
    """
    try:
        scopes = getattr(creds, "scopes", None) or []
        return CALENDAR_READONLY_SCOPE in scopes
    except Exception:  # pragma: no cover — defensive
        return False


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


# ---------------------------------------------------------------------------
# Kanban #2100 — Tier-3 SEND functions (reply / forward / send)
# ---------------------------------------------------------------------------
#
# All three call `users.messages.send` (the actual fire) — distinct from
# save_draft's `drafts.create`. The router gates these behind the operator-proof
# tier gate (reply/forward = REPLY; send = SEND_INTERNAL/EXTERNAL_SEND). Caller
# is responsible for the gate + cap BEFORE invoking. Each returns
# {message_id, thread_id} (thread_id present so the response can thread).
#
# MIME built with stdlib `email.message.EmailMessage` (UTF-8, base64url) —
# identical construction to save_draft, no extra dependency.


def _send_raw(service, raw: str, *, thread_id: str | None = None) -> dict:
    """POST a base64url-encoded MIME message via users.messages.send.

    `thread_id` (when set) threads the sent message into an existing thread —
    used by reply. Returns {message_id, thread_id}.
    """
    msg_body: dict = {"raw": raw}
    if thread_id:
        msg_body["threadId"] = thread_id
    sent = service.users().messages().send(userId="me", body=msg_body).execute()
    return {
        "message_id": sent.get("id"),
        "thread_id": sent.get("threadId"),
    }


def _fetch_headers(service, message_id: str) -> dict[str, str]:
    """Fetch From/Subject/Message-Id/References headers for reply threading.

    Returns a lowercased-key header dict. Best-effort — missing headers simply
    aren't present in the result.
    """
    msg = (
        service.users()
        .messages()
        .get(
            userId="me",
            id=message_id,
            format="metadata",
            metadataHeaders=["From", "Subject", "Message-Id", "References", "To"],
        )
        .execute()
    )
    headers = {
        h["name"].lower(): h["value"]
        for h in (msg.get("payload", {}).get("headers", []) or [])
    }
    headers["_thread_id"] = msg.get("threadId", "")
    return headers


def send_reply(
    creds: Credentials, *, message_id: str, body: str, thread_id: str | None = None
) -> dict:
    """Reply to `message_id` in-thread via users.messages.send. Returns {message_id, thread_id}.

    Fetches the original's From/Subject/Message-Id to set To, a `Re:` subject,
    and In-Reply-To/References so the reply threads correctly in mail clients.
    """
    from email.message import EmailMessage

    service = _build_service(creds)
    orig = _fetch_headers(service, message_id)
    resolved_thread = thread_id or orig.get("_thread_id") or None

    msg = EmailMessage()
    reply_to = orig.get("from")
    if reply_to:
        msg["To"] = reply_to
    # #2100 WARN-2: subject is inbound-derived — truncate at first CR/LF (drop the
    # injected tail, not concatenate it) + cap (998) before building the Re: header
    # so a crafted inbound subject can neither fold nor smuggle a token.
    subject = _safe_header_value(orig.get("subject", ""))
    msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    # #2100 WARN-2 / NIT-1: In-Reply-To + References are inbound-derived. Truncate
    # at first CR/LF + cap (msg-id 998, the accumulating refs chain 4096) BEFORE
    # header assignment — converts the crafted-inbound 502 into normal operation and
    # drops any smuggled continuation entirely.
    orig_msg_id = _safe_header_value(orig.get("message-id", ""))
    if orig_msg_id:
        msg["In-Reply-To"] = orig_msg_id
        refs = _safe_header_value(orig.get("references", ""), cap=_REFS_MAX)
        msg["References"] = _safe_header_value(
            f"{refs} {orig_msg_id}".strip(), cap=_REFS_MAX
        )
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    return _send_raw(service, raw, thread_id=resolved_thread)


def send_forward(creds: Credentials, *, message_id: str, to: str, body: str = "") -> dict:
    """Forward `message_id` to `to` via users.messages.send. Returns {message_id, thread_id}.

    Builds a new message with a `Fwd:` subject and the operator's prefatory body
    plus a quoted copy of the original body text. The forward is NOT threaded
    (a forward starts a new conversation for the new recipient).
    """
    from email.message import EmailMessage

    service = _build_service(creds)
    # Pull the original body + subject to quote into the forward.
    orig = get_message(creds, message_id)
    raw_subject = orig.get("subject") or ""
    # #2100 NIT-2: the fetched subject is inbound-derived; truncate at first CR/LF
    # (drop the smuggled tail) + cap (998) for the Subject HEADER assignment. We
    # also quote the SANITIZED subject into the body below — quoting the raw inbound
    # subject verbatim would echo the attacker's CRLF-smuggled "Bcc:/X-..." text
    # into the outbound body (harmless as a header, but still attacker-controlled
    # content we needn't propagate), so the quote uses the cleaned value too.
    fwd_subject = _safe_header_value(raw_subject)
    fwd_subject = fwd_subject if fwd_subject.lower().startswith("fwd:") else f"Fwd: {fwd_subject}"
    subject = _safe_header_value(raw_subject)

    quoted = (
        f"\n\n---------- Forwarded message ----------\n"
        f"From: {orig.get('from') or ''}\n"
        f"Subject: {subject}\n"
        f"Date: {orig.get('date') or ''}\n\n"
        f"{orig.get('body_text') or ''}"
    )

    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = fwd_subject
    msg.set_content(f"{body}{quoted}")
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    return _send_raw(service, raw)


def send_message(
    creds: Credentials,
    *,
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    bcc: str | None = None,
) -> dict:
    """Compose + send a NEW message via users.messages.send. Returns {message_id, thread_id}.

    Used by both send-internal and external-send (the router decides the tier).
    """
    from email.message import EmailMessage

    service = _build_service(creds)
    msg = EmailMessage()
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    msg["Subject"] = subject
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    return _send_raw(service, raw)


# TODO(#1585 follow-up): Outlook parity for modify_labels/save_draft (mark/archive/draft).


# ---------------------------------------------------------------------------
# Kanban #1939 — READ endpoints (search + get)
# ---------------------------------------------------------------------------


def search_messages(
    creds: Credentials, query: str, max_results: int = 10
) -> list[dict]:
    """Search the mailbox and return metadata for up to max_results messages.

    Uses `users.messages.list` (q=query) to get ids, then fetches each with
    `format=metadata` (metadataHeaders=From,Subject,Date) so we get preview-
    level metadata without pulling full bodies.

    Returns a list of:
      {id, thread_id, from, subject, date, snippet}

    Cost: 5 units for the list call + 5 per metadata-fetch (each is cheap).
    Caller is responsible for cap enforcement BEFORE invoking.
    """
    service = _build_service(creds)

    # 1. List message ids.
    # FIX-5 (#1939): schema already bounds max_results to <=50; drop redundant cap.
    list_resp = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=max_results)
        .execute()
    )
    items = list_resp.get("messages", []) or []
    ids = [m["id"] for m in items[:max_results]]

    if not ids:
        return []

    # 2. Fetch metadata for each id.
    results: list[dict] = []
    for mid in ids:
        try:
            msg = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=mid,
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                )
                .execute()
            )
            headers = {
                h["name"].lower(): h["value"]
                for h in (msg.get("payload", {}).get("headers", []) or [])
            }
            results.append(
                {
                    "id": msg.get("id"),
                    "thread_id": msg.get("threadId"),
                    "from": headers.get("from"),
                    "subject": headers.get("subject"),
                    "date": headers.get("date"),
                    "snippet": msg.get("snippet"),
                }
            )
        except HttpError as e:
            logger.warning(
                "search_messages: metadata fetch failed for id=%s: %s",
                mid,
                type(e).__name__,
            )
        except Exception as e:
            logger.warning(
                "search_messages: unexpected error for id=%s: %s",
                mid,
                type(e).__name__,
            )
    return results


def get_message(creds: Credentials, message_id: str) -> dict:
    """Fetch a single message in full and return its content.

    Uses `users.messages.get` with format=full. Walks the MIME tree to
    extract the text/plain part as body_text (falls back to an empty
    string if no plain-text part is found).

    Returns:
      {id, thread_id, from, to, subject, date, body_text}

    PRIVACY: body_text MUST NOT be logged, written to any audit trail,
    or echoed in error responses. Error paths use only type(exc).__name__.

    Caller is responsible for cap enforcement BEFORE invoking.
    """
    # FIX-2 (#1939): defense-in-depth — validate id before interpolating into URL.
    if not _ID_RE.fullmatch(message_id):
        raise ValueError("invalid message_id")

    service = _build_service(creds)
    msg = (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )

    headers = {
        h["name"].lower(): h["value"]
        for h in (msg.get("payload", {}).get("headers", []) or [])
    }

    body_text = _extract_plain_text(msg.get("payload", {}))

    return {
        "id": msg.get("id"),
        "thread_id": msg.get("threadId"),
        "from": headers.get("from"),
        "to": headers.get("to"),
        "subject": headers.get("subject"),
        "date": headers.get("date"),
        "body_text": body_text,
    }


# ---------------------------------------------------------------------------
# Kanban #1940 — READ extras (get_thread, list_labels, get_attachment)
# ---------------------------------------------------------------------------


# Size cap for attachment fetch: refuse data retrieval beyond this limit.
_ATTACHMENT_SIZE_CAP_BYTES = 10 * 1024 * 1024  # 10 MB


class AttachmentTooLargeError(Exception):
    """Raised when an attachment exceeds _ATTACHMENT_SIZE_CAP_BYTES."""


class AttachmentNotFoundError(Exception):
    """Raised when the attachment_id is not found in the message MIME tree."""


def get_thread(creds: Credentials, thread_id: str) -> dict:
    """Fetch all messages in a Gmail thread and return their content.

    Uses `users.threads().get(format="full")` to pull the full thread.
    Each message is mapped using the same header-extract + _extract_plain_text
    logic as `get_message`.

    Returns:
      {thread_id, messages: [{id, from, to, subject, date, body_text}]}

    PRIVACY: body_text values MUST NOT be logged, audited, or echoed in errors.
    Caller is responsible for cap enforcement BEFORE invoking.
    """
    # Defense-in-depth: validate id before interpolating into URL.
    if not _ID_RE.fullmatch(thread_id):
        raise ValueError("invalid thread_id")

    service = _build_service(creds)
    thread = (
        service.users()
        .threads()
        .get(userId="me", id=thread_id, format="full")
        .execute()
    )

    messages = []
    for msg in thread.get("messages", []) or []:
        headers = {
            h["name"].lower(): h["value"]
            for h in (msg.get("payload", {}).get("headers", []) or [])
        }
        body_text = _extract_plain_text(msg.get("payload", {}))
        messages.append(
            {
                "id": msg.get("id"),
                "from": headers.get("from"),
                "to": headers.get("to"),
                "subject": headers.get("subject"),
                "date": headers.get("date"),
                "body_text": body_text,
            }
        )

    return {"thread_id": thread_id, "messages": messages}


def list_labels(creds: Credentials) -> list[dict]:
    """List all Gmail labels for the authenticated account.

    Uses `users.labels().list(userId="me")`. Returns a list of:
      [{id, name, type}]

    `type` is the Gmail label type string (e.g. "system", "user") if present.
    Caller is responsible for cap enforcement BEFORE invoking.
    """
    service = _build_service(creds)
    resp = service.users().labels().list(userId="me").execute()
    result = []
    for lbl in resp.get("labels", []) or []:
        result.append(
            {
                "id": lbl.get("id", ""),
                "name": lbl.get("name", ""),
                "type": lbl.get("type"),
            }
        )
    return result


def get_attachment(creds: Credentials, message_id: str, attachment_id: str) -> dict:
    """Fetch a single Gmail attachment and return its content as base64url data.

    Validates both ids with _ID_RE (defense-in-depth). Fetches the message
    with format="full" to locate the attachment part (by body.attachmentId)
    and read its filename, mimeType, and size. If size exceeds 10 MB, raises
    AttachmentTooLargeError before fetching data.

    Returns:
      {filename, mime_type, size, data_base64}

    PRIVACY: filename, mime_type, and data_base64 MUST NOT be logged, audited,
    or echoed in error responses. Error paths use only type(exc).__name__.
    Caller is responsible for cap enforcement BEFORE invoking.
    """
    # Defense-in-depth: validate both ids before interpolating into URLs.
    if not _ID_RE.fullmatch(message_id):
        raise ValueError("invalid message_id")
    if not _ID_RE.fullmatch(attachment_id):
        raise ValueError("invalid attachment_id")

    service = _build_service(creds)

    # Step 1: fetch the message to find the attachment part metadata.
    msg = (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )

    # Walk the MIME tree to find the part with the matching attachmentId.
    filename: str | None = None
    mime_type: str | None = None
    size: int = 0

    def _find_part(payload: dict, depth: int = 0) -> bool:
        """Recursively search for the part with body.attachmentId == attachment_id."""
        nonlocal filename, mime_type, size
        if depth > 20:
            return False
        body = payload.get("body") or {}
        if body.get("attachmentId") == attachment_id:
            filename = payload.get("filename") or None
            mime_type = payload.get("mimeType") or None
            size = body.get("size", 0)
            return True
        for part in payload.get("parts", []) or []:
            if _find_part(part, depth + 1):
                return True
        return False

    found = _find_part(msg.get("payload", {}))
    if not found:
        raise AttachmentNotFoundError("attachment_id not found in message")

    # Step 2: size cap check before fetching data (pre-fetch guard on metadata size).
    if size > _ATTACHMENT_SIZE_CAP_BYTES:
        raise AttachmentTooLargeError("attachment exceeds size cap")

    # Step 3: fetch the attachment data.
    att = (
        service.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=message_id, id=attachment_id)
        .execute()
    )
    data_base64 = att.get("data", "")

    # FIX-A: post-fetch guard — base64 expands ~33%; bound the actual returned
    # payload in case Gmail-reported metadata size was stale/inaccurate.
    if len(data_base64) > _ATTACHMENT_SIZE_CAP_BYTES * 4 // 3 + 1024:
        raise AttachmentTooLargeError("attachment exceeds size cap")

    return {
        "filename": filename,
        "mime_type": mime_type,
        "size": size,
        "data_base64": data_base64,
    }


def _extract_plain_text(payload: dict, depth: int = 0) -> str:
    """Walk a Gmail MIME payload tree and return the first text/plain body.

    Returns an empty string if no text/plain part is found.
    PRIVACY: this function's return value must never be logged.

    FIX-1 (#1939): depth guard — bail out past 20 levels to prevent a
    pathologically nested MIME payload from causing unbounded recursion.
    """
    # FIX-1 (#1939): bound recursion depth.
    if depth > 20:
        return ""

    mime_type = payload.get("mimeType", "")
    if mime_type == "text/plain":
        data = (payload.get("body") or {}).get("data", "")
        if data:
            try:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            except Exception:
                return ""
        return ""
    # Recurse into parts.
    for part in payload.get("parts", []) or []:
        text = _extract_plain_text(part, depth + 1)
        if text:
            return text
    return ""
