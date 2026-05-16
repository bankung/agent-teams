"""projects: per-project HITL timeout (Kanban #989)

Revision ID: 0029_projects_hitl_timeout
Revises: 0028_tool_calls
Create Date: 2026-05-16 03:00 UTC

Adds `projects.hitl_timeout_hours INTEGER NULL` — the per-project knob the
on-demand HITL timeout gate consults inside `GET /api/tasks/next-autorun`.

Mirrors the #951 budget-cap pattern:
- NULL = unlimited (preserves current "pause indefinitely" behavior).
- On-demand enforcement (no APScheduler / cron) — the gate runs every time
  the headless autorun loop polls /next-autorun. Zero scheduler infra.
- Halt-only — when a paused HITL task (`halt_reason IN ('question','decision')`
  on a process_status=BLOCKED row) has been waiting longer than the
  configured threshold, the gate stamps `halt_reason='hitl_timeout'` so the
  operator can decide cancel / retry / re-prompt. NEVER auto-cancels.

Pydantic enforces `ge=1` on writes — at least one hour when set. CHECK is
defense-in-depth against raw-SQL drift; mirrors `ck_projects_budget_caps_nonneg`
precedent.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0029_projects_hitl_timeout"
down_revision = "0028_tool_calls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("hitl_timeout_hours", sa.Integer(), nullable=True),
    )
    # Defense-in-depth: timeout (when set) must be >= 1. NULL allowed
    # (= unlimited). Pydantic ProjectUpdate is the first wall (422); CHECK
    # catches raw-SQL drift. Same precedent as `ck_projects_budget_caps_nonneg`.
    op.create_check_constraint(
        "ck_projects_hitl_timeout_positive",
        "projects",
        "hitl_timeout_hours IS NULL OR hitl_timeout_hours >= 1",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_projects_hitl_timeout_positive", "projects", type_="check"
    )
    op.drop_column("projects", "hitl_timeout_hours")
