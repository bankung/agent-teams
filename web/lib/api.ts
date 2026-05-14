// API types + fetch helpers — mirror of api/src/schemas/{project,task}.py.
// Source of truth: context/projects/agent-teams/shared/api-contracts.md.

import type {
  TaskStatusValue,
  TaskPriorityValue,
  TaskRoleValue,
  ProjectTeamValue,
  TaskRunModeValue,
  TaskKindValue,
} from "./constants";

// Source — one entry in ProjectRead.sources (#778). Curated reference URL or
// local path that an agent / human may need while working on the project.
// `label` / `kind` are optional human metadata; `kind` is free-form (common
// values: doc | spec | repo | dashboard | other) and rendered as a chip when
// present. Local paths (no `scheme://`) are rendered as plain text — browsers
// can't navigate to them.
export type Source = {
  url: string;
  label?: string;
  kind?: string;
};

// ProjectRead — mirror of api/src/schemas/project.py:ProjectRead.
export type ProjectRead = {
  id: number;
  name: string;
  description: string | null;
  paths_web: string;
  paths_api: string;
  paths_db: string;
  stack_web: string | null;
  stack_api: string | null;
  stack_db: string | null;
  config: Record<string, unknown>;
  is_active: boolean;
  team: ProjectTeamValue;
  created_at: string; // ISO 8601 with timezone
  updated_at: string;
  auto_run_consent_at: string | null; // null = not consented; set by POST /api/projects/{id}/grant-consent (#483)
  sources: Source[]; // #778 — curated references; ALWAYS a list (never null), each entry has a required `url`
};

// AcceptanceCriterion — one entry in TaskRead.acceptance_criteria (#797).
// JSONB shape; verified_at is ISO 8601 string (serialized via mode='json' on
// the backend per shared/decisions.md 2026-05-12 fix to #801).
export type AcceptanceCriterion = {
  text: string;
  status: "pending" | "passed" | "failed" | "na";
  verified_by: string | null;
  verified_at: string | null;
  notes: string | null;
};

// AnswerHistoryEntry — one entry in QuestionPayload.answer_history (#834).
export type AnswerHistoryEntry = {
  value: string;
  answered_by: string;
  answered_at: string | null;
  is_valid: boolean;
  invalidated_reason: string | null;
};

// QuestionPayload — JSONB payload for question/decision tasks (#834).
export type QuestionPayload = {
  question: string;
  options: string[] | null;
  answer_history: AnswerHistoryEntry[];
};

// TaskRead — mirror of api/src/schemas/task.py:TaskRead.
export type TaskRead = {
  id: number;
  project_id: number;
  parent_task_id: number | null;
  title: string;
  description: string | null;
  process_status: TaskStatusValue;
  priority: TaskPriorityValue;
  assigned_role: TaskRoleValue | null;
  run_mode: TaskRunModeValue; // #483 — default "manual"
  task_kind: TaskKindValue; // #706 — default "human"
  is_template: boolean; // #706 — recurrence template flag
  is_pending: boolean; // #750 — paired with process_status=IN_PROGRESS to render the yellow "pending" marker
  recurrence_rule: string | null; // #706 — cron expression
  recurrence_timezone: string; // #706 — IANA TZ name, default "UTC"
  next_fire_at: string | null; // #706 — ISO 8601 UTC "Z" form
  spawned_from_task_id: number | null; // #706 — system-managed lineage
  scheduled_at: string | null; // #723 — one-shot fire path; XOR with is_template
  blocked_by: number | null; // #771 — peer-task blocker FK; null = unblocked
  sort_order: number | null; // #772 — float lane-local ordering key (NULL = unordered; ORDER BY sort_order ASC NULLS LAST, created_at ASC)
  acceptance_criteria: AcceptanceCriterion[] | null; // #797 — structured per-criterion verdicts; null/empty when not authored
  interaction_kind: "work" | "question" | "decision"; // #834 — task interaction type; default "work"
  question_payload: QuestionPayload | null; // #834 — question/options/history; non-null when interaction_kind != "work"
  resume_context: Record<string, unknown> | null; // #834 — opaque context passed back on resume
  status_change_reason: string | null; // #854 — free-form rationale captured on a process_status flip (most commonly ps=6 CANCELLED). Audit-trigger snapshot includes it.
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
};

