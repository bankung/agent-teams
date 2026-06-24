"""Inbound Telegram poller for async-HITL gate resolution (Kanban #2565).

A DUMB local loop — NO AI / NO LLM. It long-polls Telegram `getUpdates`
(purely OUTBOUND — no inbound port, no public URL, no Tailscale), and when the
OPERATOR taps an inline approve/reject button on a gate notification, it resolves
that gate via the agent-teams API. The AI never runs here; this process only
moves an operator's tap into a durable DB write (through the API).

Run it:
    python -m scripts.telegram_poller

Required environment (read at startup):
    TELEGRAM_BOT_TOKEN          the bot's API token (same bot that sent the cards)
    TELEGRAM_OPERATOR_CHAT_ID   numeric chat id — the SECURITY LOCK. A
                                callback_query / message is processed ONLY when
                                from.id == this value; everything else is
                                silently ignored. Unset -> the poller refuses to
                                resolve anything (logs + idles).
Optional environment:
    AGENT_TEAMS_API_BASE        API base URL (default http://localhost:8456)
    TELEGRAM_POLLER_PROJECT_ID  X-Project-Id for the resolve call. Default: read
                                from <repo_root>/_runtime/lead_project_id.txt (the
                                bound project). v1 assumes the operator runs one
                                project at a time (the session-binding model) so
                                a gate's task is in that project; the resolve
                                endpoint 400s on a project mismatch (logged, not
                                fatal — the poller keeps polling).
    REPO_ROOT                   repo root (default: the repo this file lives in)
    TELEGRAM_POLL_TIMEOUT       getUpdates long-poll timeout seconds (default 25)

Offset persistence:
    The Telegram update offset is persisted to <repo_root>/_runtime/telegram_offset.txt
    after each processed batch. A restart resumes from there so updates are
    neither re-processed nor lost across restarts.

SINGLE INSTANCE ONLY: Telegram returns HTTP 409 (Conflict) if two getUpdates
long-polls run against the same bot token concurrently. Run exactly one poller
per bot token. (If you ALSO set a webhook on the bot, getUpdates is disabled —
this poller assumes no webhook is set.)

Callback contract (set by services/notify_gate.py via the adapter):
    callback_data == "g:<gate_id>:<option>"  (<=64 bytes)
On an allowed callback:
    1. decode -> {gate_id, option}
    2. POST <api>/api/task-gates/<gate_id>/resolve
         body {answer: option, provenance: "telegram", answered_by: <chat_id>}
         header X-Project-Id: <project_id>
    3. answerCallbackQuery to ack the tap (clears the button's spinner). A
       resolve 409 (already-answered) is handled gracefully: ack with an
       "already handled" toast, do NOT retry.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

# When run as `python -m scripts.telegram_poller` from api/, `src` is importable.
from src.services.notify_telegram import (
    TELEGRAM_API_BASE,
    TELEGRAM_ENV_TOKEN,
    decode_callback_data,
)

logger = logging.getLogger("scripts.telegram_poller")

# --- env var names (single source of truth) --------------------------------
ENV_OPERATOR_CHAT_ID = "TELEGRAM_OPERATOR_CHAT_ID"
ENV_API_BASE = "AGENT_TEAMS_API_BASE"
ENV_PROJECT_ID = "TELEGRAM_POLLER_PROJECT_ID"
ENV_REPO_ROOT = "REPO_ROOT"
ENV_POLL_TIMEOUT = "TELEGRAM_POLL_TIMEOUT"

DEFAULT_API_BASE = "http://localhost:8456"
DEFAULT_POLL_TIMEOUT = 25  # seconds — Telegram long-poll hold
_OFFSET_FILENAME = "telegram_offset.txt"
_LEAD_PROJECT_FILENAME = "lead_project_id.txt"


def _repo_root() -> Path:
    """Resolve the repo root: REPO_ROOT env, else two parents up from this file
    (api/scripts/telegram_poller.py -> api/ -> repo root)."""
    env = os.environ.get(ENV_REPO_ROOT, "").strip()
    if env:
        p = Path(env)
        if not p.is_absolute():
            logger.warning(
                "telegram_poller: %s='%s' is not absolute; ignoring (falling back to default)",
                ENV_REPO_ROOT,
                env,
            )
        else:
            return p
    return Path(__file__).resolve().parents[2]


def _runtime_dir() -> Path:
    return _repo_root() / "_runtime"


def read_offset(offset_path: Path) -> int:
    """Read the persisted update offset (0 when absent / unparseable)."""
    try:
        raw = offset_path.read_text(encoding="utf-8").strip()
        return int(raw) if raw else 0
    except (OSError, ValueError):
        return 0


def write_offset(offset_path: Path, offset: int) -> None:
    """Persist the next offset. Best-effort — a write failure logs but does not
    stop the loop (worst case: a few updates reprocessed after a crash, which
    the gate stale-reject 409 makes idempotent)."""
    try:
        offset_path.parent.mkdir(parents=True, exist_ok=True)
        offset_path.write_text(str(offset), encoding="utf-8")
    except OSError as exc:
        logger.warning("telegram_poller: offset write failed: %r", exc)


def resolve_project_id() -> str | None:
    """Resolve the X-Project-Id for resolve calls.

    Priority: TELEGRAM_POLLER_PROJECT_ID env -> _runtime/lead_project_id.txt.
    Returns None when neither is available (the poller still acks taps but logs
    that it cannot resolve without a project id).
    """
    env = os.environ.get(ENV_PROJECT_ID, "").strip()
    if env:
        return env
    try:
        raw = (_runtime_dir() / _LEAD_PROJECT_FILENAME).read_text(encoding="utf-8").strip()
        return raw or None
    except OSError:
        return None


def _api_url(base: str, gate_id: int) -> str:
    return f"{base.rstrip('/')}/api/task-gates/{gate_id}/resolve"


def resolve_gate_via_api(
    client: httpx.Client,
    *,
    api_base: str,
    project_id: str,
    gate_id: int,
    option: str,
    answered_by: str,
) -> dict[str, Any]:
    """POST the resolve endpoint. Returns a small status dict; never raises.

    {"status": "resolved"|"already"|"error", "http": int|None, "detail": str}
      - resolved : 200 (the gate was open and is now answered).
      - already  : 409 (stale-reject — already answered/cancelled/expired). The
                   caller acks with an "already handled" toast and does NOT retry.
      - error    : anything else (network, 4xx/5xx) — logged; the offset still
                   advances so we don't wedge on a poison update.
    """
    url = _api_url(api_base, gate_id)
    body = {"answer": option, "provenance": "telegram", "answered_by": answered_by}
    headers = {"X-Project-Id": str(project_id)}
    try:
        resp = client.post(url, json=body, headers=headers)
    except httpx.RequestError as exc:
        logger.warning("resolve_gate: request_error gate=%d err=%s", gate_id, type(exc).__name__)
        return {"status": "error", "http": None, "detail": f"request_error:{type(exc).__name__}"}
    if resp.status_code == 200:
        return {"status": "resolved", "http": 200, "detail": "ok"}
    if resp.status_code == 409:
        return {"status": "already", "http": 409, "detail": "already_resolved"}
    logger.warning("resolve_gate: gate=%d http=%d", gate_id, resp.status_code)
    logger.debug("resolve_gate: gate=%d http=%d body=%.200s", gate_id, resp.status_code, resp.text or "")
    snippet = (resp.text or "")[:200]
    return {"status": "error", "http": resp.status_code, "detail": snippet}


def answer_callback(
    client: httpx.Client,
    *,
    token: str,
    callback_query_id: str,
    text: str | None = None,
) -> None:
    """Ack a callback_query (clears the inline-button spinner). Best-effort —
    a failed ack only leaves the spinner; the resolve already happened."""
    url = f"{TELEGRAM_API_BASE}/bot{token}/answerCallbackQuery"
    payload: dict[str, Any] = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text[:200]  # Telegram caps toast text at 200 chars.
    try:
        client.post(url, json=payload)
    except httpx.RequestError as exc:
        # Log only the exception type — exc repr carries the token-bearing URL.
        logger.warning("answer_callback: request_error err=%s", type(exc).__name__)


def _from_id_of(update: dict[str, Any]) -> Any:
    """Extract the sender's numeric id from a callback_query (preferred) or a
    plain message. Returns None when neither shape carries a from.id."""
    cq = update.get("callback_query")
    if isinstance(cq, dict):
        frm = cq.get("from")
        if isinstance(frm, dict):
            return frm.get("id")
    msg = update.get("message")
    if isinstance(msg, dict):
        frm = msg.get("from")
        if isinstance(frm, dict):
            return frm.get("id")
    return None


def process_update(
    client: httpx.Client,
    update: dict[str, Any],
    *,
    token: str,
    operator_chat_id: str,
    api_base: str,
    project_id: str | None,
) -> dict[str, Any]:
    """Process ONE update. Pure-ish (all IO via the injected client) so it is
    unit-testable with a mocked transport. Returns a status dict describing what
    happened (for tests + logging); never raises.

    chat-id LOCK: a callback/message whose from.id != operator_chat_id is
    IGNORED (status='ignored_foreign') — the security boundary of the poller.
    """
    from_id = _from_id_of(update)
    # Compare as strings so an env value ("12345") matches Telegram's int id.
    if from_id is None or str(from_id) != str(operator_chat_id):
        return {"action": "ignored_foreign", "from_id": from_id}

    cq = update.get("callback_query")
    if not isinstance(cq, dict):
        # An allowed plain message (not a button tap) — nothing to resolve.
        return {"action": "ignored_non_callback"}

    callback_query_id = cq.get("id")
    decoded = decode_callback_data(cq.get("data"))
    if decoded is None:
        if callback_query_id:
            answer_callback(client, token=token, callback_query_id=callback_query_id)
        return {"action": "ignored_bad_callback_data", "data": cq.get("data")}

    gate_id = decoded["gate_id"]
    option = decoded["option"]

    if not project_id:
        # We can ack the tap but cannot resolve without an X-Project-Id.
        if callback_query_id:
            answer_callback(
                client,
                token=token,
                callback_query_id=callback_query_id,
                text="No project bound; resolve skipped.",
            )
        return {"action": "no_project", "gate_id": gate_id}

    result = resolve_gate_via_api(
        client,
        api_base=api_base,
        project_id=project_id,
        gate_id=gate_id,
        option=option,
        answered_by=str(operator_chat_id),
    )
    if callback_query_id:
        if result["status"] == "resolved":
            toast = f"Recorded: {option}"
        elif result["status"] == "already":
            toast = "Already handled."
        else:
            toast = "Resolve failed; check the terminal."
        answer_callback(client, token=token, callback_query_id=callback_query_id, text=toast)

    return {"action": "resolved", "gate_id": gate_id, "option": option, "result": result}


def get_updates(
    client: httpx.Client, *, token: str, offset: int, timeout: int
) -> list[dict[str, Any]]:
    """Long-poll getUpdates from `offset`. Returns the result list (empty on
    timeout / error). `timeout` is Telegram's server-side hold; the client
    timeout is set a few seconds longer so the HTTP read doesn't fire first."""
    url = f"{TELEGRAM_API_BASE}/bot{token}/getUpdates"
    params = {"offset": offset, "timeout": timeout}
    try:
        resp = client.get(url, params=params, timeout=timeout + 5)
    except httpx.RequestError as exc:
        # Log only the exception type — the exc repr carries the token-bearing URL.
        logger.warning("get_updates: request_error err=%s", type(exc).__name__)
        return []
    if resp.status_code != 200:
        snippet = (resp.text or "")[:200]
        # 409 here == another getUpdates consumer (or a webhook) on this token.
        logger.warning("get_updates: http=%d body=%s", resp.status_code, snippet)
        return []
    try:
        parsed = resp.json()
    except ValueError:
        logger.warning("get_updates: json decode error")
        return []
    if not parsed.get("ok"):
        logger.warning("get_updates: telegram ok=false desc=%s", parsed.get("description"))
        return []
    result = parsed.get("result")
    return result if isinstance(result, list) else []


