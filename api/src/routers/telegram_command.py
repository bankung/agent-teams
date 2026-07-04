"""HTTP route for the Telegram command surface — Kanban #2778 (Phase 1 of
`telegram-command-surface-2720.md`).

  POST /api/telegram/command

D1 (poller stays DUMB): ALL parse / authz / dispatch / idempotency lives
here. `telegram_poller.py` only chat-id-verifies the sender, forwards the raw
message text, and relays `reply_text` back via `sendMessage` — it never
inspects the shape of a command itself.

D2 (per-chat sticky project): `/project <name>` resolves via the existing
`GET /api/projects/by-name/{name}` lookup path (querying the SAME table
directly here, in-process) and stores chat_id -> project_id in
`telegram_chat_state`. We deliberately do NOT read
`_runtime/lead_project_id.txt` — that file is a session-write signal for
notification routing, not a command target (see the model docstring).

D4/AC4 (update_id dedup): a monotonic per-chat watermark. `update_id <=` the
stored watermark is a no-op (no re-dispatch) — checked BEFORE dispatch.

D5 (no new abstraction): each verb handler calls the EXISTING service/ORM
layer directly (the same query a REST handler would run) — no verb-layer
indirection, no MCP server.

Phase 1 verb catalog (D6): `/project`, `/projects`, `/tasks`, `/task <id>`,
`/gates`. Deny-by-default: unmatched text -> a polite "unknown command" reply
listing the available verbs, and NOTHING is dispatched. Phase 2/3 verbs
(`/new /approve /deny /hold /run`, halt) are NOT built here.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import RecordStatus, TaskStatus
from src.db import get_session
from src.middleware.rate_limit import limiter
from src.models.project import Project
from src.models.task import Task
from src.models.task_gate import TaskGate
from src.models.telegram_chat_state import TelegramChatState
from src.schemas.telegram_command import TelegramCommandRequest, TelegramCommandResponse

logger = logging.getLogger("api.telegram_command")

router = APIRouter(tags=["telegram-command"])

# Deny-by-default verb list — echoed verbatim in the "unknown command" reply
# so an operator always sees what IS available. Keep in lockstep with the
# dispatcher dict below (order = display order).
_PHASE1_VERBS: list[str] = ["/project <name>", "/projects", "/tasks", "/task <id>", "/gates"]

_UNKNOWN_COMMAND_REPLY = (
    "Unknown command. Available:\n" + "\n".join(_PHASE1_VERBS)
)
_NO_STICKY_PROJECT_REPLY = "No project set for this chat. Run /project <name> first."


# ---------------------------------------------------------------------------
# Chat-state helpers (D2 sticky project + D4 dedup watermark)
# ---------------------------------------------------------------------------


async def _get_or_create_chat_state(
    session: AsyncSession, chat_id: str
) -> TelegramChatState:
    """Fetch the chat's state row, creating a bare (no sticky project) one on
    first contact. Not yet committed — caller commits after mutating."""
    state = await session.get(TelegramChatState, chat_id)
    if state is None:
        state = TelegramChatState(chat_id=chat_id)
        session.add(state)
    return state


# ---------------------------------------------------------------------------
# Verb handlers — each takes (session, state, args) -> reply_text.
# `state.project_id` is the resolved sticky project (may be None); handlers
# that require one check it themselves (D2 AC2) rather than relying on a
# shared guard, since `/project` itself must run WITHOUT one set.
# ---------------------------------------------------------------------------

VerbHandler = Callable[[AsyncSession, TelegramChatState, str], Awaitable[str]]


async def _verb_project(session: AsyncSession, state: TelegramChatState, args: str) -> str:
    """`/project <name>` — set the chat's sticky project (D2)."""
    name = args.strip()
    if not name:
        return "Usage: /project <name>"
    proj = (
        await session.execute(
            select(Project).where(
                Project.name == name, Project.status == RecordStatus.ACTIVE
            )
        )
    ).scalar_one_or_none()
    if proj is None:
        return f"Project {name!r} not found."
    state.project_id = proj.id
    return f"Sticky project set: {proj.name} (id={proj.id})"


