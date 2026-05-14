"""Kanban schema integer codes — mirror context/standards/general.md.

These are the canonical values for tasks.process_status / tasks.priority / tasks.assigned_role
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


def in_clause_text(column: str, values: tuple[str, ...]) -> str:
    """Render a SQL IN-list of single-quoted string literals.

    Restricted to lowercase alnum + `_`/`-` codes — anything else raises.
    repr() is NOT a SQL quoter; we don't want a half-baked string-quoter
    on the SQL surface. Mirrors `in_clause` (integer variant) — kept
    in sync with the migration's local copy per general.md
    "Helper duplication between app and migration".
    """
    _allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_-")
    for v in values:
        if not v or any(c not in _allowed for c in v):
            raise ValueError(
                f"in_clause_text only allows [a-z0-9_-]+ values; got {v!r}"
            )
    return f"{column} IN ({', '.join(f"'{v}'" for v in values)})"


class TaskStatus:
    """tasks.process_status — 1=TODO..6=CANCELLED (#854).
    Mirror of migration 0022 (intentionally duplicated — see standards/sqlalchemy/migrations.md).
    """

    TODO = 1
    IN_PROGRESS = 2
    REVIEW = 3
    BLOCKED = 4
    DONE = 5
    CANCELLED = 6

    ALL = (TODO, IN_PROGRESS, REVIEW, BLOCKED, DONE, CANCELLED)


class RecordStatus:
    """Soft-delete flag — 1=active, 0=deleted. Every business table. App code never issues SQL DELETE."""

    ACTIVE = 1
    DELETED = 0

    ALL = (DELETED, ACTIVE)


class ProjectTeam:
    """projects.team — 'dev'/'novel'/'general'. Mirror of migration 0021 (intentionally duplicated)."""

    DEV = "dev"
    NOVEL = "novel"
    GENERAL = "general"

    ALL = (DEV, NOVEL, GENERAL)


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


class TaskRunMode:
    """tasks.run_mode — 'manual'/'auto_pickup'/'auto_headless' (#481). auto_headless requires consent.
    Mirror of migration (intentionally duplicated).
    """

    MANUAL = "manual"
    AUTO_PICKUP = "auto_pickup"
    AUTO_HEADLESS = "auto_headless"

    ALL: tuple[str, ...] = (MANUAL, AUTO_PICKUP, AUTO_HEADLESS)


class TaskKind:
    """tasks.task_kind — 'ai'/'human' (#706). human → run_mode='manual'.
    Mirror of migration 0007 (intentionally duplicated).
    """

    AI = "ai"
    HUMAN = "human"

    ALL: tuple[str, ...] = (AI, HUMAN)


class TaskType:
    """tasks.task_type — 'bug'/'feature'/'chore'/'docs'/'refactor' (#803).
    Mirror of migration 0015 (intentionally duplicated).
    """

    BUG = "bug"
    FEATURE = "feature"
    CHORE = "chore"
    DOCS = "docs"
    REFACTOR = "refactor"

    ALL: tuple[str, ...] = (BUG, FEATURE, CHORE, DOCS, REFACTOR)


class TaskInteractionKind:
    """tasks.interaction_kind — 'work'/'question'/'decision' (#830). Mirror of migration 0019."""

    WORK = "work"
    QUESTION = "question"
    DECISION = "decision"

    ALL: tuple[str, ...] = (WORK, QUESTION, DECISION)


class SessionStatus:
    """sessions.status — 'active'/'compacting'/'closed' (#716). 'closed' is terminal.
    Mirror of migration 0008.
    """

    ACTIVE = "active"
    COMPACTING = "compacting"
    CLOSED = "closed"

    ALL: tuple[str, ...] = (ACTIVE, COMPACTING, CLOSED)


class SessionRunStatus:
    """session_runs.status — 'running'/'done'/'error'/'timeout' (#716). Three terminal states."""

    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    TIMEOUT = "timeout"

    ALL: tuple[str, ...] = (RUNNING, DONE, ERROR, TIMEOUT)


class SessionCompactTrigger:
    """session_compacts.trigger_kind — 'size'/'manual'/'run_count' (#716)."""

    SIZE = "size"
    MANUAL = "manual"
    RUN_COUNT = "run_count"

    ALL: tuple[str, ...] = (SIZE, MANUAL, RUN_COUNT)
