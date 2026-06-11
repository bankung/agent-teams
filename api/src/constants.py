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
    """projects.team — dev / novel / general / content / seo / data-analytics / sem.

    SINGLE SOURCE OF TRUTH for the team enum (Kanban #1620, 2026-05-28). The
    DB-side CHECK `ck_projects_team_valid` was DROPPED by migration
    `0051_drop_projects_team_check` — adding a new team no longer requires a
    migration. Validation now happens at the API boundary: `routers/projects.py`
    `create_project` / `update_project` reject `team not in ProjectTeam.ALL`
    with 422, and `schemas/project.py::TeamCode` is auto-derived from `ALL`.

    Extending the enum (post-#1620): add the value here + its roster entry in
    `TEAM_ROSTERS` below + drop `.claude/teams/<t>.md` and the new roster roles'
    `.claude/agents/<r>.md`. NO migration, no ORM CheckConstraint, no ps1/
    scaffold/FE edits — those all derive from this module + the API.
    """

    DEV = "dev"
    NOVEL = "novel"
    GENERAL = "general"
    CONTENT = "content"
    SEO = "seo"
    DATA_ANALYTICS = "data-analytics"
    SEM = "sem"
    NETOPS = "netops"

    ALL = (DEV, NOVEL, GENERAL, CONTENT, SEO, DATA_ANALYTICS, SEM, NETOPS)


# Per-team scaffold roster — the SINGLE source for which dedicated agents own a
# per-project role-state folder (Kanban #1620, 2026-05-28). Consumed by:
#   * services/project_scaffold.py — creates `context/projects/<name>/<role>/`
#   * services/zero_config_scaffold.py::_resolve_manifest — copies
#     `.claude/agents/<role>.md` per team
#   * routers/teams.py (GET /api/teams) + routers/scaffold.py
#     (role_folders on the manifest response) — so the FE select + the host-side
#     bin/agent-teams-init.ps1 derive folders without re-hardcoding the map.
#
# Reconciled from each team's `.claude/teams/<team>.md` "Roster" table — the
# DEDICATED agents that own a `context/projects/<active>/<role>/` folder. EXCLUDES:
#   * "Cross-team reuse" / borrowed agents (general-researcher in every team's
#     table — it writes _scratch, not a role folder).
#   * dev-documentor (in the dev Roster table but drafts into _scratch, not a
#     per-project role folder).
# Every role here MUST have a matching `.claude/agents/<role>.md` file — the
# scaffold manifest convention relies on it. Verified 2026-05-28.
#
# NOTE: `content` roster is OPERATOR-CONFIRMED (2026-05-28, Kanban #1623),
# reconciled against `.claude/teams/content.md` Roster table. Pipeline order:
# write → edit → hook → on-page SEO → veracity → proofread.
# content-seo-optimizer is shared with the SEO team (appears in both rosters —
# TEAM_ROSTERS values are per-team; an agent may serve multiple teams).
# thai-proofreader is cross-team but a standing content-pipeline step.
TEAM_ROSTERS: dict[str, tuple[str, ...]] = {
    ProjectTeam.DEV: (
        "dev-sr-frontend",
        "dev-sr-backend",
        "dev-frontend",
        "dev-backend",
        "dev-devops",
        "dev-tester",
        "dev-reviewer",
        "dev-security-reviewer",
    ),
    ProjectTeam.NOVEL: (
        "novel-writer",
        "novel-editor",
        "thai-proofreader",
    ),
    ProjectTeam.GENERAL: ("general",),
    ProjectTeam.CONTENT: (
        "content-writer",
        "content-editor",
        "content-hook-doctor",
        "content-seo-optimizer",
        "content-veracity-checker",
        "thai-proofreader",
    ),
    ProjectTeam.SEO: (
        "seo-strategist",
        "technical-seo-specialist",
        "content-seo-optimizer",
        "seo-reporting-analyst",
    ),
    ProjectTeam.SEM: (
        "sem-campaign-lead",
        "google-ads-specialist",
        "meta-ads-specialist",
        "platform-ads-coordinator",
    ),
    ProjectTeam.NETOPS: ("netops-monitoring-reader",),
    ProjectTeam.DATA_ANALYTICS: (
        "bi-analyst",
        "sql-optimizer",
        "dashboard-designer",
        "analytics-platform-integrator",
    ),
}

