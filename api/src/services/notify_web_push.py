"""Web Push adapter for the #1224 notification routing layer (Kanban #955.A).

Mirrors the SHAPE of `notify_telegram.py`. Registered in
`notification_router.py::_ADAPTERS` under key `'web_push'` — when a
NotificationTarget carries `kind='web_push'`, the router calls
`send_web_push(target, payload)` with the standard adapter contract:
returns `{ok: bool, detail: str}`; NEVER raises.

target shape (NotificationTarget dict — chat_id is the push_subscriptions.id
serialized as a string, parity with how telegram embeds chat_id):
    {"kind": "web_push", "chat_id": "<sub_id>", "priority": int, "label": str}

payload shape (D4 locked):
    {"title": str, "body": str, "url": str, "icon"?: str}
    `url` is a relative path the FE service worker (slice 955.C) interprets
    on `notificationclick`.

D6 — auto-soft-delete on 404/410 Gone: per Web Push hygiene, the push
service returns these when the subscription has expired client-side
(uninstalled PWA / cleared browser data / expired key). The adapter flips
`status=0` on the offending row and returns ok=False so the router's
fall-through loop continues to the next priority target.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import RecordStatus
from src.db import SessionLocal
from src.models.push_subscription import PushSubscription

logger = logging.getLogger(__name__)


# Module-level constants — exported for tests + monkeypatch parity with
# notify_telegram. Env names match docker-compose.yml + .env.example.
VAPID_ENV_PUBLIC = "VAPID_PUBLIC_KEY"
VAPID_ENV_PRIVATE = "VAPID_PRIVATE_KEY"
VAPID_ENV_SUBJECT = "VAPID_SUBJECT"

# pywebpush returns these statuses on a dead endpoint — auto-soft-delete (D6).
PUSH_GONE_STATUSES = (404, 410)


def _max_payload_bytes() -> int:
    """Web Push spec caps the encrypted record at 4096 bytes; the plain JSON
    is then encrypted + padded. Real-world ceiling for the JSON we ship is
    well under 3KB. We pin a soft cap of 3000 to leave headroom for the AES
    overhead; oversize → truncate `body` and ship the rest verbatim.
    """
    return 3000


def _serialize_payload(payload: dict[str, Any]) -> str:
    """Render the structured payload as a compact JSON string the FE
    service worker (slice 955.C) parses on `push` event.

    Shape per D4: `{title, body, url, icon?}`. Unknown keys pass through —
    forward-compat with whatever 955.C ships. If serialization itself fails
    (non-serializable value), fall back to repr() so the adapter doesn't
    raise — the router needs `ok=False` to fall through cleanly.
    """
    try:
        rendered = json.dumps(payload or {}, ensure_ascii=False)
    except (TypeError, ValueError):
        rendered = json.dumps({"title": "(payload encode error)", "body": repr(payload)[:500]})
    cap = _max_payload_bytes()
    if len(rendered.encode("utf-8")) > cap:
        # Cheap truncation — strip the `body` field down. Keep `title` + `url`
        # intact so the click-through still works.
        try:
            obj = json.loads(rendered)
        except ValueError:
            obj = {"title": "(payload truncated)"}
        body_text = str(obj.get("body", ""))
        keep = max(0, cap - 200)  # 200B headroom for the rest of the keys.
        obj["body"] = body_text[:keep] + ("..." if len(body_text) > keep else "")
        rendered = json.dumps(obj, ensure_ascii=False)
    return rendered


async def _soft_delete_subscription(sub_id: int) -> None:
    """Mark a dead subscription `status=0` per D6.

    Uses a fresh SessionLocal — the router's session is for the per-task
    audit row; mixing this UPDATE into it would couple the failure of one
    adapter call to the audit-row commit. Fire-and-forget here keeps the
    adapter contract clean (returns {ok, detail} — no shared transaction).

    Swallow all exceptions: an inability to soft-delete is logged but does
    NOT change the adapter's return contract — the WebPush failure itself
    is already being reported back to the router as ok=False.
    """
    try:
        async with SessionLocal() as session:  # type: AsyncSession
            sub = (
                await session.execute(
                    select(PushSubscription).where(PushSubscription.id == sub_id)
                )
            ).scalar_one_or_none()
            if sub is None or sub.status != RecordStatus.ACTIVE:
                return
            sub.status = RecordStatus.DELETED
            await session.commit()
            logger.info(
                "notify_web_push: auto-soft-deleted dead subscription id=%d",
                sub_id,
            )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "notify_web_push: failed to soft-delete subscription id=%d: %r",
            sub_id, exc,
        )


async def send_web_push(
    target: dict[str, Any],
    payload: dict[str, Any],
    *,
    webpush_fn=None,
) -> dict[str, Any]:
    """Send a Web Push payload to the subscription identified by `target['chat_id']`.

    Args:
        target: NotificationTarget dict — `chat_id` is the push_subscriptions.id
                as a string. The router's resolution layer (#1224) hands JSONB
                rows as dicts; we mirror notify_telegram's signature.
        payload: D4-locked dict `{title, body, url, icon?}`. Forwarded as
                JSON to the FE service worker via the encrypted push record.
        webpush_fn: Test seam — when provided, the function calls this
                instead of `pywebpush.webpush`. Production callers pass None
                and we use the real library.

    Returns:
        {ok: bool, detail: str}
        - ok=True when pywebpush returns a 2xx Response object.
        - ok=False + detail descriptor on every failure path:
          - missing_chat_id / invalid_chat_id
          - missing_env_VAPID_* (any of the 3 env vars unset)
          - subscription_not_found / subscription_soft_deleted
          - http_<status>: <snippet> (Web Push service 4xx/5xx)
          - request_error: <ExcName>

    D6 — when the WebPushException carries status 404 or 410 the adapter
    soft-deletes the offending subscription (status=0) BEFORE returning the
    failure so the next attempt against the same `chat_id` would surface as
    `subscription_soft_deleted` instead of looping back to the dead endpoint.

    Never raises — every failure lands as ok=False so the router's fall-
    through loop stays linear (same contract as notify_telegram).
    """
    chat_id_raw = target.get("chat_id")
    if chat_id_raw is None or chat_id_raw == "":
        return {"ok": False, "detail": "missing_chat_id"}
    try:
        sub_id = int(chat_id_raw)
    except (TypeError, ValueError):
        return {"ok": False, "detail": f"invalid_chat_id: {chat_id_raw!r}"}

    # Env gate — the three VAPID values are operator-managed. A missing key
    # surfaces as ok=False so the router falls through (parity with
    # notify_telegram's TELEGRAM_BOT_TOKEN gate).
    vapid_public = os.environ.get(VAPID_ENV_PUBLIC, "").strip()
    vapid_private = os.environ.get(VAPID_ENV_PRIVATE, "").strip()
    vapid_subject = os.environ.get(VAPID_ENV_SUBJECT, "").strip()
    for env_name, env_val in (
        (VAPID_ENV_PUBLIC, vapid_public),
        (VAPID_ENV_PRIVATE, vapid_private),
        (VAPID_ENV_SUBJECT, vapid_subject),
    ):
        if not env_val:
            return {"ok": False, "detail": f"missing_env_{env_name}"}

    # Fetch the live subscription row — we need endpoint + p256dh + auth.
    # A SessionLocal here is independent of the router's session (parity with
    # _soft_delete_subscription's reasoning).
    try:
        async with SessionLocal() as session:  # type: AsyncSession
            sub = (
                await session.execute(
                    select(PushSubscription).where(PushSubscription.id == sub_id)
                )
            ).scalar_one_or_none()
    except Exception as exc:  # pragma: no cover - DB unreachable
        logger.warning(
            "notify_web_push: db_error fetching sub_id=%d: %r", sub_id, exc
        )
        return {"ok": False, "detail": f"db_error: {type(exc).__name__}"}

    if sub is None:
        return {"ok": False, "detail": f"subscription_not_found: id={sub_id}"}
    if sub.status != RecordStatus.ACTIVE:
        return {
            "ok": False,
            "detail": f"subscription_soft_deleted: id={sub_id}",
        }

    subscription_info = {
        "endpoint": sub.endpoint,
        "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
    }
    body = _serialize_payload(payload)

    # Resolve the webpush callable — production = real library; tests inject
    # a stub via the kwarg. Import is lazy so a deployment without pywebpush
    # installed still loads the module (the env-gate above would deny first
    # in practice, but lazy-import is defensive).
    if webpush_fn is None:
        try:
            from pywebpush import WebPushException, webpush
        except ImportError as exc:  # pragma: no cover - dep is required
            return {
                "ok": False,
                "detail": f"pywebpush_not_installed: {exc}",
            }
        try:
            response = webpush(
                subscription_info=subscription_info,
                data=body,
                vapid_private_key=vapid_private,
                vapid_claims={"sub": vapid_subject},
                timeout=10,
            )
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            text_snippet = ""
            try:
                if exc.response is not None:
                    text_snippet = (exc.response.text or "")[:200]
            except Exception:  # pragma: no cover
                pass
            if status in PUSH_GONE_STATUSES:
                await _soft_delete_subscription(sub_id)
                return {
                    "ok": False,
                    "detail": "Subscription invalid; auto-removed",
                }
            return {
                "ok": False,
                "detail": f"http_{status}: {text_snippet}"
                if status is not None
                else f"webpush_error: {type(exc).__name__}",
            }
        except Exception as exc:
            logger.warning(
                "notify_web_push: request_error sub_id=%d err=%r", sub_id, exc
            )
            return {
                "ok": False,
                "detail": f"request_error: {type(exc).__name__}",
            }
    else:
        # Test seam — the stub may raise, may return an httpx-Response-like
        # object, or may return a plain dict {status_code:int}. We accept any
        # status-code-bearing duck-typed return.
        try:
            response = await _maybe_await(
                webpush_fn(
                    subscription_info=subscription_info,
                    data=body,
                    vapid_private_key=vapid_private,
                    vapid_claims={"sub": vapid_subject},
                    timeout=10,
                )
            )
        except Exception as exc:
            # Test stubs raise via duck-typed wrappers — mimic the
            # WebPushException branch for parity.
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in PUSH_GONE_STATUSES:
                await _soft_delete_subscription(sub_id)
                return {
                    "ok": False,
                    "detail": "Subscription invalid; auto-removed",
                }
            return {
                "ok": False,
                "detail": f"request_error: {type(exc).__name__}",
            }

    status_code = getattr(response, "status_code", 200)
    if status_code is not None and not (200 <= int(status_code) < 300):
        # Non-2xx that wasn't raised as a WebPushException (e.g. a test stub
        # returning a Response-shaped object). Apply the D6 gate.
        if int(status_code) in PUSH_GONE_STATUSES:
            await _soft_delete_subscription(sub_id)
            return {"ok": False, "detail": "Subscription invalid; auto-removed"}
        return {"ok": False, "detail": f"http_{status_code}"}

    return {"ok": True, "detail": "sent"}


async def _maybe_await(value):
    """Test-seam helper: if `value` is awaitable, await it; else return it.

    Lets tests pass either a sync `lambda ...: response` or an
    `async def stub(...) -> response`. Production never reaches this branch
    (only the test seam calls it).
    """
    if hasattr(value, "__await__"):
        return await value
    return value
