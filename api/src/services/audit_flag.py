"""AA3 audit-flag pipeline (Kanban #1211).

One entry point:
- `apply_flag_from_audit_report(audit_task_id, actor, session)` — called from
  the PATCH /api/tasks/{id} hook when an audit task transitions to DONE.

Pipeline (AC#3 + AC#4 + D5):
1. Read audit task; assert `task_type='audit'` + has `audit_report` JSONB.
2. Extract `recommendation` from audit_report (one of:
   'continue' | 'review' | 'pause'). Missing / unknown → no-op + WARN log.
3. If 'continue': no flag action.
4. If 'review' or 'pause':
   - SELECT open AA3 flag tasks for the project (interaction_kind='question',
     process_status IN {1, 2, 4}, question_payload->>'is_audit_flag' = 'true').
   - If found: UPDATE question_payload — increment breach_streak_days, append
     audit_history, refresh question text, update latest_audit.
   - Else: CREATE a new flag task per AC#4 shape.
5. If 'pause': ALSO call pause_project (idempotent — already-paused returns
   409 which we catch + ignore).

Caller (PATCH /api/tasks/{id} hook) wraps this in the same transaction as the
audit-task DONE write so a partial-failure rolls back the audit-DONE too.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import (
    RecordStatus,
    TaskInteractionKind,
    TaskPriority,
    TaskStatus,
    TaskType,
)
from src.models.task import Task
from src.services.pause_switch import pause_project

logger = logging.getLogger(__name__)


# Vocabulary the auditor produces. Anything outside this set is treated as
# a no-op (logged WARN) so a malformed audit_report doesn't crash the PATCH.
_VALID_RECOMMENDATIONS: frozenset[str] = frozenset({"continue", "review", "pause"})


def _format_flag_question(
    project_id: int,
    breach_streak_days: int,
    latest_audit_id: int,
    audit_report: dict[str, Any],
) -> str:
    """Build the human-readable flag question (D5 — appears in the AA4 drawer).

    Pulls a short summary from audit_report (verdict / severity / evidence
    highlights) so the operator gets the gist without opening the audit task.
    Value-tolerant on shape — the auditor's exact schema is still evolving
    (AA2's responsibility) so we degrade gracefully on missing keys.
    """
    verdict = audit_report.get("verdict") or audit_report.get("status") or "review"
    severity = audit_report.get("severity") or "unspecified"
    short_reasons: list[str] = []
    evidence = audit_report.get("evidence")
    if isinstance(evidence, list):
        # Take first 3 evidence items — keep the drawer readable.
        for ev in evidence[:3]:
            if isinstance(ev, dict):
                short_reasons.append(
                    ev.get("summary") or ev.get("note") or str(ev)[:80]
                )
            elif isinstance(ev, str):
                short_reasons.append(ev[:80])
    elif isinstance(evidence, str):
        short_reasons.append(evidence[:200])

    reasons_clause = (
        f" — {'; '.join(short_reasons)}" if short_reasons else ""
    )
    return (
        f"Project #{project_id} audit Day {breach_streak_days} of breach "
        f"(verdict={verdict}, severity={severity}, "
        f"latest_audit=#{latest_audit_id}){reasons_clause}"
    )


def _new_flag_payload(
    project_id: int,
    audit_task_id: int,
    audit_report: dict[str, Any],
) -> dict[str, Any]:
    """Build the AC#4 question_payload for a freshly-created flag."""
    breach_streak_days = 1
    return {
        "question": _format_flag_question(
            project_id, breach_streak_days, audit_task_id, audit_report
        ),
        "options": ["continue", "adjust_continue", "keep_paused", "terminate"],
        "answer_history": [],
        # AA3-specific bookkeeping (D5):
        "is_audit_flag": True,
        "breach_streak_days": breach_streak_days,
        "audit_history": [audit_task_id],
        "latest_audit": audit_task_id,
        # Snapshot the audit-report summary so the AA4 drawer doesn't need to
        # cross-join when rendering streak history. Truncated to keep payload
        # small (the audit task itself carries the full report).
        "latest_audit_summary": {
            "verdict": audit_report.get("verdict"),
            "severity": audit_report.get("severity"),
            "recommendation": audit_report.get("recommendation"),
        },
    }


def _bump_existing_flag_payload(
    existing_payload: dict[str, Any],
    project_id: int,
    audit_task_id: int,
    audit_report: dict[str, Any],
) -> dict[str, Any]:
    """Increment streak + append history on an existing AA3 flag's payload.

    Returns a NEW dict — never mutates the input (the ORM identity-map needs
    a fresh JSONB value to trigger an UPDATE; in-place dict mutation can
    silently no-op SQLAlchemy's dirty-tracking).
    """
    new_payload = dict(existing_payload)
    new_streak = int(new_payload.get("breach_streak_days") or 0) + 1
    history = list(new_payload.get("audit_history") or [])
    if audit_task_id not in history:
        history.append(audit_task_id)
    new_payload["breach_streak_days"] = new_streak
    new_payload["audit_history"] = history
    new_payload["latest_audit"] = audit_task_id
    new_payload["latest_audit_summary"] = {
        "verdict": audit_report.get("verdict"),
        "severity": audit_report.get("severity"),
        "recommendation": audit_report.get("recommendation"),
    }
    # Refresh the question text with the new streak count.
    new_payload["question"] = _format_flag_question(
        project_id, new_streak, audit_task_id, audit_report
    )
    # Ensure options + is_audit_flag stay set even if a hand-edited row
    # cleared them (defensive).
    new_payload.setdefault(
        "options",
        ["continue", "adjust_continue", "keep_paused", "terminate"],
    )
    new_payload["is_audit_flag"] = True
    return new_payload


async def apply_flag_from_audit_report(
    *,
    audit_task_id: int,
    actor: str = "system",
    session: AsyncSession,
) -> dict[str, Any]:
    """Apply AA3 flag pipeline to an audit task that just transitioned to DONE.

    The caller (PATCH /api/tasks/{id} hook) has already written the audit
    task's DONE flip — this helper only reads it + applies side effects in
    the SAME open transaction (no commits inside the helper — the caller
    owns the commit boundary).

    Returns a summary dict describing what was done. Defensive throughout —
    a malformed audit_report logs a WARN + returns `{"applied": False, ...}`
    rather than raising. The post-PATCH hook should not crash the PATCH for
    a downstream-tool data-quality issue.
    """
    summary: dict[str, Any] = {
        "audit_task_id": audit_task_id,
        "applied": False,
        "flag_action": None,
        "flag_id": None,
        "pause_triggered": False,
    }

    audit_task = await session.get(Task, audit_task_id)
    if audit_task is None or audit_task.status != RecordStatus.ACTIVE:
        logger.warning(
            "apply_flag_from_audit_report: audit task %d missing/soft-deleted; "
            "skipping",
            audit_task_id,
        )
        summary["reason"] = "audit_task_missing"
        return summary

    if audit_task.task_type != TaskType.AUDIT:
        # Defensive — caller (the PATCH hook) is supposed to gate on
        # task_type='audit'. Trip-wire log if not.
        logger.warning(
            "apply_flag_from_audit_report: task %d is task_type=%r, "
            "expected 'audit'; skipping",
            audit_task_id,
            audit_task.task_type,
        )
        summary["reason"] = "not_audit_task"
        return summary

    audit_report = audit_task.audit_report
    if not isinstance(audit_report, dict):
        logger.warning(
            "apply_flag_from_audit_report: audit task %d has no audit_report; "
            "skipping flag pipeline (audit task itself is DONE as the caller "
            "already flipped)",
            audit_task_id,
        )
        summary["reason"] = "audit_report_missing"
        return summary

    recommendation = audit_report.get("recommendation")
    if recommendation not in _VALID_RECOMMENDATIONS:
        logger.warning(
            "apply_flag_from_audit_report: audit task %d recommendation=%r "
            "is not in %s; treating as no-op",
            audit_task_id,
            recommendation,
            sorted(_VALID_RECOMMENDATIONS),
        )
        summary["reason"] = "unknown_recommendation"
        summary["recommendation"] = recommendation
        return summary

    summary["recommendation"] = recommendation

    if recommendation == "continue":
        summary["applied"] = True
        summary["flag_action"] = "no_flag"
        return summary

    # recommendation in {'review', 'pause'} — flag pipeline.
    project_id = audit_task.project_id

    # Look up existing OPEN AA3 flag for this project. We use a JSONB path
    # predicate on `is_audit_flag` to distinguish AA3 flags from other
    # question tasks (e.g., approval prompts, design Option A/B questions).
    existing_stmt = (
        select(Task)
        .where(
            Task.project_id == project_id,
            Task.status == RecordStatus.ACTIVE,
            Task.interaction_kind == TaskInteractionKind.QUESTION,
            Task.process_status.in_(
                [TaskStatus.TODO, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED]
            ),
            Task.question_payload["is_audit_flag"].astext == "true",
        )
        .order_by(Task.id.asc())
        .limit(1)
    )
    existing_flag = (await session.execute(existing_stmt)).scalars().first()

    if existing_flag is not None:
        existing_flag.question_payload = _bump_existing_flag_payload(
            existing_flag.question_payload or {},
            project_id,
            audit_task_id,
            audit_report,
        )
        existing_flag.updated_at = datetime.now(timezone.utc)
        summary["flag_action"] = "updated"
        summary["flag_id"] = existing_flag.id
    else:
        new_payload = _new_flag_payload(project_id, audit_task_id, audit_report)
        flag_task = Task(
            project_id=project_id,
            title=f"[AA3] Project #{project_id} audit flag — operator review",
            description=(
                "AA3 governance flag opened from audit task "
                f"#{audit_task_id}. Resolve via "
                f"POST /api/tasks/{{flag_id}}/resolve-flag with body "
                "{action: continue | adjust_continue | keep_paused | terminate}."
            ),
            process_status=TaskStatus.BLOCKED,
            priority=TaskPriority.HIGH,
            interaction_kind=TaskInteractionKind.QUESTION,
            task_kind="human",
            run_mode="manual",
            task_type=TaskType.CHORE,
            question_payload=new_payload,
        )
        session.add(flag_task)
        # Flush so flag_task.id is materialized for the response — keeps the
        # hook's PATCH response self-contained (FE can deep-link the new flag).
        await session.flush()
        summary["flag_action"] = "created"
        summary["flag_id"] = flag_task.id

    # If recommendation = 'pause', flip project state too. Idempotent —
    # already-paused returns 409 which we swallow (the existing pause stays
    # in place; we still wrote / updated the flag above).
    if recommendation == "pause":
        try:
            await pause_project(
                project_id=project_id,
                reason=(
                    f"AA3 audit task #{audit_task_id} recommended pause"
                ),
                actor=actor,
                session=session,
            )
            summary["pause_triggered"] = True
        except HTTPException as exc:
            if exc.status_code == 409:
                # Already paused — expected on streak day 2+; the flag-only
                # update above is the right outcome.
                summary["pause_triggered"] = False
                summary["pause_skipped_reason"] = "already_paused"
            else:
                # Surface anything else (e.g. project killed = 409 too, but
                # with a different detail). Let the caller's transaction
                # rollback. This is a defensive re-raise — the audit-task
                # DONE flip will roll back too, which is intentional.
                raise

    summary["applied"] = True
    return summary
