"""ProjectsAudit ORM model (Kanban #1209 — GOV1 hard kill switch).

Append-only audit ledger for project-level kill / revive events. NOT extending
`tasks_history` (those are per-task UPDATE/DELETE snapshots from the audit
trigger; kill/revive are project-level events with their own drain payload).
Mirrors the `transactions` ledger pattern: FK ON DELETE CASCADE so a project
hard-delete (rare; soft-delete is the norm via status=0) leaves no orphans.

Future project-auditor (GOV2) reads here to summarize kill cadence per project.

`drain_summary` JSONB shape (validated at the service layer, not by DB CHECK):
  on kill   : {recurring_suspended: N, frozen_tasks: N, in_flight_marked: N,
               cancelled_commitments: N, ...}
  on revive : {resumed_recurring: N, unfrozen_tasks: N, ...}

No audit trigger on this table — it IS the audit trail (parity with
`transactions` / `sessions` / `tool_calls` precedent per db-schema.md).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base

if TYPE_CHECKING:
    from src.models.project import Project


# Vocabulary for projects_audit.action. Mirrors the CHECK in migrations
# 0039 (kill/revive) + 0040 (pause/unpause/pause_override). Module constant
# so the Pydantic Literal in schemas/project.py stays in lockstep.
#
# - kill / revive       : GOV1 hard kill switch (Kanban #1209).
# - pause / unpause     : GOV3 soft-pause governance state (Kanban #1211).
# - pause_override      : GOV3 per-task escape hatch — a POST /api/tasks
#                         against a paused project that succeeded via
#                         allow_during_pause=true + reason. The bypass IS
#                         the audit signal (D6 + GOV5 threshold-tuning).
PROJECT_AUDIT_ACTIONS: tuple[str, ...] = (
    "kill",
    "revive",
    "pause",
    "unpause",
    "pause_override",
)


class ProjectsAudit(Base):
    """Append-only audit row for a project-level kill or revive (Kanban #1209)."""

    __tablename__ = "projects_audit"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    project_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Free-form text (no CHECK) — typically 'operator', but future automation
    # may stamp 'system' / 'project-auditor' / etc. Vocabulary is wide-open by
    # design (mirrors transactions.source pattern).
    actor: Mapped[str] = mapped_column(Text, nullable=False)

    # Gated vocabulary — 'kill' or 'revive'. Mirror of migration 0039's CHECK.
    action: Mapped[str] = mapped_column(Text, nullable=False)

    # Operator-supplied rationale for kill (>=10 chars at the API boundary);
    # null on revive. Kept free-form here; the constraint lives in the
    # Pydantic KillProjectRequest.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Counts of drained / resumed items at action time. Always-a-dict at the
    # response boundary — DB DEFAULT '{}'::jsonb covers omit-on-insert, and
    # the service always populates a concrete dict so a sparse audit row
    # never lands as JSONB null. Value-tolerant on read (dict[str, Any]).
    drain_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        default=dict,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    project: Mapped["Project"] = relationship("Project", back_populates="audit_entries")

    __table_args__ = (
        CheckConstraint(
            "action IN ('kill', 'revive', 'pause', 'unpause', 'pause_override')",
            name="ck_projects_audit_action_valid",
        ),
        Index(
            "ix_projects_audit_project_created",
            "project_id",
            text("created_at DESC"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ProjectsAudit id={self.id} project_id={self.project_id} "
            f"action={self.action!r} actor={self.actor!r}>"
        )
