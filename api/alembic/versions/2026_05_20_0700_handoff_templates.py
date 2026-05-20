"""handoff_templates table + tasks.handoff_template_id (Kanban #1004)

Revision ID: 0045_handoff_templates
Revises: 0044_projects_team_sem
Create Date: 2026-05-20 07:00 UTC

Auto-handoff: operators define reusable handoff templates; when a task that
carries a non-null `handoff_template_id` flips to DONE, the API spawns a child
task derived from that template — title interpolated from the parent, AC pre-
populated, assigned_role set, optional description carrying parent context.

Loop guard: the child's `handoff_template_id` is explicitly set to NULL on
spawn (router-side; the FK ON DELETE SET NULL here is the DB-level defense
against accidental chain corruption via raw SQL).

DESIGN locked by Lead (see #1004 spawn brief):
  - DB-backed table (vs inline JSONB on `tasks`) — CRUD endpoints are awkward
    on inline JSONB; templates are reusable identities across many parents.
  - Sibling pattern is `tasks.action_template_id` (Kanban #1006) which used
    file-based YAML because action templates are global static data. Handoff
    templates are operator-CRUD-able runtime data → DB-backed is the right call.

Schema:

1. `handoff_templates` table — the recipe.
   - `name` operator-facing label; unique per project (partial unique index
     gated on `status = 1` per soft-delete convention).
   - `title_pattern` Python `str.format(parent_title=...)` template — the
     chosen renderer (NOT jinja2; stdlib `.format` covers the locked feature
     set without adding a dep).
   - `task_kind` / `task_type` / `default_priority` mirror the tasks columns
     they pre-fill on the child.
   - `default_assigned_role` INTEGER NULLABLE, validated 1..50 at the Pydantic
     boundary (parity with `tasks.assigned_role`).
   - `ac_outline` JSONB — list[str] of AC text entries; each becomes an
     {text, status="pending"} item on the child's `acceptance_criteria`.
   - `carry_context_to_comment` BOOLEAN — when true, child gets a description
     block citing parent id/title/status_change_reason.
   - `project_id` BIGINT NULLABLE — NULL = global template (cross-project).
     FK ON DELETE CASCADE so deleting a project takes its templates with it.
   - `status` SMALLINT — uniform soft-delete (postgresql/soft-delete.md).
   - `created_at` / `updated_at` standard timestamps.

2. `tasks.handoff_template_id` BIGINT NULLABLE — FK ON DELETE SET NULL.
   When non-null + the task's PATCH transitions process_status to 5 (DONE),
   the router spawns a child task; child's own `handoff_template_id` is set
   to NULL (loop guard).

Indexes:
  - `ux_handoff_templates_name_project` — partial UNIQUE on `(name, COALESCE(project_id, 0))`
    WHERE `status = 1`. Re-use of name after soft-delete OK. COALESCE(0) so
    NULL project_id slots (global templates) share a single uniqueness namespace
    across all NULLs (PG's NULL semantics would otherwise let every NULL bypass
    the unique gate).
  - `ix_handoff_templates_status` — keeps default-filter (`status = 1`) selective.
  - `ix_handoff_templates_project_id` — list filter by project.

NO audit trigger on `handoff_templates` (mirrors `sessions` precedent — operator
CRUD on metadata, not lifecycle-tracked work).

PG 16 metadata-only ADD COLUMN on `tasks.handoff_template_id` (nullable, no
default) — no heap rewrite on the ~520 existing rows.

Downgrade caveats:
  - Dropping `tasks.handoff_template_id` discards the FK silently; no data
    migration (the auto-handoff feature is opt-in per task).
  - Dropping `handoff_templates` deletes any operator-configured templates.
    No restore path; operator re-creates after downgrade.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0045_handoff_templates"
down_revision = "0044_projects_team_sem"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- handoff_templates table --------------------------------------------
    op.create_table(
        "handoff_templates",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("title_pattern", sa.String(512), nullable=False),
        sa.Column("task_kind", sa.String(16), nullable=False),
        sa.Column("task_type", sa.String(16), nullable=False),
        sa.Column(
            "default_priority",
            sa.SmallInteger(),
            nullable=False,
            server_default="3",
        ),
        sa.Column("default_assigned_role", sa.Integer(), nullable=True),
        sa.Column(
            "ac_outline",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "carry_context_to_comment",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "project_id",
            sa.BigInteger(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # Soft-delete (postgresql/soft-delete.md) — uniform 0/1 column.
        sa.CheckConstraint(
            "status IN (0, 1)", name="ck_handoff_templates_status_valid"
        ),
        # task_kind / task_type enum gates — mirrors tasks.task_kind / .task_type
        # CHECKs at the recipe level so a corrupt template can't be created.
        sa.CheckConstraint(
            "task_kind IN ('ai', 'human')",
            name="ck_handoff_templates_task_kind_valid",
        ),
        sa.CheckConstraint(
            "task_type IN ('bug', 'feature', 'chore', 'docs', 'refactor', 'audit')",
            name="ck_handoff_templates_task_type_valid",
        ),
        # Priority mirrors tasks.priority CHECK (1..4) — but the template uses
        # SmallInteger DEFAULT 3 (the 'normal-high'-ish prior; ASSURED safe).
        sa.CheckConstraint(
            "default_priority IN (1, 2, 3, 4)",
            name="ck_handoff_templates_default_priority_valid",
        ),
        # assigned_role 1..50 mirrors the app-layer range gate (constants.TaskRole).
        sa.CheckConstraint(
            "default_assigned_role IS NULL OR (default_assigned_role >= 1 AND default_assigned_role <= 50)",
            name="ck_handoff_templates_default_assigned_role_range",
        ),
    )

    # Soft-delete-aware partial unique on (name, COALESCE(project_id, 0)).
    # COALESCE so all NULL project_id rows (global templates) share a single
    # uniqueness namespace rather than PG's "NULL != NULL" letting unlimited
    # duplicates land.
    op.create_index(
        "ux_handoff_templates_name_project",
        "handoff_templates",
        ["name", sa.text("COALESCE(project_id, 0)")],
        unique=True,
        postgresql_where=sa.text("status = 1"),
    )
    op.create_index(
        "ix_handoff_templates_status",
        "handoff_templates",
        ["status"],
    )
    op.create_index(
        "ix_handoff_templates_project_id",
        "handoff_templates",
        ["project_id"],
    )

    # ---- tasks.handoff_template_id ------------------------------------------
    # PG 16 metadata-only ADD COLUMN — nullable, no DEFAULT, no heap rewrite.
    # FK ON DELETE SET NULL so deleting a template (rare; templates are
    # soft-deleted in practice) does not cascade-delete tasks that referenced
    # it. Mirrors `spawned_from_task_id` / `blocked_by` posture (defense-in-
    # depth — app never hard-deletes templates either).
    op.add_column(
        "tasks",
        sa.Column(
            "handoff_template_id",
            sa.BigInteger(),
            sa.ForeignKey("handoff_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "handoff_template_id")
    op.drop_index("ix_handoff_templates_project_id", table_name="handoff_templates")
    op.drop_index("ix_handoff_templates_status", table_name="handoff_templates")
    op.drop_index(
        "ux_handoff_templates_name_project", table_name="handoff_templates"
    )
    op.drop_table("handoff_templates")
