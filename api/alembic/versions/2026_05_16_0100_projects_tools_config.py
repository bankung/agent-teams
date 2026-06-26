"""projects: per-project specialist-tool permission gate config (Kanban #979)

Revision ID: 0027_projects_tools_config
Revises: 0026_projects_budget_caps
Create Date: 2026-05-16 01:00 UTC

Adds `projects.tools_config JSONB NULL` — the per-project knob that the
specialist tool permission gate (`langgraph/tools/permission_gate.py`)
consults BEFORE invoking any registered tool. Locked default ships
"permissive read, halt on everything else" so a fresh project can
auto-execute read-only inspection (git_status / git_diff / future
shell_run --read-only equivalents) without prompting, but ANY mutating /
networking / destructive call hits a halt gate for human review.

The 4-tier permission taxonomy (`langgraph/tools/base.py::Tier`):

  - read         → no state change (git_status, git_diff, file read)
  - write        → modifies local state, reversible (file_edit, file_write,
                   git_commit)
  - network      → external calls, may have side effects (http_get/post —
                   shipped by #978)
  - destructive  → cannot be safely undone (shell_run; future schema-DDL
                   tools, force-push, rm -rf, etc.)

Locked default (Q2 → Option B "permissive read" — design lock #949):

    {
      "tools_enabled": false,
      "auto_allow_tiers": ["read"],
      "halt_tiers": ["write", "network", "destructive"],
      "http_hosts": []
    }

`tools_enabled` is the project-level master kill switch. While it is
false (the ship default for every fresh / existing project), the gate
returns `reject` for EVERY tool regardless of `auto_allow_tiers` /
`halt_tiers` — including read-only tools. Only the user (via PATCH from
the FE config UI, gated by Kanban #943) can flip it to true. As of #2707
this flag is decoupled from multi-board eligibility — consent-granted
projects are eligible for auto-run even with tools disabled; the
operator write path for this flag lands in #2707 Option C (the #943 UI
was never built).

`auto_allow_tiers` and `halt_tiers` MUST be disjoint (Pydantic
`ToolsConfig` enforces — 422 on overlap at the API boundary). Any tier
absent from BOTH lists ALSO falls through to `reject` (defensive default
— the gate prefers to over-block over under-block on misconfiguration).

`http_hosts` is a forward-compat slot for the HTTP-tool host allowlist
(#978 + #981 wire it). The gate (this slice, #979) only decides on
tier; the HTTP tool itself enforces host. Empty list = "no host
allowed" once the HTTP tool consults it (#981).

Server default is the JSON literal above, applied via PG's column-level
DEFAULT clause so NEW rows inherit on INSERT without the application
having to pass the column. Existing rows pre-dating this migration get
the same default through an explicit `op.execute("UPDATE projects SET
tools_config = ...")` backfill in upgrade() — PG's column-level DEFAULT
clause fires only on INSERT, not on `ADD COLUMN`, so without the
backfill the existing seed projects (id=1 agent-teams + others) would
read NULL and the permission gate would interpret NULL as "kill switch
on" (reject all) which is correct-but-confusing for the FE config UI
(empty form vs structured form). Backfill keeps the on-the-wire shape
uniform: every project has a `tools_config` dict, never NULL.

NO DB CHECK on element shape (mirrors `config` / `agent_overrides` /
`sources` / `tasks.acceptance_criteria` precedent — JSONB element-shape
validation lives at the Pydantic boundary, not in PG).

Wire-contract mirrors (atomic with this migration — see #979 spawn brief):
  - api/src/models/project.py        : Mapped[dict[str, Any] | None] column
  - api/src/schemas/project.py       : ToolsConfig Pydantic + ProjectRead /
                                       ProjectUpdate expose / accept it
  - langgraph/tools/permission_gate.py : pure check_permission() function
  - langgraph/tools/__init__.py      : re-exports check_permission +
                                       PermissionDecision
  - api/tests/test_project_tools_config.py : Pydantic + roundtrip coverage
  - langgraph/tests/tools/test_permission_gate.py : gate logic coverage
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0027_projects_tools_config"
down_revision = "0026_projects_budget_caps"
branch_labels = None
depends_on = None


# Locked default — kept in this module-level constant so the backfill UPDATE
# below and the server_default literal stay byte-for-byte identical.
# Pydantic `ToolsConfig` in api/src/schemas/project.py is the source of truth
# at the API boundary; this string is the DB-level mirror.
_DEFAULT_TOOLS_CONFIG_JSON = (
    '{'
    '"tools_enabled": false, '
    '"auto_allow_tiers": ["read"], '
    '"halt_tiers": ["write", "network", "destructive"], '
    '"http_hosts": []'
    '}'
)


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "tools_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            server_default=sa.text(f"'{_DEFAULT_TOOLS_CONFIG_JSON}'::jsonb"),
        ),
    )
    # PG's column-level DEFAULT fires only on INSERT — `ADD COLUMN ... DEFAULT
    # ...` does NOT backfill existing rows when the column is nullable. Run an
    # explicit UPDATE so every pre-existing project carries the locked default
    # on the wire, never NULL. Safe to repeat: rows already at the default get
    # a no-op write (PG still bumps xmin but we're inside a transactional
    # alembic upgrade — the cost is irrelevant at the current row count).
    op.execute(
        f"UPDATE projects SET tools_config = '{_DEFAULT_TOOLS_CONFIG_JSON}'::jsonb "
        "WHERE tools_config IS NULL"
    )


def downgrade() -> None:
    op.drop_column("projects", "tools_config")
