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

Kanban #955.B — event_kind concept:
  `event_kind` is a new parameter (str) that controls per-push-subscription
  filtering via `kinds_enabled`. It is distinct from `kind` (the adapter
  type — telegram / web_push). Callers supply both:
    - `kind`: which adapter to use for EXPLICIT NotificationTarget rows.
    - `event_kind`: which push_subscriptions.kinds_enabled key to check when
      synthesizing web_push targets from the push_subscriptions table.
  When `event_kind` is None the push-subscription resolver is skipped
  (backwards-compatible for existing callers that pre-date #955.B).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.project import Project
from src.models.task import Task, TaskHistory
from src.services.notify_telegram import send_telegram
from src.services.notify_web_push import send_web_push
from src.settings import get_settings

logger = logging.getLogger(__name__)

# Valid event_kind values — mirrors PushSubscription.kinds_enabled keys.
# session_waiting added Kanban #1937: fired by the Notification hook when the
# Lead session is idle / blocked at a permission prompt.
EventKind = Literal["hitl_needed", "task_done", "task_failed", "budget_warn", "session_waiting"]


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
# `web_push` registered by Kanban #955.A — slice 955.A backend foundation.
_ADAPTERS = {
    "telegram": send_telegram,
    "web_push": send_web_push,
}


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


async def _resolve_push_subscription_targets(
    session: AsyncSession,
    project_id: int,
    event_kind: str,
) -> list[dict[str, Any]]:
    """Synthesize web_push NotificationTarget-shaped dicts from push_subscriptions.

    Kanban #955.B — called by deliver() when event_kind is set. Returns rows
    WHERE (project_id IS NULL OR project_id = task.project_id) AND status=1
    AND kinds_enabled->>event_kind = 'true', sorted by id ASC (deterministic
    priority assignment: each row gets priority=100 baseline).

    Returns a list of dicts in the same shape as NotificationTarget so the
    deliver() adapter dispatch loop can treat them uniformly:
      {"kind": "web_push", "chat_id": str(sub.id), "priority": 100, "label": ...}

    A missing event_kind key in kinds_enabled is treated as False (permissive
    default: only fire when explicitly enabled). This handles hand-edited JSONB
    rows gracefully.
    """
    from src.models.push_subscription import PushSubscription
    from src.constants import RecordStatus

    stmt = (
        select(PushSubscription)
        .where(
            PushSubscription.status == RecordStatus.ACTIVE,
        )
        .order_by(PushSubscription.id.asc())
    )
    result = await session.execute(stmt)
    subs = result.scalars().all()

    targets: list[dict[str, Any]] = []
    for sub in subs:
        # Project scoping: NULL means all-projects; specific project_id must match.
        if sub.project_id is not None and sub.project_id != project_id:
            continue
        # kinds_enabled filter — treat missing key as False.
        kinds = sub.kinds_enabled or {}
        if not kinds.get(event_kind, False):
            continue
        targets.append(
            {
                "kind": "web_push",
                "chat_id": str(sub.id),
                "priority": 100,
                "label": f"push:{sub.id}",
            }
        )
    return targets


def _resolve_fallback_base(project: Project, repo_root: Path) -> Path:
    """Resolve the absolute fallback base directory for notification writes.

    Returns ``repo_root / "context" / "projects" / project.name`` when:
    - ``project.working_path`` is None, OR
    - ``project.working_path`` is not absolute on the current platform (e.g. a
      Windows-absolute path like ``C:\\Users\\...`` on a Linux container returns
      ``False`` for ``Path.is_absolute()``), OR
    - the resolved path does not exist as a directory (defensive — avoids
      silently writing into a stale / unmounted working tree).

    When the working_path is usable, returns it directly.

    Logs a WARNING when falling back due to an unusable working_path so the
    operator can correct the project row.
    """
    if project.working_path:
        candidate = Path(project.working_path)
        # On Linux, Windows-absolute paths (C:\...) are NOT absolute — they
        # look like relative paths and resolve relative to CWD, creating deeply
        # nested PUA-character directories. The is_absolute() + exists() guard
        # catches both the Windows-path-on-Linux case and unmounted volumes.
        if candidate.is_absolute() and candidate.exists():
            return candidate
        logger.warning(
            "notification_router: project.working_path %r is not a usable "
            "absolute path on this platform (is_absolute=%s, exists=%s); "
            "falling back to repo_root base %r",
            project.working_path,
            candidate.is_absolute(),
            candidate.exists(),
            str(repo_root),
        )
    return repo_root / "context" / "projects" / project.name


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

    Path resolution delegates to ``_resolve_fallback_base`` which anchors all
    writes at ``settings.repo_root`` when ``project.working_path`` is null or
    is not a usable absolute path on the current platform (Kanban #1285 —
    fixes the CWD-relative write bug that created nested directories under
    ``/repo/api/`` instead of ``/repo/``).
    """
    try:
        # Kanban #1285: always resolve through _resolve_fallback_base so the
        # path is anchored at repo_root (an absolute Linux path) rather than
        # CWD (/repo/api inside the container).  The old inline `Path("context")`
        # was CWD-relative and produced /repo/api/context/... instead of
        # /repo/context/... when working_path was null.
        settings = get_settings()
        repo_root = Path(settings.repo_root)
        base = _resolve_fallback_base(project, repo_root) / "notifications"
        # Filename uses %Y%m%dT%H%M%SZ — colons in ISO8601 break NTFS.
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"{task_id}-{ts.strftime('%Y%m%dT%H%M%SZ')}.txt"
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


async def deliver(
    *,
    task_id: int,
    payload: dict[str, Any],
    kind: str,
    session: AsyncSession,
    event_kind: str | None = None,
) -> dict[str, Any]:
    """Resolve the target list + attempt delivery in priority order.

    Args:
        task_id:    tasks.id — used to look up the task + its project.
        payload:    structured delivery payload, passed through to adapters.
        kind:       NotificationTarget.kind filter — only targets matching this
                    kind are attempted (allows the caller to scope a delivery
                    to one channel even when multiple kinds exist).
        session:    AsyncSession scoped to the caller; this function adds
                    audit rows + commits once on exit.
        event_kind: Optional EventKind str (Kanban #955.B). When set, the
                    resolver additionally walks `push_subscriptions` and
                    appends web_push targets for active subscriptions that
                    have kinds_enabled[event_kind]=true and match the task's
                    project_id (or project_id IS NULL for all-projects subs).
                    Appended AFTER the explicit NotificationTarget rows so
                    they're tried last in the priority chain. When None, the
                    push-subscription resolver is skipped (backwards-compat).

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

    # Kanban #955.B — append push_subscription-derived web_push targets.
    # These are tried AFTER the explicit NotificationTarget rows (appended to
    # the end of kind_targets). Guard: only append when kind=="web_push" so a
    # "telegram" deliver() call never accidentally fires web_push adapters.
    # The event_kind None path (all pre-955.B callers) skips this block entirely.
    if event_kind is not None and kind == "web_push":
        push_targets = await _resolve_push_subscription_targets(
            session, task.project_id, event_kind
        )
        kind_targets.extend(push_targets)

    attempts: list[dict[str, Any]] = []
    delivered_ok = False

    for tgt in kind_targets:
        # _ADAPTERS has an entry for every supported kind; KeyError here is
        # louder than a silent ok=False if a new kind lands in the Literal
        # without an adapter wired in.
        adapter = _ADAPTERS[tgt["kind"]]
        result = await adapter(tgt, payload)
        attempt = {
            "target": tgt,
            "ok": bool(result.get("ok")),
            "detail": str(result.get("detail", "")),
            "priority": tgt.get("priority"),
        }
        # Pass through adapter-specific extras (telegram_msg_id, etc.)
        # for the caller / audit row.
        attempt.update({k: v for k, v in result.items() if k not in ("ok", "detail")})
        attempts.append(attempt)
        # Audit row (snapshot shape locked v1: actor/target/ok/detail/
        # attempt_priority/kind/attempted_at). Caller owns the commit below.
        session.add(TaskHistory(
            task_id=task_id,
            operation=NOTIFY_OP_CODE,
            snapshot={
                "actor": NOTIFY_ACTOR,
                "target": tgt,
                "ok": attempt["ok"],
                "detail": attempt["detail"],
                "attempt_priority": tgt.get("priority"),
                "kind": kind,
                "attempted_at": now.isoformat(),
            },
        ))
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
        session.add(TaskHistory(
            task_id=task_id,
            operation=NOTIFY_OP_CODE,
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
        ))

    await session.commit()

    return {"task_id": task_id, "attempts": attempts}
