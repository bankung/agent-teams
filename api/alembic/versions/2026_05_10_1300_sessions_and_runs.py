"""sessions + session_runs + session_compacts (CTX-1 hybrid storage foundation)

Revision ID: 0008_sessions_and_runs
Revises: 0007_task_kind_and_recurrence
Create Date: 2026-05-10 13:00 UTC

Foundation slice for context-management scope-lock 2026-05-10 (Kanban #716).
Hybrid storage: DB metadata + filesystem markdown. Per project x per Claude
Code instance scope; soft token budget; Haiku 4.5 compact runner (CTX-4).

Up — single transaction adds three tables:

  - sessions: one row per Lead bootstrap / master-agent process for a project.
    Multi-instance friendly: multiple `status='active'` rows per project_id are
    allowed (no partial unique on `(project_id, status)`). The partial index is
    a HOT-PATH SCAN ACCELERATOR, not a uniqueness gate.
    + CHECK ck_sessions_status_valid: status IN ('active','compacting','closed')
    + INDEX ix_sessions_project_id (broad listing)
    + PARTIAL INDEX ix_sessions_project_id_active ON (project_id) WHERE status='active'
      (frontend / Lead bootstrap "list active sessions for project" hot path)

  - session_runs: one row per task fire / manual run within a session.
    + CHECK ck_session_runs_status_valid: status IN ('running','done','error','timeout')
    + INDEX ix_session_runs_session_id, ix_session_runs_task_id

  - session_compacts: one row per compact event within a session.
    + CHECK ck_session_compacts_trigger_valid: trigger_kind IN ('size','manual','run_count')
    + INDEX ix_session_compacts_session_id

NO audit trigger on these tables — they self-audit via the compact archive
history (each compact event archives the prior `session.md` to
`_sessions/<id>/archive/compact_NNN.md`). Tasks audit trigger (`tasks_audit_trg`)
is for `tasks` only and is NOT replicated here.

FK ondelete decisions:
- sessions.project_id ON DELETE CASCADE: when a project is hard-deleted (admin
  path; soft-delete only flips status), drop its sessions atomically.
- session_runs.session_id ON DELETE CASCADE: same reasoning — session lifetime
  is tighter than project.
- session_runs.task_id ON DELETE SET NULL: a task may be soft-deleted later;
  preserve the run audit row with a NULL task pointer instead of cascading.
- session_compacts.session_id ON DELETE CASCADE.

Filesystem layout (`session_root_path` is the column; default value computed
post-INSERT in the router as `_sessions/<id>/`). Tree:
  _sessions/<id>/
    session.md             (Compacted History + Recent Activity)
    archive/compact_NNN.md (per-compact archive)
    cards/<task_id>.md     (per-task heartbeat log)

Defaults intentionally live as `server_default` so explicit INSERTs from tests
exercise the DB defaults; the application layer reads them through ORM.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0008_sessions_and_runs"
down_revision = "0007_task_kind_and_recurrence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. sessions
    op.create_table(
        "sessions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.BigInteger(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("process_label", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="active",
        ),
        sa.Column("token_budget_per_run", sa.BigInteger(), nullable=True),
        sa.Column(
            "compacted_history_ceiling_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("13000"),
        ),
        sa.Column(
            "recent_activity_ceiling_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("15000"),
        ),
        sa.Column("session_root_path", sa.Text(), nullable=False),
        sa.Column(
            "started_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "closed_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_check_constraint(
        "ck_sessions_status_valid",
        "sessions",
        "status IN ('active','compacting','closed')",
    )
    op.create_index("ix_sessions_project_id", "sessions", ["project_id"])
    # Partial index: hot-path "active sessions for this project" lookup. NOT a
    # uniqueness gate — multiple active rows per project are allowed (multi-
    # instance support; one Claude Code session per terminal).
    op.create_index(
        "ix_sessions_project_id_active",
        "sessions",
        ["project_id"],
        postgresql_where=sa.text("status = 'active'"),
    )

    # 2. session_runs
    op.create_table(
        "session_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            sa.BigInteger(),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.BigInteger(),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="running",
        ),
        sa.Column(
            "started_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "finished_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "total_input_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "total_output_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "total_context_chars",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "total_cost_usd",
            sa.Numeric(10, 4),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "budget_warning",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("card_log_path", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_check_constraint(
        "ck_session_runs_status_valid",
        "session_runs",
        "status IN ('running','done','error','timeout')",
    )
    op.create_index("ix_session_runs_session_id", "session_runs", ["session_id"])
    op.create_index("ix_session_runs_task_id", "session_runs", ["task_id"])

    # 3. session_compacts
    op.create_table(
        "session_compacts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            sa.BigInteger(),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trigger_kind", sa.String(length=16), nullable=False),
        sa.Column("archive_path", sa.Text(), nullable=False),
        sa.Column("before_tokens", sa.BigInteger(), nullable=False),
        sa.Column("after_tokens", sa.BigInteger(), nullable=False),
        sa.Column("compact_model", sa.String(length=64), nullable=False),
        sa.Column(
            "compact_cost_usd",
            sa.Numeric(10, 4),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "compacted_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_check_constraint(
        "ck_session_compacts_trigger_valid",
        "session_compacts",
        "trigger_kind IN ('size','manual','run_count')",
    )
    op.create_index(
        "ix_session_compacts_session_id", "session_compacts", ["session_id"]
    )


def downgrade() -> None:
    # Reverse order of upgrade().
    op.drop_index("ix_session_compacts_session_id", table_name="session_compacts")
    op.drop_constraint(
        "ck_session_compacts_trigger_valid", "session_compacts", type_="check"
    )
    op.drop_table("session_compacts")

    op.drop_index("ix_session_runs_task_id", table_name="session_runs")
    op.drop_index("ix_session_runs_session_id", table_name="session_runs")
    op.drop_constraint(
        "ck_session_runs_status_valid", "session_runs", type_="check"
    )
    op.drop_table("session_runs")

    op.drop_index("ix_sessions_project_id_active", table_name="sessions")
    op.drop_index("ix_sessions_project_id", table_name="sessions")
    op.drop_constraint("ck_sessions_status_valid", "sessions", type_="check")
    op.drop_table("sessions")
