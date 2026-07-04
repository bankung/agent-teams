"""HTTP route for the Telegram command surface — Kanban #2778/#2779 (Phase 1 +
Phase 2 of `telegram-command-surface-2720.md`).

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
indirection, no MCP server. Phase 2 sharpens this for the safe_mutation verbs:
`/new`, `/approve`, `/deny`, and `/hold` call the EXISTING ROUTER FUNCTIONS
(`create_task`, `resolve_gate`, `update_task`) directly as in-process async
calls — not HTTP-to-self — so there is exactly ONE code path (validation,
row-locks, transactional writes, error mapping) shared with the REST surface.
Any `HTTPException` they raise is caught and rendered as a reply string (D6's
"never 500" requirement) rather than re-raised.

D3 (auth-per-verb — operator-proof mapping): the poller's chat-id lock
(`telegram_poller.process_update`: `from_id != operator_chat_id` ->
`ignored_foreign`, never forwarded) is the operator-identity boundary for
Telegram-originated traffic — a request that came THROUGH the poller has had
its sender proven to be the operator. The endpoint itself carries no authn of
its own (a direct localhost caller can POST any `chat_id`); it inherits the
platform's existing no-authn-on-localhost posture (same as `PATCH /api/tasks`
or the gate-resolve route a local caller can already hit directly). If this
endpoint is ever exposed off localhost, gate it with a shared secret BEFORE
that exposure (Phase-4 auth bucket). Verbs are additionally classified by
mutation risk (`_VERB_CLASS`):
  - 'read'          — Phase 1 verbs. No state mutation.
  - 'safe_mutation' — Phase 2 verbs (`/new /approve /deny /hold`). Execute
                       immediately under the chat-id lock — the lock is
                       treated as operator-equivalent for this whitelist
                       (mirrors the live `provenance=telegram` precedent
                       already accepted as operator proof by
                       `POST /api/task-gates/{id}/resolve`, gate #17/#2718).
  - 'destructive'   — NONE ship this phase. The dispatcher's deny-by-default
                       fallthrough IS the extension point: an unclassified
                       verb (including any future destructive one added to
                       `_DISPATCH` without a matching `_VERB_CLASS` entry)
                       is refused with a "needs confirm" placeholder rather
                       than executed — see the class-check in
                       `telegram_command` (below `_DISPATCH`/`_VERB_CLASS`)
                       + `_DESTRUCTIVE_CONFIRM_STUB_REPLY`. A real destructive
                       verb will need a second `CONFIRM <token>` turn; no
                       token storage is built now (YAGNI — nothing consumes
                       it yet), only the deny path.
Deny-by-default (verb classification): a verb string that is not a key in
`_DISPATCH` is unknown regardless of class (existing Phase 1 behavior); a verb
that IS in `_DISPATCH` but has no `_VERB_CLASS` entry is a bug-guard (denied
defensively, never executed) — this can only happen if a future verb is added
to one dict and not the other.

Phase 1 verb catalog (D6): `/project`, `/projects`, `/tasks`, `/task <id>`,
`/gates` (class='read').
Phase 2 verb catalog (D6): `/new <text>`, `/approve <gate>`, `/deny <gate>`,
`/hold <id>` (class='safe_mutation').
Phase 3 verb catalog (Kanban #2780): `/run <id>` (run-a-specific-id-now) and
`/halt <id>` (cooperatively halt a running task), both class='safe_mutation',
each dispatching to a DEDICATED new router function (`run_task_now` /
`halt_task` in routers/tasks.py) as an in-process call — the two D6 "gap"
endpoints. run-now PRIMES a task into the picker-selectable state (it does not
spawn a session); halt sets process_status=8 the executor observes at its next
boundary (COOPERATIVE, not a hard kill).
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import RecordStatus, TaskInteractionKind, TaskRunMode, TaskStatus
from src.db import get_session
from src.middleware.rate_limit import limiter
from src.models.project import Project
from src.models.task import Task
from src.models.task_gate import TaskGate
from src.models.telegram_chat_state import TelegramChatState
from src.routers.task_gates import resolve_gate
from src.routers.tasks import create_task, halt_task, run_task_now, update_task
from src.schemas.task import TaskCreate, TaskUpdate
from src.schemas.task_gate import GateResolveRequest
from src.schemas.telegram_command import TelegramCommandRequest, TelegramCommandResponse
from src.services.ai_task_parser import (
    AiCallFailed,
    AiCallTimeout,
    AiUnparseable,
    MissingApiKey as AiMissingApiKey,
    parse_task_text,
)

logger = logging.getLogger("api.telegram_command")

router = APIRouter(tags=["telegram-command"])

# Deny-by-default verb list — echoed verbatim in the "unknown command" reply
# so an operator always sees what IS available. Keep in lockstep with the
# dispatcher dict below (order = display order).
_PHASE1_VERBS: list[str] = ["/project <name>", "/projects", "/tasks", "/task <id>", "/gates"]
_PHASE2_VERBS: list[str] = ["/new <text>", "/approve <gate id>", "/deny <gate id>", "/hold <id> <reason>"]
# Phase 3 (#2780): the two gap verbs — run-a-specific-id-now + cooperative halt.
_PHASE3_VERBS: list[str] = ["/run <id>", "/halt <id>"]
_ALL_VERBS: list[str] = _PHASE1_VERBS + _PHASE2_VERBS + _PHASE3_VERBS

_UNKNOWN_COMMAND_REPLY = (
    "Unknown command. Available:\n" + "\n".join(_ALL_VERBS)
)
_NO_STICKY_PROJECT_REPLY = "No project set for this chat. Run /project <name> first."
# D3 destructive-verb stub reply — no destructive verb is registered in
# _DISPATCH this phase, so this string is currently unreachable in practice;
# kept as the documented extension point (see module docstring) rather than
# building unused CONFIRM-token storage (YAGNI).
_DESTRUCTIVE_CONFIRM_STUB_REPLY = (
    "This action is destructive and requires confirmation "
    "(not yet available in this phase)."
)


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


# ---------------------------------------------------------------------------
# Phase 2 (Kanban #2779) — safe_mutation verbs. Each dispatches to the
# EXISTING router function as a direct in-process async call (D5) — the
# SAME validation / row-locks / transactional writes / error mapping the REST
# route uses, zero duplication. `HTTPException`s raised by the callee are
# caught in `telegram_command` (the dispatch try/except below, near the
# bottom of this module) and rendered as a reply string — a handler itself
# does not need its own try/except for that.
# ---------------------------------------------------------------------------


async def _verb_new(session: AsyncSession, state: TelegramChatState, args: str) -> str:
    """`/new <text>` — AI-parse then create, landing the task **manual /
    TODO** (D4 create != execute — AC1). Two existing endpoints chained:

      1. `parse_task_text` (the service backing `POST /api/tasks/ai-parse`,
         itself read-only — see its docstring) extracts a `ProposedTask`
         (title/description/task_type/priority/assigned_role/blocked_by).
         `ProposedTask` carries NO `run_mode` field at all (checked:
         schemas/ai_task.py) — the LLM never gets a vote on execution mode.
      2. The proposal is folded into a `TaskCreate` body with
         `run_mode=TaskRunMode.MANUAL` and `interaction_kind=WORK` set
         EXPLICITLY here (belt-and-suspenders — `TaskCreate.run_mode`
         already schema-defaults to MANUAL, so a caller that omitted the
         field would land manual anyway; setting it explicitly documents the
         guarantee at this call site and survives a future schema-default
         change). `create_task` (the exact function `POST /api/tasks`
         calls) does the actual INSERT — same kill/pause/budget gates, same
         IntegrityError translation, same everything.

    A freshly-created task therefore lands `process_status=TODO(1)` (the
    TaskCreate default) AND `run_mode='manual'` — invisible to the
    auto_pickup/auto_headless worker queues (routers/tasks.py:671,834 filter
    `run_mode IN (auto_pickup, auto_headless)`), so an untrusted inbound
    Telegram message can never silently drive autonomous work.
    """
    if state.project_id is None:
        return _NO_STICKY_PROJECT_REPLY
    text = args.strip()
    if not text:
        return "Usage: /new <text>"

    try:
        proposed = await parse_task_text(text=text, project_id=state.project_id)
    except AiMissingApiKey as exc:
        return f"/new failed: AI provider not configured ({exc})"
    except AiCallTimeout:
        return "/new failed: AI provider timeout"
    except AiUnparseable as exc:
        return f"/new failed: could not parse into a task ({exc})"
    except AiCallFailed as exc:
        return f"/new failed: AI provider error ({exc})"

    create_payload = TaskCreate(
        project_id=state.project_id,
        title=proposed.title,
        description=proposed.description,
        task_type=proposed.task_type,
        priority=proposed.priority,
        assigned_role=proposed.assigned_role,
        blocked_by=proposed.blocked_by,
        interaction_kind=TaskInteractionKind.WORK,
        run_mode=TaskRunMode.MANUAL,  # AC1/D4 — explicit, not just relying on the schema default
    )
    task = await create_task(create_payload, session_project_id=state.project_id, session=session)
    return f"/new -> created #{task.id} (TODO, run_mode={task.run_mode})"


async def _verb_approve(session: AsyncSession, state: TelegramChatState, args: str) -> str:
    """`/approve <gate id>` — resolve a gate with answer='approve' (D6)."""
    return await _resolve_gate_verb(session, state, args, answer="approve")


async def _verb_deny(session: AsyncSession, state: TelegramChatState, args: str) -> str:
    """`/deny <gate id>` — resolve a gate with answer='deny' (D6)."""
    return await _resolve_gate_verb(session, state, args, answer="deny")


async def _resolve_gate_verb(
    session: AsyncSession, state: TelegramChatState, args: str, *, answer: str
) -> str:
    """Shared body for `/approve` and `/deny` — both call the EXISTING
    `resolve_gate` router function (the one `POST
    /api/task-gates/{id}/resolve` calls) with `provenance='telegram'`. This
    is the EXACT precedent already live for gate #17/#2718 — the resolve
    endpoint already accepts telegram-provenance as operator proof; nothing
    new is invented here, just a second caller of the same function.
    `answered_by` carries the chat_id for the audit trail.
    """
    if state.project_id is None:
        return _NO_STICKY_PROJECT_REPLY
    raw = args.strip()
    if not raw.isdigit():
        return f"Usage: /{answer} <gate id>"
    gate_id = int(raw)
    resolve_payload = GateResolveRequest(
        answer=answer, provenance="telegram", answered_by=state.chat_id
    )
    resolved = await resolve_gate(
        _build_stub_request(),
        gate_id=gate_id,
        payload=resolve_payload,
        session_project_id=state.project_id,
        session=session,
    )
    return f"/{answer} -> gate#{resolved.gate_id} resolved (task#{resolved.task_id} ps={resolved.process_status})"


async def _verb_hold(session: AsyncSession, state: TelegramChatState, args: str) -> str:
    """`/hold <id> <reason>` — soft hold: task STAYS `process_status=TODO(1)`
    with `status_change_reason` set to the note (VERIFIED convention, not
    invented — `.claude/skills/zb-task-update/SKILL.md` step 3: "If the user
    means 'on hold / waiting', keep it TODO (1) and record why in
    status_change_reason — do not use status 4 for a soft hold." BLOCKED(4)
    is reserved exclusively for the `blocked_by` FK path). Calls the EXISTING
    `update_task` router function (the one `PATCH /api/tasks/{id}` calls) —
    same optimistic-lock / operator-proof-gate / IntegrityError-translation
    path. The operator-proof gate (`check_operator_proof`) only fires when
    an AC item's `verified_by` is set to a reserved literal (routers/
    tasks.py `_patch_sets_operator_only_verified_by`) — this PATCH touches
    only `status_change_reason`, so `x_operator_token=None` never trips it.
    """
    if state.project_id is None:
        return _NO_STICKY_PROJECT_REPLY
    raw = args.strip()
    if not raw:
        return "Usage: /hold <id> <reason>"
    parts = raw.split(maxsplit=1)
    task_id_raw = parts[0]
    reason = parts[1].strip() if len(parts) > 1 else ""
    if not task_id_raw.isdigit():
        return "Usage: /hold <id> <reason>"
    if not reason:
        return "Usage: /hold <id> <reason> (a reason is required)"
    task_id = int(task_id_raw)
    update_payload = TaskUpdate(status_change_reason=reason)
    task = await update_task(
        task_id,
        update_payload,
        session_project_id=state.project_id,
        session=session,
        if_unmodified_since=None,
        x_operator_token=None,
    )
    return f"/hold -> #{task.id} held (TODO, reason: {reason})"


# ---------------------------------------------------------------------------
# Phase 3 (Kanban #2780) — the two GAP verbs. `/run` and `/halt` each call the
# NEW dedicated router function (`run_task_now` / `halt_task`) as a direct
# in-process async call (D5) — the SAME state-transition / kill-pause gate /
# 4xx-refuse / idempotency logic the REST route runs, zero duplication. Neither
# new endpoint is `@limiter`-decorated (unlike resolve_gate), so no
# `_build_stub_request()` is needed here — the functions are plain async and
# take (task_id, session_project_id, session) directly. Any `HTTPException`
# they raise (404/409/423) is caught by `telegram_command` and rendered as a
# reply string (D6 "never 500").
# ---------------------------------------------------------------------------


async def _verb_run(session: AsyncSession, state: TelegramChatState, args: str) -> str:
    """`/run <id>` — make the task the Mode-A engine's next pick (Phase 3 gap A).

    Calls `run_task_now` (the exact function `POST /api/tasks/{id}/run-now`
    calls): sets run_mode='auto_pickup' + process_status=TODO + clears
    halt_reason/scheduled_at, honoring the kill/pause/blocker/gate gates.
    Idempotent (re-priming an already-primed task is a no-op). Cooperative —
    the running walker picks it up on its next /next-autorun poll; this does
    NOT spawn a session synchronously.
    """
    if state.project_id is None:
        return _NO_STICKY_PROJECT_REPLY
    raw = args.strip()
    if not raw.isdigit():
        return "Usage: /run <id>"
    task_id = int(raw)
    task = await run_task_now(
        task_id, session_project_id=state.project_id, session=session
    )
    return (
        f"/run -> #{task.id} queued to run next "
        f"(ps={task.process_status}, run_mode={task.run_mode})"
    )


async def _verb_halt(session: AsyncSession, state: TelegramChatState, args: str) -> str:
    """`/halt <id>` — cooperatively halt a RUNNING task (Phase 3 gap B).

    Calls `halt_task` (the exact function `POST /api/tasks/{id}/halt` calls):
    flips a ps=2 task to process_status=8 (HALTED_PENDING_USER) +
    halt_reason='operator_halt'. COOPERATIVE — sets the state the executor
    observes at its next boundary, NOT a hard kill. Refuses (readable reply)
    when the task is not running (ps != 2).
    """
    if state.project_id is None:
        return _NO_STICKY_PROJECT_REPLY
    raw = args.strip()
    if not raw.isdigit():
        return "Usage: /halt <id>"
    task_id = int(raw)
    task = await halt_task(
        task_id, session_project_id=state.project_id, session=session
    )
    return (
        f"/halt -> #{task.id} halt signalled (ps={task.process_status}, "
        f"reason={task.halt_reason}); the runner stops at its next checkpoint."
    )


def _build_stub_request() -> Request:
    """Minimal-but-REAL `starlette.requests.Request` for calling `resolve_gate`
    in-process (D5 — direct function call, not HTTP-to-self).

    `resolve_gate` is `@limiter.limit`-decorated on its REST route; slowapi's
    `async_wrapper` does `isinstance(request, Request)` on the decorated
    function's first positional arg BEFORE the function body ever runs (found
    live: a bare stand-in class raised "parameter `request` must be an
    instance of starlette.requests.Request" — the body itself never touches
    `request`, but the decorator wrapper does). A minimal ASGI `http` scope
    satisfies the isinstance check and gives slowapi's key_func (which reads
    `request.client.host`) something sane to key the (separate, REST-only)
    rate limit on — this in-process call is invoked from the ALREADY
    rate-limited `telegram_command` route, so double-limiting the SAME
    request is not a concern here.
    """
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/task-gates/stub/resolve",
        "headers": [],
        "client": ("127.0.0.1", 0),
        "server": ("telegram-command", 0),
        "scheme": "http",
        "query_string": b"",
    }
    return Request(scope)


# Verb -> handler. A dict, not a class hierarchy (D5 — no new abstraction).
_DISPATCH: dict[str, VerbHandler] = {
    "/project": _verb_project,
    "/projects": _verb_projects,
    "/tasks": _verb_tasks,
    "/task": _verb_task,
    "/gates": _verb_gates,
    "/new": _verb_new,
    "/approve": _verb_approve,
    "/deny": _verb_deny,
    "/hold": _verb_hold,
    "/run": _verb_run,
    "/halt": _verb_halt,
}

# D3 — per-verb auth class. Every key in _DISPATCH MUST have an entry here;
# `_class_for_verb` denies (never executes) a dispatched verb missing one, so
# a future verb added to _DISPATCH-without-a-class-entry fails CLOSED, not
# open. 'destructive' has NO members yet (Phase 2 ships none) — see the
# module docstring for the CONFIRM-turn extension point.
_VERB_CLASS: dict[str, str] = {
    "/project": "safe_mutation",  # writes chat_state.project_id, but that's the D2 targeting mechanism itself
    "/projects": "read",
    "/tasks": "read",
    "/task": "read",
    "/gates": "read",
    "/new": "safe_mutation",
    "/approve": "safe_mutation",
    "/deny": "safe_mutation",
    "/hold": "safe_mutation",
    # Phase 3 (#2780): both are safe_mutation — run-now only PRIMES a task for
    # the walker (it does not itself execute anything), and halt is a
    # COOPERATIVE stop-signal (ps->8), not a destructive delete/mass-op. Both
    # run under the chat-id lock per D3, riding update_id dedup for redelivery.
    "/run": "safe_mutation",
    "/halt": "safe_mutation",
}
_DESTRUCTIVE_CLASS = "destructive"  # no verb carries this yet; documented for the future CONFIRM-turn extension


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
        # Deny-by-default: verb not in _DISPATCH at all.
        reply_text = _UNKNOWN_COMMAND_REPLY
        dispatched = False
        matched_verb = None
    else:
        verb_class = _VERB_CLASS.get(verb)
        if verb_class == _DESTRUCTIVE_CLASS:
            # D3 — no destructive verb ships this phase, but if one is ever
            # added to _DISPATCH it does NOT single-shot execute here.
            reply_text = _DESTRUCTIVE_CONFIRM_STUB_REPLY
            dispatched = False
            matched_verb = verb
        elif verb_class not in ("read", "safe_mutation"):
            # Bug-guard (D3 fail-closed): a verb registered in _DISPATCH with
            # NO _VERB_CLASS entry (or an unrecognized one) is denied rather
            # than executed. Should be unreachable in normal operation — every
            # _DISPATCH key has a _VERB_CLASS entry, checked by
            # test_every_dispatch_verb_has_a_recognized_auth_class.
            logger.warning("telegram_command: verb=%s has no recognized auth class; denying", verb)
            reply_text = _UNKNOWN_COMMAND_REPLY
            dispatched = False
            matched_verb = None
        else:
            # 'read' and 'safe_mutation' both execute under the chat-id lock
            # (D3 — the lock IS operator-equivalent for this whitelist).
            try:
                reply_text = await handler(session, state, args)
            except HTTPException as exc:
                # D6 "never 500": any REST-route error (404/400/403/409/422/
                # 423/429) from the reused create_task/resolve_gate/update_task
                # call surfaces as a readable reply instead of propagating.
                # The callee's own guard clauses all raise BEFORE any ORM
                # mutation on this session (verified per-callee at review
                # time), so a defensive rollback here is a no-op in practice
                # and cheap insurance against ever depending on that invariant
                # implicitly.
                await session.rollback()
                # Re-attach state to the fresh transaction (rollback expires
                # it); refresh so the watermark write below applies to a live
                # row, not a stale detached instance.
                state = await _get_or_create_chat_state(session, payload.chat_id)
                detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
                reply_text = f"{verb} failed ({exc.status_code}): {detail}"
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
