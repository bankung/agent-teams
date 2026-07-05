"""Pydantic schemas for the Telegram command surface (Kanban #2778, Phase 1).

The wire contract for `POST /api/telegram/command` per
`telegram-command-surface-2720.md` D1 (dumb poller forwards raw text; ALL
parse/authz/dispatch/dedup lives here) and D6 (verb catalog v1: read verbs +
`/project` sticky-set).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# Per-field size ceiling (serialised JSON bytes) — mirrors the #1115 /
# schemas/task_gate.py per-field cap discipline. A Telegram message is capped
# at 4096 chars by Telegram itself; this is a defensive belt for anything
# forwarded from a non-Telegram caller (tests, a future MCP sibling).
_TEXT_MAX_LEN = 4_096


class TelegramCommandRequest(BaseModel):
    """Request body for `POST /api/telegram/command`.

    Carries the RAW fields the dumb poller forwards from a Telegram `message`
    update — no parsing happens on the poller side (D1). `chat_id` is kept as
    a string (mirrors `notify_telegram`'s `str(chat_id)` posture — some
    Telegram chat ids exceed safe int ranges). `update_id` drives the AC4
    dedup watermark; `text` is the raw message text (e.g. "/tasks" or
    "/project agent-teams").
    """

    model_config = ConfigDict(extra="forbid")

    chat_id: str = Field(min_length=1, max_length=64)
    update_id: int = Field(ge=0)
    text: str = Field(default="", max_length=_TEXT_MAX_LEN)


class TelegramCommandResponse(BaseModel):
    """Response body for `POST /api/telegram/command`.

    `reply_text` is the ONLY thing the poller relays back to the chat
    (`sendMessage`, plain text) — the poller stays dumb and does not
    interpret `dispatched` / `verb`; those are for tests + logging.
    """

    reply_text: str
    dispatched: bool
    verb: str | None = None
