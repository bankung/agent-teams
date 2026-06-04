"""Project ORM model."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.constants import ProjectTeam, RecordStatus, in_clause
from src.models.base import Base

if TYPE_CHECKING:
    from src.models.milestone import Milestone
    from src.models.project_resource import ProjectResource
    from src.models.projects_audit import ProjectsAudit
    from src.models.task import Task
    from src.models.transaction import Transaction


class Project(Base):
    """A registered project — typically maps to one Next.js + FastAPI + DB stack on disk.

    `is_active` is a free boolean — multiple rows may carry `is_active=true`
    simultaneously. Each Claude Code session binds to a project by name at
    bootstrap (Kanban #694, session-scoped active); the legacy "single active
    project" invariant + its partial unique index `ux_projects_active_one`
    were dropped by `0006_drop_active_one`.

    Soft-delete: `status=1` active, `status=0` deleted; uniqueness on `name` is
    partial (gated on `status=1`) so a name can be reused after a soft delete.
    """

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # `name` uniqueness is enforced via partial unique index `ux_projects_name_active`
    # (see __table_args__) — NOT via column-level UNIQUE — so soft-deleted rows free
    # the name for re-use.
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    paths_web: Mapped[str] = mapped_column(Text, nullable=False)
    paths_api: Mapped[str] = mapped_column(Text, nullable=False)
    paths_db: Mapped[str] = mapped_column(Text, nullable=False)

    stack_web: Mapped[str | None] = mapped_column(Text, nullable=True)
    stack_api: Mapped[str | None] = mapped_column(Text, nullable=True)
    stack_db: Mapped[str | None] = mapped_column(Text, nullable=True)

    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="{}",
        default=dict,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
        default=False,
    )

    team: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=ProjectTeam.DEV,
        default=ProjectTeam.DEV,
    )

    status: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        server_default="1",
        default=RecordStatus.ACTIVE,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Step 2 (Kanban #481/#483): per-project consent gate for Mode B (auto_headless).
    # NULL = no consent yet. The first POST /api/projects/{id}/grant-consent stamps
    # this; re-grant is idempotent (no re-stamp). Cross-table rule lives in
    # src/services/run_mode.py — see decisions.md 2026-05-09.
    auto_run_consent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Kanban #777: project-root metadata. Orthogonal to paths_web/api/db (which
    # are per-lane sub-paths); working_path is the single project root.
    working_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    working_repo: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_overrides: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        server_default="{}",
        default=dict,
    )

    # Kanban #778 (2026-05-13): per-project curated source list. Element shape
    # ({url, label?, kind?}) validated at the API boundary by Pydantic SourceEntry;
    # NO DB CHECK on element shape (mirrors `config` / `agent_overrides` /
    # `tasks.acceptance_criteria` precedent). DB CHECK `ck_projects_sources_length`
    # caps array length <= 20 as defense-in-depth — Pydantic `max_length=20` is the
    # first wall. nullable=True with server_default '[]'::jsonb: pre-existing rows
    # read `[]` via the default (PG 16 metadata-only ADD COLUMN); ORM dict-default
    # `list` keeps Python-side INSERT a list rather than None when omitted.
    sources: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB,
        nullable=True,
        server_default=text("'[]'::jsonb"),
        default=list,
    )

    # Kanban #951 (2026-05-16): per-project budget caps for the headless-engine
    # pickup gate. All three NULL = UNLIMITED (pre-#951 default behavior).
    # NUMERIC(10,2) — user-typed dollars, 2 places. Pydantic ProjectUpdate
    # validates `Decimal >= 0` at the boundary; DB CHECK
    # `ck_projects_budget_caps_nonneg` catches raw-SQL drift. The
    # budget_enforcer service short-circuits on all-NULL → no warn / no halt.
    # See migration 0026 for "reset" semantics (free via on-demand
    # `compute_spend(since=midnight)`; no scheduled job).
    budget_daily_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2),
        nullable=True,
    )
    budget_monthly_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2),
        nullable=True,
    )
    budget_total_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2),
        nullable=True,
    )

    # Kanban #979 (2026-05-16): per-project specialist-tool permission gate
    # config. Drives `langgraph/tools/permission_gate.check_permission()`.
    # Locked default (Q2 Option B, design lock #949) lives in migration
    # 0027 as the column's PG-level server_default + a backfill UPDATE so
    # every existing row also reads the dict, never NULL. NULL semantics
    # at the gate = "kill switch on / reject everything" (defensive — same
    # outcome as `tools_enabled=false`). API boundary validates element
    # shape via `ToolsConfig` Pydantic model; NO DB CHECK on shape
    # (mirrors `config` / `agent_overrides` / `sources` / acceptance_criteria
    # precedent — JSONB element-shape validation lives at the API layer).
    # No Python-side `default=` here: POST /api/projects intentionally OMITS
    # the column from INSERT so the PG server_default fires (parity with
    # `agent_overrides` "omit-when-None" pattern in the router).
    tools_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    # Kanban #989 (2026-05-16): per-project HITL timeout. NULL = pause
    # indefinitely (pre-#989 behavior — preserved as default). When set, the
    # on-demand gate inside GET /api/tasks/next-autorun stamps
    # `halt_reason='hitl_timeout'` on any BLOCKED HITL task
    # (halt_reason IN 'question'/'decision') whose updated_at is older than
    # the threshold. Halt-only; never auto-cancels. Pydantic ProjectUpdate
    # enforces `ge=1` (422 boundary); DB CHECK `ck_projects_hitl_timeout_positive`
    # catches raw-SQL drift. See migration 0029 for the locked design
    # rationale (Q2 → A: on-demand enforcement, not APScheduler).
    hitl_timeout_hours: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    # Kanban #1011 (2026-05-20): per-project HITL aging nudge threshold.
    # NULL or 0 = nudge disabled for this project. Non-zero positive int =
    # threshold in hours. Migration 0047 backfills existing rows to 24 via
    # server_default so projects get nudges by default (operator may PATCH to
    # null to disable). Pydantic ProjectUpdate enforces `ge=0` (422 boundary);
    # DB CHECK `ck_projects_hitl_nudge_threshold_nonneg` is defense-in-depth.
    # Sibling of hitl_timeout_hours — same NULL-as-disabled convention.
    hitl_nudge_threshold_hours: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    # Kanban #960 (2026-05-17): per-project Health monitor tuning knobs.
    # JSONB element shape (validated at the API boundary):
    # {enabled, stale_hours, max_retry_cycles, token_burn_threshold_per_hour,
    #  burn_spike_multiplier}. NULL = use env defaults. `enabled=false`
    # short-circuits the sweep for the project entirely. No DB CHECK on
    # element shape (mirrors config / agent_overrides / sources / tools_config
    # precedent — JSONB element-shape validation lives at the API layer).
    health_thresholds: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    # Kanban #957 (2026-05-17): per-project HITL approval policies. JSONB list
    # of rules matched against pending `request_user_input` payloads — see
    # migration 0033 for the element-shape contract + service layer at
    # `services/approval_evaluator.py` for the evaluation order (first match
    # wins; ANDed predicates within a rule). NULL = no policies; every HITL
    # prompt requires operator attention (preserves pre-#957 behavior). No
    # DB CHECK on shape; the worker tolerates malformed values gracefully
    # (falls back to REQUIRE_ATTENTION + logs a warning).
    approval_policies: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    # Kanban #1224 (2026-05-19): push-notification routing targets (Hermes
    # DeliveryTarget DSL borrowed shape). Element shape (validated at API
    # boundary by Pydantic NotificationTarget): {kind, chat_id, priority,
    # label}. NULL = no default configured (router falls back to local-file
    # write per AC4). No DB CHECK on element shape (mirrors agent_overrides /
    # tools_config / sources / acceptance_criteria precedent — JSONB
    # element-shape validation lives at the API layer).
    notification_targets: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    # Kanban #1800 / #1652 (2026-06-02): Mode-B Phase-1 host-prereq guard.
    # Declared list of host-binary names the project's Mode-B (langgraph
    # headless) tools require on PATH, e.g. ["ffmpeg", "yt-dlp"]. The langgraph
    # worker runs a pre-pickup `shutil.which()` check against this list and
    # PATCHes the task BLOCKED (halt_reason='runtime_prereq_missing') when any
    # declared binary is absent. NULL = no host-binary requirements = today's
    # behavior (gate skips entirely). Standalone column, NOT runtime_config —
    # Phase 1 does NO image build, so it must not introduce the security-gated
    # runtime_config surface prematurely (#1801; memo §B.3 #5). Element shape
    # (each name `^[A-Za-z0-9][A-Za-z0-9._-]*$`) validated at the API boundary
    # by Pydantic; NO DB CHECK on shape (mirrors notification_targets /
    # tools_config / sources precedent — element-shape validation lives at the
    # API layer). See migration 0055_required_binaries.
    required_binaries: Mapped[list[str] | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    # Kanban #953 (2026-05-17): per-project financial-separation columns.
    # Each project becomes an isolated accounting unit. All four NULLABLE for
    # legacy-row resilience; fiscal_year_start + currency_default carry
    # server-side defaults so new INSERTs land non-null.
    # - tax_jurisdiction : free-form region code (e.g. 'TH', 'US-CA').
    # - legal_entity     : owning entity for accountant hand-off.
    # - fiscal_year_start: month-of-year 1..12 (DB CHECK + Pydantic ge=1,le=12).
    # - currency_default : ISO 4217 alpha-3 (e.g. 'USD', 'THB', 'JPY').
    tax_jurisdiction: Mapped[str | None] = mapped_column(Text, nullable=True)
    legal_entity: Mapped[str | None] = mapped_column(Text, nullable=True)
    fiscal_year_start: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        server_default="1",
    )
    currency_default: Mapped[str | None] = mapped_column(
        CHAR(3),
        nullable=True,
        server_default=text("'USD'"),
    )

    # Kanban #1209 (2026-05-19): GOV1 hard kill switch. `is_killed` is the hot
    # pause state — operator-triggered emergency stop, revive-able. Separate
    # from `is_active` (cold archive). `killed_at` is the first-kill timestamp
    # (PRESERVED across revive — historical signal); `killed_reason` mirrors
    # (free-form text, >=10 chars enforced at the Pydantic boundary).
    # NOT NULL with DEFAULT false on is_killed so existing 91 projects backfill
    # cleanly via migration 0039 (PG 16 metadata-only ADD COLUMN).
    is_killed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        default=False,
    )
    killed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    killed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Kanban #1211 (2026-05-19): GOV3 soft-pause governance. Orthogonal to
    # `is_killed` — DB CHECK `ck_projects_kill_pause_mutex` enforces the
    # two cannot both be true. Soft semantics (in-flight tasks complete
    # naturally; new POSTs blocked unless the per-task `allow_during_pause`
    # escape hatch is set with a reason). `paused_at` + `paused_reason`
    # preserved across unpause for the historical-signal pattern mirrored
    # from GOV1's killed_at/killed_reason (D4). `audit_enabled` defaults true;
    # operator flips false to suppress audit-template creation/firing for a
    # project that doesn't want governance audits (AC#2 deferred — column
    # added now to avoid a follow-up migration when AC#2 lands).
    is_paused: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        default=False,
    )
    paused_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    paused_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    audit_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
        default=True,
    )

    tasks: Mapped[list["Task"]] = relationship(
        "Task",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # Kanban #953 — per-project ledger entries. cascade=delete-orphan +
    # passive_deletes mirror the `tasks` relationship; the DB-side ON DELETE
    # CASCADE on transactions.project_id is the load-bearing invariant
    # (passive_deletes=True tells SQLAlchemy not to try to load + delete
    # rows itself before issuing the parent DELETE).
    transactions: Mapped[list["Transaction"]] = relationship(
        "Transaction",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # Kanban #1209 — per-project kill/revive audit ledger. Cascade-delete
    # mirrors `tasks` / `transactions` precedent; the DB-side ON DELETE CASCADE
    # on projects_audit.project_id is the load-bearing invariant.
    audit_entries: Mapped[list["ProjectsAudit"]] = relationship(
        "ProjectsAudit",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # Kanban #1868 — per-project release-planning milestones. Cascade-delete
    # mirrors `tasks` / `transactions`; the DB-side ON DELETE CASCADE on
    # milestones.project_id is the load-bearing invariant (passive_deletes=True
    # tells SQLAlchemy to let the DB handle the cascade).
    milestones: Mapped[list["Milestone"]] = relationship(
        "Milestone",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # Kanban #1302 — per-project file/URL attachments. Cascade-delete mirrors
    # `tasks` / `milestones`; the DB-side ON DELETE CASCADE on
    # project_resources.project_id is the load-bearing invariant
    # (passive_deletes=True lets the DB handle the cascade).
    resources: Mapped[list["ProjectResource"]] = relationship(
        "ProjectResource",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint(
            in_clause("status", RecordStatus.ALL),
            name="ck_projects_status_valid",
        ),
        # Kanban #1620 (2026-05-28): the `ck_projects_team_valid` CHECK was
        # DROPPED (migration 0051_drop_projects_team_check). `team` stays NOT NULL
        # DEFAULT 'dev', but valid-value enforcement now lives at the API boundary
        # (routers/projects.py validates `team in ProjectTeam.ALL` -> 422, and the
        # auto-derived TeamCode Literal 422s at the Pydantic boundary). Adding a
        # team no longer needs a migration. ProjectTeam is still imported above for
        # the column's server_default/default ('dev').
        # Partial unique on name — only one ACTIVE row per name; soft-deleted rows
        # don't occupy the unique slot, so the name can be reused.
        Index(
            "ux_projects_name_active",
            "name",
            unique=True,
            postgresql_where=(status == 1),
        ),
        # NOTE: the partial unique on is_active (`ux_projects_active_one`) was
        # dropped by `0006_drop_active_one` — Phase 2 of the session-scoped
        # active project shift (Kanban #694). Multiple rows may now legitimately
        # have `is_active=true` because each Claude Code session binds to a
        # project by name independently.
        Index("ix_projects_status", "status"),
        # Kanban #951 — budget caps must be >= 0 (NULL = unlimited). Mirror of
        # migration 0026's named CHECK so ORM autogen stays in lockstep.
        CheckConstraint(
            "(budget_daily_usd IS NULL OR budget_daily_usd >= 0) AND "
            "(budget_monthly_usd IS NULL OR budget_monthly_usd >= 0) AND "
            "(budget_total_usd IS NULL OR budget_total_usd >= 0)",
            name="ck_projects_budget_caps_nonneg",
        ),
        # Kanban #989 — HITL timeout must be >= 1 hour when set (NULL =
        # indefinite pause, current behavior). Mirror of migration 0029's
        # named CHECK so ORM autogen stays in lockstep.
        CheckConstraint(
            "hitl_timeout_hours IS NULL OR hitl_timeout_hours >= 1",
            name="ck_projects_hitl_timeout_positive",
        ),
        # Kanban #953 — fiscal_year_start must be a month 1..12 when set.
        # NULL allowed for legacy resilience; new INSERTs land DEFAULT 1.
        # Mirror of migration 0032's named CHECK.
        CheckConstraint(
            "fiscal_year_start IS NULL OR (fiscal_year_start >= 1 AND fiscal_year_start <= 12)",
            name="ck_projects_fiscal_year_start_valid",
        ),
        # Kanban #1211 — GOV1 hard kill (is_killed) and GOV3 soft pause
        # (is_paused) are mutually exclusive. Mirror of migration 0040's
        # CHECK; defense-in-depth against raw-SQL drift. The pause/unpause
        # service ALSO checks at the app layer so the 409 fires before the
        # DB IntegrityError 400 fallback.
        CheckConstraint(
            "NOT (is_killed AND is_paused)",
            name="ck_projects_kill_pause_mutex",
        ),
        # Kanban #1011 (2026-05-20): HITL nudge threshold must be >= 0 when
        # set (NULL = disabled). Mirror of migration 0047's CHECK — defense-
        # in-depth against raw-SQL drift. Pydantic ProjectUpdate ge=0 is the
        # first wall.
        CheckConstraint(
            "hitl_nudge_threshold_hours IS NULL OR hitl_nudge_threshold_hours >= 0",
            name="ck_projects_hitl_nudge_threshold_nonneg",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Project id={self.id} name={self.name!r} "
            f"active={self.is_active} status={self.status} team={self.team!r}>"
        )
