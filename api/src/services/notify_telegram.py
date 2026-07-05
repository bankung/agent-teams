"""Telegram bot adapter for the push-notification routing layer (Kanban #1224).

Single-bot v1: one bot token via env `TELEGRAM_BOT_TOKEN`.

Contract — `send_telegram(target, payload) -> {ok, detail, telegram_msg_id}`:
- `ok=True` on HTTP 200 with parsed `result.message_id`.
- `ok=False` on any failure (non-200, token invalid, chat blocked, network).

Never raises — every failure lands as `ok=False` so the router's fall-through
loop stays linear.

Kanban #2565 — inline buttons (async-HITL over Telegram):
The plain-text path is UNCHANGED. When the payload carries a reserved control
key `_telegram` with a non-empty `buttons` list, the adapter attaches a Telegram
`reply_markup` inline keyboard so the operator can approve/reject from the chat.
Each button's `callback_data` encodes `{gate_id, option}` via `encode_callback_data`
(<=64 bytes — Telegram's hard cap). The dumb inbound poller (scripts/telegram_poller.py)
decodes it with `decode_callback_data` and resolves the gate via the API. The
control key is stripped from the rendered text body so it never leaks into the
visible message. See `services/notify_gate.py` for the tier->policy that BUILDS
these payloads.

Kanban #2721 — HTML formatter:
When the payload carries `_html` (TELEGRAM_HTML_KEY), `send_telegram` sends
it verbatim with `parse_mode=HTML` instead of calling `_serialize_payload_for_text`.
Build that string with `format_telegram_html` — it escapes every dynamic piece
so titles containing `<`/`>`/`&` never produce malformed markup.
"""

from __future__ import annotations

import html as _html_mod
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

# Reserved payload key carrying inline-button control data (Kanban #2565). Held
# UNDER the payload (not a new adapter arg) so the router's generic
# `deliver(kind, payload)` dispatch needs no signature change — the adapter
# contract `adapter(target, payload) -> dict` is preserved. Stripped from the
# text render so it never appears in the visible message.
TELEGRAM_CONTROL_KEY = "_telegram"

# Reserved payload key for a pre-composed HTML string (Kanban #2721). When
# present and non-empty, `send_telegram` uses it as the message text with
# `parse_mode=HTML` instead of the plain-text serialisation path.
TELEGRAM_HTML_KEY = "_html"

# Telegram hard cap on callback_data is 64 BYTES (UTF-8). We keep the encoding
# ASCII so byte-length == char-length. Format: "g:<gate_id>:<option>".
CALLBACK_DATA_MAX_BYTES = 64
_CALLBACK_PREFIX = "g"  # marks a gate-resolve callback; future kinds can add prefixes.


def format_telegram_html(
    *,
    status: str,
    title: str,
    context: list[str] | None = None,
) -> str:
    """Compose a Telegram HTML-mode message string.

    Layout (parse_mode=HTML):
      Line 1: <status> — plain escaped tag, e.g. "Gate · decision"
      Line 2: <b><title></b> — task title, bold
      Blank line, then each context line escaped, one per line.

    Every dynamic piece is HTML-escaped (& < >) BEFORE tag wrapping so a
    title like "<b>auth</b> & login" renders as literal text, never markup.
    Length cap: Telegram's hard limit is 4096 chars; when the assembled string
    exceeds that, context lines are trimmed (character-level) to fit.
    # shortcut: trims the context block as a whole string (not line-by-line);
    # ceiling = context is the only variable-length part; a per-line trim would
    # be marginally cleaner but adds complexity for a rare edge.
    """
    esc_status = _html_mod.escape(status, quote=False)
    esc_title = _html_mod.escape(title, quote=False)
    header = f"{esc_status}\n<b>{esc_title}</b>"

    if not context:
        text = header
        if len(text) > 4096:
            text = text[: 4093] + "..."
        return text

    esc_context = "\n".join(_html_mod.escape(line, quote=False) for line in context)
    text = f"{header}\n\n{esc_context}"
    if len(text) > 4096:
        budget = 4096 - len(header) - 5  # 5 = "\n\n" + "..."
        if budget > 0:
            esc_context = esc_context[:budget] + "..."
        else:
            esc_context = "..."
        text = f"{header}\n\n{esc_context}"
    return text


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
        # The inline-button control block is metadata, not visible text — skip
        # it so it never renders into the chat message (Kanban #2565).
        if key == TELEGRAM_CONTROL_KEY:
            continue
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
    # All keys were control-only (e.g. a bare buttons payload) — Telegram
    # rejects an empty `text`, so emit a minimal placeholder.
    if not parts:
        return "(no message)"
    text = "\n".join(parts)
    if len(text) > 4096:
        text = text[: 4096 - 3] + "..."
    return text