def run() -> int:
    """Entry point. Blocks forever long-polling Telegram. Returns a non-zero
    exit code on fatal misconfiguration: missing TELEGRAM_BOT_TOKEN (exit 1)
    OR missing TELEGRAM_OPERATOR_CHAT_ID (exit 1 — security lock required)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    token = os.environ.get(TELEGRAM_ENV_TOKEN, "").strip()
    if not token:
        logger.error(
            "telegram_poller: %s is unset — cannot poll. Set it in .env and rerun.",
            TELEGRAM_ENV_TOKEN,
        )
        return 1

    operator_chat_id = os.environ.get(ENV_OPERATOR_CHAT_ID, "").strip()
    if not operator_chat_id:
        # Refuse to resolve anything without the chat-id lock (security). We do
        # NOT poll in this state — an unlocked poller could resolve on anyone's
        # tap. Operator must set the lock.
        logger.error(
            "telegram_poller: %s is unset — refusing to run without the chat-id "
            "lock (would resolve on any sender's tap). Set it in .env and rerun.",
            ENV_OPERATOR_CHAT_ID,
        )
        return 1

    api_base = os.environ.get(ENV_API_BASE, DEFAULT_API_BASE).strip() or DEFAULT_API_BASE
    try:
        poll_timeout = int(os.environ.get(ENV_POLL_TIMEOUT, str(DEFAULT_POLL_TIMEOUT)))
    except ValueError:
        poll_timeout = DEFAULT_POLL_TIMEOUT
    project_id = resolve_project_id()
    offset_path = _runtime_dir() / _OFFSET_FILENAME
    offset = read_offset(offset_path)

    logger.info(
        "telegram_poller: starting (api_base=%s project_id=%s offset=%d timeout=%ds). "
        "Single-instance only (Telegram 409 on concurrent getUpdates).",
        api_base,
        project_id,
        offset,
        poll_timeout,
    )

    # One long-lived client for both Telegram + the local API (different hosts,
    # same connection-pool is fine). Per-request timeouts are set at call sites.
    with httpx.Client() as client:
        while True:
            loop_start = time.monotonic()
            updates = get_updates(
                client, token=token, offset=offset, timeout=poll_timeout
            )
            if not updates:
                # A real long-poll hold returns empty after ~poll_timeout seconds
                # (normal timeout) — loop straight back, no sleep needed.
                # A fast return (error / 409) completes in <2 s; sleep then to
                # avoid a busy-spin hammering the endpoint.
                if time.monotonic() - loop_start < 2.0:
                    time.sleep(1)
                continue
            # Re-resolve the bound project each batch so a mid-run project switch
            # (Lead rewrites lead_project_id.txt) is picked up without a restart.
            project_id = resolve_project_id()
            for update in updates:
                update_id = update.get("update_id")
                try:
                    outcome = process_update(
                        client,
                        update,
                        token=token,
                        operator_chat_id=operator_chat_id,
                        api_base=api_base,
                        project_id=project_id,
                    )
                    logger.info("telegram_poller: update=%s -> %s", update_id, outcome.get("action"))
                except Exception:  # noqa: BLE001 — one bad update must not kill the loop.
                    logger.exception("telegram_poller: update=%s processing error", update_id)
                # Advance offset past this update regardless of outcome (a poison
                # update must not wedge the loop; resolve is idempotent via 409).
                if isinstance(update_id, int):
                    offset = update_id + 1
            write_offset(offset_path, offset)


if __name__ == "__main__":
    sys.exit(run())
