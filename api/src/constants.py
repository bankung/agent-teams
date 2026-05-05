"""Kanban schema integer codes — mirror context/standards/general.md.

These are the canonical values for tasks.status / tasks.priority / tasks.assigned_role
across every project that uses this schema. Numbers are stable forever — extend by
adding new codes, never repurpose existing ones.
"""

from __future__ import annotations


def in_clause(column: str, values: tuple[int, ...]) -> str:
    """Render a SQL IN-list expression — e.g. `in_clause("status", (1, 2, 3))`
    returns `"status IN (1, 2, 3)"`. Used by ORM CheckConstraints to mirror the
    `ALL` tuples below (and also duplicated verbatim in the initial migration —
    keep this function's output format in sync with that file).
    """
    return f"{column} IN ({', '.join(str(v) for v in values)})"


class TaskStatus:
    """tasks.status — INTEGER NOT NULL DEFAULT 1, CHECK IN (1..5)."""

    TODO = 1
    IN_PROGRESS = 2
    REVIEW = 3
    BLOCKED = 4
    DONE = 5

    ALL = (TODO, IN_PROGRESS, REVIEW, BLOCKED, DONE)


class TaskPriority:
    """tasks.priority — INTEGER NOT NULL DEFAULT 2, CHECK IN (1..4)."""

    LOW = 1
    NORMAL = 2
    HIGH = 3
    URGENT = 4

    ALL = (LOW, NORMAL, HIGH, URGENT)


class TaskRole:
    """tasks.assigned_role — INTEGER NULLABLE, CHECK IN (1..5) when not null."""

    FRONTEND = 1
    BACKEND = 2
    DEVOPS = 3
    QA = 4
    REVIEWER = 5

    ALL = (FRONTEND, BACKEND, DEVOPS, QA, REVIEWER)


class TaskHistoryOperation:
    """tasks_history.operation — CHAR(1) CHECK IN ('U','D')."""

    UPDATE = "U"
    DELETE = "D"

    ALL = (UPDATE, DELETE)
