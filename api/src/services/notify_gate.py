"""Tier->channel policy for async-HITL gate notifications over Telegram (Kanban #2565).

ONE reusable seam that the gate-open path (routers/task_gates.py) and the future
Mode-A runner (Task C #2566) both call so the policy lives in a single place.

What it does, given a gate (gate_id, gate_tier, question_payload):
  1. Resolve whether a `telegram` NotificationTarget exists for the task/project
     AND the bot token is present. Absent EITHER -> soft no-op (no notify, no
     crash), exactly the ntfy posture (`_fire_hitl_push`).
  2. Build the message + (maybe) inline buttons per the LOCKED tier policy
     (`mode-a-autonomy-boundary.md` §3 + `async-hitl-gates.md` §6):

       decision / hitl  -> message + approve/reject (or question_payload.options)
                           buttons whose callback_data encodes {gate_id, option}.
       commit / push    -> INFORMED-APPROVAL: render the evidence the runner
                           placed in question_payload (diff-stat + pre-push
                           keyword-scan result + test result) INTO the card,
                           THEN approve/reject buttons. Evidence absent -> still
                           show the ask, flagged "evidence missing".
       key / external   -> FYI ONLY, NO answerable buttons (Ring 4 = terminal
                           only): a plain "needs your attention in terminal"
                           notice. NEVER an approve button.

  3. Fire via the notification fabric (`notification_router.deliver(kind=
     'telegram')`) — soft-fail; the adapter returns ok=False on any error and
     deliver() never raises.

Everything here is best-effort: the public entry `notify_gate_opened` swallows
all exceptions and returns a small status dict (never raises) so a notify bug
can never break the open_gate API response.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.project import Project
from src.models.task import Task
from src.services.notify_telegram import (
    TELEGRAM_CONTROL_KEY,
    TELEGRAM_ENV_TOKEN,
    encode_callback_data,
)

logger = logging.getLogger("api.notify_gate")

# Tier buckets (mirror async-hitl-gates.md §6 / mode-a-autonomy-boundary.md §3).
_SIMPLE_TIERS = frozenset({"decision", "hitl"})
_FORBIDDEN_TIERS = frozenset({"key", "external"})

# The five operator_gate tiers carry only key/commit/decision/hitl/external. The
# locked policy splits `commit` into auto-commit (runner never gates) vs `push`
# (informed-approval). At the gate layer the tier value is `commit` for a push
# gate; the runner sets question_payload to flag a push + carry the evidence.
# So: commit-tier gate -> informed-approval card (evidence + buttons). The
# evidence card is the right rendering whether or not it is strictly a push,
# because the only commit-tier gate the runner EVER opens is the push gate
# (local commits are auto, never gated — §2 "Commit tier").
_EVIDENCE_TIER = "commit"

# Default option ids for a simple approve/reject gate when question_payload
# carries no explicit options list.
_DEFAULT_OPTIONS = (("approve", "Approve"), ("reject", "Reject"))


def _extract_options(question_payload: dict[str, Any] | None) -> list[tuple[str, str]]:
    """Pull (option_id, label) pairs from the gate's question_payload.

    Supported shapes (defensive — Mode-A/Telegram callers vary):
      - {"options": ["approve", "reject"]}                      -> id==label
      - {"options": [{"id": "a", "label": "Ship it"}, ...]}     -> explicit
      - {"options": [{"value": "a", "text": "Ship it"}, ...]}   -> alt keys
    Anything unparseable falls back to the default approve/reject pair so the
    operator always has a way to answer.
    """
    qp = question_payload or {}
    raw = qp.get("options") if isinstance(qp, dict) else None
    if not isinstance(raw, list) or not raw:
        return list(_DEFAULT_OPTIONS)
    out: list[tuple[str, str]] = []
    for item in raw:
        if isinstance(item, str):
            out.append((item, item))
        elif isinstance(item, dict):
            oid = item.get("id") or item.get("value") or item.get("option")
            label = item.get("label") or item.get("text") or oid
            if oid:
                out.append((str(oid), str(label)))
    return out or list(_DEFAULT_OPTIONS)


def _buttons_for_options(
    gate_id: int, options: list[tuple[str, str]]
) -> list[dict[str, str]]:
    """Map (option_id, label) pairs to inline-button dicts the adapter renders."""
    return [
        {"text": label, "callback_data": encode_callback_data(gate_id, oid)}
        for oid, label in options
    ]


def _render_evidence_lines(question_payload: dict[str, Any] | None) -> list[str]:
    """Render the informed-approval evidence block for a commit/push gate.

    Looks for the runner-supplied evidence keys in question_payload:
      diff_stat / pre_push_scan / test_result  (each optional).
    Returns one display line per present key; when NONE are present, returns a
    single "evidence missing" warning line (§3: "If evidence is absent, still
    show the ask but flag 'evidence missing'").
    """
    qp = question_payload or {}
    if not isinstance(qp, dict):
        qp = {}
    lines: list[str] = []
    diff_stat = qp.get("diff_stat")
    scan = qp.get("pre_push_scan")
    test = qp.get("test_result")
    if diff_stat:
        lines.append(f"diff-stat: {diff_stat}")
    if scan:
        lines.append(f"pre-push scan: {scan}")
    if test:
        lines.append(f"tests: {test}")
    if not lines:
        lines.append("WARNING: evidence missing (diff-stat / scan / tests not supplied)")
    return lines


def build_gate_notify_payload(
    *,
    task_id: int,
    task_title: str,
    gate_id: int,
    gate_tier: str,
    question_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the Telegram delivery payload for a gate, per the LOCKED tier policy.

    Returns a payload dict ready for `deliver(kind='telegram', payload=...)`.
    The `_telegram` control block (when present) carries the inline buttons the
    adapter turns into a reply_markup; it is stripped from the visible text.

    Pure function (no IO) so it is unit-testable in isolation — the tier->shape
    mapping is the heart of AC3 and is exercised directly in the tests.
    """
    question_text = ""
    if isinstance(question_payload, dict):
        question_text = str(question_payload.get("question") or "")

    base: dict[str, Any] = {
        "title": f"Gate #{gate_id} [{gate_tier}] — {task_title}",
        "task_id": task_id,
        "url": f"/tasks/{task_id}",
    }

    # --- Ring 4: key / external — FYI ONLY, never answerable -----------------
    if gate_tier in _FORBIDDEN_TIERS:
        base["body"] = (
            f"Needs your attention in the TERMINAL (tier '{gate_tier}' is not "
            f"chat-approvable). " + (question_text or "Open the terminal to act.")
        )
        # NO _telegram control block -> adapter sends plain text, no buttons.
        return base

    # --- commit / push: informed-approval — evidence THEN buttons -----------
    if gate_tier == _EVIDENCE_TIER:
        evidence = _render_evidence_lines(question_payload)
        body_parts = []
        if question_text:
            body_parts.append(question_text)
        body_parts.append("Evidence:")
        body_parts.extend(f"  - {line}" for line in evidence)
        base["body"] = "\n".join(body_parts)
        options = _extract_options(question_payload)
        base[TELEGRAM_CONTROL_KEY] = {"buttons": _buttons_for_options(gate_id, options)}
        return base

    # --- decision / hitl (and any other non-forbidden tier): simple buttons --
    base["body"] = question_text or "Approve or reject this gate."
    options = _extract_options(question_payload)
    base[TELEGRAM_CONTROL_KEY] = {"buttons": _buttons_for_options(gate_id, options)}
    return base


async def notify_task_event(
    *,
    session: AsyncSession,
    task_id: int,
    task_title: str,
    event: str,
    body: str,
) -> dict[str, Any]:
    """Best-effort Telegram FYI for a task lifecycle event (Kanban #2565, §2).

    Fired on the notify-event policy events: 'blocked' and 'done' (wired at the
    PATCH ps->4 / ps->5 transitions in routers/tasks.py). 'runner-stopped/empty'
    is the RUNNER's event -> Task C #2566 / #2531 wires it there, NOT here.
    Routine task-start (ps->2) and subagent-spawn are intentionally NOT notified
    (avoid phone spam — §2).

    These are FYI messages (no buttons) — plain text only. Gated + soft-fail
    exactly like notify_gate_opened: missing token or no telegram target ->
    silent no-op; never raises.

    # Task C #2566 / #2531: the runner-stopped/empty event is the RUNNER's to
    # fire (it owns the loop's stop condition) — call this helper with
    # event="stopped" from the runner, NOT from any API PATCH path here.

    `deliver(kind='telegram')` resolves the target itself, but we pre-check the
    target so a deployment without one doesn't accumulate a local-file fallback
    on every blocked/done transition.
    """
    from src.services.notification_router import deliver

    try:
        if not os.environ.get(TELEGRAM_ENV_TOKEN, "").strip():
            return {"fired": False, "skipped": "missing_token"}
        # Load task + project just for the target pre-check. One light roundtrip.
        task = await session.get(Task, task_id)
        if task is None:
            return {"fired": False, "skipped": "task_not_found"}
        project = await session.get(Project, task.project_id)
        if project is None or not await _has_telegram_target(session, task, project):
            return {"fired": False, "skipped": "no_telegram_target"}

        payload = {
            "title": f"Task {event}: {task_title}",
            "body": body,
            "task_id": task_id,
            "url": f"/tasks/{task_id}",
        }
        result = await deliver(
            task_id=task_id, payload=payload, kind="telegram", session=session
        )
        attempts = result.get("attempts") or []
        delivered_ok = any(a.get("ok") and a.get("target") for a in attempts)
        return {"fired": True, "skipped": None, "delivered_ok": delivered_ok}
    except Exception:  # noqa: BLE001 — notify must never crash the PATCH.
        logger.exception(
            "notify_task_event: unexpected error task=%d event=%s; notify skipped",
            task_id,
            event,
        )
        return {"fired": False, "skipped": "exception"}


async def _has_telegram_target(
    session: AsyncSession, task: Task, project: Project
) -> bool:
    """True iff a `telegram` NotificationTarget is resolvable for this task/project.

    Mirrors notification_router._resolve_targets precedence (task-level overrides
    project-level) but only needs a yes/no — we gate the whole notify on it so a
    deployment without a telegram target stays a silent no-op (no spurious
    local-file fallback that deliver() would otherwise write).
    """
    raw = task.notification_targets
    if raw is None:
        raw = project.notification_targets
    if not raw:
        return False
    return any(isinstance(t, dict) and t.get("kind") == "telegram" for t in raw)


async def notify_gate_opened(
    *,
    session: AsyncSession,
    task: Task,
    project: Project,
    gate_id: int,
    gate_tier: str,
    question_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Best-effort: fire a Telegram notify for a freshly-opened gate.

    Gate (BOTH must hold, else silent no-op):
      - a `telegram` NotificationTarget resolves for the task/project, AND
      - TELEGRAM_BOT_TOKEN is present (the adapter checks this; we also short-
        circuit before building the payload to avoid pointless work).

    NEVER raises — wraps the whole body in a try/except and returns a status
    dict: {"fired": bool, "skipped": str|None, "delivered_ok": bool|None}. The
    caller (open_gate) ignores the return except for logging.

    Soft-fail mirrors `_fire_hitl_push`: a notify failure must never break the
    open_gate API response.
    """
    from src.services.notification_router import deliver

    try:
        if not os.environ.get(TELEGRAM_ENV_TOKEN, "").strip():
            return {"fired": False, "skipped": "missing_token", "delivered_ok": None}
        if not await _has_telegram_target(session, task, project):
            return {"fired": False, "skipped": "no_telegram_target", "delivered_ok": None}

        payload = build_gate_notify_payload(
            task_id=task.id,
            task_title=task.title or "",
            gate_id=gate_id,
            gate_tier=gate_tier,
            question_payload=question_payload,
        )
        result = await deliver(
            task_id=task.id,
            payload=payload,
            kind="telegram",
            session=session,
        )
        # deliver() returns {"task_id", "attempts": [...]}; a telegram attempt
        # with ok=True means the message (and buttons) went out.
        attempts = result.get("attempts") or []
        delivered_ok = any(a.get("ok") and a.get("target") for a in attempts)
        if not delivered_ok:
            logger.warning(
                "notify_gate_opened: task=%d gate=%d telegram not delivered (attempts=%s)",
                task.id,
                gate_id,
                [a.get("detail") for a in attempts],
            )
        return {"fired": True, "skipped": None, "delivered_ok": delivered_ok}
    except Exception:  # noqa: BLE001 — notify must never crash the gate open.
        logger.exception(
            "notify_gate_opened: unexpected error task=%d gate=%d; notify skipped",
            getattr(task, "id", -1),
            gate_id,
        )
        return {"fired": False, "skipped": "exception", "delivered_ok": None}
