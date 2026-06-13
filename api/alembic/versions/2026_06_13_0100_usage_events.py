"""usage_events table — append-only Mode-A cost ledger (Kanban #2354)

Revision ID: 0067_usage_events
Revises: 0066_tool_calls_lead_source
Create Date: 2026-06-13 01:00 UTC

An APPEND-ONLY ledger of token-usage events. The Mode-A ingest endpoint
(`POST /api/usage/events`, this feature's P1) writes one row per Claude Code
turn / subagent invocation; cost is computed SERVER-SIDE via
`services/cost_tracker` (same price card + cache multipliers the session-run
metering uses). Hooks/parser (P2) and the monthly rollup/UI (P3) are SEPARATE
later tasks — this migration ships the table only.

No existing code queries `usage_events`, so the live API stays healthy with the
table absent until this migration applies (devops applies after review).

DESIGN (locked spec, #2354, review fixes applied 2026-06-13):

1. `usage_events` table — the append-only event entity.
   - `id` BIGSERIAL PK (sa.Identity, mirrors task_comments 0062 / project_resources
     0059) — monotonic with insertion.
   - `occurred_at` TIMESTAMPTZ NOT NULL DEFAULT now() — the event's REAL time
     (the client supplies it for correct cross-day/month bucketing; falls back
     to now() when omitted). Distinct from `created_at` (the persist time).
   - `project_id` BIGINT NOT NULL, FK projects(id) ON DELETE CASCADE — the event
     dies with its project (the ledger has no meaning once the project is gone).
   - `task_id` BIGINT NULL, FK tasks(id) ON DELETE SET NULL — a Mode-A event may
     have no owning task; if the task is hard-deleted the event survives with a
     NULL task_id (a token-usage fact is durable independent of the task row).
   - `session_ext_id` TEXT NULL — the Claude Code session uuid STRING (an
     external identifier, NOT a FK onto the `sessions` table).
   - `agent_name` TEXT NULL — the subagent name; NULL = Lead/main.
   - `provider` TEXT NOT NULL DEFAULT 'anthropic' — cost-card provider key.
   - `model` TEXT NOT NULL — the model identifier (resolved to a price-card key
     server-side; an unknown model still stores the row with cost 0).
   - `input_tokens` / `output_tokens` / `cache_read_input_tokens` /
     `cache_creation_input_tokens` BIGINT NOT NULL DEFAULT 0 — the token totals.
   - `cost_usd` NUMERIC(10,4) NOT NULL DEFAULT 0 — server-computed USD cost
     (4dp, same scale as session_runs.total_cost_usd).
   - `is_estimate` BOOLEAN NOT NULL DEFAULT true — Mode-A token counts are an
     estimate until reconciled.
   - `source` TEXT NOT NULL DEFAULT 'mode_a' — provenance tag.
   - `dedup_key` TEXT NULL — idempotency key. A NULL dedup_key is always
     insertable (Postgres treats NULLs as distinct in a UNIQUE index); a
     non-NULL repeat within the SAME PROJECT collapses to the existing row (the
     endpoint returns it). The same dedup_key string used across different projects
     inserts cleanly as a distinct (project_id, dedup_key) pair — no cross-project
     collision.
   - `created_at` TIMESTAMPTZ NOT NULL DEFAULT now() — the persist timestamp.

   NO `updated_at` / NO soft-delete column — append-only: a row never changes
   and the only removal is FK CASCADE on project hard-delete.

2. Indexes:
   - `ix_usage_events_occurred_at` ON (occurred_at) — time-window rollups (P3).
   - `ix_usage_events_project_id` ON (project_id) — per-project aggregation.
   - `ix_usage_events_task_id` ON (task_id) — per-task cost lookup.
   - `uq_usage_events_project_dedup_key` UNIQUE ON (project_id, dedup_key) —
     per-project idempotency guard. The composite scope means: a given dedup_key
     is idempotent within one project only; the same key in another project is a
     distinct insert. Also prevents the cross-project enumeration oracle that a
     global unique would expose. The endpoint's SELECT-first / IntegrityError path
     relies on this index.

History capture: NO audit trigger (mirrors task_comments / project_resources
precedent — already append-only, no update/delete history to capture).

Downgrade caveat: dropping `usage_events` deletes every ledger row. The feature
is additive (no existing data depends on it); no restore path.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0067_usage_events"
down_revision = "0066_tool_calls_lead_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # ON DELETE CASCADE — the ledger row dies with its project.
        sa.Column(
            "project_id",
            sa.BigInteger(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # ON DELETE SET NULL — a token-usage fact outlives its task row.
        sa.Column(
            "task_id",
            sa.BigInteger(),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # The Claude Code session uuid STRING — NOT a FK onto `sessions`.
        sa.Column("session_ext_id", sa.Text(), nullable=True),
        sa.Column("agent_name", sa.Text(), nullable=True),
        sa.Column(
            "provider",
            sa.Text(),
            nullable=False,
            server_default="anthropic",
        ),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column(
            "input_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "output_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "cache_read_input_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "cache_creation_input_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "cost_usd",
            sa.Numeric(10, 4),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "is_estimate",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "source",
            sa.Text(),
            nullable=False,
            server_default="mode_a",
        ),
        # Idempotency key — NULL always insertable (NULLs are distinct in UNIQUE);
        # a non-NULL repeat within the SAME project collapses to the existing row.
        # The uniqueness is scoped to (project_id, dedup_key) so the same key
        # string used in another project inserts cleanly without collision.
        sa.Column("dedup_key", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # Per-project idempotency guard. Composite on (project_id, dedup_key)
        # prevents cross-project key collisions (M1/W1 review fix, 2026-06-13).
        sa.UniqueConstraint(
            "project_id", "dedup_key", name="uq_usage_events_project_dedup_key"
        ),
    )
    op.create_index(
        "ix_usage_events_occurred_at", "usage_events", ["occurred_at"]
    )
    op.create_index(
        "ix_usage_events_project_id", "usage_events", ["project_id"]
    )
    op.create_index("ix_usage_events_task_id", "usage_events", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_usage_events_task_id", table_name="usage_events")
    op.drop_index("ix_usage_events_project_id", table_name="usage_events")
    op.drop_index("ix_usage_events_occurred_at", table_name="usage_events")
    op.drop_table("usage_events")
