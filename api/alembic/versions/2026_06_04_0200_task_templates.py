"""task_templates table — reusable Kanban-task starting points (Kanban #1303)

Revision ID: 0060_task_templates
Revises: 0059_project_resources
Create Date: 2026-06-04 02:00 UTC

A config table holding per-TEAM task templates: a name/icon + a mustache-style
({{placeholder}}) description + an AC template array + default task metadata.
The placeholder-substitution helper (`src/services/template_render.py`) renders
`description_template` + `acceptance_criteria_template[].text` against caller
values when a new task is spawned from a template. SCHEMA + ORM + Pydantic +
CRUD router this slice (#1303). The UI picker (X.6) and data-team seeds (D.6)
are SEPARATE tasks — no code outside this slice queries `task_templates` yet, so
the live API stays healthy with the table absent until this migration applies.

DESIGN (locked spec, #1303 — with the #1620 team-CHECK correction below):

1. `task_templates` table — the template entity.
   - `team` TEXT NOT NULL — the owning team. APP-VALIDATED against the single-
     source team enum (`constants.ProjectTeam.ALL`), MIRRORING how
     `projects.team` is validated since #1620. There is DELIBERATELY *NO* DB
     CHECK constraint on `team`: migration `0051_drop_projects_team_check`
     removed per-team CHECKs in favor of the app-layer enum so adding a team
     (e.g. `netops`) needs no migration. Reintroducing a CHECK here would
     regress #1620 and omit netops/seo/sem/data-analytics. The router's
     create/update handlers reject `team not in ProjectTeam.ALL` with 422
     (mirror of `routers/projects.py`).
   - `name` TEXT NOT NULL — human display name of the template.
   - `icon` TEXT NULLABLE — optional icon hint for the FE picker.
   - `description_template` TEXT NOT NULL — mustache-style body; {{placeholder}}
     tokens are rendered by `template_render.render_template`.
   - `acceptance_criteria_template` JSONB NOT NULL DEFAULT '[]'::jsonb — a list
     of `{text, ...}` AC objects whose `text` field is ALSO mustache-rendered.
     Element-shape validated at the API layer (mirrors projects.sources /
     project_resources.tags — no DB CHECK on element shape).
   - `default_task_type` TEXT NOT NULL DEFAULT 'feature' — seeds the spawned
     task's `task_type` (constants.TaskType). App-validated, NO DB CHECK (a new
     TaskType value would otherwise need a migration here too).
   - `default_priority` SMALLINT NOT NULL DEFAULT 2 — seeds `tasks.priority`
     (constants.TaskPriority). App-validated.
   - `default_task_kind` TEXT NOT NULL DEFAULT 'ai' — seeds `tasks.task_kind`
     (constants.TaskKind). App-validated.
   - `placeholders` JSONB NOT NULL DEFAULT '[]'::jsonb — declared placeholder
     names the template expects (a list of strings, FE picker prompts for each).
   - `status` SMALLINT NOT NULL DEFAULT 1 — uniform soft-delete flag
     (RecordStatus): 0=disabled/soft-deleted, 1=active. App code never issues a
     SQL DELETE — DELETE flips status=0.
   - `created_at` TIMESTAMPTZ NOT NULL DEFAULT now().
   - `updated_at` TIMESTAMPTZ NULLABLE — set EXPLICITLY by the router on PATCH
     (no DB trigger; NULL until first edit). This differs from
     project_resources (which keeps updated_at NOT NULL DEFAULT now()) and
     deliberately follows the #1303 spec.

   STATUS is the ONLY DB CHECK on this table (ck_task_templates_status_valid,
   IN (0,1)) — the uniform soft-delete gate, mirror of project_resources. All
   enum-bearing TEXT columns (team, default_task_type, default_task_kind) are
   plain TEXT validated app-side per the #1620 doctrine.

2. Index (AC4):
   - `idx_task_templates_team_status` ON (team, status) — the hot list query is
     "active templates for team X" (`WHERE team=$1 AND status=1`). A composite
     (team, status) index serves it directly.

History capture: NO audit trigger on `task_templates` (mirrors `milestones` /
`handoff_templates` / `project_resources` / `sessions` precedent — operator-CRUD
config metadata, NOT lifecycle-tracked work; `tasks_history` is the `tasks`
table's trigger-only trail). Operator-action provenance for POST/PATCH/DELETE is
captured by the `operator_auth` JSONL trail (the gate wired onto those routes).

Downgrade caveat: dropping `task_templates` deletes any defined templates. No
restore path; operator re-creates after downgrade.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0060_task_templates"
down_revision = "0059_project_resources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_templates",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
        ),
        # App-validated against ProjectTeam.ALL — NO DB CHECK (#1620 doctrine).
        sa.Column("team", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("icon", sa.Text(), nullable=True),
        sa.Column("description_template", sa.Text(), nullable=False),
        # AC template array — list of {text, ...} objects. Element shape
        # validated at the API layer (mirror of projects.sources). DEFAULT '[]'.
        sa.Column(
            "acceptance_criteria_template",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        # Default task metadata seeded onto tasks spawned from this template.
        # App-validated (TaskType / TaskPriority / TaskKind) — NO DB CHECK.
        sa.Column(
            "default_task_type",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'feature'"),
        ),
        sa.Column(
            "default_priority",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("2"),
        ),
        sa.Column(
            "default_task_kind",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'ai'"),
        ),
        # Declared placeholder names — JSONB list of strings. Element shape
        # validated at the API layer. DEFAULT '[]'.
        sa.Column(
            "placeholders",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        # Uniform soft-delete flag (RecordStatus) — 0=disabled, 1=active.
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
        # NULL until first edit; the router sets it explicitly on PATCH
        # (no DB trigger). Mirror of the #1303 spec.
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        # Soft-delete flag gate — the ONLY CHECK on this table. Mirror of
        # project_resources' ck_project_resources_status_valid intent.
        sa.CheckConstraint(
            "status IN (0, 1)",
            name="ck_task_templates_status_valid",
        ),
    )
    # AC4: composite (team, status) index — serves the hot list query
    # `WHERE team=$1 AND status=1` directly.
    op.create_index(
        "idx_task_templates_team_status",
        "task_templates",
        ["team", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_task_templates_team_status", table_name="task_templates"
    )
    op.drop_table("task_templates")