async def _verb_projects(session: AsyncSession, state: TelegramChatState, args: str) -> str:
    """`/projects` — GET /api/projects equivalent: list active projects."""
    rows = (
        await session.execute(
            select(Project.id, Project.name, Project.team)
            .where(Project.status == RecordStatus.ACTIVE)
            # Recent-first (id is monotonic, no created_at needed): a chat
            # quick-list capped at 50 with no pagination is more useful
            # showing newest projects than alphabetical (Kanban #2793).
            .order_by(Project.id.desc())
            .limit(50)
        )
    ).all()
    if not rows:
        return "No active projects."
    lines = [f"#{pid} {name} ({team})" for pid, name, team in rows]
    return "Active projects:\n" + "\n".join(lines)


async def _verb_tasks(session: AsyncSession, state: TelegramChatState, args: str) -> str:
    """`/tasks` — the sticky project's actionable (pending) tasks, compact.

    Dispatches the same predicate as `GET /api/tasks?pending=true` (D6,
    routers/tasks.py:449-466): process_status != DONE(5) AND != CANCELLED(6)
    (Kanban #854), soft-delete-active rows only, AND is_active=true (Kanban
    #1240 — excludes auto-archived rows), id ASC, capped.
    """
    if state.project_id is None:
        return _NO_STICKY_PROJECT_REPLY
    rows = (
        await session.execute(
            select(Task.id, Task.title, Task.process_status)
            .where(
                Task.project_id == state.project_id,
                Task.status == RecordStatus.ACTIVE,
                Task.is_active.is_(True),
                Task.process_status != TaskStatus.DONE,
                Task.process_status != TaskStatus.CANCELLED,
            )
            .order_by(Task.id.asc())
            .limit(20)
        )
    ).all()
    if not rows:
        return "No pending tasks."
    lines = [f"#{tid} [ps={ps}] {title}" for tid, title, ps in rows]
    return "Pending tasks:\n" + "\n".join(lines)


async def _verb_task(session: AsyncSession, state: TelegramChatState, args: str) -> str:
    """`/task <id>` — title/status/AC summary for one task."""
    if state.project_id is None:
        return _NO_STICKY_PROJECT_REPLY
    raw = args.strip()
    if not raw.isdigit():
        return "Usage: /task <id>"
    task_id = int(raw)
    task = await session.get(Task, task_id)
    if task is None or task.project_id != state.project_id:
        return f"Task #{task_id} not found in the sticky project."
    ac = task.acceptance_criteria or []
    ac_summary = f"{len(ac)} AC item(s)" if ac else "no AC"
    return (
        f"#{task.id} {task.title}\n"
        f"status: ps={task.process_status}\n"
        f"{ac_summary}"
    )


async def _verb_gates(session: AsyncSession, state: TelegramChatState, args: str) -> str:
    """`/gates` — pending operator gates for the sticky project (open task_gates
    rows only; Phase 1 keeps this read simple — the legacy-union shape from
    `GET /api/operator-gates/pending` is a Phase-2+ nicety, not required by D6's
    Phase-1 verb table).
    """
    if state.project_id is None:
        return _NO_STICKY_PROJECT_REPLY
    rows = (
        await session.execute(
            select(TaskGate.id, TaskGate.task_id, TaskGate.gate_tier, Task.title)
            .join(Task, TaskGate.task_id == Task.id)
            .where(
                Task.project_id == state.project_id,
                Task.status == RecordStatus.ACTIVE,
                TaskGate.status == "open",
            )
            .order_by(TaskGate.created_at.asc())
            .limit(20)
        )
    ).all()
    if not rows:
        return "No pending gates."
    lines = [
        f"gate#{gid} task#{tid} [{tier}] {title}" for gid, tid, tier, title in rows
    ]
    return "Pending gates:\n" + "\n".join(lines)


