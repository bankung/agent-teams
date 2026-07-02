"""ix_tasks_subagent_models_gin: GIN index on tasks.subagent_models (Kanban #2352)

Revision ID: 0074_ix_subagent_models_gin
Revises: 0073_ix_task_gates_answered
Create Date: 2026-07-02 04:30 UTC

The agent-gallery spawn-history query (`fetch_agent_spawns` in
`api/src/services/agent_spawns.py`) filters on
`t.subagent_models @> :containment` as a pre-filter before the per-element
LATERAL unnest, on every `GET /api/agents/{name}` detail load. No index existed
on `subagent_models` — confirmed via live `pg_indexes` — so this predicate ran
a full sequential scan of `tasks` on every call.

WHY jsonb_path_ops (mirrors migration 0064's `ix_tasks_ac_gin` reasoning):
`jsonb_path_ops` is the smaller/faster GIN opclass that indexes ONLY the `@>`
(containment) operator — no `jsonb_path_exists` (`@?`) support. The query
above uses `@>` exclusively (`subagent_models @> '[{"agent": :name}]'`), which
is exactly what this opclass serves; the default `jsonb_ops` opclass supports
more operators but is larger, so we deliberately choose the leaner one.

NULL is never stored in a `@>`-only GIN index for a NULL column, but
`subagent_models` is NOT NULL DEFAULT '[]'::jsonb (migration 0024) — an empty
array is a normal indexable value, just never matched by a non-empty `@>`
probe.

Downgrade drops the index; no data touch either direction.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0074_ix_subagent_models_gin"
down_revision = "0073_ix_task_gates_answered"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # GIN index for the `@>` containment pre-filter in fetch_agent_spawns()
    # (jsonb_path_ops opclass — indexes @> only; see migration docstring for
    # why not jsonb_path_exists). tasks is small — plain CREATE INDEX (not
    # CONCURRENTLY) is fine, mirroring migration 0064's posture for the
    # sibling ix_tasks_ac_gin index.
    op.create_index(
        "ix_tasks_subagent_models_gin",
        "tasks",
        ["subagent_models"],
        postgresql_using="gin",
        postgresql_ops={"subagent_models": "jsonb_path_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_subagent_models_gin", table_name="tasks")
