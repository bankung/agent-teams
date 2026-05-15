"""tool_calls — specialist-tool audit table (Kanban #980)

Revision ID: 0028_tool_calls
Revises: 0027_projects_tools_config
Create Date: 2026-05-16 02:00 UTC

Adds the permanent audit table that records EVERY specialist-tool
invocation issued by the LangGraph specialist nodes. Wired into the
tool-use loop in #981; this slice (#980) ships the table, the writer
service, and the read endpoint that the FE timeline (parallel spawn)
consumes.

Schema shape (locked at #949 design review):

  - id                BIGINT PK IDENTITY  (audit rows are permanent +
                                          append-only; need a stable surrogate
                                          even though task_id + invoked_at
                                          would form a natural key)
  - task_id           BIGINT NOT NULL     FK -> tasks(id) ON DELETE CASCADE.
                                          BIGINT (NOT INTEGER as the #980
                                          brief listed) — `tasks.id` is
                                          BIGINT identity, and matching the
                                          referenced column's type avoids an
                                          implicit-cast join path. Deviation
                                          from brief is documented in the
                                          final report.
                                          Cascade rationale: the audit row
                                          has no meaning without its parent
                                          task; if the task is hard-deleted
                                          (operator psql cleanup of a typo'd
                                          row) the audit rows go too. Note
                                          that the app NEVER hard-deletes
                                          tasks — soft-delete (status=0)
                                          leaves the FK intact and the audit
                                          rows remain.
  - invoked_at        TIMESTAMPTZ         server-now() default; the tool
                                          invocation timestamp.
  - tool_name         TEXT NOT NULL       e.g. 'file_edit', 'http_get'.
                                          Free-form text (no FK to a registry
                                          table — the registry is in-memory in
                                          the langgraph container). The
                                          (tool_name) index supports cross-task
                                          analytics for "how often did X
                                          fire?" without a full scan.
  - tier              TEXT NOT NULL       'read' / 'write' / 'network' /
                                          'destructive'. Mirrors
                                          `langgraph/tools/base.py::Tier`.
                                          No CHECK constraint — the langgraph
                                          container is the source of truth for
                                          tiers and may add new values; the
                                          audit log shouldn't 23514 on a new
                                          tier.
  - input_json        JSONB NOT NULL      the validated tool input args.
                                          Stored as-is so a future replay /
                                          forensic audit can reconstruct the
                                          exact call.
  - success           BOOLEAN NOT NULL    mirrors ToolResult.success.
  - error_code        TEXT NULL           machine-readable code from
                                          ToolResult.error_code (e.g.
                                          'permission_denied', 'timeout').
                                          NULL on success.
  - error_msg         TEXT NULL           human-readable msg truncated to
                                          1 KB at the writer (#949 Q10 lock).
  - output_summary    TEXT NULL           first 256 chars of ToolResult.output
                                          (raw byte cut per #949 Q10 lock A;
                                          UTF-8 mid-char risk accepted because
                                          this is a SUMMARY for the timeline
                                          UI, not a full transcript). NULL
                                          when output is None.
  - duration_ms       INTEGER NOT NULL    wall-clock duration of the call.
  - permission_decision TEXT NOT NULL     'auto_allow' / 'halt' / 'reject'.
                                          Mirrors `PermissionDecision` enum
                                          in `langgraph/tools/permission_gate.py`.
                                          No CHECK constraint — same reasoning
                                          as `tier` (langgraph container is
                                          source of truth; may add verdicts).

Indexes:
  - (task_id, invoked_at DESC)  hot path: the GET endpoint orders by
                                invoked_at DESC for a single task. The
                                composite covers the WHERE + ORDER BY in
                                one B-tree walk.
  - (invoked_at)                future cross-task analytics ("show me all
                                tool calls in the last hour"). Cheap to
                                maintain; pays for itself on the first
                                report query.
  - (tool_name)                 filtering analytics ("how many file_edit
                                calls fired this week?"). Same rationale.

NO soft-delete column. Audit rows are permanent (Kanban #980 brief +
soft-delete.md exempt-tables clause — `tasks_history` precedent). The
table grows monotonically; eventual sweep policy is out of scope (see
soft-delete.md "Operational consequences").

NO audit trigger on this table — it IS the audit log; recursing would
be silly. Mirrors the `tasks_history` / `sessions` precedent.

NO X-Project-Id-equivalent column on the row itself — project ownership
is derived via task_id -> tasks.project_id. The GET endpoint enforces
the X-Project-Id header gate at the task level (same as the rest of the
tasks sub-resource endpoints).

Wire-contract mirrors (atomic with this migration):
  - api/src/models/tool_call.py             : ORM model
  - api/src/schemas/tool_call.py            : ToolCallRead (no Create/Update —
                                              clients can't write audit rows)
  - api/src/services/tool_call_writer.py    : record_tool_call() service
  - api/src/routers/tool_calls.py           : GET /api/tasks/{id}/tool-calls
  - langgraph/audit.py                      : HTTP-side wrapper (#981 wires
                                              it into the tool-use loop)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0028_tool_calls"
down_revision = "0027_projects_tools_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tool_calls",
        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "task_id",
            sa.BigInteger(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "invoked_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("tier", sa.Text(), nullable=False),
        sa.Column(
            "input_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("output_summary", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("permission_decision", sa.Text(), nullable=False),
    )
    # Composite index for the GET /api/tasks/{id}/tool-calls hot path —
    # WHERE task_id = ? ORDER BY invoked_at DESC. The DESC ordering on
    # the second column lets PG walk the index in the response order
    # without a separate sort.
    op.create_index(
        "ix_tool_calls_task_id_invoked_at",
        "tool_calls",
        ["task_id", sa.text("invoked_at DESC")],
    )
    # Standalone (invoked_at) for future cross-task analytics.
    op.create_index(
        "ix_tool_calls_invoked_at",
        "tool_calls",
        ["invoked_at"],
    )
    # Tool-name filtering analytics.
    op.create_index(
        "ix_tool_calls_tool_name",
        "tool_calls",
        ["tool_name"],
    )


def downgrade() -> None:
    op.drop_index("ix_tool_calls_tool_name", table_name="tool_calls")
    op.drop_index("ix_tool_calls_invoked_at", table_name="tool_calls")
    op.drop_index("ix_tool_calls_task_id_invoked_at", table_name="tool_calls")
    op.drop_table("tool_calls")
