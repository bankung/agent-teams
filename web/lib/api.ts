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

// TaskTypeValue — mirror of api/src/schemas/task.TaskTypeLiteral.
// Kanban #803 ('bug'/'feature'/'chore'/'docs'/'refactor') + #1211 AA3 ('audit').
export type TaskTypeValue =
  | "bug"
  | "feature"
  | "chore"
  | "docs"
  | "refactor"
  | "audit";

// Source — #778 curated reference; label/kind optional; non-http rendered as plain text
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
  // #777 — project-root + repo override (nullable TEXT on BE); surfaced on
  // every ProjectRead. EditProjectModal (#943) consumes both for pre-fill.
  working_path: string | null;
  working_repo: string | null;
  // #951 AC #5 — per-project spend caps (BE spawn 2026-05-15). Numeric(10,4) on
  // the DB serialized as JSON string (e.g. "5.0000") per the same Decimal-as-
  // string convention used by session_runs.total_cost_usd. NULL = unlimited
  // (no budget configured for that period). Daily and monthly are currently
  // V1-only caps; lifetime spend (`cost_usage.total_cost_usd` on the stats
  // endpoint) is the only spend signal available — per-period spend ships in
  // a follow-up. FE renderer (BudgetBar) falls back total → monthly → daily
  // when picking which cap to display.
  budget_daily_usd: string | null;
  budget_monthly_usd: string | null;
  budget_total_usd: string | null;
  // Kanban #1209 (2026-05-19) AA1 — hard kill switch state. `is_killed` is
  // ALWAYS present on ProjectRead (NOT NULL DEFAULT false on the column);
  // `killed_at` / `killed_reason` are preserved through revive (D4 history),
  // so the FE can show "last killed YYYY-MM-DD" even on revived projects.
  // Optional in the FE type for legacy-row defensive resilience — pre-AA1
  // serialized payloads may omit them, in which case treat as not-killed.
  is_killed?: boolean;
  killed_at?: string | null;
  killed_reason?: string | null;
  // Kanban #1211 AA3 — soft-pause state (separate from AA1 hard kill).
  // is_paused stays true between the audit task DONE and the operator's
  // resolve-flag action. paused_at / paused_reason preserved across unpause
  // for audit-trail continuity (D4 history pattern from AA1).
  is_paused?: boolean;
  paused_at?: string | null;
  paused_reason?: string | null;
  // Kanban #1212 AA4 — adjustments allowlist (services/pause_switch.py
  // ADJUST_CONTINUE_ALLOWED_KEYS). FE pre-fills the Adjust+Continue form
  // from these. NULL on health_thresholds = use auditor defaults
  // (budget_burn_threshold_pct=100, failure_rate_threshold_pct=20, etc).
  health_thresholds?: Record<string, unknown> | null;
  approval_policies?: Record<string, unknown> | null;
  hitl_timeout_hours?: number | null;
  audit_enabled?: boolean;
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
//
// Kanban #1211 AA3 (2026-05-19) added the AA3-flag bookkeeping fields below
// (`is_audit_flag`, `breach_streak_days`, `audit_history`, `latest_audit`,
// `latest_audit_summary`, `reasons`, `metrics`, plus the resolution sentinel
// triplet written by AA3 resolve_flag). All are optional — generic question
// tasks (approval prompts, design Option A/B questions) carry only the base
// triad (question/options/answer_history); only AA3-spawned flag rows set
// `is_audit_flag=true` and the AA3 fields.
export type QuestionPayload = {
  question: string;
  options: string[] | null;
  answer_history: AnswerHistoryEntry[];
  // ---- AA3 audit-flag fields (services/audit_flag.py:_new_flag_payload) ----
  is_audit_flag?: boolean;
  breach_streak_days?: number;
  audit_history?: number[];
  latest_audit?: number;
  latest_audit_summary?: {
    verdict?: string | null;
    severity?: string | null;
    recommendation?: string | null;
  };
  // Optional auditor-surfaced extras (rendered in the expand-card view when
  // present). Auditor schema is still evolving (AA2 ownership) — value-
  // tolerant on shape.
  reasons?: string[];
  metrics?: Record<string, unknown>;
  raw_evidence?: unknown;
  // Resolution sentinel written by services/pause_switch.resolve_flag on
  // keep_paused / terminate branches (also set on continue / adjust_continue
  // for symmetry once the flag is DONE). Lets the AA4 UI show
  // "kept paused on YYYY-MM-DD" rather than just "DONE".
  resolved_action?: "continue" | "adjust_continue" | "keep_paused" | "terminate";
  resolved_at?: string | null;
  resolved_by?: string | null;
  kill_audit_id?: number;
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
  // #803 (2026-05-12) + #1211 AA3 (2026-05-19 — added "audit"). Backfilled to
  // 'feature' on legacy rows by migration 0015's server_default. Always present
  // on TaskRead from the BE; defensive optional on the FE for legacy serialized
  // payloads that pre-date the addition.
  task_type?: TaskTypeValue;
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
  // #944 — per-task LLM-cost estimate; populated on done-flip. Null on legacy
  // rows + tasks that never reached DONE. estimated_cost_usd is the BE Decimal
  // serialized as a string (e.g. "0.0001", 4 decimals) — keep as string on
  // the wire to avoid float rounding.
  estimated_input_tokens: number | null;
  estimated_output_tokens: number | null;
  estimated_cost_usd: string | null;
  // #952 — in-graph auditor outputs; structure value-tolerant (verdict /
  // severity / recommendation / evidence keys when populated). Surfaces the
  // raw blob so the Audit History expand-card can pretty-print it.
  audit_report?: Record<string, unknown> | null;
  // #1211 AA3 — per-task override hatch (paired). The pair is set on POST
  // when the operator chose to file the task against a paused project; the
  // FE reads them to render a "bypassed pause" indicator + the rationale.
  allow_during_pause?: boolean;
  allow_during_pause_reason?: string | null;
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

// INTERNAL_API_URL for SSR; NEXT_PUBLIC_API_URL for browser
const BROWSER_API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";
const SERVER_API_URL = process.env.INTERNAL_API_URL ?? BROWSER_API_URL;

function apiBaseUrl(): string {
  return typeof window === "undefined" ? SERVER_API_URL : BROWSER_API_URL;
}

// formatDetail — 400/404: string detail; 422: join Pydantic msgs
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
  // #761 — BACKEND_FAILURE_INJECT: test-only synthetic 500; guarded by NODE_ENV
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

// #407 — status=1 filter; no X-Project-Id header (project endpoint)
type ListProjectsOpts = { status?: 0 | 1 };

export async function listProjects(
  opts: ListProjectsOpts = {},
): Promise<ProjectRead[]> {
  const qs = new URLSearchParams();
  if (opts.status !== undefined) qs.set("status", String(opts.status));
  const path = qs.toString() ? `/api/projects?${qs}` : `/api/projects`;
  return jsonFetch<ProjectRead[]>(path);
}

// #871 — token/cost roll-up; total_cost_usd is STRING (Decimal); use parseFloat, not unary +
export type ProjectStatsCostUsage = {
  total_input_tokens: number;
  total_output_tokens: number;
  total_context_chars: number;
  total_cost_usd: string;
  budget_warning_count: number;
  session_run_count: number;
};

// #769/#871 — stats row; counts["1".."6"] always present; cost_usage zero-filled
export type ProjectStatsEntry = {
  id: number;
  name: string;
  team: ProjectTeamValue;
  run_mode_breakdown: Record<TaskRunModeValue, number>;
  counts: Record<"1" | "2" | "3" | "4" | "5" | "6", number>; // #854 — "6"=CANCELLED added 2026-05-13; dashboard LANES tuple iterates 1..5 only (cancelled count display = #870).
  last_activity_at: string | null;
  cost_usage: ProjectStatsCostUsage;
};

export async function getProjectsStats(opts?: {
  projectId?: number;
}): Promise<ProjectStatsEntry[]> {
  const url =
    opts?.projectId != null
      ? `/api/projects/stats?project_id=${opts.projectId}`
      : `/api/projects/stats`;
  return jsonFetch<ProjectStatsEntry[]>(url);
}

// #1082 — auditor cross-project daily rollup. BE pre-sorts (project_id ASC,
// day DESC), zero-fills all 5 verdict keys, and filters out tasks with
// audit_report=null + soft-deleted rows. Empty array is the typical state
// today (#952 auditor not yet running against real data) — FE hides the
// dashboard section entirely when the response is [].
export type AuditDailyCounts = {
  pass: number;
  auto_resolved: number;
  escalated: number;
  failed_giveup: number;
  pending_escalation: number;
};

export type AuditDailyRollupEntry = {
  project_id: number;
  project_name: string;
  day: string; // ISO date "YYYY-MM-DD" (not a timestamp)
  counts: AuditDailyCounts;
};

export async function getAuditDailyRollup(
  opts: { from?: string; to?: string } = {},
): Promise<AuditDailyRollupEntry[]> {
  const qs = new URLSearchParams();
  if (opts.from) qs.set("from", opts.from);
  if (opts.to) qs.set("to", opts.to);
  const path = qs.toString()
    ? `/api/audit/daily-rollup?${qs}`
    : `/api/audit/daily-rollup`;
  return jsonFetch<AuditDailyRollupEntry[]>(path);
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

// updateProject — PATCH /api/projects/{id} body (Kanban #943 FE).
// Mirrors api/src/schemas/project.py:ProjectUpdate. All fields optional —
// caller sends only the diff. Explicit `null` on a nullable text column
// CLEARS it (BE writes NULL); explicit array on `sources` (incl. `[]`)
// REPLACES the prior list. `config` is REPLACE (not deep-merge); callers
// must spread the existing `project.config` before mutating to avoid
// dropping unrelated keys.
//
// Out of scope for this body: `name`, `team`, `is_active`, `agent_overrides`,
// `tools_config`, `budget_*_usd`, `paths_*`, `auto_run_consent_at` — those
// have separate UX flows (rename, consent, budget, tool gate). The form
// in EditProjectModal omits them; the type narrows them out here too so
// callers can't accidentally PATCH them through this helper.
export type ProjectUpdateBody = {
  description?: string | null;
  stack_web?: string | null;
  stack_api?: string | null;
  stack_db?: string | null;
  config?: Record<string, unknown>;
  working_path?: string | null;
  working_repo?: string | null;
  sources?: Source[];
};

export async function updateProject(
  projectId: number,
  body: ProjectUpdateBody,
): Promise<ProjectRead> {
  return jsonFetch<ProjectRead>(`/api/projects/${projectId}`, {
    method: "PATCH",
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

// killProject / reviveProject — Kanban #1209 AA1 hard kill switch (D5).
// Shared response shape (`KillReviveResponse`): success, project_id, action,
// is_killed, killed_at, killed_reason, drain_summary (operator-readable counts),
// audit_id (FK into projects_audit for any future audit-log deep-link).
//
// Status contract (mirrors api/src/routers/projects.py + schemas/project.py):
//   kill   200 → success
//          404 → project not found / soft-deleted
//          409 → already killed (idempotent guard)
//          422 → reason < 10 chars / missing
//   revive 200 → success
//          404 → not found / soft-deleted
//          409 → not currently killed (idempotent guard)
//
// The optional `X-Actor` header stamps `projects_audit.actor`; backend defaults
// to "operator" when absent. v1 leaves it null on the wire (single-operator
// dev mode) but the helper exposes it for future multi-operator UIs.
export type KillReviveResponse = {
  success: boolean;
  project_id: number;
  action: "kill" | "revive";
  is_killed: boolean;
  killed_at: string | null;
  killed_reason: string | null;
  drain_summary: Record<string, unknown>;
  audit_id: number;
};

export type KillProjectBody = { reason: string };

export async function killProject(
  projectId: number,
  body: KillProjectBody,
  force = false,
  actor?: string,
): Promise<KillReviveResponse> {
  const qs = force ? "?force=true" : "";
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (actor && actor.trim().length > 0) headers["X-Actor"] = actor.trim();
  return jsonFetch<KillReviveResponse>(`/api/projects/${projectId}/kill${qs}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
}

export async function reviveProject(
  projectId: number,
  actor?: string,
): Promise<KillReviveResponse> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (actor && actor.trim().length > 0) headers["X-Actor"] = actor.trim();
  return jsonFetch<KillReviveResponse>(`/api/projects/${projectId}/revive`, {
    method: "POST",
    headers,
    body: JSON.stringify({}),
  });
}

// pauseProject / unpauseProject — Kanban #1211 AA3 soft-pause (D3).
// Mirror of api/src/routers/projects.py pause / unpause endpoints + the
// PauseUnpauseResponse schema in api/src/schemas/project.py.
//
// Status contract:
//   pause   200 → applied (returns drain_summary + audit_id)
//           404 → project not found / soft-deleted
//           409 → already paused OR currently killed (mutex)
//           422 → reason missing / shorter than 10 chars
//   unpause 200 → applied
//           404 → not found / soft-deleted
//           409 → NOT currently paused (idempotent guard)
//
// Shape is deliberately distinct from KillReviveResponse — pause carries the
// `is_paused` + `paused_*` triad rather than the kill triad. The `X-Actor`
// header stamps `projects_audit.actor`; backend defaults to "operator" when
// absent (v1 leaves it null on the wire for single-operator dev mode).
export type PauseUnpauseResponse = {
  success: boolean;
  project_id: number;
  action: "pause" | "unpause" | "pause_override";
  is_paused: boolean;
  paused_at: string | null;
  paused_reason: string | null;
  drain_summary: Record<string, unknown>;
  audit_id: number;
};

export type PauseProjectBody = { reason: string };
// Unpause carries no body fields today; the type exists so a future
// unpause-time field (e.g. `recompute_recurrence: bool`) can land without
// breaking the wire contract.
export type UnpauseProjectBody = Record<string, never>;

export async function pauseProject(
  projectId: number,
  body: PauseProjectBody,
  actor?: string,
): Promise<PauseUnpauseResponse> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (actor && actor.trim().length > 0) headers["X-Actor"] = actor.trim();
  return jsonFetch<PauseUnpauseResponse>(`/api/projects/${projectId}/pause`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
}

export async function unpauseProject(
  projectId: number,
  actor?: string,
): Promise<PauseUnpauseResponse> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (actor && actor.trim().length > 0) headers["X-Actor"] = actor.trim();
  return jsonFetch<PauseUnpauseResponse>(`/api/projects/${projectId}/unpause`, {
    method: "POST",
    headers,
    body: JSON.stringify({}),
  });
}

// listProjectAuditTasks — convenience wrapper for the Audit History section
// on the project detail page. The BE /api/tasks endpoint has no `task_type`
// query param (single source of truth for that filter today is client-side),
// so we fetch every task for the project (cap=500 matches the Board page's
// initial-load cap) and filter to task_type='audit'. Sorted by completed_at
// DESC so the freshest verdict is first; tasks without a completed_at fall
// to the bottom (typically not-yet-DONE audit rows).
//
// If the volume ever grows past the 500-row cap, swap to a paginated fetch
// or land a BE `task_type` filter param — both are forward-compat.
export async function listProjectAuditTasks(
  projectId: number,
  limit = 500,
): Promise<TaskRead[]> {
  const all = await listTasks(projectId, { limit });
  const audits = all.filter((t) => t.task_type === "audit");
  audits.sort((a, b) => {
    const aDone = a.completed_at ?? "";
    const bDone = b.completed_at ?? "";
    if (aDone === bDone) return b.id - a.id;
    return aDone < bDone ? 1 : -1;
  });
  return audits;
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

// createTask — POST /api/tasks body (Kanban #855 FE). Mirrors
// api/src/schemas/task.py:TaskCreate. Only the fields the manual-create modal
// exposes are typed here; backend defaults (task_type='feature', task_kind='ai',
// run_mode='manual', etc.) cover the rest. project_id is required by the schema
// even though X-Project-Id is also sent — the header is the auth gate, the body
// field is the persisted FK.
export type TaskCreateBody = {
  project_id: number;
  title: string;
  description?: string;
  process_status?: TaskStatusValue;
  priority?: TaskPriorityValue;
  assigned_role?: TaskRoleValue;
  blocked_by?: number;
  // Kanban #1211 AA3 — per-task override hatch for paused projects. When BOTH
  // are set on POST, the BE allows the task to land against an otherwise-paused
  // project AND writes a `projects_audit` row with action='pause_override'.
  // `allow_during_pause_reason` is min_length=10 on the BE; FE forms enforce
  // the same gate before submit. Omitted on non-paused projects.
  allow_during_pause?: boolean;
  allow_during_pause_reason?: string;
};

export async function createTask(
  projectId: number,
  body: TaskCreateBody,
): Promise<TaskRead> {
  return jsonFetch<TaskRead>(`/api/tasks`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Project-Id": String(projectId),
    },
    body: JSON.stringify(body),
  });
}

// parseTaskText — POST /api/tasks/ai-parse (Kanban #857 FE / #856 BE).
// Sends free text (1..2000 chars); returns a proposed TaskCreate subset for the
// FE to pre-fill an editable preview form. The FE then calls createTask with
// the (possibly edited) fields.
//
// Status contract (per api/src/routes/tasks_ai_parse.py):
//   200 → { proposed: ParsedTaskProposal }
//   422 → empty text OR LLM produced an invalid proposal
//   502 → provider 5xx / network
//   503 → provider not configured (LANGGRAPH_LLM_PROVIDER unset / unsupported,
//          or ANTHROPIC_API_KEY empty)
//   504 → provider exceeded 10s wall
export type ParsedTaskProposal = {
  title: string;
  description: string;
  task_type: "bug" | "feature" | "chore" | "docs" | "refactor";
  priority: TaskPriorityValue;
  assigned_role: TaskRoleValue | null;
  blocked_by: number | null;
};

export async function parseTaskText(
  projectId: number,
  text: string,
): Promise<ParsedTaskProposal> {
  const envelope = await jsonFetch<{ proposed: ParsedTaskProposal }>(
    `/api/tasks/ai-parse`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Project-Id": String(projectId),
      },
      body: JSON.stringify({ text }),
    },
  );
  return envelope.proposed;
}

// PATCH /api/tasks/{id} — partial update; blocked_by explicit null clears (#771); run_mode #860; status_change_reason #854
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

// #772 — anchor-based reorder; 422 on cross-lane/deleted/blocker-order violation
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

// #771 — reverse blocked_by lookup; soft-deleted excluded
export async function getTaskBlocks(
  projectId: number,
  id: number,
): Promise<TaskRead[]> {
  return jsonFetch<TaskRead[]>(`/api/tasks/${id}/blocks`, {
    headers: { "X-Project-Id": String(projectId) },
  });
}

// #834 — append answer via patchTask; returns updated TaskRead
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

// #834 — invalidate last valid answer; reason required
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

// #854 — PATCH ps=6 + reason; cancelled rows excluded from default list
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

// #980 / #949d — per-task tool-call audit rows. BE writes one row per tool
// invocation during agent runs (success / failure / permission decision /
// timing / input + truncated output). Section is lazy-loaded in TaskDetail
// and hidden entirely when the array is empty. Tier drives chip color in the
// UI (read=zinc / write=amber / network=blue / destructive=red).
export type ToolCallTier = "read" | "write" | "network" | "destructive";
export type ToolCallPermissionDecision =
  | "auto_allow"
  | "halt"
  | "reject";

export type ToolCallRead = {
  id: number;
  task_id: number;
  invoked_at: string; // ISO 8601
  tool_name: string;
  tier: ToolCallTier;
  input_json: Record<string, unknown>;
  success: boolean;
  error_code: string | null;
  error_msg: string | null;
  output_summary: string | null; // first 256 chars of tool output
  duration_ms: number;
  permission_decision: ToolCallPermissionDecision;
};

// #980 — GET /api/tasks/{id}/tool-calls. Backend returns [] for tasks with
// no recorded calls; callers should hide the section in that case.
export async function getTaskToolCalls(
  projectId: number,
  taskId: number,
): Promise<ToolCallRead[]> {
  return jsonFetch<ToolCallRead[]>(`/api/tasks/${taskId}/tool-calls`, {
    headers: { "X-Project-Id": String(projectId) },
  });
}

// ============================================================================
// Kanban #1212 AA4 — operator board-chairman /review surface.
// ============================================================================

// AuditFlagAction — vocabulary mirror of services/pause_switch.RESOLVE_FLAG_ACTIONS
// + schemas/project.ResolveFlagAction. Kept here as a string union so callers
// type-check at the call site rather than relying on string literals.
export type AuditFlagAction =
  | "continue"
  | "adjust_continue"
  | "keep_paused"
  | "terminate";

// ResolveFlagAdjustments — allowlisted keys mirror of pause_switch.py
// ADJUST_CONTINUE_ALLOWED_KEYS (2026-05-19). Non-allowlisted keys are
// silently dropped by the BE; the FE form only exposes the subset that
// has a concrete UI today (budget triad + health_thresholds). Other keys
// (approval_policies / hitl_timeout_hours / audit_enabled) round-trip via
// the `Record<string, unknown>` escape hatch when future UI lands.
export type ResolveFlagAdjustments = {
  budget_daily_usd?: string | number | null;
  budget_monthly_usd?: string | number | null;
  budget_total_usd?: string | number | null;
  health_thresholds?: Record<string, number | null> | null;
  approval_policies?: Record<string, unknown> | null;
  hitl_timeout_hours?: number | null;
  audit_enabled?: boolean;
};

// ResolveFlagBody — POST body for /api/tasks/{flag_id}/resolve-flag.
// `adjustments` required (and non-empty) ONLY when action='adjust_continue';
// BE returns 422 otherwise. extra='forbid' on the BE schema — don't sneak
// in extra keys at the top level.
export type ResolveFlagBody = {
  action: AuditFlagAction;
  adjustments?: ResolveFlagAdjustments;
};

// ResolveFlagResponse — extra='allow' on BE so branch-specific keys
// (is_paused / is_killed / kill_audit_id / adjustments_applied / stale)
// surface as optional. Always carries the triad below.
export type ResolveFlagResponse = {
  flag_id: number;
  project_id: number;
  action: AuditFlagAction;
  flag_completed_at: string | null;
  is_paused?: boolean | null;
  is_killed?: boolean | null;
  audit_id?: number | null;
  kill_audit_id?: number | null;
  adjustments_applied?: Record<string, unknown> | null;
  drain_summary?: Record<string, unknown> | null;
  stale?: boolean | null;
};

export async function resolveFlag(
  flagId: number,
  projectId: number,
  body: ResolveFlagBody,
  actor?: string,
): Promise<ResolveFlagResponse> {
  // X-Project-Id required (the resolve-flag endpoint is /api/tasks/* and
  // gates on the session-bound project header per Kanban #695).
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Project-Id": String(projectId),
  };
  if (actor && actor.trim().length > 0) headers["X-Actor"] = actor.trim();
  return jsonFetch<ResolveFlagResponse>(
    `/api/tasks/${flagId}/resolve-flag`,
    {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    },
  );
}

// AuditFlagWithProject — bundle a flag task with its parent project for
// the /review surface. The page groups by project_id so consumers want
// the project metadata adjacent to each flag.
export type AuditFlagWithProject = {
  flag: TaskRead;
  project: ProjectRead;
};

// listAuditFlags — cross-project aggregation for the AA4 /review page.
//
// Implementation: the existing /api/tasks endpoint is single-project-scoped
// (gates on X-Project-Id per Kanban #695). To aggregate across N projects
// we (1) list active projects, (2) per project fetch open question tasks
// in parallel, (3) client-side filter on `question_payload.is_audit_flag`.
//
// Why client-side filter: there is no BE filter for JSONB-path predicates
// on the tasks endpoint today. The volume is small (≤10s of question tasks
// per project) so a single round-trip per project + a in-memory predicate
// is acceptable for v1. If this grows, add a BE filter param + revisit.
//
// `pending=true` returns process_status != 5 (TODO/IN_PROGRESS/REVIEW/BLOCKED);
// `include_cancelled` defaults to false so CANCELLED (ps=6) is also out.
// AA3 flag tasks are created with process_status=BLOCKED (4); operator-resolved
// flags transition to DONE (5) which the pending filter naturally drops.
export async function listAuditFlags(): Promise<AuditFlagWithProject[]> {
  const projects = await listProjects({ status: 1 });
  // Per-project parallel fetch. Errors on individual projects degrade to
  // an empty list for that project rather than failing the whole page —
  // a single project's API outage shouldn't blank the /review surface for
  // the other N-1 projects.
  const perProject = await Promise.all(
    projects.map(async (project) => {
      try {
        const tasks = await listTasks(project.id, {
          pending: true,
          limit: 500,
        });
        const flags = tasks.filter(
          (t) =>
            t.interaction_kind === "question" &&
            t.question_payload?.is_audit_flag === true,
        );
        return flags.map((flag) => ({ flag, project }));
      } catch {
        return [];
      }
    }),
  );
  return perProject.flat();
}