def encode_callback_data(gate_id: int, option: str) -> str:
    """Encode an inline-button callback as `g:<gate_id>:<option>` (<=64 bytes).

    Telegram caps `callback_data` at 64 bytes; the inbound poller round-trips
    this with `decode_callback_data`. `option` is the answer the tap means
    (e.g. 'approve' / 'reject' / an option id). We keep it ASCII so byte-length
    equals char-length, and TRUNCATE an over-long option rather than raise — a
    truncated option is still a deterministic token the poller forwards as the
    answer (the gate's resume_context records the exact bytes received). The
    caller (notify_gate) should keep option ids short; this is the safety net.
    """
    prefix = f"{_CALLBACK_PREFIX}:{int(gate_id)}:"
    budget = CALLBACK_DATA_MAX_BYTES - len(prefix.encode("utf-8"))
    if budget <= 0:
        # Pathological: gate_id alone overflows. Return the prefix sans option
        # truncated to the cap — decode still yields the gate_id + empty option.
        return prefix.encode("utf-8")[:CALLBACK_DATA_MAX_BYTES].decode("ascii", "ignore")
    opt_str = str(option)
    # Encode → slice on byte boundary → decode back. If the slice falls in the
    # middle of a multi-byte character, re-encode the decoded candidate and trim
    # one character at a time until it fits — guarantees clean round-trip.
    opt_bytes = opt_str.encode("utf-8")
    if len(opt_bytes) <= budget:
        return prefix + opt_str
    truncated = opt_bytes[:budget].decode("utf-8", "ignore")
    # Verify the round-trip: if decode dropped a partial char, the re-encode may
    # still fit; otherwise trim one Unicode char until it does (loop runs ≤4×).
    while truncated and len(truncated.encode("utf-8")) > budget:
        truncated = truncated[:-1]
    return prefix + truncated


def decode_callback_data(data: str) -> dict[str, Any] | None:
    """Parse `g:<gate_id>:<option>` -> {'gate_id': int, 'option': str}.

    Returns None when `data` is not a gate-resolve callback (wrong prefix /
    malformed / non-int gate_id) so the poller can ignore foreign callbacks
    without raising. `option` may contain ':' — only the first two ':' delimit
    the prefix and gate_id; the remainder is the option verbatim.
    """
    if not isinstance(data, str):
        return None
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != _CALLBACK_PREFIX:
        return None
    # FIX 4 (#2685): CVE-2020-10735-style int-parse cost guard — an attacker
    # can craft a callback_data with a very long digit string in parts[1] to
    # force CPython's O(n²) big-int conversion. Reject before int() if the
    # gate_id field exceeds 20 characters (max int64 is 19 digits + optional
    # sign, so 20 is a safe ceiling with no false-positives for real gate ids).
    if len(parts[1]) > 20:
        return None
    try:
        gate_id = int(parts[1])
    except (TypeError, ValueError):
        return None
    return {"gate_id": gate_id, "option": parts[2]}


def _build_reply_markup(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Build a Telegram `reply_markup` inline keyboard from the payload control
    block, or None when the payload carries no buttons (plain-text path).

    Expected shape (built by `notify_gate.build_gate_notify_payload`):
        payload["_telegram"]["buttons"] = [
            {"text": "Approve", "callback_data": "g:42:approve"},
            {"text": "Reject",  "callback_data": "g:42:reject"},
        ]
    Each button becomes its own ROW (one button per row) — readable on a phone
    and unambiguous to tap. A button missing `text` or `callback_data`, or whose
    callback_data exceeds 64 bytes, is skipped defensively (the adapter must not
    400 the whole send on one malformed button). Returns None when no valid
    button survives so the caller omits reply_markup entirely.
    """
    control = payload.get(TELEGRAM_CONTROL_KEY)
    if not isinstance(control, dict):
        return None
    buttons = control.get("buttons")
    if not isinstance(buttons, list) or not buttons:
        return None
    rows: list[list[dict[str, str]]] = []
    for btn in buttons:
        if not isinstance(btn, dict):
            continue
        text = btn.get("text")
        cb = btn.get("callback_data")
        if not text or not isinstance(cb, str):
            continue
        if len(cb.encode("utf-8")) > CALLBACK_DATA_MAX_BYTES:
            logger.warning("send_telegram: skip button cb>64B: %r", cb[:80])
            continue
        rows.append([{"text": str(text), "callback_data": cb}])
    if not rows:
        return None
    return {"inline_keyboard": rows}


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
    # Kanban #2721: HTML path — when the payload carries a pre-composed HTML
    # string under TELEGRAM_HTML_KEY, use it with parse_mode=HTML; otherwise
    # fall through to the original plain-text serialisation (unchanged).
    html_text = payload.get(TELEGRAM_HTML_KEY)
    if html_text and isinstance(html_text, str):
        body: dict[str, Any] = {
            "chat_id": str(chat_id),
            "text": html_text,
            "parse_mode": "HTML",
        }
    else:
        body = {
            "chat_id": str(chat_id),
            "text": _serialize_payload_for_text(payload),
        }
    # Kanban #2565: attach an inline keyboard when the payload carries buttons.
    # Both the HTML and plain-text paths attach reply_markup identically.
    reply_markup = _build_reply_markup(payload)
    if reply_markup is not None:
        body["reply_markup"] = reply_markup

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=timeout)
    try:
        try:
            resp = await client.post(url, json=body)
        except httpx.RequestError as exc:
            # Log only the exception type — the exc repr carries the request URL
            # which contains the bot token; never log it.
            logger.warning(
                "send_telegram: request_error chat_id=%s err=%s",
                chat_id,
                type(exc).__name__,
            )
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
        except ValueError:
            logger.warning("send_telegram: json_decode_error chat_id=%s", chat_id)
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
