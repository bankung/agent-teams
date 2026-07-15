// Mirror of api/src/constants.py — keep in sync. Numbers are stable forever; never repurpose.
// Mirrored: RecordStatus, TaskStatus, TaskPriority, TaskRole (all 28 named codes across
// 6 team ranges — dev 1-10, novel 11-20, seo 21-30, sem 31-40, data-analytics 41-50,
// social 51-60), ProjectTeam (incl. netops), TaskRunMode.
// Deferred: TaskHistoryOperation ('U','D') — internal audit-trigger payload, no browser-facing use.

export const RecordStatus = {
  ACTIVE: 1,
  DELETED: 0,
} as const;
export type RecordStatusValue = typeof RecordStatus[keyof typeof RecordStatus];

export const TaskStatus = {
  TODO: 1,
  IN_PROGRESS: 2,
  REVIEW: 3,
  BLOCKED: 4,
  DONE: 5,
  CANCELLED: 6,
  HALTED_PENDING_USER: 8,
} as const;
export type TaskStatusValue = typeof TaskStatus[keyof typeof TaskStatus];

export const TaskPriority = {
  LOW: 1,
  NORMAL: 2,
  HIGH: 3,
  URGENT: 4,
} as const;
export type TaskPriorityValue = typeof TaskPriority[keyof typeof TaskPriority];

// tasks.assigned_role — INTEGER NULLABLE, 1..60 range (validated at the API
// boundary, not a DB CHECK). Named codes below mirror api/src/constants.py's
// TaskRole class 1:1, grouped by its docstring's "range partition": dev 1-10,
// novel 11-20, seo 21-30, sem 31-40, data-analytics 41-50, social 51-60. Gaps
// inside a range are RESERVED for that team to name later — the API accepts
// them as raw ints today; the FE falls back to `role${n}` for any code with
// no entry in ROLE_LABEL (TaskCard.tsx) / ROLE_SHORT (ListView.tsx).
export const TaskRole = {
  // Dev range (1..10)
  FRONTEND: 1,
  BACKEND: 2,
  DEVOPS: 3,
  QA: 4,
  REVIEWER: 5,
  SECURITY_REVIEWER: 6, // Kanban #7 Section B (2026-05-16)

  // Novel range (11..20)
  NOVEL_WRITER: 11,
  NOVEL_EDITOR: 12,
  NOVEL_PROOFREADER: 13,

  // SEO range (21..30) — Kanban #1266 AC3 (2026-05-20)
  SEO_STRATEGIST: 21,
  TECHNICAL_SEO_SPECIALIST: 22,
  CONTENT_SEO_OPTIMIZER: 23,
  SEO_REPORTING_ANALYST: 24,

  // SEM range (31..40) — Kanban #1269 AC8 (2026-05-20)
  SEM_CAMPAIGN_LEAD: 31,
  GOOGLE_ADS_SPECIALIST: 32,
  META_ADS_SPECIALIST: 33,
  PLATFORM_ADS_COORDINATOR: 34,

  // Data-analytics range (41..50) — Kanban #1271 AC7 (2026-05-20)
  BI_ANALYST: 41,
  SQL_OPTIMIZER: 42,
  DASHBOARD_DESIGNER: 43,
  ANALYTICS_PLATFORM_INTEGRATOR: 44,

  // Social range (51..60) — Kanban #2812 (2026-07-10)
  CONTENT_WRITER: 51,
  CONTENT_HOOK_DOCTOR: 52,
  CONTENT_EDITOR: 53,
  CONTENT_VERACITY_CHECKER: 54,
  THAI_PROOFREADER: 55,
  SOCIAL_BI_ANALYST: 56, // cross-team, data-analytics
  SOCIAL_GENERAL_RESEARCHER: 57, // cross-team
} as const;
export type TaskRoleValue = typeof TaskRole[keyof typeof TaskRole];

export const ProjectTeam = {
  DEV: "dev",
  NOVEL: "novel",
  GENERAL: "general",
  CONTENT: "content",
  SEO: "seo", // Kanban #1266 (migration 0042, 2026-05-20)
  DATA_ANALYTICS: "data-analytics", // Kanban #1271 (migration 0043, 2026-05-20)
  SEM: "sem", // Kanban #1269 (migration 0044, 2026-05-20)
  NETOPS: "netops", // Kanban #2827 (app-validated post-#1620, no migration)
  SOCIAL: "social", // Kanban #2811 (no migration — app-validated post-#1620, 2026-07-10)
} as const;
export type ProjectTeamValue = typeof ProjectTeam[keyof typeof ProjectTeam];

// TaskRunMode — Step 2 execution mode (Kanban #483).
// auto_headless requires per-project consent (projects.auto_run_consent_at IS NOT NULL).
export const TaskRunMode = {
  MANUAL: "manual",
  AUTO_PICKUP: "auto_pickup",
  AUTO_HEADLESS: "auto_headless",
} as const;
export type TaskRunModeValue = typeof TaskRunMode[keyof typeof TaskRunMode];

// TaskKind — V3+ scope-lock (Kanban #706). Discriminates AI-runner work from human work.
// Cross-table rule enforced by API: task_kind='human' requires run_mode='manual'.
export const TaskKind = {
  AI: "ai",
  HUMAN: "human",
} as const;
export type TaskKindValue = typeof TaskKind[keyof typeof TaskKind];

