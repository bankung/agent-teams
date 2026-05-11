"""projects.working_path / working_repo / agent_overrides (Kanban #777)

Revision ID: 0012_projects_path_repo_ovr
Revises: 0011_tasks_is_pending
Create Date: 2026-05-11 17:00 UTC

Adds three optional columns to the `projects` table to capture project-root
metadata that lives ABOVE the per-lane scaffold paths (`paths_web`, `paths_api`,
`paths_db`) already on the row:

- `working_path` (TEXT, NULL) — absolute filesystem root of the project on the
  developer's machine. Distinct from `paths_web/api/db` which are LANE-specific
  sub-paths within a project; `working_path` is the single project root.
- `working_repo` (TEXT, NULL) — git remote URL (or local repo identifier) for
  the project. Optional; not every registered project is git-tracked.
- `agent_overrides` (JSONB, NULL DEFAULT '{}') — per-project overrides for
  subagent prompts / standards / role behavior. Default `{}` keeps the field
  introspectable as `{}` after INSERT without forcing the app layer to send it.
  NULL is permitted at the DB level (e.g., legacy backfill) and is treated
  identically to `{}` by the current app layer; PATCH-to-null is normalized to
  `{}` by the router. See Kanban #777 WARN-1 + WARN-6.

All three columns are nullable (and the JSONB carries a literal `{}` default),
so PG 16 treats this as a metadata-only column add — no heap rewrite, no row
backfill, instant on the existing rows.

Note on naming overlap: `paths_web/api/db` (existing) and `working_path` (new)
are intentionally KEPT side by side. The dev-team scaffold uses the lane-paths
for per-role code; `working_path` is the project root that wraps them. Do not
rename or merge — the two concepts are orthogonal and both will be referenced
by upcoming dev-backend slices.

Down: drop the three columns.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0012_projects_path_repo_ovr"
down_revision = "0011_tasks_is_pending"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("working_path", sa.Text(), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("working_repo", sa.Text(), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column(
            "agent_overrides",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "agent_overrides")
    op.drop_column("projects", "working_repo")
    op.drop_column("projects", "working_path")
