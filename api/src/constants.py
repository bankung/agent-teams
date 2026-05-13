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
    """tasks.process_status — INTEGER NOT NULL DEFAULT 1, CHECK IN (1..6).

    Renamed from tasks.status -> tasks.process_status by the soft-delete migration
    (2026_05_08_*) so the bare `status` name carries the uniform 0/1 soft-delete
    flag across every business table.

    Codes:
      1=TODO, 2=IN_PROGRESS, 3=REVIEW, 4=BLOCKED, 5=DONE, 6=CANCELLED.

    Kanban #854 (2026-05-13) added `CANCELLED=6`. Cancelled rows are excluded
    from the GET /api/tasks default list (opt back in via `?include_cancelled=true`)
    and from the `last_activity_at` aggregate on GET /api/projects/stats
    (parity with soft-delete semantics — a cancelled task is dead-end work, not
    activity). `counts["6"]` IS emitted on the stats endpoint for transparency.

    Mirror of migration 0022's `_TASK_PROCESS_STATUS_ALL_NEW` (intentionally
    duplicated — migrations don't import app code, see
    standards/sqlalchemy/migrations.md).
    """

    TODO = 1
    IN_PROGRESS = 2
    REVIEW = 3
    BLOCKED = 4
    DONE = 5
    CANCELLED = 6

    ALL = (TODO, IN_PROGRESS, REVIEW, BLOCKED, DONE, CANCELLED)


class RecordStatus:
    """Uniform soft-delete flag — every business table has SMALLINT NOT NULL
    DEFAULT 1 CHECK (status IN (0, 1)). 1=active, 0=deleted. App code never
    issues SQL DELETE; "delete" endpoints flip the flag.
    """

    ACTIVE = 1
    DELETED = 0

    ALL = (DELETED, ACTIVE)


class ProjectTeam:
    """projects.team — TEXT NOT NULL DEFAULT 'dev',
    CHECK team IN ('dev','novel','general').

    Drives subagent roster selection (see scaffold service + .claude/teams/<team>.md).
    Codes 1..5 reserved for dev roles; 11..12 reserved for novel; 'general' is a
    domain-agnostic team that uses a single generalist agent (.claude/teams/general.md
    drafted by Kanban #845, blocked on this task). Future teams pick their own
    ranges. App-layer validates assigned_role per active team's roster (no DB
    CHECK on tasks.assigned_role after the soft-delete migration).

    Mirror of migration 0021's `_PROJECT_TEAM_ALL_NEW` (intentionally duplicated —
    migrations don't import app code, see standards/sqlalchemy/migrations.md).
    """

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
    """tasks.run_mode — TEXT NOT NULL DEFAULT 'manual',
    CHECK run_mode IN ('manual','auto_pickup','auto_headless').

    Drives the Kanban-driven AI execution model (Step 2 — Kanban #481):
    - MANUAL (default): no auto-pickup; tasks are advanced by humans only.
    - AUTO_PICKUP: Mode A2 — Claude Code session polls + dispatches to Lead
      (per-Write/Edit/Bash approval prompts stay).
    - AUTO_HEADLESS: Mode B — separate worker service runs `claude -p` headless
      (no per-action prompts). Cross-table validator requires
      `projects.auto_run_consent_at IS NOT NULL`.

    Mirror of the migration's `_TASK_RUN_MODE_ALL` (intentionally duplicated —
    migrations don't import app code, see standards/sqlalchemy/migrations.md).
    """

    MANUAL = "manual"
    AUTO_PICKUP = "auto_pickup"
    AUTO_HEADLESS = "auto_headless"

    ALL: tuple[str, ...] = (MANUAL, AUTO_PICKUP, AUTO_HEADLESS)


