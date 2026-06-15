"""cost-forecast columns — projects.cost_forecast_threshold_usd + tasks.forecast_cost_usd (Kanban #1304)

Revision ID: 0068_cost_forecast_threshold
Revises: 0067_usage_events
Create Date: 2026-06-15 01:00 UTC

Adds the two columns the pre-task cost forecast (#1304) needs:

1. `projects.cost_forecast_threshold_usd` NUMERIC(10,2) NULL — the per-project
   gate threshold. NULL = "no gate" (never show the confirm modal). A non-null
   value is the USD ceiling above which the FE offers the run/sample/cancel
   confirmation. nullable (no DB server_default) so existing rows read NULL =
   no gate until opted in; the API CREATE schema seeds new projects at $1.00.
   CHECK `ck_projects_cost_forecast_threshold_nonneg` mirrors the budget-cap
   CHECK style already on `projects` (ck_projects_budget_caps_nonneg) — the
   Pydantic `ge=0` boundary is the first wall; this CHECK is defense-in-depth
   against raw-SQL drift.

2. `tasks.forecast_cost_usd` NUMERIC(10,4) NULL — the pre-run forecast persisted
   by `POST /api/tasks/{id}/cost-forecast`. Mirrors the EXISTING
   `tasks.estimated_cost_usd` column shape exactly (Numeric(10,4), nullable, no
   CHECK — see migration 0025). NULL until the operator first runs the forecast.
   Persisting it (alongside the post-hoc `estimated_cost_usd`) is what makes the
   ±30% calibration loop measurable on tasks created after this ships.

No existing code reads either column, so the live API stays healthy with the
columns absent until this migration applies (devops applies after review).

Downgrade drops the CHECK + both columns; additive feature, no restore path.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0068_cost_forecast_threshold"
down_revision = "0067_usage_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # projects.cost_forecast_threshold_usd — NUMERIC(10,2) NULL (NULL = no gate).
    op.add_column(
        "projects",
        sa.Column("cost_forecast_threshold_usd", sa.Numeric(10, 2), nullable=True),
    )
    # Mirror of ck_projects_budget_caps_nonneg style — NULL allowed, else >= 0.
    op.create_check_constraint(
        "ck_projects_cost_forecast_threshold_nonneg",
        "projects",
        "cost_forecast_threshold_usd IS NULL OR cost_forecast_threshold_usd >= 0",
    )

    # tasks.forecast_cost_usd — mirror of tasks.estimated_cost_usd (#944): same
    # Numeric(10,4), nullable, no CHECK / no comment (parity with that column).
    op.add_column(
        "tasks",
        sa.Column("forecast_cost_usd", sa.Numeric(10, 4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tasks", "forecast_cost_usd")
    op.drop_constraint(
        "ck_projects_cost_forecast_threshold_nonneg", "projects", type_="check"
    )
    op.drop_column("projects", "cost_forecast_threshold_usd")