// ProjectGrantConsent — body shape for POST /api/projects/{id}/grant-consent (#483).
// Backend uses Pydantic extra="forbid" — sending any other field returns 422.
export type ProjectGrantConsent = {
  confirm_name: string;
};

// HttpError — typed error for non-2xx API responses. Callers (page.tsx) can
// discriminate on `.status` to separate 404 from 500 / network / 422. The
// `.message` is the formatted detail (string for 400, joined `msg` for Pydantic
// 422 arrays) so consumers that only read `err.message` keep working unchanged.
export class HttpError extends Error {
  readonly status: number;
  readonly detail: unknown;
  constructor(status: number, detail: unknown, message: string) {
    super(message);
    this.name = "HttpError";
    this.status = status;
    this.detail = detail;
  }
}

// Base URL split: BROWSER_API_URL for client-bundle fetches; SERVER_API_URL for SSR
// inside the web container (set INTERNAL_API_URL=http://api:8456 — see
// shared/api-contracts.md "Conventions"). Selection: typeof window === 'undefined'.

const BROWSER_API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8456";
const SERVER_API_URL = process.env.INTERNAL_API_URL ?? BROWSER_API_URL;

function apiBaseUrl(): string {
  return typeof window === "undefined" ? SERVER_API_URL : BROWSER_API_URL;
}

// formatDetail — render the parsed FastAPI `detail` field as a single string.
// 400 / 404: `detail` is a string (route-locked source text). 422: Pydantic
// returns an array of `{ type, loc, msg, input, ... }` error objects — join
// the human-readable msgs. Returns null for unknown shapes so callers fall
// back to the status-line.
function formatDetail(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((d) => {
      if (
        d &&
        typeof d === "object" &&
        "msg" in d &&
        typeof (d as { msg: unknown }).msg === "string"
      ) {
        return (d as { msg: string }).msg;
      }
      return JSON.stringify(d);
    });
    return parts.length > 0 ? parts.join("; ") : null;
  }
  return null;
}

