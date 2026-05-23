"""GOV3 soft-pause governance — projects.is_paused + audit_enabled + tasks.allow_during_pause + 'pause'/'unpause'/'pause_override' actions + 'audit' task_type (Kanban #1211)

Revision ID: 0040_aa3_soft_pause
Revises: 0039_aa1_kill_switch
Create Date: 2026-05-19 19:00 UTC

Phase 1 of GOV3 — api-side governance ONLY. The auto-fire mechanism (Path A:
Lead session manually invokes auditor) is deferred. This slice adds:

1. `projects` soft-pause columns (separate from GOV1's hard kill — DB CHECK
   enforces mutual exclusion):
   - `is_paused` BOOLEAN NOT NULL DEFAULT false — soft pause for review.
   - `paused_at` TIMESTAMPTZ NULL                — first-pause timestamp.
   - `paused_reason` TEXT NULL                   — operator/system rationale.
   - `audit_enabled` BOOLEAN NOT NULL DEFAULT true — per-project opt-out for
     projects that don't want governance audits.
   - CHECK ck_projects_kill_pause_mutex: NOT (is_killed AND is_paused).

2. `tasks` per-spawn override columns (D6 escape hatch for the pause gate):
   - `allow_during_pause` BOOLEAN NOT NULL DEFAULT false.
   - `allow_during_pause_reason` TEXT NULL.
   - CHECK ck_tasks_pause_reason_length: allow=false OR reason >= 10 chars.

3. EXTEND `ck_projects_audit_action_valid` to allow three new actions:
   - 'pause'          — pause_project service writes this row.
   - 'unpause'        — unpause_project service writes this row.
   - 'pause_override' — POST /api/tasks with allow_during_pause=true on a
                        paused project; the bypass IS the audit signal so
                        operators can review override frequency (D6 callout).

4. EXTEND `ck_tasks_task_type_valid` to allow `'audit'` — Phase 1 doesn't
   auto-create audit-template tasks (AC#2 deferred), but the post-PATCH
   hook in routers/tasks.py needs `task_type='audit'` to be a valid value
   so callers can manually create audit tasks + the PATCH-to-DONE hook
   fires `apply_flag_from_audit_report`. Without this CHECK extension the
   value isn't insertable.

Indexes:
- No new indexes this slice. is_paused has low cardinality (2 values across
  ~96 rows) — full scans are cheap; the per-project enforcement queries
  always carry project_id=N which is already indexed via the PK.

NO data migration needed — defaults backfill existing rows cleanly
(`is_paused=false`, `audit_enabled=true`, `allow_during_pause=false`). PG 16
metadata-only ADD COLUMN with DEFAULT for the bool columns; existing 96
projects + ~517 tasks see no row rewrite (instant on Postgres >= 11).

Downgrade caveats:
- Dropping the columns silently discards is_paused=true rows (they revert
  to "not paused" semantics). Same approach as GOV1's downgrade for is_killed.
- DROP CONSTRAINT then re-ADD with the old definitions for the two CHECK
  extensions — reversible without data loss as long as no rows currently
  carry the new action values.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0040_aa3_soft_pause"
down_revision = "0039_aa1_kill_switch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- projects soft-pause columns ---------------------------------------
    op.add_column(
        "projects",
        sa.Column(
            "is_paused",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "paused_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "projects",
        sa.Column("paused_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column(
            "audit_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )

    # ---- DB-side mutual exclusion: cannot be both killed AND paused ---------
    op.create_check_constraint(
        "ck_projects_kill_pause_mutex",
        "projects",
        "NOT (is_killed AND is_paused)",
    )

    # ---- tasks per-spawn override columns -----------------------------------
    op.add_column(
        "tasks",
        sa.Column(
            "allow_during_pause",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "tasks",
        sa.Column("allow_during_pause_reason", sa.Text(), nullable=True),
    )

    # CHECK: when allow_during_pause=true the reason must be present + >= 10 chars.
    # The Pydantic TaskCreate boundary enforces the same rule; this is defense-
    # in-depth against raw-SQL drift (mirrors the kill-switch reason >=10 chars
    # pattern that lives at Pydantic only — GOV3 promotes it to the DB layer for
    # the bypass column specifically since this is operator-audit critical).
    op.create_check_constraint(
        "ck_tasks_pause_reason_length",
        "tasks",
        "allow_during_pause = FALSE OR "
        "(allow_during_pause_reason IS NOT NULL AND length(allow_during_pause_reason) >= 10)",
    )

    # ---- EXTEND projects_audit.action CHECK to allow pause/unpause/override --
    # CHECK constraints are immutable in PG — DROP + ADD is the only path.
    op.drop_constraint(
        "ck_projects_audit_action_valid", "projects_audit", type_="check"
    )
    op.create_check_constraint(
        "ck_projects_audit_action_valid",
        "projects_audit",
        "action IN ('kill', 'revive', 'pause', 'unpause', 'pause_override')",
    )

    # ---- EXTEND tasks.task_type CHECK to allow 'audit' ----------------------
    # Same DROP + ADD pattern. The 'audit' type marks tasks whose handler ran
    # the project-auditor agent and whose audit_report drives the flag-creation
    # pipeline. Phase 1 doesn't auto-fire these — manual create + PATCH-to-DONE
    # is the v1 path until AC#2 (Path A) is wired.
    op.drop_constraint("ck_tasks_task_type_valid", "tasks", type_="check")
    op.create_check_constraint(
        "ck_tasks_task_type_valid",
        "tasks",
        "task_type IN ('bug', 'feature', 'chore', 'docs', 'refactor', 'audit')",
    )


def downgrade() -> None:
    # Reverse the CHECK extensions first. If any row currently carries the new
    # values, DROP/ADD will fail at the CHECK validation step — caller must
    # clean up first (UPDATE pause/unpause/pause_override rows to one of the
    # GOV1 values, UPDATE 'audit' tasks to one of the pre-GOV3 values).
    op.drop_constraint("ck_tasks_task_type_valid", "tasks", type_="check")
    op.create_check_constraint(
        "ck_tasks_task_type_valid",
        "tasks",
        "task_type IN ('bug', 'feature', 'chore', 'docs', 'refactor')",
    )

    op.drop_constraint(
        "ck_projects_audit_action_valid", "projects_audit", type_="check"
    )
    op.create_check_constraint(
        "ck_projects_audit_action_valid",
        "projects_audit",
        "action IN ('kill', 'revive')",
    )

    op.drop_constraint(
        "ck_tasks_pause_reason_length", "tasks", type_="check"
    )
    op.drop_column("tasks", "allow_during_pause_reason")
    op.drop_column("tasks", "allow_during_pause")

    op.drop_constraint(
        "ck_projects_kill_pause_mutex", "projects", type_="check"
    )
    op.drop_column("projects", "audit_enabled")
    op.drop_column("projects", "paused_reason")
    op.drop_column("projects", "paused_at")
    op.drop_column("projects", "is_paused")