// Shared form option arrays — consumed by NewTaskModal + AiTaskModal.
// ListView extends these with sentinel rows (value=0/"All") — keep those local.

export type PriorityOption = { value: TaskPriorityValue; label: string };
export const PRIORITY_OPTIONS: PriorityOption[] = [
  { value: TaskPriority.URGENT, label: "Urgent" },
  { value: TaskPriority.HIGH, label: "High" },
  { value: TaskPriority.NORMAL, label: "Normal" },
  { value: TaskPriority.LOW, label: "Low" },
];

// "" sentinel = unassigned. Modals that filter by enabled_roles pass this
// through filterRoleOptions() which always retains the empty-string entry.
export type RoleOption = { value: "" | TaskRoleValue; label: string };
export const ROLE_OPTIONS: RoleOption[] = [
  { value: "", label: "— unassigned —" },
  // Dev
  { value: TaskRole.FRONTEND, label: "Frontend" },
  { value: TaskRole.BACKEND, label: "Backend" },
  { value: TaskRole.DEVOPS, label: "DevOps" },
  { value: TaskRole.QA, label: "QA" },
  { value: TaskRole.REVIEWER, label: "Reviewer" },
  { value: TaskRole.SECURITY_REVIEWER, label: "Security Reviewer" },
  // Novel
  { value: TaskRole.NOVEL_WRITER, label: "Novel Writer" },
  { value: TaskRole.NOVEL_EDITOR, label: "Novel Editor" },
  { value: TaskRole.NOVEL_PROOFREADER, label: "Novel Proofreader" },
  // SEO
  { value: TaskRole.SEO_STRATEGIST, label: "SEO Strategist" },
  { value: TaskRole.TECHNICAL_SEO_SPECIALIST, label: "Technical SEO Specialist" },
  { value: TaskRole.CONTENT_SEO_OPTIMIZER, label: "Content SEO Optimizer" },
  { value: TaskRole.SEO_REPORTING_ANALYST, label: "SEO Reporting Analyst" },
  // SEM
  { value: TaskRole.SEM_CAMPAIGN_LEAD, label: "SEM Campaign Lead" },
  { value: TaskRole.GOOGLE_ADS_SPECIALIST, label: "Google Ads Specialist" },
  { value: TaskRole.META_ADS_SPECIALIST, label: "Meta Ads Specialist" },
  { value: TaskRole.PLATFORM_ADS_COORDINATOR, label: "Platform Ads Coordinator" },
  // Data-analytics
  { value: TaskRole.BI_ANALYST, label: "BI Analyst" },
  { value: TaskRole.SQL_OPTIMIZER, label: "SQL Optimizer" },
  { value: TaskRole.DASHBOARD_DESIGNER, label: "Dashboard Designer" },
  { value: TaskRole.ANALYTICS_PLATFORM_INTEGRATOR, label: "Analytics Platform Integrator" },
  // Social
  { value: TaskRole.CONTENT_WRITER, label: "Content Writer" },
  { value: TaskRole.CONTENT_HOOK_DOCTOR, label: "Content Hook Doctor" },
  { value: TaskRole.CONTENT_EDITOR, label: "Content Editor" },
  { value: TaskRole.CONTENT_VERACITY_CHECKER, label: "Content Veracity Checker" },
  { value: TaskRole.THAI_PROOFREADER, label: "Thai Proofreader" },
  { value: TaskRole.SOCIAL_BI_ANALYST, label: "Social BI Analyst" },
  { value: TaskRole.SOCIAL_GENERAL_RESEARCHER, label: "Social General Researcher" },
];

// Kanban #2826 — per-team role-code ranges, mirroring the "range partition"
// documented on api/src/constants.py's TaskRole class. Lets NewTaskModal scope
// its role dropdown to the bound project's team instead of always showing the
// full catalog. `general` / `content` / `netops` are NOT part of that range
// partition (content shares the 51-60 "social" batch rather than owning a
// range; general/netops carry no TaskRole-coded roster at all) — those teams
// intentionally fall through to the full, unscoped ROLE_OPTIONS list.
const TEAM_ROLE_RANGE: Partial<Record<ProjectTeamValue, readonly [number, number]>> = {
  [ProjectTeam.DEV]: [1, 10],
  [ProjectTeam.NOVEL]: [11, 20],
  [ProjectTeam.SEO]: [21, 30],
  [ProjectTeam.SEM]: [31, 40],
  [ProjectTeam.DATA_ANALYTICS]: [41, 50],
  [ProjectTeam.SOCIAL]: [51, 60],
};

/**
 * Narrow ROLE_OPTIONS to the codes owned by `team`'s range (the "" unassigned
 * sentinel is always retained). Unknown / unranged teams (general, content,
 * netops, or an unrecognized string) return the full, unscoped list — the
 * same "show everything" default the caller had before this existed.
 */
export function roleOptionsForTeam(team: string | null | undefined): RoleOption[] {
  const range = team ? TEAM_ROLE_RANGE[team as ProjectTeamValue] : undefined;
  if (!range) return ROLE_OPTIONS;
  const [min, max] = range;
  return ROLE_OPTIONS.filter(
    (o) => o.value === "" || (typeof o.value === "number" && o.value >= min && o.value <= max),
  );
}

// Minimum length for the pause-override reason (mirrors BE min_length=10).
export const REASON_MIN_CHARS = 10;