async function jsonFetch<T>(
  path: string,
  init?: {
    method?: string;
    headers?: Record<string, string>;
    body?: string;
  },
): Promise<T> {
  // BACKEND_FAILURE_INJECT — test-only knob (Kanban #761). When set in a
  // non-production env, synthesize a 500 before hitting the real backend.
  // Use case: dev-tester probes for app/error.tsx routing on non-404 throws
  // (WARN-1 follow-up from #760). Guarded by NODE_ENV so a misconfigured
  // prod build cannot accidentally inject failures.
  if (
    process.env.NODE_ENV !== "production" &&
    process.env.BACKEND_FAILURE_INJECT === "true"
  ) {
    throw new HttpError(
      500,
      "BACKEND_FAILURE_INJECT=true (synthetic 500 from web/lib/api.ts)",
      "BACKEND_FAILURE_INJECT=true (synthetic 500 from web/lib/api.ts)",
    );
  }

  const url = `${apiBaseUrl()}${path}`;
  const response = await fetch(url, {
    method: init?.method,
    body: init?.body,
    cache: "no-store",
    headers: { Accept: "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as {
      detail?: unknown;
    };
    const message =
      formatDetail(body.detail) ?? `${response.status} ${response.statusText}`;
    throw new HttpError(response.status, body.detail, message);
  }
  return (await response.json()) as T;
}

export async function getProjectByName(name: string): Promise<ProjectRead> {
  return jsonFetch<ProjectRead>(
    `/api/projects/by-name/${encodeURIComponent(name)}`,
  );
}

// listProjects — V3 project switcher data source (Kanban #407).
// `status=1` is the documented migration path from the deprecated /active endpoint
// (api-contracts.md L62-65); backend filters soft-deleted by default. No X-Project-Id
// header — project endpoints are project-scoped by URL, not by header.
type ListProjectsOpts = { status?: 0 | 1 };

export async function listProjects(
  opts: ListProjectsOpts = {},
): Promise<ProjectRead[]> {
  const qs = new URLSearchParams();
  if (opts.status !== undefined) qs.set("status", String(opts.status));
  const path = qs.toString() ? `/api/projects?${qs}` : `/api/projects`;
  return jsonFetch<ProjectRead[]>(path);
}

// ProjectStatsCostUsage — Kanban #871. Per-project token/cost roll-up,
// nested under ProjectStatsEntry.cost_usage. ALL 6 keys always present
// (zero-filled when the project has no session_runs) — FE can use
// `session_run_count === 0` as the cheap empty-state guard.
//
// `total_cost_usd` is a JSON STRING, not a number (Pydantic v2 serializes
// `Decimal` as string for precision). Parse via Number()/parseFloat() before
// arithmetic; NEVER use the unary `+` coercion (silently returns NaN on
// non-numeric strings).
export type ProjectStatsCostUsage = {
  total_input_tokens: number;
  total_output_tokens: number;
  total_context_chars: number;
  total_cost_usd: string;
  budget_warning_count: number;
  session_run_count: number;
};

// ProjectStatsEntry — mirror of GET /api/projects/stats row (Kanban #769).
// counts: all 5 TaskStatus keys ("1".."5") always present even when zero —
// FE renders the lane grid without `||0` coalescing. run_mode_breakdown: all
// 3 keys always present. last_activity_at: MAX(tasks.updated_at) over active
// tasks; null when project has no active tasks. Ordering preserved by backend
// (projects.created_at ASC). cost_usage: #871 — always present, zero-filled
// when the project has no session_runs.
export type ProjectStatsEntry = {
  id: number;
  name: string;
  team: ProjectTeamValue;
  run_mode_breakdown: Record<TaskRunModeValue, number>;
  counts: Record<"1" | "2" | "3" | "4" | "5" | "6", number>; // #854 — "6"=CANCELLED added 2026-05-13; dashboard LANES tuple iterates 1..5 only (cancelled count display = #870).
  last_activity_at: string | null;
  cost_usage: ProjectStatsCostUsage;
};

export async function getProjectsStats(): Promise<ProjectStatsEntry[]> {
  return jsonFetch<ProjectStatsEntry[]>(`/api/projects/stats`);
}

// createProject — POST /api/projects body (Kanban #843 FE).
// Mirrors api/src/schemas/project.py:ProjectCreate. `paths` is required with
// all 3 lane keys; the modal derives them from working_path (or name when
// blank) so the user never sees a raw paths form. `working_path` /
// `working_repo` omitted when blank (Pydantic min_length=1 would 422 on "").
export type ProjectCreateBody = {
  name: string;
  paths: { web: string; api: string; db: string };
  team: ProjectTeamValue;
  working_path?: string;
  working_repo?: string;
};

export async function createProject(
  body: ProjectCreateBody,
): Promise<ProjectRead> {
  return jsonFetch<ProjectRead>(`/api/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// grantConsent — V3 consent grant flow (Kanban #407 / #483 follow-up).
// Body uses extra="forbid" — only `confirm_name` is accepted. 400 on mismatch with
// stable detail "confirm_name must match project name exactly". Idempotent re-grant.
export async function grantConsent(
  projectId: number,
  confirmName: string,
): Promise<ProjectRead> {
  return jsonFetch<ProjectRead>(`/api/projects/${projectId}/grant-consent`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirm_name: confirmName }),
  });
}

type ListTasksOpts = {
  pending?: boolean;
  parent_task_id?: number;
  top_level_only?: boolean;
  limit?: number;
};

export async function listTasks(
  projectId: number,
  opts: ListTasksOpts = {},
): Promise<TaskRead[]> {
  const qs = new URLSearchParams();
  if (opts.pending) qs.set("pending", "true");
  if (opts.top_level_only) qs.set("top_level_only", "true");
  else if (opts.parent_task_id !== undefined)
    qs.set("parent_task_id", String(opts.parent_task_id));
  if (opts.limit !== undefined) qs.set("limit", String(opts.limit));
  const path = qs.toString() ? `/api/tasks?${qs}` : `/api/tasks`;
  return jsonFetch<TaskRead[]>(path, {
    headers: { "X-Project-Id": String(projectId) },
  });
}

export async function getTask(
  projectId: number,
  id: number,
): Promise<TaskRead> {
  return jsonFetch<TaskRead>(`/api/tasks/${id}`, {
    headers: { "X-Project-Id": String(projectId) },
  });
}

// PATCH /api/tasks/{id} — partial update; minimal subset for T4 drag-drop.
// Wider set (title/priority/assigned_role/run_mode/task_kind/is_template/...) is
// accepted by the API per shared/api-contracts.md; expand the type as new
// mutation surfaces land.
// blocked_by (#771): explicit null clears; positive int sets; key-absent =
// unchanged. Picker UI (TaskDetail) is the only consumer for now.
// new_answer / invalidate_last_answer (#834): question/decision answer flow.
// status_change_reason (#854): free-form rationale paired with a process_status
// flip (most commonly ps=6 CANCELLED). min_length=1 — backend 422 on "".
// run_mode (#860): flip 'manual' → 'auto_pickup' to enqueue an AI task for the
// autorun loop. Skip 'auto_headless' from FE — backend rejects with 400 when
// project lacks consent.
export type TaskPatch = Partial<
  Pick<TaskRead, "process_status" | "priority" | "title" | "blocked_by" | "sort_order" | "run_mode">
> & {
  new_answer?: string | null;
  new_answer_by?: string | null;
  invalidate_last_answer?: boolean | null;
  invalidated_reason?: string | null;
  status_change_reason?: string | null;
};

export async function patchTask(
  projectId: number,
  id: number,
  body: TaskPatch,
): Promise<TaskRead> {
  return jsonFetch<TaskRead>(`/api/tasks/${id}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      "X-Project-Id": String(projectId),
    },
    body: JSON.stringify(body),
  });
}