# Import-time invariant: every team in the enum MUST carry a roster entry. A new
# ProjectTeam value with no TEAM_ROSTERS row would scaffold zero role folders and
# (post-#1620) raise in _resolve_manifest — catch the drift at module load, not
# at the first POST. Use a real exception (not assert) so it survives `python -O`.
if set(TEAM_ROSTERS) != set(ProjectTeam.ALL):
    raise RuntimeError(
        f"TEAM_ROSTERS keys {sorted(TEAM_ROSTERS)!r} drifted from "
        f"ProjectTeam.ALL {sorted(ProjectTeam.ALL)!r}"
    )


class TaskPriority:
    """tasks.priority — INTEGER NOT NULL DEFAULT 2, CHECK IN (1..4)."""

    LOW = 1
    NORMAL = 2
    HIGH = 3
    URGENT = 4

    ALL = (LOW, NORMAL, HIGH, URGENT)


class TaskRole:
    """tasks.assigned_role — INTEGER NULLABLE. Validated 1..20 at app layer
    (the DB CHECK was dropped 2026-05-08 by migration 0002; per-team roster
    enforcement is too dynamic for a single static CHECK).

    Range partition:
      *  1..10  → dev team (.claude/teams/dev.md)
      * 11..20  → novel team (.claude/teams/novel.md)
      * 21..30  → seo team (.claude/teams/seo.md)
      * 31..40  → sem team (.claude/teams/sem.md)
      * 41..50  → data-analytics team (.claude/teams/data-analytics.md)
      * 51+     → reserved for future team domains

    Each team's playbook owns the named codes inside its range. Unnamed codes
    inside an existing range (e.g. 6..10) are RESERVED for that team to claim
    later; the Pydantic validator accepts them as raw ints today.
    """

    # Dev range (1..10)
    FRONTEND = 1
    BACKEND = 2
    DEVOPS = 3
    QA = 4
    REVIEWER = 5
    SECURITY_REVIEWER = 6  # Kanban #7 Section B (2026-05-16)

    # Novel range (11..20)
    NOVEL_WRITER = 11
    NOVEL_EDITOR = 12
    NOVEL_PROOFREADER = 13

    # SEO range (21..30) — Kanban #1266 AC3 (2026-05-20)
    SEO_STRATEGIST = 21
    TECHNICAL_SEO_SPECIALIST = 22
    CONTENT_SEO_OPTIMIZER = 23
    SEO_REPORTING_ANALYST = 24
    # 25-30 reserved for future seo team roles

    # SEM range (31..40) — Kanban #1269 AC8 (2026-05-20)
    SEM_CAMPAIGN_LEAD = 31
    GOOGLE_ADS_SPECIALIST = 32
    META_ADS_SPECIALIST = 33
    PLATFORM_ADS_COORDINATOR = 34
    # 35-40 reserved for future sem team roles

    # Data-analytics range (41..50) — Kanban #1271 AC7 (2026-05-20)
    BI_ANALYST = 41
    SQL_OPTIMIZER = 42
    DASHBOARD_DESIGNER = 43
    ANALYTICS_PLATFORM_INTEGRATOR = 44
    # 45-50 reserved for future data-analytics team roles

    # Validator bounds — range, not membership. ALL stays as the union of
    # currently-named codes (used by callers that want to enumerate the
    # known roster, e.g. tests / docs); the wire-layer range gate lives in
    # the Pydantic validator on `assigned_role`.
    RANGE_MIN = 1
    RANGE_MAX = 50

    ALL = (
        FRONTEND,
        BACKEND,
        DEVOPS,
        QA,
        REVIEWER,
        SECURITY_REVIEWER,
        NOVEL_WRITER,
        NOVEL_EDITOR,
        NOVEL_PROOFREADER,
        SEO_STRATEGIST,
        TECHNICAL_SEO_SPECIALIST,
        CONTENT_SEO_OPTIMIZER,
        SEO_REPORTING_ANALYST,
        SEM_CAMPAIGN_LEAD,
        GOOGLE_ADS_SPECIALIST,
        META_ADS_SPECIALIST,
        PLATFORM_ADS_COORDINATOR,
        BI_ANALYST,
        SQL_OPTIMIZER,
        DASHBOARD_DESIGNER,
        ANALYTICS_PLATFORM_INTEGRATOR,
    )


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
    """tasks.task_type — 'bug'/'feature'/'chore'/'docs'/'refactor'/'audit'.
    Mirror of migrations 0015 (initial five) + 0040 (added 'audit' for GOV3
    governance audit tasks, Kanban #1211). Intentionally duplicated from
    the migrations to keep the constants module the single import-time
    source of truth for Pydantic Literals.
    """

    BUG = "bug"
    FEATURE = "feature"
    CHORE = "chore"
    DOCS = "docs"
    REFACTOR = "refactor"
    # Kanban #1211 (2026-05-19): GOV3 governance audit. A task whose handler
    # runs the project-auditor agent + writes audit_report. The PATCH-to-DONE
    # hook in routers/tasks.py invokes `services/audit_flag.apply_flag_from_audit_report`
    # when an 'audit' task transitions to process_status=5.
    AUDIT = "audit"

    ALL: tuple[str, ...] = (BUG, FEATURE, CHORE, DOCS, REFACTOR, AUDIT)


