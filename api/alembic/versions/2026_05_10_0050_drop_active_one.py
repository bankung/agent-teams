"""drop ux_projects_active_one — partial unique index obsolete after session-scoped active

Revision ID: 0006_drop_active_one
Revises: 0005_run_mode_and_consent
Create Date: 2026-05-10 00:50 UTC

Phase 2 of the session-scoped active project shift (Kanban #694; Phase 1 = CLAUDE.md
bootstrap rewrite shipped commit e0939e0; Phase 3 = X-Project-Id header gate shipped
commit 83d9314). The "single active project" invariant — formerly enforced by the
partial unique index `ux_projects_active_one` ON projects(is_active)
WHERE is_active IS TRUE AND status = 1 — is obsolete: each Claude Code session now
binds to a project by name (independent terminals run in parallel against different
projects), so multiple rows may legitimately have `is_active=true` simultaneously.

Up:
  - drop INDEX ux_projects_active_one (no other constraint references it; bare
    DROP is sufficient).

Down (reverse): re-create the partial unique index with the SAME predicate the
0002_soft_delete_and_lead migration shipped (`is_active IS TRUE AND status = 1`)
so the round-trip lands on the same physical index definition. Round-trip
up→down→up is verified to preserve the seeded `agent-teams` row (id=1,
is_active=true) byte-identical.

App-side coupling (handled in the same commit, not in this migration file):
  - drop the `Index("ux_projects_active_one", ...)` declaration from
    `api/src/models/project.py` so app and migration stay in sync.
  - PATCH /api/projects/{id} no longer atomically clears every other row's
    `is_active` (the side-effect was load-bearing on the dropped invariant).
  - GET /api/projects/active returns 410 Gone (deprecation per Kanban #694).

The `ux_projects_name_active` partial unique index on `name` is UNTOUCHED —
project names remain unique among active rows.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0006_drop_active_one"
down_revision = "0005_run_mode_and_consent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ux_projects_active_one", table_name="projects")


def downgrade() -> None:
    # Re-create with the SAME predicate the 0002 migration shipped — verified
    # against `2026_05_08_0300_soft_delete_and_lead.py:160-167` so the
    # round-trip up→down→up lands on byte-identical index DDL.
    op.create_index(
        "ux_projects_active_one",
        "projects",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active IS TRUE AND status = 1"),
    )
