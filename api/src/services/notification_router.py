"""Push-notification routing layer (Kanban #1224).

Borrows the SHAPE of Hermes' `gateway/delivery.py` DeliveryTarget DSL: a
priority-ordered list of explicit delivery targets with a local-file
fallback. NOT a multi-platform copy — Telegram is the only v1 adapter.

Resolution priority (AC3):
  1. Task-level `notification_targets` (when the task row's column is non-NULL).
  2. Project-level `notification_targets` (the task's project's column).
  3. Local-file fallback (AC4) — writes `<working_path>/notifications/<task_id>-<ISO>.txt`
     with the serialized payload + audit metadata header.

For each target in priority order: invoke the adapter; on `ok=False` fall
through to the next target. Return the full per-attempt log so the caller
(POST /api/notifications/deliver endpoint, daily-digest cron, HITL halt
trigger, kill-switch confirm) can render the outcome.

Audit (AC7): every delivery attempt — including the local-file fallback —
appends a `tasks_history` row with `operation='N'` and a JSONB snapshot
holding `{target, ok, detail, attempt_priority, kind, attempted_at}`. The
existing `tasks_audit_trg` trigger writes 'U'/'D' on the `tasks` row itself;
the notification audit is an INSERT we issue directly. The CHECK extension
landed in migration 0041.

Anti-pattern callout (AP1 from #1220): platform-kind is metadata ON a
notification target, NEVER part of a session key. agent-teams sessions are
bound to `project_id` only.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.project import Project
from src.models.task import Task, TaskHistory
from src.services.notify_telegram import send_telegram

logger = logging.getLogger(__name__)


# Module constants — exposed for tests + monkeypatch.
NOTIFY_OP_CODE = "N"  # tasks_history.operation value for delivery-attempt rows.
NOTIFY_ACTOR = "notification_router"  # captured in snapshot, NOT a separate column.


# ---------------------------------------------------------------------------
# Adapter dispatch table — extend here when a new `kind` lands.
# ---------------------------------------------------------------------------

# Maps NotificationTarget.kind -> async adapter callable. The adapter contract:
#   async def adapter(target: dict, payload: dict) -> dict[str, Any]
# where the returned dict carries at minimum `{ok: bool, detail: str}`.
# Discord / Slack adapters land here as separate keys when implemented.
_ADAPTERS = {
    "telegram": send_telegram,
}


def _target_to_dict(target: Any) -> dict[str, Any]:
    """Coerce a target (Pydantic instance OR plain dict from JSONB) to a dict.

    JSONB column reads always surface as dict; explicit Pydantic instances
    come in from test fixtures that pre-validate. Normalize to dict so the
    rest of the pipeline (logging / audit serialization / adapter dispatch)
    operates on a single shape.
    """
    if isinstance(target, dict):
        return target
    if hasattr(target, "model_dump"):
        return target.model_dump()
    raise TypeError(f"notification target must be dict or pydantic model; got {type(target).__name__}")


def _resolve_targets(task: Task, project: Project) -> list[dict[str, Any]]:
    """Pick the effective target list for `task`.

    Returns the task-level list if non-NULL, else the project-level list if
    non-NULL, else an empty list. The empty case triggers the local-file
    fallback in `deliver()`.

    Within a list, ordering is `priority ASC` (lower number tried first);
    list-position breaks ties. Non-dict elements / elements missing `priority`
    are skipped silently (defensive — should not happen after the API
    boundary's Pydantic validation, but JSONB columns can be hand-edited via
    raw SQL).
    """
    raw = task.notification_targets
    if raw is None:
        raw = project.notification_targets
    if not raw:
        return []
    cleaned = [t for t in raw if isinstance(t, dict) and "priority" in t]
    cleaned.sort(key=lambda t: t["priority"])
    return cleaned


def _fallback_path(project: Project, task_id: int, ts: datetime) -> Path:
    """Compute the local-file fallback path per AC4 + #1185 path resolution.

    Uses `projects.working_path` when set; otherwise falls back to the
    legacy `agent-teams/context/projects/<name>/notifications/` layout (per
    the working_path migration audit Kanban #941 — pre-#1185 projects still
    work via the fallback).

    Filename: `<task_id>-<ISO8601-no-colons>.txt` (Windows-safe; colons in
    ISO8601 break NTFS).
    """
    iso_safe = ts.strftime("%Y%m%dT%H%M%SZ")
    filename = f"{task_id}-{iso_safe}.txt"

    if project.working_path:
        base = Path(project.working_path) / "notifications"
    else:
        # Legacy fallback path — Kanban #1185 migration audit (#941) tracks
        # the in-repo content; this branch keeps the router functional for
        # working_path=NULL projects (currently agent-teams itself + legacy).
        base = Path("context") / "projects" / project.name / "notifications"

    base.mkdir(parents=True, exist_ok=True)
    return base / filename


def _write_local_fallback(
    project: Project,
    task_id: int,
    payload: dict[str, Any],
    *,
    fallback_reason: str,
    kind: str,
    ts: datetime,
) -> dict[str, Any]:
    """Materialize the payload to disk per AC4. Always returns ok=True (the
    write is the success path) unless the filesystem itself errors — in which
    case ok=False + detail carries the OSError repr so the audit row records
    the failure mode.

    Header block carries audit metadata so an operator scanning the file by
    hand can identify which kind / when / why-not-pushed without consulting
    the `tasks_history` JSONB.
    """
    try:
        path = _fallback_path(project, task_id, ts)
        header_lines = [
            f"# notification fallback (Kanban #1224)",
            f"# task_id: {task_id}",
            f"# project_id: {project.id}",
            f"# project_name: {project.name}",
            f"# kind: {kind}",
            f"# attempted_at: {ts.isoformat()}",
            f"# fallback_reason: {fallback_reason}",
            f"# ----- payload below -----",
        ]
        body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        path.write_text("\n".join(header_lines) + "\n" + body + "\n", encoding="utf-8")
        return {
            "ok": True,
            "detail": f"wrote_local_fallback: {path}",
            "path": str(path),
        }
    except OSError as exc:
        logger.warning("local fallback write failed: %r", exc)
        return {
            "ok": False,
            "detail": f"local_fallback_write_error: {type(exc).__name__}: {exc}",
            "path": None,
        }


async def _append_history_row(
    session: AsyncSession,
    *,
    task_id: int,
    snapshot: dict[str, Any],
) -> None:
    """Insert one tasks_history row with operation='N'. Caller is responsible
    for the commit (we batch the attempt rows + return them together so a
    single transaction covers the whole deliver() call).

    snapshot shape (locked at v1):
        {
          actor: 'notification_router',
          target: {kind, chat_id, label, priority}  | null for fallback,
          ok: bool,
          detail: str,
          attempt_priority: int | null,
          kind: str,
          attempted_at: ISO8601,
        }
    """
    row = TaskHistory(
        task_id=task_id,
        operation=NOTIFY_OP_CODE,
        snapshot=snapshot,
    )
    session.add(row)


async def deliver(
    *,
    task_id: int,
    payload: dict[str, Any],
    kind: str,
    session: AsyncSession,
) -> dict[str, Any]:
    """Resolve the target list + attempt delivery in priority order.

    Args:
        task_id: tasks.id — used to look up the task + its project.
        payload: structured delivery payload, passed through to adapters.
        kind:    NotificationTarget.kind filter — only targets matching this
                 kind are attempted (allows the caller to scope a delivery
                 to one channel even when multiple kinds exist).
        session: AsyncSession scoped to the caller; this function adds
                 audit rows + commits once on exit.

    Returns:
        {
          "task_id": task_id,
          "attempts": [
            {"target": {...} | null, "ok": bool, "detail": str, "priority": int | null},
            ...
          ],
        }

    Behavior:
        - 404 caller-side if task_id not found / soft-deleted (we raise
          ValueError; the router translates).
        - When NO targets resolve (task + project both NULL) → only the
          local-file fallback is attempted; the attempt list has one entry.
        - When targets exist but ALL adapter calls return ok=False → the
          local-file fallback is appended as the last attempt (so an audit
          row always exists for the delivery event regardless of outcome).
        - Each attempt — including the fallback — generates one
          `tasks_history` row with operation='N'.
    """
    # Load task + project in a single roundtrip. selectinload would be
    # natural but the relationship is already loaded eagerly by the existing
    # router patterns; here we use a join + scalar to keep this service
    # standalone (no dependency on the calling router's session state).
    task = (
        await session.execute(select(Task).where(Task.id == task_id))
    ).scalar_one_or_none()
    if task is None:
        raise ValueError(f"task_id={task_id} not found")
    project = (
        await session.execute(select(Project).where(Project.id == task.project_id))
    ).scalar_one_or_none()
    if project is None:
        # Defensive — the FK CASCADE means this should be unreachable, but
        # we surface a clean error rather than NPE on the next line.
        raise ValueError(
            f"task_id={task_id} has no project (project_id={task.project_id})"
        )

    now = datetime.now(timezone.utc)
    targets = _resolve_targets(task, project)
    # Filter to the requested kind so a caller asking for "telegram" doesn't
    # accidentally fire a future "discord" target.
    kind_targets = [t for t in targets if t.get("kind") == kind]

    attempts: list[dict[str, Any]] = []
    delivered_ok = False

    for tgt in kind_targets:
        adapter = _ADAPTERS.get(tgt.get("kind"))
        if adapter is None:
            attempt = {
                "target": tgt,
                "ok": False,
                "detail": f"no_adapter_for_kind: {tgt.get('kind')!r}",
                "priority": tgt.get("priority"),
            }
        else:
            result = await adapter(tgt, payload)
            attempt = {
                "target": tgt,
                "ok": bool(result.get("ok")),
                "detail": str(result.get("detail", "")),
                "priority": tgt.get("priority"),
            }
            # Pass through adapter-specific extras (telegram_msg_id, etc.)
            # for the caller / audit row.
            for extra_key, extra_val in result.items():
                if extra_key not in ("ok", "detail"):
                    attempt[extra_key] = extra_val
        attempts.append(attempt)
        await _append_history_row(
            session,
            task_id=task_id,
            snapshot={
                "actor": NOTIFY_ACTOR,
                "target": tgt,
                "ok": attempt["ok"],
                "detail": attempt["detail"],
                "attempt_priority": tgt.get("priority"),
                "kind": kind,
                "attempted_at": now.isoformat(),
            },
        )
        if attempt["ok"]:
            delivered_ok = True
            break  # first success wins; do not try lower-priority targets.

    # Local-file fallback fires when:
    #   (a) no targets were resolved at all (both task + project NULL), or
    #   (b) every adapter call returned ok=False.
    if not delivered_ok:
        fallback_reason = (
            "no_targets_configured" if not kind_targets else "all_adapters_failed"
        )
        fb = _write_local_fallback(
            project,
            task_id,
            payload,
            fallback_reason=fallback_reason,
            kind=kind,
            ts=now,
        )
        attempts.append(
            {
                "target": None,
                "ok": fb["ok"],
                "detail": fb["detail"],
                "priority": None,
                "path": fb.get("path"),
            }
        )
        await _append_history_row(
            session,
            task_id=task_id,
            snapshot={
                "actor": NOTIFY_ACTOR,
                "target": None,
                "ok": fb["ok"],
                "detail": fb["detail"],
                "attempt_priority": None,
                "kind": kind,
                "fallback_reason": fallback_reason,
                "path": fb.get("path"),
                "attempted_at": now.isoformat(),
            },
        )

    await session.commit()

    return {"task_id": task_id, "attempts": attempts}