class TaskInteractionKind:
    """tasks.interaction_kind — 'work'/'question'/'decision' (#830). Mirror of migration 0019."""

    WORK = "work"
    QUESTION = "question"
    DECISION = "decision"

    ALL: tuple[str, ...] = (WORK, QUESTION, DECISION)


class CommentAuthorKind:
    """task_comments.author_kind — 'user'/'agent'/'system' (#1005).

    The discriminator for WHO appended a comment to a task's thread:
      - 'user'   — a human operator (UI / API).
      - 'agent'  — a specialist subagent / Lead recording a progress note.
      - 'system' — an automated event (status flip, audit, scheduler note).

    Stored as TEXT NOT NULL + a CHECK; mirror of migration 0062
    (intentionally duplicated — see standards/sqlalchemy/migrations.md).
    """

    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"

    ALL: tuple[str, ...] = (USER, AGENT, SYSTEM)


class MilestoneStatus:
    """milestones.milestone_status — 'planned'/'active'/'released'/'cancelled' (#1868).

    The lifecycle code for a milestone (release-planning grouping of tasks).
    Stored as TEXT NOT NULL DEFAULT 'planned' + a CHECK; mirror of migration
    0057 (intentionally duplicated — see standards/sqlalchemy/migrations.md).

    NAMING (#1868): the lifecycle column is `milestone_status` (this enum); the
    uniform 0/1 soft-delete flag is the separate `status` column (RecordStatus).
    This mirrors how tasks separate `process_status` (lifecycle) from `status`
    (soft-delete).
    """

    PLANNED = "planned"
    ACTIVE = "active"
    RELEASED = "released"
    CANCELLED = "cancelled"

    ALL: tuple[str, ...] = (PLANNED, ACTIVE, RELEASED, CANCELLED)


class ResourceKind:
    """project_resources.kind — 'file'/'link' (#1302).

    The discriminator for a project resource attachment. Stored as TEXT NOT NULL
    + a CHECK; mirror of migration 0059 (intentionally duplicated — see
    standards/sqlalchemy/migrations.md).

    Per-kind required fields (enforced by the DB CHECK
    `ck_project_resources_kind_fields` + the Pydantic model_validator on
    ResourceCreate):
      - 'file' → `filename` MUST be present (the stored object's name).
      - 'link' → `url` MUST be present (the external URL).
    """

    FILE = "file"
    LINK = "link"

    ALL: tuple[str, ...] = (FILE, LINK)


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
