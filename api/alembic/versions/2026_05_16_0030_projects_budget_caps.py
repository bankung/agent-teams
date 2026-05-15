"""projects: add per-project budget cap columns (Kanban #951)

Revision ID: 0026_projects_budget_caps
Revises: 0025_tasks_cost_estimates
Create Date: 2026-05-16 00:30 UTC

Per-project soft-warn (80%) / hard-halt (100%) budget enforcement on
auto-pickup task gating. Builds on #944 (per-task cost estimation) +
#871 (session_runs.total_cost_usd per-run cost). Closes the burn-loop
by giving the headless engine a "is this project over its cap?" gate
before invoking a runnable task.

Three nullable NUMERIC(10,2) columns mirroring `session_runs.total_cost_usd`
+ `tasks.estimated_cost_usd` (which are 4-place — caps are user-typed
dollars, 2 places is enough for the UI / EditProjectModal):

  - budget_daily_usd    : rolling 24h cap, resets at midnight in UTC
  - budget_monthly_usd  : rolling 1-month cap, resets 1st of month UTC
  - budget_total_usd    : lifetime cap, never resets (manual user clear via PATCH)

NULL semantics = UNLIMITED (current behavior; pre-#951 every project
had implicit-NULL caps and burned freely). Every existing row reads
NULL on this migration — PG 16 metadata-only ADD COLUMN, no heap
rewrite. The budget_enforcer service short-circuits on NULL → returns
`soft_warn=False, hard_halt=False` regardless of spend.

The "reset" semantics is FREE — `compute_spend(project_id, since=midnight)`
recomputes spend on-demand by filtering `tasks.completed_at` /
`session_runs.created_at` >= the appropriate anchor. No cron job
needed; no rollover table; no APScheduler hook. Documented in
`src/services/budget_enforcer.py`.

Reset anchor is UTC for this slice (no per-project recurrence_timezone
column exists on `projects`; `recurrence_timezone` is a TASK-level field
for cron templates, not a project default). Future migration could add
`projects.tz` if per-project TZ becomes load-bearing — out of scope here.

Wire-contract mirrors (atomic with this migration — see #951 spawn brief):
  - api/src/models/project.py        : 3 Mapped[Decimal | None] columns
  - api/src/schemas/project.py       : ProjectRead exposes; ProjectUpdate
                                       accepts (Decimal | None, >= 0 validator)
  - api/src/services/budget_enforcer.py : compute_spend + check_budget pure service
  - api/src/routers/tasks.py         : next-autorun budget gate (hard_halt skips
                                       pickup + stamps tasks.halt_reason on the
                                       candidate row's first poll)
  - api/tests/test_budget_enforcer.py : per-AC coverage (NULL=unlimited, 80%
                                       soft, 100% hard, manual bypass,
                                       double-count avoidance, daily window)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0026_projects_budget_caps"
down_revision = "0025_tasks_cost_estimates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("budget_daily_usd", sa.Numeric(10, 2), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("budget_monthly_usd", sa.Numeric(10, 2), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("budget_total_usd", sa.Numeric(10, 2), nullable=True),
    )
    # Defense-in-depth: caps must be >= 0 (NULL allowed = unlimited).
    # Pydantic ProjectUpdate is the first wall (422); CHECK catches raw-SQL
    # drift. Same precedent as `tasks.priority` / `projects.status` CHECKs.
    op.create_check_constraint(
        "ck_projects_budget_caps_nonneg",
        "projects",
        "(budget_daily_usd IS NULL OR budget_daily_usd >= 0) AND "
        "(budget_monthly_usd IS NULL OR budget_monthly_usd >= 0) AND "
        "(budget_total_usd IS NULL OR budget_total_usd >= 0)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_projects_budget_caps_nonneg", "projects", type_="check")
    op.drop_column("projects", "budget_total_usd")
    op.drop_column("projects", "budget_monthly_usd")
    op.drop_column("projects", "budget_daily_usd")
