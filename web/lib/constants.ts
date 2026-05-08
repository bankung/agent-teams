// Mirror of api/src/constants.py — keep in sync. Numbers are stable forever; never repurpose.
// Mirrored: RecordStatus, TaskStatus, TaskPriority, TaskRole, ProjectLead.
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
} as const;
export type TaskRoleValue = typeof TaskRole[keyof typeof TaskRole];

export const ProjectLead = {
  DEV: "dev",
  NOVEL: "novel",
} as const;
export type ProjectLeadValue = typeof ProjectLead[keyof typeof ProjectLead];
