"""credentials_vault UNIQUE index: partial on status=1 (ACTIVE) only (Kanban #1375)

Revision ID: 0050_credentials_unique_active
Revises: 0049_transaction_source_unique
Create Date: 2026-05-21 11:00 UTC

Bug fix: `0048_credentials_vault` created `ux_project_credentials_project_name`
as an unbounded UNIQUE spanning ALL rows including soft-deleted (status=0).
Consequence: DELETE + re-POST with the same (project_id, name) pair returns 409
permanently until a manual hard-DELETE. The soft-deleted row holds the slot.

Fix: replace the unbounded index with a partial UNIQUE index that only covers
ACTIVE rows (`WHERE status = 1`). Soft-deleted rows (status=0) are excluded from
the uniqueness check, so the slot is immediately reclaimed after a soft-delete.

Reference pattern: `ux_projects_name_active` in `0002_soft_delete_and_lead.py`
uses the same `postgresql_where=sa.text('status = 1')` shape for projects.name.

ORM note: `api/src/models/credential.py` declares the old unbounded index in
`__table_args__` as `Index("ux_project_credentials_project_name", ...)`. The ORM
`Index` declaration does NOT control which index exists on the live DB (Alembic
migrations own the DDL), so no ORM change is needed for this bugfix — the DB-level
partial index is the authoritative constraint. A future hygiene pass can update the
ORM to declare `postgresql_where=text('status = 1')` but that is out of scope here.

Up:
  - Drop `ux_project_credentials_project_name` (unbounded UNIQUE).
  - Create `ux_project_credentials_project_name_active` — partial UNIQUE on
    (project_id, name) WHERE status = 1.

Down:
  - Drop `ux_project_credentials_project_name_active`.
  - Re-create `ux_project_credentials_project_name` (unbounded UNIQUE) to
    match the 0048 migration exactly — round-trip is up→down→up safe.

PG 16 — table is newly created (0048 is same-session); no concurrent readers.
DROP + CREATE without CONCURRENTLY is fine.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0050_credentials_unique_active"
down_revision = "0049_transaction_source_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the old unbounded UNIQUE (spans deleted rows — see bug description).
    op.drop_index(
        "ux_project_credentials_project_name",
        table_name="project_credentials",
    )

    # Replace with a partial UNIQUE that excludes soft-deleted rows.
    # Only active (status=1) rows compete for the (project_id, name) slot.
    op.create_index(
        "ux_project_credentials_project_name_active",
        "project_credentials",
        ["project_id", "name"],
        unique=True,
        postgresql_where=sa.text("status = 1"),
    )


def downgrade() -> None:
    # Drop the partial index added by this migration.
    op.drop_index(
        "ux_project_credentials_project_name_active",
        table_name="project_credentials",
    )

    # Restore the original unbounded UNIQUE from 0048 — exact replica.
    op.create_index(
        "ux_project_credentials_project_name",
        "project_credentials",
        ["project_id", "name"],
        unique=True,
    )
