"""project_resources table — attach files / URLs to a project (Kanban #1302)

Revision ID: 0059_project_resources
Revises: 0058_session_runs_cache_tokens
Create Date: 2026-06-04 01:00 UTC

A first-class entity so ANY team can attach a file or an external URL to a
project, optionally pinned to one task. Rows are TAG-BEARING. This migration is
SCHEMA + ORM + Pydantic only (Kanban #1302 / X.1) — the POST upload endpoint
is a SEPARATE task (#1309 / X.2). No code queries `project_resources` yet, so
the live API stays healthy with the table absent until this migration applies.

DESIGN (locked spec, #1302):

1. `project_resources` table — the attachment entity.
   - `project_id` BIGINT NOT NULL, FK ON DELETE CASCADE (AC5) — a resource is
     deleted with its project (mirrors `tasks` / `milestones` / `transactions`).
   - `task_id` BIGINT NULLABLE, FK ON DELETE SET NULL (AC5) — a resource may be
     pinned to one task; deleting that task DETACHES the resource (it survives,
     just unpinned). Mirrors the `tasks.blocked_by` / `tasks.milestone_id`
     SET-NULL posture (defense-in-depth; app soft-deletes in practice).
   - `kind` TEXT NOT NULL + CHECK IN ('file','link') — the discriminator
     (constants.ResourceKind). 'file' = an uploaded object; 'link' = an
     external URL.
   - `filename` TEXT NULLABLE — the stored object's name (REQUIRED for 'file').
   - `url` TEXT NULLABLE — the external URL (REQUIRED for 'link').
   - `content_type` TEXT NULLABLE — MIME type for 'file' rows (the upload
     endpoint #1309 populates it; NULL for 'link').
   - `size_bytes` BIGINT NULLABLE — byte size for 'file' rows (NULL for 'link').
   - `label` TEXT NULLABLE — optional human display label.
   - `tags` JSONB NOT NULL DEFAULT '[]'::jsonb — TAG-BEARING (#1302). JSONB list
     of strings, MIRRORS the `projects.sources` / `projects.required_binaries`
     precedent (element-shape validation lives at the API layer; no DB CHECK on
     element shape). DEFAULT '[]' so rows without tags read an empty list.
   - `status` SMALLINT NOT NULL DEFAULT 1 + CHECK IN (0,1) — uniform soft-delete
     flag (RecordStatus). App code never issues a SQL DELETE — soft-delete only.
   - `created_at` / `updated_at` TIMESTAMPTZ NOT NULL DEFAULT now().

   CHECK `ck_project_resources_kind_fields` (AC3) enforces the per-kind required
   fields AT THE DB LEVEL:
       (kind='file' AND filename IS NOT NULL) OR (kind='link' AND url IS NOT NULL)
   So a `kind='file'` row carrying a url but NO filename is REJECTED by the DB
   (IntegrityError), independent of any app-layer Pydantic validation. The
   `kind`-enum CHECK (`ck_project_resources_kind_valid`) gates the discriminator
   value itself.

2. Indexes (AC4):
   - `ix_project_resources_project_id` — list-by-project (the hot query, every
     team's "show this project's attachments").
   - `ix_project_resources_task_id` — PARTIAL index WHERE task_id IS NOT NULL.
     Most resources are project-scoped (task_id NULL); the partial index keeps
     the per-task reverse-lookup ("resources pinned to this task") cheap without
     indexing the sparse NULL majority. Mirrors the sparse-partial-index policy
     used for `tasks.next_fire_at` / `tasks.scheduled_at`.

History capture: NO audit trigger on `project_resources` (mirrors `milestones`
/ `handoff_templates` / `sessions` precedent — operator-CRUD metadata, not
lifecycle-tracked work).

Downgrade caveat: dropping `project_resources` deletes any attached resources.
No restore path; operator re-attaches after downgrade.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0059_project_resources"
down_revision = "0058_session_runs_cache_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_resources",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
        ),
        # AC5: ON DELETE CASCADE — resource dies with its project.
        sa.Column(
            "project_id",
            sa.BigInteger(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # AC5: ON DELETE SET NULL — resource SURVIVES task deletion (detaches).
        sa.Column(
            "task_id",
            sa.BigInteger(),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("label", sa.Text(), nullable=True),
        # Tag-bearing (#1302) — JSONB list of strings. Mirror of
        # projects.sources / projects.required_binaries (element-shape validated
        # at the API layer; no DB CHECK on shape). DEFAULT '[]' so omitted rows
        # read an empty list rather than NULL.
        sa.Column(
            "tags",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        # Uniform soft-delete flag (RecordStatus) — MIRRORS tasks / milestones.
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
        # Discriminator enum gate — mirror of constants.ResourceKind.ALL.
        sa.CheckConstraint(
            "kind IN ('file', 'link')",
            name="ck_project_resources_kind_valid",
        ),
        # AC3: per-kind required fields enforced at the DB level. A 'file' row
        # MUST carry a filename; a 'link' row MUST carry a url. A 'file' row with
        # url-but-no-filename violates this CHECK → IntegrityError.
        sa.CheckConstraint(
            "(kind = 'file' AND filename IS NOT NULL) "
            "OR (kind = 'link' AND url IS NOT NULL)",
            name="ck_project_resources_kind_fields",
        ),
        # Soft-delete flag gate — mirror of tasks' ck_tasks_status_valid intent.
        sa.CheckConstraint(
            "status IN (0, 1)",
            name="ck_project_resources_status_valid",
        ),
    )
    # AC4: project_id index — list-by-project hot query.
    op.create_index(
        "ix_project_resources_project_id",
        "project_resources",
        ["project_id"],
    )
    # AC4: task_id PARTIAL index — sparse reverse-lookup (resources pinned to a
    # task). WHERE task_id IS NOT NULL keeps the index off the NULL majority.
    op.create_index(
        "ix_project_resources_task_id",
        "project_resources",
        ["task_id"],
        postgresql_where=sa.text("task_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_project_resources_task_id", table_name="project_resources"
    )
    op.drop_index(
        "ix_project_resources_project_id", table_name="project_resources"
    )
    op.drop_table("project_resources")