class TaskKind:
    """tasks.task_kind — VARCHAR(8) NOT NULL DEFAULT 'human',
    CHECK task_kind IN ('ai','human').

    Added 2026-05-10 (Kanban #706 / V3+ scope-lock T1). Distinguishes runner-
    driven AI work from human work:
    - HUMAN (default): drag-droppable on the FE board; lifecycle is user-driven;
      MUST pair with run_mode='manual' (cross-table validator at services/task_kind.py).
    - AI: lifecycle is queue-runner-driven (Kanban #481 / Step 2); not drag-droppable;
      may carry run_mode IN ('auto_pickup','auto_headless') in addition to 'manual'.

    Mirror of migration 0007's `_TASK_KIND_ALL` (intentionally duplicated —
    migrations don't import app code, see standards/sqlalchemy/migrations.md).
    """

    AI = "ai"
    HUMAN = "human"

    ALL: tuple[str, ...] = (AI, HUMAN)


class TaskType:
    """tasks.task_type — VARCHAR(16) NOT NULL DEFAULT 'feature',
    CHECK task_type IN ('bug','feature','chore','docs','refactor').

    Added 2026-05-12 (Kanban #803). Classifies work type so bug-fix tasks
    (e.g. #801) and feature tasks (e.g. #792, #795) are structurally
    distinguishable rather than mixed in the same shape. Motivated by the
    2026-05-12 AC-discipline audit. Drives report grouping later.

    Mirror of migration 0015's `_TASK_TYPE_ALL` (intentionally duplicated —
    migrations don't import app code, see standards/sqlalchemy/migrations.md).
    """

    BUG = "bug"
    FEATURE = "feature"
    CHORE = "chore"
    DOCS = "docs"
    REFACTOR = "refactor"

    ALL: tuple[str, ...] = (BUG, FEATURE, CHORE, DOCS, REFACTOR)


class TaskInteractionKind:
    """tasks.interaction_kind — VARCHAR(16) NOT NULL DEFAULT 'work'.
    CHECK ck_tasks_interaction_kind_valid: interaction_kind IN ('work','question','decision').
    Added 2026-05-12 (Kanban #830). Distinguishes agent-executed work from
    user-interaction gates created by auto-run when ambiguity is detected.
    Mirror of migration 0019's _INTERACTION_KIND_ALL.
    """

    WORK = "work"
    QUESTION = "question"
    DECISION = "decision"

    ALL: tuple[str, ...] = (WORK, QUESTION, DECISION)


class SessionStatus:
    """sessions.status — VARCHAR(16) NOT NULL DEFAULT 'active',
    CHECK status IN ('active','compacting','closed').

    Added 2026-05-10 (Kanban #716 / CTX-1). 'closed' is terminal — router
    400s any subsequent PATCH on a closed row.

    Mirror of migration 0008's CHECK predicate (intentionally duplicated —
    migrations don't import app code, see standards/sqlalchemy/migrations.md).
    """

    ACTIVE = "active"
    COMPACTING = "compacting"
    CLOSED = "closed"

    ALL: tuple[str, ...] = (ACTIVE, COMPACTING, CLOSED)


class SessionRunStatus:
    """session_runs.status — VARCHAR(16) NOT NULL DEFAULT 'running',
    CHECK status IN ('running','done','error','timeout').

    Added 2026-05-10 (Kanban #716 / CTX-1). Three terminal states (done /
    error / timeout) — the router auto-stamps `finished_at` on transition.
    """

    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    TIMEOUT = "timeout"

    ALL: tuple[str, ...] = (RUNNING, DONE, ERROR, TIMEOUT)


class SessionCompactTrigger:
    """session_compacts.trigger_kind — VARCHAR(16) NOT NULL,
    CHECK trigger_kind IN ('size','manual','run_count').

    Added 2026-05-10 (Kanban #716 / CTX-1). CTX-4 wires the runner; CTX-1
    only ships the schema + read endpoints.
    """

    SIZE = "size"
    MANUAL = "manual"
    RUN_COUNT = "run_count"

    ALL: tuple[str, ...] = (SIZE, MANUAL, RUN_COUNT)
