"""GOV1 hard kill switch — projects.is_killed + tasks.kill_frozen + projects_audit (Kanban #1209)

Revision ID: 0039_aa1_kill_switch
Revises: 0038_projects_team_content
Create Date: 2026-05-19 17:00 UTC

Operator emergency-stop per project (the rare-event tier; GOV3 will ship the
soft-pause audit pipeline for the regular tier). Three surfaces in one slice:

1. `projects` hot-pause columns (separate from `is_active` which is the cold
   archive flag):
   - `is_killed` BOOLEAN NOT NULL DEFAULT false  — hot pause state.
   - `killed_at` TIMESTAMPTZ NULL                 — first-kill timestamp.
   - `killed_reason` TEXT NULL                    — operator-supplied rationale.

2. `tasks.kill_frozen` BOOLEAN NOT NULL DEFAULT false — per-task frozen-in-place
   marker. Open TODO/IN_PROGRESS rows are flipped to TRUE on project kill and
   cleared on revive. Preserves the operator mental model: "ค้างไว้แบบไหน
   กลับมาแบบนั้น" (D3 — freeze in place, do NOT archive).

3. NEW `projects_audit` table — append-only kill/revive audit log. Future
   project-auditor agent (GOV2) reads here. NOT extending `tasks_history`
   (semantics mismatch — those are per-task UPDATE/DELETE snapshots; kill is
   a project-level event). Element shape:
   - actor TEXT NOT NULL       — 'operator' / 'system' / 'project-auditor' etc.
   - action TEXT NOT NULL CHECK IN ('kill','revive')
   - reason TEXT NULL          — required at API layer for kill; null on revive.
   - drain_summary JSONB NOT NULL DEFAULT '{}' — counts of drained / resumed
     items captured at action time (recurring_suspended, frozen_tasks, …).

Indexes:
- `ix_projects_audit_project_created` on (project_id, created_at DESC) — covers
  the per-project audit timeline lookup the future project-auditor will hit.

NO data migration needed — defaults backfill existing rows cleanly
(`is_killed=false`, `kill_frozen=false`). PG 16 metadata-only ADD COLUMN with
DEFAULT for the bool columns; existing 91 projects + 510 tasks see no row
rewrite (instant on Postgres ≥ 11).

NO audit trigger on `projects_audit` (parity with `transactions` / `sessions`
/ `tool_calls` — V1 ledger-style tables that are themselves the audit trail).

Downgrade caveats:
- `projects_audit` rows lost on downgrade. Operator should `pg_dump
  projects_audit` first if rolling back in a production-like env.
- Dropping `is_killed=true` rows is silently fine — they revert to "not
  killed" semantics, but the operator should `is_killed=false` them via the
  API first if they want the audit trail to reflect the rollback.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0039_aa1_kill_switch"
down_revision = "0038_projects_team_content"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- projects hot-pause columns -----------------------------------------
    op.add_column(
        "projects",
        sa.Column(
            "is_killed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "killed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "projects",
        sa.Column("killed_reason", sa.Text(), nullable=True),
    )

    # ---- tasks frozen-in-place marker ---------------------------------------
    op.add_column(
        "tasks",
        sa.Column(
            "kill_frozen",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # ---- projects_audit ledger ----------------------------------------------
    op.create_table(
        "projects_audit",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
        ),
        sa.Column(
            "project_id",
            sa.BigInteger(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "drain_summary",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "action IN ('kill', 'revive')",
            name="ck_projects_audit_action_valid",
        ),
    )
    op.create_index(
        "ix_projects_audit_project_created",
        "projects_audit",
        ["project_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_projects_audit_project_created", table_name="projects_audit"
    )
    op.drop_table("projects_audit")
    op.drop_column("tasks", "kill_frozen")
    op.drop_column("projects", "killed_reason")
    op.drop_column("projects", "killed_at")
    op.drop_column("projects", "is_killed")
