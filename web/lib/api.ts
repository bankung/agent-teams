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
export type TaskPatch = Partial<
  Pick<TaskRead, "process_status" | "priority" | "title" | "blocked_by">
>;

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
