"""Unit tests for src.constants.in_clause.

This helper renders the SQL fragment used by ORM CheckConstraints AND duplicated
verbatim in the initial Alembic migration. The output format MUST stay byte-for-byte
identical (`"<col> IN (<csv>)"` with `", "` separator) — drift here means the
ORM-side and migration-side CHECK constraints diverge.
"""

from __future__ import annotations

from src.constants import TaskPriority, TaskRole, TaskStatus, in_clause


def test_in_clause_status_canonical() -> None:
    assert in_clause("status", TaskStatus.ALL) == "status IN (1, 2, 3, 4, 5)"


def test_in_clause_priority_canonical() -> None:
    assert in_clause("priority", TaskPriority.ALL) == "priority IN (1, 2, 3, 4)"


def test_in_clause_role_canonical() -> None:
    assert in_clause("assigned_role", TaskRole.ALL) == "assigned_role IN (1, 2, 3, 4, 5)"


def test_in_clause_single_value() -> None:
    """No trailing comma on a 1-tuple."""
    assert in_clause("x", (7,)) == "x IN (7)"


def test_in_clause_uses_comma_space_separator() -> None:
    """Migration files copy this format — keep the separator stable."""
    assert in_clause("foo", (1, 2)) == "foo IN (1, 2)"
