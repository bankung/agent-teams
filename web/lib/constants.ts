// Mirror of api/src/constants.py — keep in sync. Numbers are stable forever; never repurpose.
// Mirrored: RecordStatus, TaskStatus, TaskPriority, TaskRole, ProjectTeam, TaskRunMode.
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
} as const;
export type TaskStatusValue = typeof TaskStatus[keyof typeof TaskStatus];

export const TaskPriority = {
  LOW: 1,
  NORMAL: 2,
  HIGH: 3,
  URGENT: 4,
} as const;
export type TaskPriorityValue = typeof TaskPriority[keyof typeof TaskPriority];

export const TaskRole = {
  FRONTEND: 1,
  BACKEND: 2,
  DEVOPS: 3,
  QA: 4,
  REVIEWER: 5,
  SECURITY_REVIEWER: 6, // Kanban #7 Section B (2026-05-16)
} as const;
export type TaskRoleValue = typeof TaskRole[keyof typeof TaskRole];

export const ProjectTeam = {
  DEV: "dev",
  NOVEL: "novel",
  GENERAL: "general",
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
