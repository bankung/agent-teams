"""Unit tests for src.constants.in_clause.

This helper renders the SQL fragment used by ORM CheckConstraints AND duplicated
verbatim in the initial Alembic migration. The output format MUST stay byte-for-byte
identical (`"<col> IN (<csv>)"` with `", "` separator) — drift here means the
ORM-side and migration-side CHECK constraints diverge.
"""

from __future__ import annotations

import pytest

from src.constants import (
    TaskPriority,
    TaskRole,
    TaskStatus,
    in_clause,
    in_clause_text,
)


def test_in_clause_status_canonical() -> None:
    assert in_clause("process_status", TaskStatus.ALL) == "process_status IN (1, 2, 3, 4, 5, 6)"


def test_in_clause_priority_canonical() -> None:
    assert in_clause("priority", TaskPriority.ALL) == "priority IN (1, 2, 3, 4)"


def test_in_clause_role_canonical() -> None:
    # Kanban #926 (2026-05-15): TaskRole.ALL widened to include novel codes (11/12/13).
    # Kanban #1266/#1269/#1271 (2026-05-20): further widened to include SEO codes
    # (21-24), SEM codes (31-34), data-analytics codes (41-44).
    # Note: this IN-clause is no longer referenced by any active CHECK constraint —
    # the DB CHECK on tasks.assigned_role was dropped 2026-05-08 by migration 0002.
    # The helper is exercised here only to guarantee its render stays stable; the
    # migration files carry their own historical snapshots of _TASK_ROLE_ALL per the
    # "Helper duplication between app and migration" pattern (standards/general.md).
    assert (
        in_clause("assigned_role", TaskRole.ALL)
        == "assigned_role IN (1, 2, 3, 4, 5, 6, 11, 12, 13, 21, 22, 23, 24, 31, 32, 33, 34, 41, 42, 43, 44)"
    )


def test_in_clause_single_value() -> None:
    """No trailing comma on a 1-tuple."""
    assert in_clause("x", (7,)) == "x IN (7)"


def test_in_clause_uses_comma_space_separator() -> None:
    """Migration files copy this format — keep the separator stable."""
    assert in_clause("foo", (1, 2)) == "foo IN (1, 2)"


# Kanban #1620 (2026-05-28): test_in_clause_text_canonical_team_values was
# REMOVED — it source-locked the `ck_projects_team_valid` CHECK string, which was
# dropped (migration 0051). `in_clause_text` is still exercised below (the helper
# remains in use for the `status` CHECK). The team enum's single source of truth
# is now `ProjectTeam.ALL`; its coverage is asserted by the GET /api/teams +
# unknown-team-422 contract tests in test_routes_smoke.py.


def test_in_clause_text_rejects_apostrophe() -> None:
    with pytest.raises(ValueError, match="only allows"):
        in_clause_text("col", ("o'brien",))


def test_in_clause_text_rejects_uppercase() -> None:
    with pytest.raises(ValueError, match="only allows"):
        in_clause_text("col", ("Dev",))


def test_in_clause_text_rejects_empty_string() -> None:
    with pytest.raises(ValueError, match="only allows"):
        in_clause_text("col", ("",))