// POST /api/tasks/{id}/reorder — anchor-based within-lane sort_order write
// (Kanban #772). Body: {before_id?, after_id?} — at least one required. Server
// computes the new sort_order atomically with same-lane + blocker-order checks.
// 422 on cross-lane anchor, soft-deleted anchor, blocker-order violation, or
// shape error (same id in both anchors / both omitted).
export type TaskReorderBody = {
  before_id?: number;
  after_id?: number;
};

export async function reorderTask(
  projectId: number,
  id: number,
  body: TaskReorderBody,
): Promise<TaskRead> {
  return jsonFetch<TaskRead>(`/api/tasks/${id}/reorder`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Project-Id": String(projectId),
    },
    body: JSON.stringify(body),
  });
}

// GET /api/tasks/{id}/blocks — reverse-lookup for blocked_by (Kanban #771).
// Returns active tasks whose blocked_by == id (dependents). Used by TaskDetail
// for the optional "Also blocks" affordance. Soft-deleted excluded by API.
export async function getTaskBlocks(
  projectId: number,
  id: number,
): Promise<TaskRead[]> {
  return jsonFetch<TaskRead[]>(`/api/tasks/${id}/blocks`, {
    headers: { "X-Project-Id": String(projectId) },
  });
}

// submitAnswer — append an answer to a question/decision task (#834).
// Delegates to patchTask; callers get the full updated TaskRead back so they
// can call onPatch(updated) to refresh the drawer without a separate GET.
export async function submitAnswer(
  projectId: number,
  taskId: number,
  value: string,
  answeredBy = "user",
): Promise<TaskRead> {
  return patchTask(projectId, taskId, {
    new_answer: value,
    new_answer_by: answeredBy,
  });
}

// invalidateAnswer — flip the last valid answer to is_valid=false (#834).
// Requires a non-empty reason; backend enforces the same constraint.
export async function invalidateAnswer(
  projectId: number,
  taskId: number,
  reason: string,
): Promise<TaskRead> {
  return patchTask(projectId, taskId, {
    invalidate_last_answer: true,
    invalidated_reason: reason,
  });
}

// cancelTask — terminal-state flip to process_status=6 (CANCELLED) paired with
// a required free-form reason (#854). Backend Pydantic guards: process_status
// must be 6 AND status_change_reason min_length=1. The cancelled row is
// excluded from the default GET /api/tasks list (must opt-in with
// ?include_cancelled=true to see it again).
export async function cancelTask(
  projectId: number,
  taskId: number,
  reason: string,
): Promise<TaskRead> {
  return patchTask(projectId, taskId, {
    process_status: 6 as TaskStatusValue,
    status_change_reason: reason,
  });
}
