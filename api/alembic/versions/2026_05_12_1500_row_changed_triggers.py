"""row_changed triggers — pg_notify on tasks + projects writes (Kanban #782)

Revision ID: 0016_row_changed_triggers
Revises: 0015_tasks_task_type
Create Date: 2026-05-12 15:00 UTC

Adds AFTER-INSERT/UPDATE/DELETE triggers on `tasks` and `projects` that emit
`pg_notify('row_changed', payload)` where payload is a small JSON object:

    {"table": "tasks"|"projects",
     "op": "insert"|"update"|"delete",
     "id": <bigint>,
     "project_id": <bigint>,   -- only on tasks; absent on projects
     "ts": "<now()::text>"}

Used by `GET /api/events/stream` (SSE) to push row-changed events to the FE
in lieu of polling. One channel (`row_changed`) for both tables — the SSE
fan-out filters per-listener by `project_id`.

Payload budget: PG `pg_notify` caps payload at ~8 KB. Our payload is well
under 100 bytes by design — clients refetch via the REST endpoints on event
arrival rather than receiving full-row payloads. This keeps NOTIFY tiny and
avoids duplicating the row-serialization logic.

Trigger function returns NEW for INSERT/UPDATE (AFTER trigger ignores return
value but standards demand NEW for AFTER-INSERT/UPDATE and OLD for
AFTER-DELETE so the function works correctly if reused as BEFORE later).

Down: drop triggers (one per table) then drop the function.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0016_row_changed_triggers"
down_revision = "0015_tasks_task_type"
branch_labels = None
depends_on = None


# Helper duplication per standards/sqlalchemy/migrations.md — migrations
# never import app code. The channel name is duplicated in
# src/services/row_changed_listener.py CHANNEL constant; if you change one,
# change the other.
_CHANNEL = "row_changed"


def upgrade() -> None:
    # PL/pgSQL function — builds JSON payload conditionally on TG_TABLE_NAME.
    # `projects` has no `project_id` column; `tasks` does. We branch in SQL
    # rather than write 2 functions because the divergence is one column.
    #
    # `op_lower` mirrors TG_OP lowercased — matches the wire contract on the
    # SSE event ("insert"|"update"|"delete"), not Postgres's "INSERT" form.
    #
    # NOTE: payload size budget = 8 KB (pg_notify limit). Our payloads
    # average <100 bytes; comment here for the next maintainer.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION notify_row_changed() RETURNS trigger AS $$
        DECLARE
            row_id      bigint;
            proj_id     bigint;
            op_lower    text;
            payload     text;
        BEGIN
            -- Row id + project_id from NEW (INSERT/UPDATE) or OLD (DELETE).
            IF (TG_OP = 'DELETE') THEN
                row_id := OLD.id;
                IF (TG_TABLE_NAME = 'tasks') THEN
                    proj_id := OLD.project_id;
                END IF;
            ELSE
                row_id := NEW.id;
                IF (TG_TABLE_NAME = 'tasks') THEN
                    proj_id := NEW.project_id;
                END IF;
            END IF;

            op_lower := lower(TG_OP);

            -- Build JSON. `tasks` payload includes project_id; `projects`
            -- payload omits it (projects-level events reach all listeners).
            IF (TG_TABLE_NAME = 'tasks') THEN
                payload := json_build_object(
                    'table', TG_TABLE_NAME,
                    'op', op_lower,
                    'id', row_id,
                    'project_id', proj_id,
                    'ts', now()::text
                )::text;
            ELSE
                payload := json_build_object(
                    'table', TG_TABLE_NAME,
                    'op', op_lower,
                    'id', row_id,
                    'ts', now()::text
                )::text;
            END IF;

            -- Fire-and-forget NOTIFY. pg_notify payload ≤ 8KB (our payload
            -- is well under 200 bytes by design).
            PERFORM pg_notify('{_CHANNEL}', payload);

            IF (TG_OP = 'DELETE') THEN
                RETURN OLD;
            ELSE
                RETURN NEW;
            END IF;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # AFTER INSERT/UPDATE/DELETE triggers on both tables.
    op.execute(
        """
        CREATE TRIGGER tasks_row_changed_trg
        AFTER INSERT OR UPDATE OR DELETE ON tasks
        FOR EACH ROW EXECUTE FUNCTION notify_row_changed();
        """
    )
    op.execute(
        """
        CREATE TRIGGER projects_row_changed_trg
        AFTER INSERT OR UPDATE OR DELETE ON projects
        FOR EACH ROW EXECUTE FUNCTION notify_row_changed();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS projects_row_changed_trg ON projects;")
    op.execute("DROP TRIGGER IF EXISTS tasks_row_changed_trg ON tasks;")
    op.execute("DROP FUNCTION IF EXISTS notify_row_changed();")