# Verb -> handler. A dict, not a class hierarchy (D5 — no new abstraction).
_DISPATCH: dict[str, VerbHandler] = {
    "/project": _verb_project,
    "/projects": _verb_projects,
    "/tasks": _verb_tasks,
    "/task": _verb_task,
    "/gates": _verb_gates,
}


def _split_verb(text: str) -> tuple[str, str]:
    """Split raw text into (verb, rest). '/task 42' -> ('/task', '42').
    A bare verb with no trailing text -> args=''. Case-sensitive (Telegram
    slash-commands are conventionally lowercase; an uppercase variant is
    deny-by-default, matching D3's explicit-whitelist posture)."""
    stripped = text.strip()
    if not stripped:
        return "", ""
    parts = stripped.split(maxsplit=1)
    verb = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    return verb, rest


@router.post("/telegram/command", response_model=TelegramCommandResponse)
@limiter.limit("30/minute")
async def telegram_command(
    request: Request,  # required by slowapi key_func — not used in handler body
    payload: TelegramCommandRequest,
    session: AsyncSession = Depends(get_session),
) -> TelegramCommandResponse:
    """POST /api/telegram/command — the single Telegram command entry point.

    Order of operations (D1/D2/D4):
      1. Load/create the chat's state row.
      2. AC4 dedup: `update_id <=` the stored watermark -> reply without
         re-dispatch (idempotent no-op on a redelivered update).
      3. Deny-by-default parse: unmatched verb -> "unknown command" reply,
         nothing dispatched, watermark still advances (a garbage command is
         still a "processed" update — it must not be redelivered forever).
      4. Dispatch to the matched verb handler.
      5. Persist: watermark advance + any state mutation (e.g. `/project`'s
         project_id write) commit together, atomically.

    No X-Project-Id header — this endpoint is chat-scoped (the D2 sticky
    project on the chat state row IS the targeting mechanism), not
    session-scoped like the Lead-bound `/api/tasks*` surface.
    """
    state = await _get_or_create_chat_state(session, payload.chat_id)

    # --- AC4: update_id dedup watermark (runs BEFORE dispatch) -------------
    # shortcut: read-check-write with no row lock. Safe under the CURRENT
    # single-producer assumption — exactly one telegram_poller.py process ever
    # calls this endpoint (Telegram itself enforces one getUpdates consumer
    # per bot token, HTTP 409 on a second concurrent long-poll), so no two
    # requests for the same chat_id can race each other. Ceiling: this breaks
    # if a second producer ever lands (e.g. the MCP sibling named in the
    # design doc's "Goal" section calling this endpoint directly, bypassing
    # the poller). Upgrade path: `select(TelegramChatState).where(chat_id=...)
    # .with_for_update()` on the state row, mirroring task_gates.py
    # resolve_gate's gate-row lock (task_gates.py:258-264) — same
    # read-check-write race, same fix shape.
    if state.last_update_id is not None and payload.update_id <= state.last_update_id:
        logger.info(
            "telegram_command: dedup no-op chat=%s update_id=%d watermark=%d",
            payload.chat_id,
            payload.update_id,
            state.last_update_id,
        )
        return TelegramCommandResponse(
            reply_text="(duplicate update — already processed)",
            dispatched=False,
            verb=None,
        )

    verb, args = _split_verb(payload.text)
    handler = _DISPATCH.get(verb)

    if handler is None:
        reply_text = _UNKNOWN_COMMAND_REPLY
        dispatched = False
        matched_verb = None
    else:
        reply_text = await handler(session, state, args)
        dispatched = True
        matched_verb = verb

    # Advance the watermark regardless of match (an unknown command is still
    # a processed update — must not be redelivered forever) and persist any
    # handler-side mutation (e.g. /project's project_id write) atomically.
    state.last_update_id = payload.update_id
    await session.commit()

    logger.info(
        "telegram_command: chat=%s update_id=%d verb=%s dispatched=%s",
        payload.chat_id,
        payload.update_id,
        matched_verb,
        dispatched,
    )

    return TelegramCommandResponse(
        reply_text=reply_text, dispatched=dispatched, verb=matched_verb
    )
