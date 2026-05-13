"""projects.sources — per-project source list (Kanban #778)

Revision ID: 0020_projects_sources
Revises: 0019_tasks_interaction_kind
Create Date: 2026-05-13 11:32 UTC

Adds one optional JSONB column to the `projects` table to capture a list of
project-level "sources" (links, references, upstream URLs, doc anchors etc.)
that the UI can show in a popover. Per the Kanban #778 spec slice, the column
is created in isolation here; the ORM attribute, Pydantic shape, and PATCH
endpoint wiring are owned by the dev-backend lane and land in a follow-up.

- `sources` (JSONB, NULL, DEFAULT '[]'::jsonb)
    Array of source entries. NULL is permitted at the DB level (legacy /
    bare-INSERT path) and is treated identically to `[]` by the upcoming
    app layer. The DEFAULT `'[]'::jsonb` keeps the column introspectable as
    an empty array after a vanilla INSERT without forcing every caller to
    send it.
    Element shape (e.g., `{url, label, ...}`) is intentionally NOT enforced
    by a DB CHECK — same precedent as `projects.config`, `projects.agent_overrides`,
    and `tasks.acceptance_criteria` (Pydantic validation at the API boundary
    is sufficient).

- CHECK `ck_projects_sources_length` — `jsonb_array_length(sources) <= 20`
    Hard cap on list size. The CHECK tolerates NULL by virtue of SQL three-
    valued logic (`NULL <= 20` is UNKNOWN, which CHECK treats as passing);
    explicit `sources IS NULL` short-circuit is not needed.

PG 16 treats a nullable JSONB column add with a constant DEFAULT as
metadata-only — no heap rewrite, no row backfill, instant on the existing
rows. Each existing row will read back as `sources = []` after the upgrade.

Down: drop the CHECK first, then the column.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0020_projects_sources"
down_revision = "0019_tasks_interaction_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "sources",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_projects_sources_length",
        "projects",
        "jsonb_array_length(sources) <= 20",
    )


def downgrade() -> None:
    op.drop_constraint("ck_projects_sources_length", "projects", type_="check")
    op.drop_column("projects", "sources")
