"""projects.required_binaries — Mode-B Phase-1 host-prereq guard (Kanban #1800 / #1652)

Revision ID: 0055_required_binaries
Revises: 0054_email_oauth_tokens
Create Date: 2026-06-02 01:00 UTC

Phase 1 of the Mode-B runtime/dependency gap remediation (design memo
`context/projects/agent-teams/shared/design/mode-b-runtime-options.md` §B.5).

Adds ONE nullable JSONB column `projects.required_binaries` storing a declared
list of host-binary names the project's Mode-B (langgraph headless) tools need
on PATH, e.g. `["ffmpeg", "yt-dlp"]`. The langgraph worker runs a pre-pickup
`shutil.which()` check against this list and fails CLEAN (PATCH the task BLOCKED
with halt_reason='runtime_prereq_missing') when any declared binary is absent —
turning today's opaque mid-run `FileNotFoundError` into a crisp, documented
"this project is Mode-A-only until #1652 Phase 2" status.

DESIGN — standalone column, NOT `runtime_config`:
  Phase 1 deliberately does NOT introduce the full `runtime_config` JSONB (that
  is the Phase-2 / #1801 surface). `runtime_config` drives an engine-side image
  BUILD from adopter-supplied config, which is a supply-chain + code-exec
  surface gated on an operator-vs-AI write distinction that does NOT exist yet
  (memo §B.3 #5 — blocking prerequisite). Phase 1 performs NO build; it only
  READS a declared binary list and fails clean. Introducing the security-
  sensitive `runtime_config` field now would ship a write surface ahead of its
  gate, so the two phases stay decoupled. The memo explicitly sanctions
  "`required_binaries` (or `runtime_config.required_binaries`)".

Column shape (validated at the API boundary by Pydantic, NOT a DB CHECK —
mirrors `notification_targets` / `tools_config` / `sources` precedent: JSONB
element-shape validation lives at the API layer):
    ["ffmpeg", "yt-dlp"]   # list[str], each name ^[A-Za-z0-9][A-Za-z0-9._-]*$
  NULL = "no host-binary requirements" = today's behavior, byte-for-byte
  unaffected (the worker gate skips entirely on NULL/empty). Nullable, NO
  server_default — explicit NULL is the meaningful "unset" sentinel (parity
  with `notification_targets`).

PG 16 — nullable ADD COLUMN with no DEFAULT is metadata-only (no heap rewrite,
no backfill). Existing ~136 projects unaffected.

Downgrade caveat:
- Dropping `required_binaries` discards any declared binary lists silently.
  Recovery is a re-PATCH of the affected projects (operator-CRUD metadata).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0055_required_binaries"
down_revision = "0054_email_oauth_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable, no server_default — explicit NULL = "no host-binary
    # requirements" (worker gate skips). Mirrors `notification_targets`
    # "element-shape validated at API boundary, no DB CHECK on shape" precedent.
    op.add_column(
        "projects",
        sa.Column(
            "required_binaries",
            sa.dialects.postgresql.JSONB(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "required_binaries")
