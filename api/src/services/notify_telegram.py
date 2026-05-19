"""Telegram bot adapter for the push-notification routing layer (Kanban #1224).

Single-bot v1: one bot token via env `TELEGRAM_BOT_TOKEN`.

Contract — `send_telegram(target, payload) -> {ok, detail, telegram_msg_id}`:
- `ok=True` on HTTP 200 with parsed `result.message_id`.
- `ok=False` on any failure (non-200, token invalid, chat blocked, network).

Never raises — every failure lands as `ok=False` so the router's fall-through
loop stays linear.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# Module-level constants — pinned for test introspection / monkeypatch.
TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_DEFAULT_TIMEOUT_SECONDS = 10.0
TELEGRAM_ENV_TOKEN = "TELEGRAM_BOT_TOKEN"


def _serialize_payload_for_text(payload: dict[str, Any]) -> str:
    """Render the structured payload as a single text block.

    Telegram's `sendMessage.text` is plain (or HTML/MarkdownV2 with parse_mode);
    v1 sends plain text with `<key>: <value>` lines so a digest payload reads
    naturally in the chat. Non-stringifiable values fall back to JSON encoding.
    Max length capped at 4096 (Telegram's hard message length); excess is
    truncated with a trailing ellipsis.
    """
    if not payload:
        return "(empty payload)"

    parts: list[str] = []
    for key, value in payload.items():
        if isinstance(value, str):
            rendered = value
        elif isinstance(value, (int, float, bool)) or value is None:
            rendered = str(value)
        else:
            try:
                rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
            except (TypeError, ValueError):
                rendered = repr(value)
        parts.append(f"{key}: {rendered}")
    text = "\n".join(parts)
    if len(text) > 4096:
        text = text[: 4096 - 3] + "..."
    return text


async def send_telegram(
    target: dict[str, Any],
    payload: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = TELEGRAM_DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Send `payload` to the Telegram chat identified by `target['chat_id']`.

    Args:
        target: NotificationTarget dict — `chat_id` is the only required key;
                other keys are ignored. JSONB column reads always surface as
                dict; callers passing a Pydantic instance must `.model_dump()`
                first.
        payload: structured delivery payload (e.g. {"title": "...", "body": "..."})
        client: optional httpx.AsyncClient — when provided, tests inject a
                MockTransport-backed client. Production callers pass None and
                the function builds its own short-lived client.
        timeout: per-request timeout in seconds. Defaults to 10s.

    Returns:
        Dict with three keys:
            ok               : bool — True only on HTTP 200 + parsed message_id.
            detail           : str  — short human-readable status / error.
            telegram_msg_id  : int | None — Telegram's per-message id when ok.

    Never raises — catches httpx.RequestError + JSON decode errors and
    returns ok=False with detail.
    """
    chat_id = target.get("chat_id")
    if not chat_id:
        return {"ok": False, "detail": "missing_chat_id", "telegram_msg_id": None}

    token = os.environ.get(TELEGRAM_ENV_TOKEN, "").strip()
    if not token:
        # Surface as a normal "ok=False" result so the router can fall through;
        # a misconfigured env should not crash the delivery loop.
        return {
            "ok": False,
            "detail": f"missing_env_{TELEGRAM_ENV_TOKEN}",
            "telegram_msg_id": None,
        }

    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    body = {
        "chat_id": str(chat_id),
        "text": _serialize_payload_for_text(payload),
    }

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=timeout)
    try:
        try:
            resp = await client.post(url, json=body)
        except httpx.RequestError as exc:
            logger.warning("send_telegram: request_error chat_id=%s err=%r", chat_id, exc)
            return {
                "ok": False,
                "detail": f"request_error: {type(exc).__name__}",
                "telegram_msg_id": None,
            }
        if resp.status_code != 200:
            # Truncate response body to keep audit-row noise bounded.
            snippet = (resp.text or "")[:200]
            logger.info(
                "send_telegram: non_200 chat_id=%s status=%d body=%s",
                chat_id, resp.status_code, snippet,
            )
            return {
                "ok": False,
                "detail": f"http_{resp.status_code}: {snippet}",
                "telegram_msg_id": None,
            }
        try:
            parsed = resp.json()
        except ValueError as exc:
            logger.warning("send_telegram: json_decode_error chat_id=%s err=%r", chat_id, exc)
            return {
                "ok": False,
                "detail": "json_decode_error",
                "telegram_msg_id": None,
            }

        # Telegram's success envelope: {"ok": true, "result": {"message_id": N, ...}}.
        if not parsed.get("ok"):
            detail = parsed.get("description") or "telegram_returned_ok_false"
            return {
                "ok": False,
                "detail": f"telegram_api: {detail}"[:200],
                "telegram_msg_id": None,
            }
        result = parsed.get("result") or {}
        msg_id = result.get("message_id")
        return {
            "ok": True,
            "detail": "sent",
            "telegram_msg_id": int(msg_id) if msg_id is not None else None,
        }
    finally:
        if own_client:
            await client.aclose()
