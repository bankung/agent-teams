"""DeliveryTarget DSL — projects.notification_targets + tasks.notification_targets + tasks_history 'N' op (Kanban #1224)

Revision ID: 0041_notification_targets
Revises: 0040_aa3_soft_pause
Create Date: 2026-05-19 20:00 UTC

Phase 1 of the push-notification routing layer (followup #1218 daily-digest +
HITL halt + kill-switch confirm). Borrows the SHAPE of Hermes'
`gateway/delivery.py` DeliveryTarget DSL: priority-ordered list of explicit
delivery targets with local-file fallback.

Three surfaces in one slice:

1. `projects.notification_targets JSONB NULL` — project-level default targets.
   Element shape (validated at the API boundary by Pydantic NotificationTarget):
       [{"kind": "telegram", "chat_id": "123", "priority": 1, "label": "..."}]
   NULL = no default targets configured (router falls back to local-file write
   per AC4). Mirrors `agent_overrides` / `tools_config` "element shape lives at
   the API layer, no DB CHECK" precedent.

2. `tasks.notification_targets JSONB NULL` — per-task override (rarely set).
   Same element shape. NULL = inherit project default. Resolution priority
   (per AC3): task override > project default > local-file fallback. Per-task
   override is the "I want this one alert to ping a different chat" hatch;
   the project default is the normal case.

3. EXTEND `ck_tasks_history_operation_valid` from `IN ('U', 'D')` to
   `IN ('U', 'D', 'N')` — adds 'N' for NOTIFY delivery-attempt audit rows.
   The notification_router writes one history row per delivery attempt
   (target + ok + detail + priority) into `snapshot` JSONB; this is NOT a
   per-task UPDATE/DELETE snapshot (those keep firing via the existing
   tasks_audit_trg trigger). Single-char operation code matches the existing
   CHAR(1) column shape — no column type change needed.

NO data migration: nullable columns default to NULL; the operation CHECK
extension is metadata-only on PG 16 (no row rewrite). Existing ~520 tasks +
~95 projects unaffected.

Downgrade caveats:
- Dropping `notification_targets` discards any configured targets silently.
- Reverting the operation CHECK to `IN ('U', 'D')` will FAIL if any
  `operation='N'` rows exist; caller must DELETE those manually first (the
  NOTIFY audit trail is append-only and considered preserved per kill_switch
  precedent — D4 history-preservation pattern).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0041_notification_targets"
down_revision = "0040_aa3_soft_pause"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- projects.notification_targets --------------------------------------
    # Nullable, no server_default — explicit NULL = "no targets configured"
    # (router falls back to local-file write). Mirrors agent_overrides /
    # tools_config "element-shape validated at API boundary, no DB CHECK on
    # shape" precedent.
    op.add_column(
        "projects",
        sa.Column(
            "notification_targets",
            sa.dialects.postgresql.JSONB(),
            nullable=True,
        ),
    )

    # ---- tasks.notification_targets -----------------------------------------
    # Nullable; NULL = inherit project default (per AC2). Resolution priority
    # in the router service: task override > project default > local-file.
    op.add_column(
        "tasks",
        sa.Column(
            "notification_targets",
            sa.dialects.postgresql.JSONB(),
            nullable=True,
        ),
    )

    # ---- EXTEND tasks_history.operation CHECK -------------------------------
    # CHECK constraints are immutable in PG — DROP + ADD is the only path.
    # Add 'N' for NOTIFY delivery-attempt rows. The existing tasks_audit_trg
    # trigger writes 'U'/'D' only; 'N' rows are inserted directly by the
    # notification_router service (see api/src/services/notification_router.py).
    op.drop_constraint(
        "ck_tasks_history_operation_valid",
        "tasks_history",
        type_="check",
    )
    op.create_check_constraint(
        "ck_tasks_history_operation_valid",
        "tasks_history",
        "operation IN ('U', 'D', 'N')",
    )


def downgrade() -> None:
    # Revert CHECK first. Will FAIL if any operation='N' rows exist — caller
    # must DELETE those manually before downgrade (intentional — the NOTIFY
    # audit trail is preserved, parity with kill_switch D4 pattern).
    op.drop_constraint(
        "ck_tasks_history_operation_valid",
        "tasks_history",
        type_="check",
    )
    op.create_check_constraint(
        "ck_tasks_history_operation_valid",
        "tasks_history",
        "operation IN ('U', 'D')",
    )

    op.drop_column("tasks", "notification_targets")
    op.drop_column("projects", "notification_targets")
