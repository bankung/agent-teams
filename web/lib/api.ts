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
// Kanban #803 ('bug'/'feature'/'chore'/'docs'/'refactor') + #1211 GOV3 ('audit').
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
  // Kanban #1209 (2026-05-19) GOV1 — hard kill switch state. `is_killed` is
  // ALWAYS present on ProjectRead (NOT NULL DEFAULT false on the column);
  // `killed_at` / `killed_reason` are preserved through revive (D4 history),
  // so the FE can show "last killed YYYY-MM-DD" even on revived projects.
  // Optional in the FE type for legacy-row defensive resilience — pre-GOV1
  // serialized payloads may omit them, in which case treat as not-killed.
  is_killed?: boolean;
  killed_at?: string | null;
  killed_reason?: string | null;
  // Kanban #1211 GOV3 — soft-pause state (separate from GOV1 hard kill).
  // is_paused stays true between the audit task DONE and the operator's
  // resolve-flag action. paused_at / paused_reason preserved across unpause
  // for audit-trail continuity (D4 history pattern from GOV1).
  is_paused?: boolean;
  paused_at?: string | null;
  paused_reason?: string | null;
  // Kanban #1212 GOV4 — adjustments allowlist (services/pause_switch.py
  // ADJUST_CONTINUE_ALLOWED_KEYS). FE pre-fills the Adjust+Continue form
  // from these. NULL on health_thresholds = use auditor defaults
  // (budget_burn_threshold_pct=100, failure_rate_threshold_pct=20, etc).
  health_thresholds?: Record<string, unknown> | null;
  approval_policies?: Record<string, unknown> | null;
  hitl_timeout_hours?: number | null;
  audit_enabled?: boolean;
  // Kanban #1011 (2026-05-20) — per-project HITL aging nudge threshold.
  // NULL or 0 = nudges disabled; positive int = fire after N hours.
  // Backfilled to 24 by migration 0047. Optional on the FE type for
  // defensive resilience against pre-migration serialized payloads.
  hitl_nudge_threshold_hours?: number | null;
  // Kanban #2300 (2026-06-11) — per-project thinking effort for headless engine.
  // NULL = use global default (= off). Values: off|low|medium|high|extra|auto.
  effort_mode?: string | null;
  // Kanban #1304 (2026-06-15) — per-project pre-task cost-forecast gate ceiling
  // (USD). NULL = NO gate (the NewTaskModal never shows the confirm modal). A
  // number = the USD ceiling above which the post-create forecast triggers the
  // confirm modal. Serialized as a JSON number (BE Decimal, 2dp). Optional on
  // the FE type for defensive resilience against pre-#1304 serialized payloads.
  cost_forecast_threshold_usd?: number | null;
};

// AcceptanceCriterion — one entry in TaskRead.acceptance_criteria (#797).
// JSONB shape; verified_at is ISO 8601 string (serialized via mode='json' on
// the backend per shared/decisions.md 2026-05-12 fix to #801).
// Kanban #2127 — optional operator-gate fields on AC items. `gate='operator'`
// marks the item as requiring operator action; `gate_kind` narrows the kind
// (matches BE GateKind enum: key|commit|decision|hitl|external).
export type AcceptanceCriterion = {
  text: string;
  status: "pending" | "passed" | "failed" | "na";
  verified_by: string | null;
  verified_at: string | null;
  notes: string | null;
  gate?: "operator" | null;
  gate_kind?: "key" | "commit" | "decision" | "hitl" | "external" | null;
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
// Kanban #1211 GOV3 (2026-05-19) added the GOV3-flag bookkeeping fields below
// (`is_audit_flag`, `breach_streak_days`, `audit_history`, `latest_audit`,
// `latest_audit_summary`, `reasons`, `metrics`, plus the resolution sentinel
// triplet written by GOV3 resolve_flag). All are optional — generic question
// tasks (approval prompts, design Option A/B questions) carry only the base
// triad (question/options/answer_history); only GOV3-spawned flag rows set
// `is_audit_flag=true` and the GOV3 fields.
//
// `options` is heterogeneous on the wire (BE schema is `list[str | OptionItem]
// | None` per api/src/schemas/task.py): question tasks carry plain `string[]`
// (free-form approval / Option A/B prompts); decision tasks carry typed
// `OptionItem[]` dicts (Kanban #1007, structured `/decide` validation). The
// FE narrows per-option at the render site (string vs object).
// Kanban #1007 / #1335 (2026-05-20) — added `chosen_id` / `rationale` /
// `chosen_at` / `chosen_by` written by POST /api/tasks/{id}/decide. Null
// until decided; set together atomically on decide.
export type QuestionPayload = {
  question: string;
  options: Array<string | OptionItem> | null;
  answer_history: AnswerHistoryEntry[];
  // Kanban #1007 / #1335 — decision-result fields. Null until /decide fires.
  chosen_id?: string | null;
  rationale?: string | null;
  chosen_at?: string | null;
  chosen_by?: string | null;
  // ---- GOV3 audit-flag fields (services/audit_flag.py:_new_flag_payload) ----
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
  // present). Auditor schema is still evolving (GOV2 ownership) — value-
  // tolerant on shape.
  reasons?: string[];
  metrics?: Record<string, unknown>;
  raw_evidence?: unknown;
  // Resolution sentinel written by services/pause_switch.resolve_flag on
  // keep_paused / terminate branches (also set on continue / adjust_continue
  // for symmetry once the flag is DONE). Lets the GOV4 UI show
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
  // #803 (2026-05-12) + #1211 GOV3 (2026-05-19 — added "audit"). Backfilled to
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
  // #1211 GOV3 — per-task override hatch (paired). The pair is set on POST
  // when the operator chose to file the task against a paused project; the
  // FE reads them to render a "bypassed pause" indicator + the rationale.
  allow_during_pause?: boolean;
  allow_during_pause_reason?: string | null;
  // Kanban #1004 (2026-05-20) — auto-handoff template pointer. NULL = no
  // auto-handoff configured; non-null = on the next DONE-flip the BE spawns
  // a child task via services/handoff_spawn.py. The CHILD's value is always
  // NULL (loop guard enforced server-side). Optional on FE for defensive
  // resilience against pre-migration serialized payloads.
  handoff_template_id?: number | null;
  // Kanban #1868 (2026-06-03) — optional milestone grouping for release
  // planning. NULL = unassigned. Set to NULL automatically when the parent
  // milestone is soft-deleted (routers/milestones.py DELETE, same transaction).
  // Optional on the FE type for defensive resilience against pre-migration
  // serialized payloads.
  milestone_id?: number | null;
  // Kanban #1868 (2026-06-03) — optional display/planning date for the Calendar
  // view (future). ISO-8601 date string ("YYYY-MM-DD") when set; NULL = unset.
  // Decoupled from scheduled_at / autorun / Gantt.
  due_date?: string | null;
  // Kanban #1011 (2026-05-20) — HITL aging nudge dedup + per-task toggle.
  //   `last_nudge_at`: timestamp of last fired nudge; null = never nudged.
  //   `nudge_disabled`: per-task off switch; default false (nudges enabled
  //                     per the project threshold). Operator flips true to
  //                     silence a noisy task.
  last_nudge_at?: string | null;
  nudge_disabled?: boolean;
  // Kanban #1677 — per-task model-tier override. null = inherit from project /
  // role defaults. Precedence: task > project.agent_overrides > role default.
  model_override: "haiku" | "sonnet" | "opus" | null;
  // Kanban #1001 (2026-05-20) — halt reason set by Lead at halt time per #787
  // decision matrix. NULL = task runs normally; non-null = halted with reason.
  // Present on every TaskRead response; FE mirrors the BE nullable TEXT column.
  halt_reason: string | null;
  // Kanban #2127 — operator-gate fields. `operator_gate` is non-null when the
  // task has a task-level gate (value = gate_kind string). `operator_gate_note`
  // is the optional free-text note the Lead attached. Both are nullable TEXT
  // columns on the BE; optional here for defensive resilience against pre-#2127
  // serialized payloads that omit the fields.
  operator_gate?: string | null;
  operator_gate_note?: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
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

// buildPath — append URLSearchParams as a query string when non-empty.
function buildPath(base: string, qs: URLSearchParams): string {
  return qs.toString() ? `${base}?${qs}` : base;
}

// applyActor — stamp X-Actor header when actor is a non-empty string.
function applyActor(
  headers: Record<string, string>,
  actor: string | undefined,
): void {
  if (actor && actor.trim().length > 0) headers["X-Actor"] = actor.trim();
}

export async function getProjectByName(name: string): Promise<ProjectRead> {
  return jsonFetch<ProjectRead>(
    `/api/projects/by-name/${encodeURIComponent(name)}`,
  );
}

// getProjectById — GET /api/projects/{id}. Used by the focus page (#1001) to
// resolve a task's project_id → name for the "Open full" deep-link target.
// 404 / non-2xx surface as HttpError (caller discriminates).
export async function getProjectById(id: number): Promise<ProjectRead> {
  return jsonFetch<ProjectRead>(`/api/projects/${id}`);
}

// #407 — status=1 filter; no X-Project-Id header (project endpoint)
type ListProjectsOpts = { status?: 0 | 1 };

export async function listProjects(
  opts: ListProjectsOpts = {},
): Promise<ProjectRead[]> {
  const qs = new URLSearchParams();
  if (opts.status !== undefined) qs.set("status", String(opts.status));
  const path = buildPath("/api/projects", qs);
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

// G1 — heuristic estimate from task-level estimated_cost_usd roll-up.
// total_cost_usd is a Decimal STRING (same Pydantic serialization as
// cost_usage.total_cost_usd — #871). Parse via parseUsd() before arithmetic.
// Optional: absent on older API versions — FE degrades gracefully.
export type ProjectStatsEstimatedCost = {
  total_cost_usd: string;
  total_input_tokens: number;
  total_output_tokens: number;
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
  // G1 — heuristic task-estimate roll-up; optional until BE slice lands.
  estimated_cost?: ProjectStatsEstimatedCost;
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
  const path = buildPath("/api/audit/daily-rollup", qs);
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
  // Kanban #1011 (2026-05-20) — per-project HITL aging nudge threshold.
  // Semantics: key-absent → unchanged; explicit `null` → CLEAR to NULL
  // (= nudges disabled); explicit int → set threshold (0 is accepted but
  // app-layer treats it identical to NULL = disabled).
  hitl_nudge_threshold_hours?: number | null;
  // Kanban #2300 (2026-06-11) — per-project thinking effort for headless engine.
  // NULL = global default (= off). Values: off|low|medium|high|extra|auto.
  effort_mode?: "off" | "low" | "medium" | "high" | "extra" | "auto" | null;
  // Kanban #1014 — form-authored approval policies over the #957 evaluator.
  // Explicit null clears; object replaces the full JSONB document.
  approval_policies?: Record<string, unknown> | null;
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

// killProject / reviveProject — Kanban #1209 GOV1 hard kill switch (D5).
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
  applyActor(headers, actor);
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
  applyActor(headers, actor);
  return jsonFetch<KillReviveResponse>(`/api/projects/${projectId}/revive`, {
    method: "POST",
    headers,
    body: JSON.stringify({}),
  });
}

// pauseProject / unpauseProject — Kanban #1211 GOV3 soft-pause (D3).
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

export async function pauseProject(
  projectId: number,
  body: PauseProjectBody,
  actor?: string,
): Promise<PauseUnpauseResponse> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  applyActor(headers, actor);
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
  applyActor(headers, actor);
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
  process_status?: TaskStatusValue;
  parent_task_id?: number;
  top_level_only?: boolean;
  limit?: number;
  // Kanban #1868 — filter to tasks assigned to a given milestone id. Used by
  // the milestones page to surface a milestone's task list.
  milestone_id?: number;
  // Kanban #1873 (M2 Calendar) — inclusive due_date range filter. Both are ISO
  // "YYYY-MM-DD" date strings; the BE returns tasks whose due_date falls within
  // [due_from, due_to]. Used by the Calendar view to fetch a visible month's
  // tasks. Either may be sent independently (open-ended range).
  due_from?: string;
  due_to?: string;
};

export async function listTasks(
  projectId: number,
  opts: ListTasksOpts = {},
): Promise<TaskRead[]> {
  const qs = new URLSearchParams();
  if (opts.pending) qs.set("pending", "true");
  if (opts.process_status !== undefined)
    qs.set("process_status", String(opts.process_status));
  if (opts.top_level_only) qs.set("top_level_only", "true");
  else if (opts.parent_task_id !== undefined)
    qs.set("parent_task_id", String(opts.parent_task_id));
  if (opts.milestone_id !== undefined)
    qs.set("milestone_id", String(opts.milestone_id));
  if (opts.due_from !== undefined) qs.set("due_from", opts.due_from);
  if (opts.due_to !== undefined) qs.set("due_to", opts.due_to);
  if (opts.limit !== undefined) qs.set("limit", String(opts.limit));
  const path = buildPath("/api/tasks", qs);
  return jsonFetch<TaskRead[]>(path, {
    headers: { "X-Project-Id": String(projectId) },
  });
}

// #2033 — the BE hard-caps list_tasks at 500 per page. Projects with >500
// active tasks (e.g. agent-teams itself) would silently drop rows, making
// milestone filters appear incomplete. listAllTasks paginates at PAGE=500
// until a page shorter than PAGE is returned (= last page), then merges.
// Only the opts fields that are safe to combine with offset are forwarded
// (pending / process_status / top_level_only / parent_task_id / milestone_id /
// due_from / due_to). `opts.limit` is intentionally ignored — the caller wants ALL rows.
const _PAGE = 500;
export async function listAllTasks(
  projectId: number,
  opts: Omit<ListTasksOpts, "limit"> = {},
): Promise<TaskRead[]> {
  const all: TaskRead[] = [];
  let offset = 0;
  while (true) {
    const qs = new URLSearchParams();
    if (opts.pending) qs.set("pending", "true");
    if (opts.process_status !== undefined)
      qs.set("process_status", String(opts.process_status));
    if (opts.top_level_only) qs.set("top_level_only", "true");
    else if (opts.parent_task_id !== undefined)
      qs.set("parent_task_id", String(opts.parent_task_id));
    if (opts.milestone_id !== undefined)
      qs.set("milestone_id", String(opts.milestone_id));
    if (opts.due_from !== undefined) qs.set("due_from", opts.due_from);
    if (opts.due_to !== undefined) qs.set("due_to", opts.due_to);
    qs.set("limit", String(_PAGE));
    qs.set("offset", String(offset));
    const page = await jsonFetch<TaskRead[]>(buildPath("/api/tasks", qs), {
      headers: { "X-Project-Id": String(projectId) },
    });
    all.push(...page);
    if (page.length < _PAGE) break;
    offset += _PAGE;
  }
  return all;
}

export async function getTask(
  projectId: number,
  id: number,
): Promise<TaskRead> {
  return jsonFetch<TaskRead>(`/api/tasks/${id}`, {
    headers: { "X-Project-Id": String(projectId) },
  });
}

// Kanban #2112 — DONE-lane keyset pagination.
// Fetches one page of DONE tasks ordered by (updated_at DESC, id DESC)
// matching the sortDoneLane client sort. Cursor = last row of prior page.
// has-more: returned.length === limit.
export type DoneLanePageOpts = {
  limit: number;
  before_updated_at?: string;
  before_id?: number;
};
export async function listDoneLanePage(
  projectId: number,
  opts: DoneLanePageOpts,
): Promise<TaskRead[]> {
  const qs = new URLSearchParams();
  qs.set("process_status", "5");
  qs.set("order", "done_lane");
  qs.set("limit", String(opts.limit));
  if (opts.before_updated_at !== undefined)
    qs.set("before_updated_at", opts.before_updated_at);
  if (opts.before_id !== undefined)
    qs.set("before_id", String(opts.before_id));
  return jsonFetch<TaskRead[]>(buildPath("/api/tasks", qs), {
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
  // Kanban #1211 GOV3 — per-task override hatch for paused projects. When BOTH
  // are set on POST, the BE allows the task to land against an otherwise-paused
  // project AND writes a `projects_audit` row with action='pause_override'.
  // `allow_during_pause_reason` is min_length=10 on the BE; FE forms enforce
  // the same gate before submit. Omitted on non-paused projects.
  allow_during_pause?: boolean;
  allow_during_pause_reason?: string;
  // Kanban #1006 (2026-05-20) — pre-fill task fields from a named action
  // template. Server reads `action_template_id` (the template's `name` /
  // `id`), looks it up in the in-memory cache, and pre-fills task_kind,
  // task_type, priority, and acceptance_criteria. Caller-explicit values in
  // the same body win over the template. Field is request-only metadata —
  // does NOT round-trip on TaskRead. 400 on unknown template id.
  action_template_id?: string;
  // Kanban #1004 (2026-05-20) — store a handoff-template pointer on the
  // task. The BE persists this on the row; the DONE-flip hook reads it and
  // spawns the child task. The CHILD's value is always NULL (loop guard).
  // 400 on unknown template id; 400 on project-scope mismatch.
  handoff_template_id?: number;
  // Kanban #1677 — per-task model-tier override. Omit or null = inherit from
  // project.agent_overrides / role default. 422 on unknown tier string.
  model_override?: "haiku" | "sonnet" | "opus" | null;
  // Kanban #1868 — optional milestone grouping. Omit or null = unassigned.
  // The referenced milestone MUST belong to the same project (422 otherwise).
  milestone_id?: number | null;
  // Kanban #1868 — optional display/planning date (ISO "YYYY-MM-DD"). Omit or
  // null = unset. No coupling to scheduled_at / autorun.
  due_date?: string | null;
  // Wave B (#4) — optional task_type override. BE default is 'feature'.
  // Omit to keep the default; 'bug' triggers red border on the board.
  task_type?: "bug" | "feature" | "chore" | "docs" | "refactor";
  // Kanban #1310 — template-derived acceptance criteria. The Task Template
  // picker substitutes {{placeholders}} client-side, then sends the resulting
  // AC rows here (the BE accepts up to 50; each item needs non-empty `text`).
  // No template id is sent — the created task is a plain task.
  acceptance_criteria?: AcceptanceCriterion[];
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

// ============================================================================
// Kanban #1304 (2026-06-15) — POST /api/tasks/{id}/cost-forecast.
//
// PRE-run cost forecast for an already-created task (Option-A flow: create the
// task first, THEN forecast). NO request body — the BE reads the task's text
// fields + attached resources. X-Project-Id scoped (404 on missing / wrong-
// project task). Mirror of api/src/schemas/task.py:CostForecastRead.
//
// `breakdown.{prompt,role_brief,attached_resources}` sum to estimated_tokens
// (the INPUT total); `completion` is the priced output proxy. `estimated_usd`
// is a JSON number (BE Decimal, 4dp). `confidence` reflects resource-tag
// completeness + model-known state.
// ============================================================================

export type CostForecastBreakdown = {
  prompt: number;
  role_brief: number;
  attached_resources: number;
  completion: number;
};

export type CostForecastResult = {
  estimated_usd: number;
  estimated_tokens: number;
  breakdown: CostForecastBreakdown;
  confidence: "low" | "med" | "high";
};

export async function costForecast(
  projectId: number,
  taskId: number,
): Promise<CostForecastResult> {
  return jsonFetch<CostForecastResult>(`/api/tasks/${taskId}/cost-forecast`, {
    method: "POST",
    headers: { "X-Project-Id": String(projectId) },
  });
}

// deleteTask — DELETE /api/tasks/{id} (soft-delete; flips status=0). Returns
// 204 (no body) on success; idempotent on already-deleted rows. X-Project-Id
// scoped (404 on cross-project / missing; 409 when active subtasks reference
// the task). Used by the #1304 cost-gate "Cancel" path to remove a task that
// was created in the Option-A flow but the operator declined to run.
export async function deleteTask(
  projectId: number,
  taskId: number,
): Promise<void> {
  // DELETE returns 204 (no body) — jsonFetch would explode parsing JSON on an
  // empty body; call fetch directly (mirrors deleteMilestone / push.unsubscribe).
  const url = `${apiBaseUrl()}/api/tasks/${taskId}`;
  const response = await fetch(url, {
    method: "DELETE",
    cache: "no-store",
    headers: { Accept: "application/json", "X-Project-Id": String(projectId) },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as {
      detail?: unknown;
    };
    const message =
      formatDetail(body.detail) ?? `${response.status} ${response.statusText}`;
    throw new HttpError(response.status, body.detail, message);
  }
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
// halt_reason added 2026-05-20 by Kanban #1001 — Halt quick-action sets ps=4 + halt_reason in one PATCH.
//   PATCH semantics (per #785): key-absent = unchanged; explicit `null` = clear/unhalt; non-empty string = halt.
// Kanban #2181 — description + acceptance_criteria inline editing from task drawer.
export type TaskPatch = Partial<
  Pick<TaskRead, "process_status" | "priority" | "title" | "blocked_by" | "sort_order" | "run_mode" | "description" | "acceptance_criteria">
> & {
  new_answer?: string | null;
  new_answer_by?: string | null;
  invalidate_last_answer?: boolean | null;
  invalidated_reason?: string | null;
  status_change_reason?: string | null;
  halt_reason?: string | null;
  // Kanban #1011 (2026-05-20) — per-task nudge on/off toggle. Key-absent
  // leaves unchanged; explicit true silences; explicit false re-enables.
  // The BE column is NOT NULL DEFAULT false; explicit `null` would 400.
  nudge_disabled?: boolean;
  // Kanban #1004 (2026-05-20) — re-point or clear the handoff template
  // pointer. Key-absent leaves unchanged; explicit `null` clears; positive
  // int re-points (BE validates existence + project scope).
  handoff_template_id?: number | null;
  // Kanban #1677 — per-task model-tier override. Key-absent = unchanged;
  // explicit `null` = clear-to-inherit; value = set tier.
  model_override?: "haiku" | "sonnet" | "opus" | null;
  // Kanban #1868 — milestone grouping. Key-absent = unchanged; explicit `null`
  // = unassign; positive int = assign (BE validates existence + project scope).
  milestone_id?: number | null;
  // Kanban #1868 — display/planning date (ISO "YYYY-MM-DD"). Key-absent =
  // unchanged; explicit `null` = clear; value = set.
  due_date?: string | null;
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

// OptionItem — mirror of api/src/schemas/task.py:OptionItem (Kanban #1007).
// Structured decision option carried inside `question_payload.options` when
// `interaction_kind='decision'`. `id` is the machine-stable identifier the
// /decide endpoint validates against; `label` is the human-readable text.
export type OptionItem = {
  id: string;
  label: string;
  description?: string | null;
  hints?: string[] | null;
};

// decideTask — POST /api/tasks/{id}/decide (Kanban #1007 BE). Used by the
// focus page (#1001) for decision tasks: records the chosen option, merges
// `chosen_id`/`rationale`/`chosen_at`/`chosen_by` into question_payload, and
// flips the task to ps=5 (DONE) atomically.
//
// Errors (per shared/api-contracts.md):
//   404 — task not found
//   409 — task already DONE
//   422 — not a decision task / chosen_id not in option list
export type DecideTaskBody = {
  chosen_id: string;
  rationale?: string | null;
  chosen_by?: string;
};

export async function decideTask(
  projectId: number,
  taskId: number,
  body: DecideTaskBody,
): Promise<TaskRead> {
  return jsonFetch<TaskRead>(`/api/tasks/${taskId}/decide`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Project-Id": String(projectId),
    },
    body: JSON.stringify(body),
  });
}

// ============================================================================
// Kanban #1451 — HITL question/decision RESUME path.
//
// Sibling to decideTask() above. Same URL (POST /api/tasks/{id}/decide), but a
// DIFFERENT body shape — the BE dispatches on body shape:
//   - legacy {chosen_id, rationale, chosen_by?}     → decideTask path (DONE-flip)
//   - new   {action, selected_option?, custom_text?} → resolveHitlTask path
//                                                      (is_pending=false +
//                                                       resume_context written;
//                                                       process_status UNCHANGED
//                                                       so Lead resumes)
//
// Decision-task DONE-flips keep using decideTask() (legacy contract — used by
// DecisionInteractionView). HITL question/decision tasks paired with the push-
// click-resolve UX use resolveHitlTask().
//
// `action`:
//   - 'approve' — operator accepted; selected_option SHOULD be set
//   - 'reject'  — operator rejected; selected_option SHOULD be set (fallback id)
//   - 'custom'  — operator typed free text; custom_text SHOULD be set
// ============================================================================

export type HitlResolveAction = "approve" | "reject" | "custom";

export type HitlResolveBody = {
  action: HitlResolveAction;
  selected_option?: string;
  custom_text?: string;
};

export type HitlResolveResponse = {
  task_id: number;
  process_status: TaskStatusValue;
  resume_context: Record<string, unknown>;
  decided_at: string;
};

export async function resolveHitlTask(
  projectId: number,
  taskId: number,
  body: HitlResolveBody,
): Promise<HitlResolveResponse> {
  return jsonFetch<HitlResolveResponse>(`/api/tasks/${taskId}/decide`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Project-Id": String(projectId),
    },
    body: JSON.stringify(body),
  });
}

// #980 / #949d — per-task tool-call audit rows. BE writes one row per tool
// invocation during agent runs (success / failure / permission decision /
// timing / input + truncated output). Section is lazy-loaded in TaskDetail
// and hidden entirely when the array is empty. Tier drives chip color in the
// UI (read=zinc / write=amber / network=blue / destructive=red).
//
// Kanban #2320 — the same rail now carries Lead-reported activity events.
// `source`: "engine" = original tool-call rows; "lead" = Lead-reported event.
// `kind`: one of spawn/tool_result/ac_verified/commit/status_change/blocked/
//         tool_gap/skill_gap/note on lead rows; null on engine rows.
// `summary`: human-readable text set by Lead; null on engine rows.
// Engine-only fields (tier/input_json/duration_ms/permission_decision) are
// nullable on the wire — always null on lead rows.
export type ToolCallTier = "read" | "write" | "network" | "destructive";
export type ToolCallPermissionDecision =
  | "auto_allow"
  | "halt"
  | "reject";
export type ToolCallSource = "engine" | "lead";
export type LeadEventKind =
  | "spawn"
  | "tool_result"
  | "ac_verified"
  | "commit"
  | "status_change"
  | "blocked"
  | "tool_gap"
  | "skill_gap"
  | "note";

export type ToolCallRead = {
  id: number;
  task_id: number;
  invoked_at: string; // ISO 8601
  tool_name: string;
  // #2320 — source + lead-event fields. Defaults to "engine" on legacy rows.
  source: ToolCallSource;
  kind: LeadEventKind | null; // non-null on lead rows
  summary: string | null;     // non-null on lead rows
  // Engine-only fields — nullable on lead rows.
  tier: ToolCallTier | null;
  input_json: Record<string, unknown> | null;
  success: boolean;
  error_code: string | null;
  error_msg: string | null;
  output_summary: string | null; // first 256 chars of tool output
  duration_ms: number | null;
  permission_decision: ToolCallPermissionDecision | null;
};

// #980 — GET /api/tasks/{id}/tool-calls. Backend returns [] for tasks with
// no recorded calls; callers should hide the section in that case.
// Kanban #2334 — optional `limit` arg (1..50) appends ?limit=N.  Client slices
// to `limit` regardless as a guard against endpoints that return all rows.
export async function getTaskToolCalls(
  projectId: number,
  taskId: number,
  limit?: number,
): Promise<ToolCallRead[]> {
  const qs = limit !== undefined ? `?limit=${limit}` : "";
  const rows = await jsonFetch<ToolCallRead[]>(
    `/api/tasks/${taskId}/tool-calls${qs}`,
    { headers: { "X-Project-Id": String(projectId) } },
  );
  // Client-side guard: slice to `limit` even when the endpoint ignores it.
  return limit !== undefined ? rows.slice(0, limit) : rows;
}

// ============================================================================
// Kanban #1212 GOV4 — operator board-chairman /review surface.
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
  description_annotation?: string;
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
  applyActor(headers, actor);
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

// ============================================================================
// Kanban #955 — Web Push subscription CRUD (slice 955.C consumer of slice
// 955.A's /api/push/* endpoints).
// ============================================================================

// PushKindsEnabled — per-subscription toggle dict. Mirror of
// api/src/schemas/push_subscription.py:KindsEnabled. extra='forbid' on the
// BE — typo'd keys 422; keep the FE shape in lockstep with the 4 locked keys.
export type PushKindsEnabled = {
  hitl_needed: boolean;
  task_done: boolean;
  task_failed: boolean;
  budget_warn: boolean;
};

// PushSubscribeBody — POST /api/push/subscribe body. Matches
// api/src/schemas/push_subscription.py:PushSubscribeRequest.
export type PushSubscribeBody = {
  endpoint: string;
  keys: { p256dh: string; auth: string };
  project_id?: number | null;
  user_agent?: string | null;
  kinds_enabled?: PushKindsEnabled | null;
};

// PushSubscriptionRead — server row shape returned by POST + GET endpoints.
// status: 1=active, 0=soft-deleted (RecordStatus enum in api/src/constants.py).
export type PushSubscriptionRead = {
  id: number;
  project_id: number | null;
  endpoint: string;
  p256dh: string;
  auth: string;
  kinds_enabled: PushKindsEnabled;
  user_agent: string | null;
  status: number;
  created_at: string;
  updated_at: string;
};

// Slice 955.B PATCH endpoint — kinds_enabled-only update. Slice B is in
// flight in parallel; this helper assumes the PATCH body shape will be
// `{ kinds_enabled: PushKindsEnabled }`. If slice B lands with a different
// shape, this helper is the single point of change on the FE.
export type PushSubscriptionPatchBody = {
  kinds_enabled?: PushKindsEnabled;
};

// All push helpers under a single namespace so callers read `pushApi.subscribe(...)`
// rather than polluting the top-level export surface with 4 more names. The
// individual functions are not exported separately — callers go through
// `push` (re-exported as `pushApi` in web/lib/push.ts where needed).
export const push = {
  async subscribe(body: PushSubscribeBody): Promise<PushSubscriptionRead> {
    return jsonFetch<PushSubscriptionRead>(`/api/push/subscribe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  },

  async unsubscribe(subscriptionId: number): Promise<void> {
    // DELETE returns 204 (no body) on success — jsonFetch parses JSON which
    // would explode on an empty 204 body; call fetch directly here.
    const url = `${apiBaseUrl()}/api/push/subscribe/${subscriptionId}`;
    const response = await fetch(url, {
      method: "DELETE",
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!response.ok && response.status !== 204) {
      const body = (await response.json().catch(() => ({}))) as {
        detail?: unknown;
      };
      const message =
        formatDetail(body.detail) ?? `${response.status} ${response.statusText}`;
      throw new HttpError(response.status, body.detail, message);
    }
  },

  // PATCH the kinds_enabled JSONB column for an existing subscription.
  // Endpoint shipped by slice 955.B (in-flight in parallel) at
  // PATCH /api/push/subscribe/{id}. Until slice B lands the call will 404 /
  // 405; the FE wraps the response error in HttpError as usual.
  async patchKinds(
    subscriptionId: number,
    body: PushSubscriptionPatchBody,
  ): Promise<PushSubscriptionRead> {
    return jsonFetch<PushSubscriptionRead>(
      `/api/push/subscribe/${subscriptionId}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
  },

  // List subscriptions. Default filter is active-only (slice A); pass
  // `include_deleted: true` for the debug surface. `project_id` filter
  // returns rows matching the id OR rows with project_id IS NULL (the
  // all-projects subscriptions).
  async list(
    opts: { include_deleted?: boolean; project_id?: number } = {},
  ): Promise<PushSubscriptionRead[]> {
    const qs = new URLSearchParams();
    if (opts.include_deleted) qs.set("include_deleted", "true");
    if (opts.project_id != null) qs.set("project_id", String(opts.project_id));
    const path = buildPath("/api/push/subscriptions", qs);
    return jsonFetch<PushSubscriptionRead[]>(path);
  },
};

// listAuditFlags — cross-project aggregation for the GOV4 /review page.
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
// GOV3 flag tasks are created with process_status=BLOCKED (4); operator-resolved
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

// ============================================================================
// Kanban #1329 (M6 FE) — P&L surfaces.
//
// Two endpoints; same Decimal-as-string convention as cost_usage / budget caps.
//   1. GET /api/projects/{id}/pl — per-project; X-Project-Id REQUIRED.
//   2. GET /api/pnl              — cross-project rollup; NO X-Project-Id.
//
// Period buckets mirror the BE's `PLPeriodLiteral` (api/src/schemas/pl.py);
// keep PL_PERIODS in lockstep with that enum.
// ============================================================================

export const PL_PERIODS = [
  "daily",
  "weekly",
  "monthly",
  "quarterly",
  "yearly",
] as const;
export type PLPeriodLiteral = (typeof PL_PERIODS)[number];

// PLBucket — one bucket within a per-project PLSummary. `label` is the
// human-readable bucket key (e.g. "2026-05", "2026-W21", "2026-Q2"); FE
// renders it as-is. All Decimal amounts as strings.
export type PLBucket = {
  label: string;
  currency: string;
  revenue: string;
  cost: string;
  expense: string;
  refund: string;
  transfer: string;
  net: string;
  transaction_count: number;
};

// PLSummary — response of /api/projects/{id}/pl.
// `currency` = first-currency-observed in the window (uppercase). FE detects
// mixed-currency by inspecting buckets[*].currency cardinality.
export type PLSummary = {
  period: PLPeriodLiteral;
  currency: string;
  revenue: string;
  cost: string;
  expense: string;
  refund: string;
  transfer: string;
  net: string;
  transaction_count: number;
  buckets: PLBucket[];
};

// PLCrossProjectRow — one row in the cross-project rollup. `mixed_currency`
// = the project had transactions in 2+ currencies inside the window; the
// totals on this row are first-currency-observed-only (the BE could not
// safely add across currencies).
export type PLCrossProjectRow = {
  project_id: number;
  project_name: string;
  team: string;
  currency_default: string;
  period: PLPeriodLiteral;
  revenue: string;
  cost: string;
  expense: string;
  refund: string;
  transfer: string;
  net: string;
  transaction_count: number;
  mixed_currency: boolean;
  bucket_count: number;
};

// PLCrossProject — response of /api/pnl.
// `grand_total_net_first_currency_only` is null when projects span multiple
// currencies (BE refuses to add across them); FE shows the per-row table
// instead of a single chip in that case.
export type PLCrossProject = {
  period: PLPeriodLiteral;
  since: string;
  until: string;
  rows: PLCrossProjectRow[];
  total_projects: number;
  grand_total_net_first_currency_only: string | null;
};

// getProjectPl — per-project P&L summary. X-Project-Id header is required
// (gates per Kanban #695); helper passes it transparently.
export async function getProjectPl(
  projectId: number,
  opts: { period?: PLPeriodLiteral; since?: string; until?: string } = {},
): Promise<PLSummary> {
  const qs = new URLSearchParams();
  if (opts.period) qs.set("period", opts.period);
  if (opts.since) qs.set("since", opts.since);
  if (opts.until) qs.set("until", opts.until);
  const path = buildPath(`/api/projects/${projectId}/pl`, qs);
  return jsonFetch<PLSummary>(path, {
    headers: { "X-Project-Id": String(projectId) },
  });
}

// ============================================================================
// Kanban #1292 — per-project progress charts (burndown + velocity).
//
// GET /api/projects/{id}/progress-stats?bucket={day|week}&days={int}
//   X-Project-Id header REQUIRED (gates per Kanban #695; must equal the path
//   id). Mirrors the getProjectPl header-passing pattern below.
//
// Contract (frozen, verified live 2026-06-01):
//   - `burndown` + `velocity` are EQUAL-LENGTH arrays, both ASCEND by `t`,
//     zero-filled (one entry per bucket, never skipped) with IDENTICAL `t`
//     axes (same dates in the same order).
//   - `t` is a bucket-start date string "YYYY-MM-DD" (NOT a timestamp).
//   - `remaining` / `completed` are plain integers (>= 0).
//   - `generated_at` is ISO 8601 UTC ("Z" suffix).
// ============================================================================

export const PROGRESS_BUCKETS = ["day", "week"] as const;
export type ProgressBucketLiteral = (typeof PROGRESS_BUCKETS)[number];

// BurndownPoint — one bucket of remaining open work. `t` is the bucket-start
// date ("YYYY-MM-DD"); `remaining` is a non-negative integer.
export type BurndownPoint = {
  t: string;
  remaining: number;
};

// VelocityPoint — one bucket of completed work. Same `t` axis as the paired
// BurndownPoint at the same index; `completed` is a non-negative integer.
export type VelocityPoint = {
  t: string;
  completed: number;
};

// ProgressStatsResponse — response of /api/projects/{id}/progress-stats.
export type ProgressStatsResponse = {
  project_id: number;
  bucket: ProgressBucketLiteral;
  window_days: number;
  burndown: BurndownPoint[];
  velocity: VelocityPoint[];
  generated_at: string;
};

// getProjectProgressStats — per-project burndown + velocity series.
// X-Project-Id header is required (gates per Kanban #695); helper passes it
// transparently. Mirrors getProjectPl's option + header shape exactly.
export async function getProjectProgressStats(
  projectId: number,
  opts: { bucket?: ProgressBucketLiteral; days?: number } = {},
): Promise<ProgressStatsResponse> {
  const qs = new URLSearchParams();
  if (opts.bucket) qs.set("bucket", opts.bucket);
  if (opts.days != null) qs.set("days", String(opts.days));
  const path = buildPath(`/api/projects/${projectId}/progress-stats`, qs);
  return jsonFetch<ProgressStatsResponse>(path, {
    headers: { "X-Project-Id": String(projectId) },
  });
}

// getCrossProjectPl — operator-level cross-project rollup. NO X-Project-Id
// header (the endpoint spans projects by design). `include_killed` defaults
// to false on the BE; pass true to include projects in is_killed=true state.
export async function getCrossProjectPl(
  opts: {
    period?: PLPeriodLiteral;
    since?: string;
    until?: string;
    include_killed?: boolean;
  } = {},
): Promise<PLCrossProject> {
  const qs = new URLSearchParams();
  if (opts.period) qs.set("period", opts.period);
  if (opts.since) qs.set("since", opts.since);
  if (opts.until) qs.set("until", opts.until);
  if (opts.include_killed) qs.set("include_killed", "true");
  const path = buildPath("/api/pnl", qs);
  return jsonFetch<PLCrossProject>(path);
}

// ============================================================================
// Kanban #945 — Cross-project active-tasks list (operator-level dashboard).
// ============================================================================

// DashboardActiveTaskRow — mirror of api/src/schemas/dashboard.py. One row per
// active task across all status=1 projects. Project fields denormalized so
// the FE doesn't N+1 lookup project_name. `process_status` is gated server-
// side to IN_PROGRESS (2) / REVIEW (3) / BLOCKED (4).
export type DashboardActiveTaskRow = {
  task_id: number;
  title: string;
  project_id: number;
  project_name: string;
  team: string;
  process_status: 2 | 3 | 4;
  run_mode: TaskRunModeValue;
  task_kind: TaskKindValue;
  assigned_role: TaskRoleValue | null;
  priority: TaskPriorityValue;
  updated_at: string; // ISO 8601
  blocked_by: number | null;
  blocked_by_terminal: boolean; // #2419: true when blocker is DONE/CANCELLED — chip suppressed
};

export type DashboardActiveTasks = {
  rows: DashboardActiveTaskRow[];
  total_count: number;
};

// getCrossProjectActiveTasks — operator-level cross-project list. NO
// X-Project-Id header (the endpoint spans projects by design, mirroring the
// /api/pnl pattern from #1329). Default sort is (project_name ASC,
// updated_at DESC) — server-side; FE renders rows as received.
export async function getCrossProjectActiveTasks(): Promise<DashboardActiveTasks> {
  return jsonFetch<DashboardActiveTasks>(`/api/dashboard/active-tasks`);
}

// ============================================================================
// Kanban #1011 (2026-05-20) — POST /api/tasks/{id}/snooze.
// ============================================================================

// SnoozeTaskBody — server schema (api/src/schemas/task.py SnoozeRequest):
// extra='forbid', hours=int default 4, ge=1, le=168. 422 on out-of-range.
export type SnoozeTaskBody = { hours?: number };

export async function snoozeTask(
  projectId: number,
  taskId: number,
  body: SnoozeTaskBody = {},
): Promise<TaskRead> {
  return jsonFetch<TaskRead>(`/api/tasks/${taskId}/snooze`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Project-Id": String(projectId),
    },
    body: JSON.stringify(body),
  });
}

// ============================================================================
// Kanban #1006 (2026-05-20) — GET /api/templates/actions (action templates).
// ============================================================================

// ActionTemplateRead — mirror of api/src/schemas/action_template.py.
// `id` is the template name (also used as POST /api/tasks
// `action_template_id`). Always returns a list; empty list = no templates
// loaded (the YAML directory was empty or all files failed to parse).
export type ActionTemplateRead = {
  id: string;
  name: string;
  version: string;
  description: string;
  default_task_type:
    | "bug"
    | "feature"
    | "chore"
    | "docs"
    | "refactor"
    | "audit";
  default_task_kind: "ai" | "human";
  default_priority: TaskPriorityValue;
  ac_outline: string[];
  hints: string[];
  suggested_attachments: string[];
};

// Team — mirror of GET /api/teams response item (Kanban #1620 Phase 2).
// Global endpoint, no X-Project-Id header. `roster` is the ordered list of
// agent-role slugs for that team (e.g. ["dev-frontend", "dev-backend", ...]).
export type Team = {
  team: string;
  roster: string[];
};

// getTeams — GET /api/teams. No X-Project-Id (global). Returns all teams with
// their roster arrays. Used by NewProjectModal to dynamically drive the team
// <select> and the per-team roster help text.
export async function getTeams(): Promise<Team[]> {
  return jsonFetch<Team[]>(`/api/teams`);
}

// templates — namespace for the read-only template surfaces. Today only
// `actions`; if a later slice adds question / decision templates, they slot
// in here.
export const templates = {
  actions: {
    async list(): Promise<ActionTemplateRead[]> {
      // Endpoint is global — no X-Project-Id required (Kanban #1006).
      return jsonFetch<ActionTemplateRead[]>(`/api/templates/actions`);
    },
  },
};

// ============================================================================
// Kanban #1004 (2026-05-20) — GET /api/handoff-templates (handoff templates).
// ============================================================================

// HandoffTemplateRead — mirror of api/src/schemas/handoff_template.py.
// `project_id IS NULL` → global (cross-project) template; otherwise scoped
// to that project. The FE list call passes the current project id; BE
// returns globals + that-project's rows.
export type HandoffTemplateRead = {
  id: number;
  name: string;
  description: string | null;
  title_pattern: string;
  task_kind: "ai" | "human";
  task_type:
    | "bug"
    | "feature"
    | "chore"
    | "docs"
    | "refactor"
    | "audit";
  default_priority: TaskPriorityValue;
  default_assigned_role: number | null;
  ac_outline: string[];
  carry_context_to_comment: boolean;
  project_id: number | null;
  created_at: string;
  updated_at: string;
};

// handoffTemplates — namespace for handoff-template CRUD. v1 surfaces only
// the list helper used by HandoffTemplatePicker. CRUD beyond list is filed
// as a follow-up (per-project settings page).
export const handoffTemplates = {
  async list(opts: { projectId?: number } = {}): Promise<HandoffTemplateRead[]> {
    const qs = new URLSearchParams();
    if (opts.projectId != null) qs.set("project_id", String(opts.projectId));
    const path = buildPath("/api/handoff-templates", qs);
    return jsonFetch<HandoffTemplateRead[]>(path);
  },
};

// ============================================================================
// Kanban #1655 — Platform Integrations settings (IntegrationsPanel on /settings).
//
// Global, operator-level surface (NO X-Project-Id header — integrations are
// platform-wide, not per-project). Status is READ-ONLY — no toggle. Keys live
// in .env — the contract returns env-var PRESENCE (`present: bool`) but NEVER a
// value, so the FE can render "configured / not configured" without ever touching
// a secret. On-demand (?) help reveals setup guidance per integration row.
//
// Contract:
//   GET /api/settings/integrations -> { integrations: Integration[], platform_security: PlatformSecurity }
// ============================================================================

// IntegrationEnvVar — one .env variable an integration depends on. `present`
// reflects whether the BE saw a non-empty value in the environment; the value
// itself is NEVER serialized (presence-only by design).
export type IntegrationEnvVar = {
  name: string;
  required: boolean;
  present: boolean;
};

// IntegrationSetupLink — a doc / dashboard link rendered as an external anchor
// (target=_blank rel=noopener) in the setup panel.
export type IntegrationSetupLink = {
  label: string;
  url: string;
};

// IntegrationSetup — on-demand guidance shown when the operator clicks (?):
// ordered steps + reference links.
export type IntegrationSetup = {
  steps: string[];
  links: IntegrationSetupLink[];
};

// Integration — one row in the integrations list.
//   configured — BE verdict: all REQUIRED env_vars present. Drives the badge.
//   env_vars   — the .env names (+ required flag + presence) the setup panel
//                lists. Presence-only; no values.
export type Integration = {
  id: string;
  label: string;
  category: string;
  configured: boolean;
  env_vars: IntegrationEnvVar[];
  setup: IntegrationSetup;
};

// PlatformSecurity — Kanban #1658. Read-only platform crypto status.
// vault_key_configured is a PRESENCE BOOLEAN; the key value is never serialized.
export type PlatformSecurity = {
  vault_key_configured: boolean;
};

// IntegrationsResponse — full GET /api/settings/integrations envelope.
export type IntegrationsResponse = {
  integrations: Integration[];
  platform_security: PlatformSecurity;
};

// getIntegrations — GET /api/settings/integrations. Global (no X-Project-Id).
// Returns the full envelope (integrations list + platform_security block).
// Read-only; there is no toggle PATCH endpoint.
export async function getIntegrations(): Promise<IntegrationsResponse> {
  return jsonFetch<IntegrationsResponse>(`/api/settings/integrations`);
}

// ============================================================================
// Kanban #1457 phase 2 — GET /api/user/pending (operator-scoped, no project header).
// ============================================================================

// UserPendingByProject — one entry in UserPendingResponse.by_project.
export type UserPendingByProject = {
  project_id: number;
  project_name: string;
  count: number;
};

// UserPendingResponse — mirror of api/src/schemas/task.py:UserPendingResponse.
// Replaces the N+1 fan-out in InboxBadge (phase 1) with a single endpoint.
// oldest_age_hours is null when count=0.
export type UserPendingResponse = {
  count: number;
  oldest_age_hours: number | null;
  by_project: UserPendingByProject[];
};

// getUserPending — GET /api/user/pending. No X-Project-Id header (cross-project).
export async function getUserPending(): Promise<UserPendingResponse> {
  return jsonFetch<UserPendingResponse>(`/api/user/pending`);
}

// ============================================================================
// Kanban #1868 (2026-06-03) — per-project Milestones CRUD + rollup.
//
// Mounted at /api/milestones; every endpoint is X-Project-Id scoped (the
// session-bound project header is canonical — mirrors the tasks router).
// Mirror of api/src/schemas/milestone.py.
//
// Column naming: `milestone_status` is the LIFECYCLE field (planned / active /
// released / cancelled); the 0/1 soft-delete `status` flag is never exposed
// (clients call deleteMilestone to soft-delete). The list `?status=` query
// param filters the LIFECYCLE column, NOT the soft-delete flag.
// ============================================================================

export const MILESTONE_STATUSES = [
  "planned",
  "active",
  "released",
  "cancelled",
] as const;
export type MilestoneStatusValue = (typeof MILESTONE_STATUSES)[number];

// MilestoneRead — full milestone row (no rollup). `start_date` / `target_date`
// are ISO-8601 date strings ("YYYY-MM-DD"); `created_at` / `updated_at` /
// `released_at` are ISO-8601 timestamps.
export type MilestoneRead = {
  id: number;
  project_id: number;
  title: string;
  description: string | null;
  milestone_status: MilestoneStatusValue;
  start_date: string | null;
  target_date: string | null;
  sort_order: number | null;
  created_at: string;
  updated_at: string;
  released_at: string | null;
};

// MilestoneRollup — task-rollup stats for a milestone.
//   total              — active (status=1) tasks pointing here, incl. cancelled.
//   by_process_status  — count per process_status bucket; keys "1".."6",
//                        always all six (zero-filled).
//   done               — count of process_status=5 (DONE) tasks.
//   progress_pct       — done / (total excluding cancelled), 0..100, 1 decimal.
//                        0.0 when the non-cancelled denominator is zero.
export type MilestoneRollup = {
  total: number;
  by_process_status: Record<string, number>;
  done: number;
  progress_pct: number;
};

// MilestoneDetail — MilestoneRead + rollup; returned by GET /api/milestones/{id}.
export type MilestoneDetail = MilestoneRead & {
  rollup: MilestoneRollup;
};

// MilestoneCreate — POST /api/milestones body. `project_id` is defense-in-depth
// (header is canonical; BE 400s on mismatch). Dates are ISO "YYYY-MM-DD" or
// omitted. 422 when start_date > target_date.
export type MilestoneCreate = {
  project_id: number;
  title: string;
  description?: string | null;
  milestone_status?: MilestoneStatusValue;
  start_date?: string | null;
  target_date?: string | null;
  sort_order?: number | null;
};

// MilestoneUpdate — PATCH /api/milestones/{id} body, all fields optional.
// Key-absent = unchanged; explicit `null` clears a nullable field. Re-scoping
// between projects is NOT supported (no project_id on the surface).
export type MilestoneUpdate = {
  title?: string;
  description?: string | null;
  milestone_status?: MilestoneStatusValue;
  start_date?: string | null;
  target_date?: string | null;
  sort_order?: number | null;
  released_at?: string | null;
};

// TaskTemplateRead — Kanban #1310 (2026-06-04). One row from the GLOBAL
// GET /api/task-templates surface (backend #1303). Mirrors the BE
// TaskTemplateRead schema. NOT project-scoped — there is NO X-Project-Id
// header on the list endpoint (parity with /api/milestones would be WRONG).
// `acceptance_criteria_template` items only need `text` (the only field the
// client substitutes); the BE may carry extra keys, which we ignore.
export type TaskTemplateRead = {
  id: number;
  team: string;
  name: string;
  icon: string | null;
  description_template: string;
  acceptance_criteria_template: { text: string }[];
  default_task_type: string;
  default_priority: TaskPriorityValue;
  default_task_kind: string;
  placeholders: string[];
  status: number;
  created_at: string;
  updated_at: string | null;
};

// listTaskTemplates — GET /api/task-templates (#1310). GLOBAL endpoint:
// sends NO X-Project-Id header. `team` filters the catalog when provided;
// omit to fetch all. Default limit 200 (small catalog). Mirrors the
// listMilestones query-building style minus the project scoping.
export async function listTaskTemplates(
  team?: string,
  opts: { includeDisabled?: boolean; limit?: number; offset?: number } = {},
): Promise<TaskTemplateRead[]> {
  const qs = new URLSearchParams();
  if (team !== undefined && team !== "") qs.set("team", team);
  if (opts.includeDisabled !== undefined)
    qs.set("include_disabled", String(opts.includeDisabled));
  qs.set("limit", String(opts.limit ?? 200));
  if (opts.offset !== undefined) qs.set("offset", String(opts.offset));
  const path = buildPath("/api/task-templates", qs);
  return jsonFetch<TaskTemplateRead[]>(path);
}

// listMilestones — GET /api/milestones. X-Project-Id scoped. Default returns
// active (status=1) rows ordered by (sort_order ASC NULLS LAST, id ASC). The
// optional `status` filters the milestone_status LIFECYCLE column.
export async function listMilestones(
  projectId: number,
  opts: { status?: MilestoneStatusValue; limit?: number; offset?: number } = {},
): Promise<MilestoneRead[]> {
  const qs = new URLSearchParams();
  if (opts.status) qs.set("status", opts.status);
  if (opts.limit !== undefined) qs.set("limit", String(opts.limit));
  if (opts.offset !== undefined) qs.set("offset", String(opts.offset));
  const path = buildPath("/api/milestones", qs);
  return jsonFetch<MilestoneRead[]>(path, {
    headers: { "X-Project-Id": String(projectId) },
  });
}

// getMilestone — GET /api/milestones/{id} (WITH rollup). X-Project-Id scoped;
// 404 when the milestone belongs to a different project than the session.
export async function getMilestone(
  projectId: number,
  id: number,
): Promise<MilestoneDetail> {
  return jsonFetch<MilestoneDetail>(`/api/milestones/${id}`, {
    headers: { "X-Project-Id": String(projectId) },
  });
}

// createMilestone — POST /api/milestones. project_id required in body
// (defense-in-depth) AND the X-Project-Id header (auth gate).
export async function createMilestone(
  projectId: number,
  body: MilestoneCreate,
): Promise<MilestoneRead> {
  return jsonFetch<MilestoneRead>(`/api/milestones`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Project-Id": String(projectId),
    },
    body: JSON.stringify(body),
  });
}

// updateMilestone — PATCH /api/milestones/{id}. Partial update; send only the
// diff. X-Project-Id scoped; 404 on cross-project / missing.
export async function updateMilestone(
  projectId: number,
  id: number,
  body: MilestoneUpdate,
): Promise<MilestoneRead> {
  return jsonFetch<MilestoneRead>(`/api/milestones/${id}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      "X-Project-Id": String(projectId),
    },
    body: JSON.stringify(body),
  });
}

// deleteMilestone — DELETE /api/milestones/{id} (soft-delete). Detaches every
// child task (milestone_id → NULL) in the same transaction. Returns 204 (no
// body) on success; idempotent on already-deleted milestones.
export async function deleteMilestone(
  projectId: number,
  id: number,
): Promise<void> {
  // DELETE returns 204 (no body) — jsonFetch would explode parsing JSON on an
  // empty body; call fetch directly (mirrors push.unsubscribe above).
  const url = `${apiBaseUrl()}/api/milestones/${id}`;
  const response = await fetch(url, {
    method: "DELETE",
    cache: "no-store",
    headers: { Accept: "application/json", "X-Project-Id": String(projectId) },
  });
  if (!response.ok && response.status !== 204) {
    const body = (await response.json().catch(() => ({}))) as {
      detail?: unknown;
    };
    const message =
      formatDetail(body.detail) ?? `${response.status} ${response.statusText}`;
    throw new HttpError(response.status, body.detail, message);
  }
}

// ============================================================================
// Kanban #1005 (2026-06-08) — append-only task comment thread.
//
// Sub-resource of a task (parity with /{task_id}/blocks + /{task_id}/tool-calls):
// every route is X-Project-Id scoped (the session-bound header is the auth gate;
// the BE 400s on a task that belongs to a different project). APPEND-ONLY — there
// is NO PATCH and NO DELETE on comments (AC#7); the only removal path is a task
// hard-delete CASCADE. Mirror of api/src/schemas/task_comment.py.
// ============================================================================

// CommentAuthorKind — wire enum mirror of constants.CommentAuthorKind.ALL.
//   'user'   — a human operator (this UI posts with this kind).
//   'agent'  — a specialist subagent / Lead progress note.
//   'system' — an automated event (status flip, audit, scheduler note).
export type CommentAuthorKindValue = "user" | "agent" | "system";

// TaskCommentRead — one comment row. `body_markdown` flags whether `body`
// should be rendered as (sanitized) markdown vs plain escaped text. `created_at`
// is ISO 8601 with timezone. id is BIGSERIAL — monotonic with insertion, so
// id-ordering IS chronological (the `before` cursor needs no created_at tiebreak).
export type TaskCommentRead = {
  id: number;
  task_id: number;
  author_kind: CommentAuthorKindValue;
  author_label: string | null;
  body: string;
  body_markdown: boolean;
  created_at: string;
};

// TaskCommentCreate — POST body. `author_kind` required (the discriminator);
// `author_label` optional attribution (max 200 chars BE-side); `body` required
// (min 1, max 20000 BE-side); `body_markdown` defaults true (matches DB DEFAULT).
// extra='forbid' on the BE schema — don't send unknown keys.
export type TaskCommentCreate = {
  author_kind: CommentAuthorKindValue;
  author_label?: string;
  body: string;
  body_markdown?: boolean;
};

// BE payload caps (kept in lockstep with schemas/task_comment.py so the FE can
// gate before submit rather than round-tripping a 422). Exported for the
// compose box's maxLength / disabled-on-overflow guard.
export const COMMENT_BODY_MAX = 20_000;
export const COMMENT_AUTHOR_LABEL_MAX = 200;

// getTaskComments — GET /api/tasks/{id}/comments. Oldest-first (id ASC),
// chronological. `before` = id cursor (returns rows with id < before) for the
// "load older" page; omit for the first page. `limit` default 50, max 200.
// Returns [] for a task with no comments. X-Project-Id scoped.
export async function getTaskComments(
  projectId: number,
  taskId: number,
  opts: { before?: number; limit?: number } = {},
): Promise<TaskCommentRead[]> {
  const qs = new URLSearchParams();
  if (opts.before !== undefined) qs.set("before", String(opts.before));
  if (opts.limit !== undefined) qs.set("limit", String(opts.limit));
  const path = buildPath(`/api/tasks/${taskId}/comments`, qs);
  return jsonFetch<TaskCommentRead[]>(path, {
    headers: { "X-Project-Id": String(projectId) },
  });
}

// postTaskComment — POST /api/tasks/{id}/comments. 201 + the created row.
// Rate-limited 30/minute BE-side; the FE serializes posts behind a `posting`
// flag so a single operator never trips it. X-Project-Id scoped.
export async function postTaskComment(
  projectId: number,
  taskId: number,
  body: TaskCommentCreate,
): Promise<TaskCommentRead> {
  return jsonFetch<TaskCommentRead>(`/api/tasks/${taskId}/comments`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Project-Id": String(projectId),
    },
    body: JSON.stringify(body),
  });
}

// ============================================================================
// Kanban #1309 / #1315 — project Resources (files + links).
//
// Two mount shapes on the BE (api/src/routers/resources.py):
//   * project-scoped (/api/projects/{id}/resources): POST create, GET list.
//   * resource-scoped (/api/resources/{id}): GET detail, GET preview, DELETE.
//
// `kind` is 'file' | 'link' (mirror of constants.ResourceKind.ALL). File rows
// carry `filename` + `content_type` + `size_bytes`; link rows carry `url`. The
// open `tags` object holds the verify-and-tag metadata (#1309): files →
// row_count / col_count / format_detected / schema_detected / preview /
// est_cost_if_full / hash; links → url_scheme / url_host / head_status / title.
// The server-internal `stored_path` is stripped before serialization (BE
// _strip_internal_tags) so it never appears on the wire.
//
// CREATE is dual-contract by request content-type (same handler):
//   * multipart/form-data → FILE upload (file part + kind='file' + task_id? +
//     label?). DO NOT set Content-Type manually — the browser sets the
//     multipart boundary. Verify-and-tag runs SYNCHRONOUSLY inside the POST
//     (store → tag → INSERT → 201 with tags). There is NO server progress
//     stream; the panel shows an optimistic staged indicator while in-flight.
//   * application/json → LINK attach ({kind:'link', url, task_id?, label?}).
//
// CREATE + DELETE are operator-gated on the BE (fail-OPEN/dormant until
// OPERATOR_ACTION_KEY is set, mirroring the task_templates gate). 413 when a
// file exceeds the upload cap.
// ============================================================================

export const RESOURCE_KINDS = ["file", "link"] as const;
export type ResourceKindValue = (typeof RESOURCE_KINDS)[number];

// EstCostIfFull — the planning-only LLM cost estimate stashed in a file row's
// tags (services/resource_verify.estimate_cost_if_full). `usd` is null when the
// model price is unknown. NEVER a billing figure — `basis` documents the math.
export type EstCostIfFull = {
  usd: number | null;
  approx_tokens: number;
  model: string;
  basis: string;
};

// ResourceTags — the open verify-and-tag metadata object. Every key is optional
// (the shape differs file vs link, and a parser may be unavailable). Typed as a
// loose record at the wire boundary; the known keys below are surfaced as chips.
export type ResourceTags = {
  // FILE keys (services/resource_verify.verify_and_tag_file)
  format_detected?: string | null;
  row_count?: number | null;
  col_count?: number | null;
  schema_detected?: string[] | null;
  preview?: unknown;
  preview_rows?: number;
  hash?: string;
  est_cost_if_full?: EstCostIfFull;
  parser_unavailable?: boolean;
  content_type_resolved?: string | null;
  parse_error?: string;
  notes?: string;
  // LINK keys (services/resource_verify.verify_and_tag_link)
  url_scheme?: string;
  url_host?: string;
  head_status?: number | null;
  title?: string | null;
  // Open for forward-compat — pipeline may add keys without a schema bump.
  [k: string]: unknown;
};

// Resource — mirror of api/src/schemas/project_resource.py:ResourceRead.
// `filename` / `content_type` / `size_bytes` are file-only (null on links);
// `url` is link-only (null on files). `tags` is the metadata object above.
export type Resource = {
  id: number;
  project_id: number;
  task_id: number | null;
  kind: ResourceKindValue;
  filename: string | null;
  url: string | null;
  content_type: string | null;
  size_bytes: number | null;
  label: string | null;
  tags: ResourceTags;
  created_at: string; // ISO 8601 with timezone
  updated_at: string;
};

// ResourcePreview — mirror of ResourcePreview schema. Read straight off the
// stored tags (the endpoint NEVER re-reads the file). `preview` is the
// first-N-rows sample (list of row-objects for CSV/TSV, parsed value for JSON,
// null when no parser ran). For links the file-stat fields are null.
export type ResourcePreview = {
  id: number;
  kind: ResourceKindValue;
  filename: string | null;
  content_type: string | null;
  format_detected: string | null;
  row_count: number | null;
  col_count: number | null;
  schema_detected: string[] | null;
  preview: unknown;
  parser_unavailable: boolean;
};

type ListResourcesOpts = {
  task_id?: number;
  kind?: ResourceKindValue;
  limit?: number;
  offset?: number;
};

// listResources — GET /api/projects/{id}/resources. Newest-first
// (created_at DESC). Ungated (read-only). Optional task_id pins to one task;
// kind filters file/link. X-Project-Id scoped (mirrors the tasks router).
export async function listResources(
  projectId: number,
  opts: ListResourcesOpts = {},
): Promise<Resource[]> {
  const qs = new URLSearchParams();
  if (opts.task_id !== undefined) qs.set("task_id", String(opts.task_id));
  if (opts.kind !== undefined) qs.set("kind", opts.kind);
  if (opts.limit !== undefined) qs.set("limit", String(opts.limit));
  if (opts.offset !== undefined) qs.set("offset", String(opts.offset));
  const path = buildPath(`/api/projects/${projectId}/resources`, qs);
  return jsonFetch<Resource[]>(path, {
    headers: { "X-Project-Id": String(projectId) },
  });
}

// createResourceFile — POST /api/projects/{id}/resources (multipart/form-data).
// The browser sets the multipart Content-Type + boundary automatically; we MUST
// NOT set it manually (a manual header omits the boundary → 422). jsonFetch
// always forces JSON, so this path uses fetch() directly to send FormData.
//
// 201 → the tagged Resource (verify-and-tag is synchronous inside the POST).
// 413 → file over the upload cap (nothing saved). 403 → operator gate active.
export async function createResourceFile(
  projectId: number,
  file: File,
  opts: { task_id?: number; label?: string } = {},
): Promise<Resource> {
  const form = new FormData();
  form.set("file", file);
  form.set("kind", "file");
  if (opts.task_id !== undefined) form.set("task_id", String(opts.task_id));
  if (opts.label !== undefined && opts.label !== "") form.set("label", opts.label);

  const url = `${apiBaseUrl()}/api/projects/${projectId}/resources`;
  const response = await fetch(url, {
    method: "POST",
    cache: "no-store",
    // NOTE: no Content-Type — the browser sets multipart/form-data + boundary.
    headers: { Accept: "application/json", "X-Project-Id": String(projectId) },
    body: form,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
    const message =
      formatDetail(body.detail) ?? `${response.status} ${response.statusText}`;
    throw new HttpError(response.status, body.detail, message);
  }
  return (await response.json()) as Resource;
}

// createResourceLink — POST /api/projects/{id}/resources (application/json).
// Body {kind:'link', url, task_id?, label?}. 201 → the tagged Resource.
export async function createResourceLink(
  projectId: number,
  body: { url: string; task_id?: number; label?: string },
): Promise<Resource> {
  return jsonFetch<Resource>(`/api/projects/${projectId}/resources`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Project-Id": String(projectId),
    },
    body: JSON.stringify({ kind: "link", ...body }),
  });
}

// getResourcePreview — GET /api/resources/{id}/preview. Ungated. 404 if missing
// or soft-deleted. No X-Project-Id (resource-scoped, mirrors the BE router).
export async function getResourcePreview(
  resourceId: number,
): Promise<ResourcePreview> {
  return jsonFetch<ResourcePreview>(`/api/resources/${resourceId}/preview`);
}

// ============================================================================
// Kanban #2135 — GET /api/usage/daily  (LLM spend surface)
// ============================================================================

// UsageDailyRow — one row in the DailyUsageResponse.rows array.
// cost_usd is a 4-dp decimal string (same Decimal-as-string convention used
// elsewhere in this file).
export type UsageDailyRow = {
  date: string;       // "YYYY-MM-DD"
  provider: string;   // "anthropic" | "google" | "unknown" | …
  model: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: string;   // e.g. "0.1234"
};

export type DailyUsageResponse = {
  days: number;
  rows: UsageDailyRow[];
  total_today_usd: string;   // 4-dp decimal string
  total_month_usd: string;   // 4-dp decimal string
  // Kanban #2137 — server UTC date used to bucket total_today_usd.
  // Optional: absent on API versions that predate this field; component
  // falls back to client UTC date when missing.
  today?: string;            // "YYYY-MM-DD" (server UTC)
};

// getDailyUsage — GET /api/usage/daily?days=N[&project_id=P].
// No X-Project-Id header — operator-level endpoint (same as /api/pnl).
export async function getDailyUsage(opts?: {
  days?: number;
  project_id?: number;
}): Promise<DailyUsageResponse> {
  const qs = new URLSearchParams();
  if (opts?.days != null) qs.set("days", String(opts.days));
  if (opts?.project_id != null) qs.set("project_id", String(opts.project_id));
  return jsonFetch<DailyUsageResponse>(buildPath("/api/usage/daily", qs));
}

// ============================================================================
// Kanban #2356 — GET /api/usage/monthly  (billing-cycle spend surface)
// ============================================================================

// UsageMonthlyTaskRow — per-task cost contribution inside one billing cycle.
// cost fields are 4-dp decimal strings (same Decimal-as-string convention).
// task_id/task_title are null for the "unattributed" bucket.
export type UsageMonthlyTaskRow = {
  task_id: number | null;
  task_title: string | null;
  mode_a_cost_usd: string;   // e.g. "10.3678"
  mode_b_cost_usd: string;
  total_cost_usd: string;
};

// UsageMonthlyCycle — one billing cycle window in MonthlyUsageResponse.cycles.
export type UsageMonthlyCycle = {
  cycle_start: string;          // "YYYY-MM-DD"
  cycle_end: string;            // "YYYY-MM-DD"
  mode_a_cost_usd: string;
  mode_a_input_tokens: number;
  mode_a_output_tokens: number;
  mode_b_cost_usd: string;
  mode_b_input_tokens: number;
  mode_b_output_tokens: number;
  total_cost_usd: string;
  tasks: UsageMonthlyTaskRow[];
};

export type MonthlyUsageResponse = {
  months: number;
  cycle_day: number;
  cycles: UsageMonthlyCycle[];   // most-recent first; zero-filled per window
  total_cost_usd: string;        // 4-dp decimal string
};

// getMonthlyUsage — GET /api/usage/monthly?months=N&cycle_day=D[&project_id=P].
// No X-Project-Id header — operator-level endpoint (same as /api/usage/daily).
export async function getMonthlyUsage(opts?: {
  months?: number;
  cycle_day?: number;
  project_id?: number;
}): Promise<MonthlyUsageResponse> {
  const qs = new URLSearchParams();
  if (opts?.months != null) qs.set("months", String(opts.months));
  if (opts?.cycle_day != null) qs.set("cycle_day", String(opts.cycle_day));
  if (opts?.project_id != null) qs.set("project_id", String(opts.project_id));
  return jsonFetch<MonthlyUsageResponse>(buildPath("/api/usage/monthly", qs));
}

// ============================================================================
// Kanban #1305 — Task output files (listing + raw bytes).
// ============================================================================

// TaskOutputEntry — one item from GET /api/tasks/{id}/outputs.
// kind ∈ chart | doc | export | text per the #1305 contract.
export type TaskOutputEntry = {
  filename: string;
  mime: string;
  size: number;
  kind: "chart" | "doc" | "export" | "text";
};

// getTaskOutputs — GET /api/tasks/{id}/outputs → list.
// Returns [] when the task has no outputs (never throws on 404/empty).
export async function getTaskOutputs(
  projectId: number,
  taskId: number,
): Promise<TaskOutputEntry[]> {
  return jsonFetch<TaskOutputEntry[]>(`/api/tasks/${taskId}/outputs`, {
    headers: { "X-Project-Id": String(projectId) },
  });
}

// fetchTaskOutputBytes — fetch a task output file as a Blob via the browser
// fetch() with X-Project-Id header.  The raw bytes API requires the header
// (400 without it), so we cannot use a plain <img src> or <iframe src>.
// Callers: create a blob URL, use it, then revoke on cleanup.
export async function fetchTaskOutputBytes(
  projectId: number,
  taskId: number,
  filename: string,
): Promise<Blob> {
  const url = `${apiBaseUrl()}/api/tasks/${taskId}/outputs/${encodeURIComponent(filename)}`;
  const response = await fetch(url, {
    cache: "no-store",
    headers: { "X-Project-Id": String(projectId) },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
    const message =
      formatDetail(body.detail) ?? `${response.status} ${response.statusText}`;
    throw new HttpError(response.status, body.detail, message);
  }
  return response.blob();
}

// deleteResource — DELETE /api/resources/{id}. Operator-gated; soft-delete +
// move file to trash. 204 (no body) on success; idempotent. Returns void.
export async function deleteResource(resourceId: number): Promise<void> {
  // DELETE returns 204 (no body) — jsonFetch would explode parsing JSON on an
  // empty body; call fetch directly (mirrors push.unsubscribe / deleteMilestone).
  const url = `${apiBaseUrl()}/api/resources/${resourceId}`;
  const response = await fetch(url, {
    method: "DELETE",
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
    const message =
      formatDetail(body.detail) ?? `${response.status} ${response.statusText}`;
    throw new HttpError(response.status, body.detail, message);
  }
}

// ============================================================================
// Kanban #1017 — Agent gallery. Platform-level resource (NO X-Project-Id
// header; mirrors /api/agents/validate #1016). Two endpoints:
//   GET /api/agents          → flat AgentSummary[] sorted by name
//   GET /api/agents/{name}   → AgentDetail (summary + raw_frontmatter +
//                              full_description + recent spawns); 404 unknown.
// ============================================================================

// AgentModelTier — model enum on the frontmatter. `null` = no `model:` key on
// the agent (Claude Code falls back to the session default).
export type AgentModelTier = "opus" | "sonnet" | "haiku";

// AgentDomain — which team/domain the agent belongs to. "other" is the catch-
// all for files that don't map to a known team.
export type AgentDomain =
  | "dev"
  | "novel"
  | "content"
  | "secretary"
  | "sem"
  | "seo"
  | "data"
  | "general"
  | "other";

// AgentValidationError — one frontmatter diagnostic. Same shape as the
// /api/agents/validate diagnostics (Kanban #1016): file basename, 1-based line,
// the offending field, a human message, and severity. `error` = blocking
// (agent won't load); `warning` = unknown key / unknown tool.
export type AgentValidationError = {
  file: string;
  line: number;
  field: string;
  message: string;
  severity: "error" | "warning";
};

// AgentSummary — one row in GET /api/agents.
//   tools_summary: human label — "All tools" | "N tools".
//   tool_count:    null when the agent grants "All tools" (no explicit list).
//   source_file:   basename only (path-stripped on the wire).
//   valid:         false when validation_errors carries any severity='error'.
export type AgentSummary = {
  name: string;
  description: string;
  model: AgentModelTier | null;
  tools_summary: string;
  tool_count: number | null;
  hook_count: number;
  source_file: string;
  domain: AgentDomain;
  valid: boolean;
  validation_errors: AgentValidationError[];
};

// AgentSpawn — one row in AgentDetail.spawns. A task this agent was spawned
// for. `at` is an ISO 8601 timestamp (feed to formatRelative); null on legacy
// rows that pre-date the timestamp column. Newest first, capped at 20 by the BE.
export type AgentSpawn = {
  task_id: number;
  project_id: number;
  project_name: string;
  model: string | null;
  at: string | null;
};

// AgentDetail — GET /api/agents/{name}. Everything in AgentSummary plus the
// raw frontmatter block, the full (untruncated) description, recent spawns,
// and — Kanban #2481 finish — the structured tools list + raw markdown body
// for edit pre-fill.
//   tools: string[] = explicit tool list; "All tools" = the literal sentinel;
//          null = no `tools:` key (inherit / all).
//   body:  raw markdown after the frontmatter fence; "" when absent.
export type AgentDetail = AgentSummary & {
  raw_frontmatter: string;
  full_description: string;
  spawns: AgentSpawn[];
  tools: string[] | "All tools" | null;
  body: string;
};

// getAgents — GET /api/agents. Platform-level (no X-Project-Id). Returns the
// full agent listing sorted by name (BE pre-sorts; FE sort control re-orders
// client-side).
export async function getAgents(): Promise<AgentSummary[]> {
  return jsonFetch<AgentSummary[]>(`/api/agents`);
}

// getAgentDetail — GET /api/agents/{name}. 404 (HttpError) on unknown name —
// the detail page discriminates on .status === 404 → notFound().
export async function getAgentDetail(name: string): Promise<AgentDetail> {
  return jsonFetch<AgentDetail>(`/api/agents/${encodeURIComponent(name)}`);
}

// ============================================================================
// Kanban #2481 — gated agent WRITE endpoints (create + edit). Platform-level
// (NO X-Project-Id; mirrors the gallery reads). Both write paths are guarded
// server-side by the operator-proof header (X-Operator-Token = the operator's
// OPERATOR_ACTION_KEY):
//   POST /api/agents          → 201 AgentSummary  (create; 409 if name exists)
//   PUT  /api/agents/{name}   → 200 AgentSummary  (edit; 404 if absent; body
//                               name MUST equal the path name)
//
// SECURITY: the operator token is NEVER persisted (no localStorage / cookie /
// NEXT_PUBLIC_*). The caller holds it in component state and passes it per-call;
// this helper only stamps the header when a non-empty token is supplied. When
// the server-side gate is dormant (OPERATOR_ACTION_KEY unset), a token-less call
// still succeeds — the server is the authority, so the client never hard-requires it.
// ============================================================================

// AgentWrite — request body for POST /api/agents + PUT /api/agents/{name}.
// Mirror of api/src/schemas/agent_metadata.py:AgentWrite (extra="forbid"
// server-side, so callers must NOT add off-schema keys).
//   - name        required; must match ^[a-z0-9]+(-[a-z0-9]+)*$. For PUT it must
//                 equal the path name (the path is authoritative).
//   - description required; non-empty after strip.
//   - model       optional tier; omit (or null) = inherit the session default.
//   - tools       optional; a string[] of tool names OR the literal "All tools";
//                 omit = inherit all tools.
//   - hooks       optional nested object (presence + mapping-type only).
//   - scope       optional string.
//   - body        the markdown body after the frontmatter fence; may be "".
export type AgentWrite = {
  name: string;
  description: string;
  model?: AgentModelTier | null;
  tools?: string[] | "All tools" | null;
  hooks?: Record<string, unknown> | null;
  scope?: string | null;
  body: string;
};

// AgentWriteDiagnostics — the SHAPE the file-validator 422 detail carries:
// `{ message, diagnostics: AgentValidationError[] }`. NOTE: jsonFetch's
// formatDetail() can only stringify a string detail or a Pydantic `msg[]`
// array; this object detail collapses to a generic "422 …" HttpError.message.
// So callers must read HttpError.detail (NOT .message) to surface the
// per-field diagnostics — use extractAgentWriteDiagnostics() below.
export type AgentWriteDiagnostics = {
  message: string;
  diagnostics: AgentValidationError[];
};

// extractAgentWriteDiagnostics — narrow an HttpError.detail to the validator's
// `{message, diagnostics[]}` object, or null when the detail is a plain-string
// Pydantic error (bad name / name-mismatch) or anything else. Lets the form
// branch: object detail → render the diagnostics list; else → render .message.
export function extractAgentWriteDiagnostics(
  detail: unknown,
): AgentWriteDiagnostics | null {
  if (
    detail &&
    typeof detail === "object" &&
    !Array.isArray(detail) &&
    "diagnostics" in detail &&
    Array.isArray((detail as { diagnostics: unknown }).diagnostics)
  ) {
    const d = detail as { message?: unknown; diagnostics: unknown[] };
    const diagnostics = d.diagnostics.filter(
      (x): x is AgentValidationError =>
        !!x &&
        typeof x === "object" &&
        "message" in x &&
        "severity" in x,
    );
    return {
      message:
        typeof d.message === "string"
          ? d.message
          : "Agent frontmatter is invalid; nothing was written.",
      diagnostics,
    };
  }
  return null;
}

// agentWriteHeaders — JSON content-type + the operator-proof header (only when
// a non-empty token is supplied). The token is stamped per-request and never
// stored anywhere by this module.
function agentWriteHeaders(operatorToken?: string): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = operatorToken?.trim();
  if (token) headers["X-Operator-Token"] = token;
  return headers;
}

// createAgent — POST /api/agents. 201 AgentSummary. 403 (operator proof),
// 409 (name exists), 422 (Pydantic field error OR validator diagnostics).
export async function createAgent(
  body: AgentWrite,
  operatorToken?: string,
): Promise<AgentSummary> {
  return jsonFetch<AgentSummary>(`/api/agents`, {
    method: "POST",
    headers: agentWriteHeaders(operatorToken),
    body: JSON.stringify(body),
  });
}

// updateAgent — PUT /api/agents/{name}. 200 AgentSummary. The body.name MUST
// equal `name` (the path is authoritative server-side). 403 / 404 / 422.
export async function updateAgent(
  name: string,
  body: AgentWrite,
  operatorToken?: string,
): Promise<AgentSummary> {
  return jsonFetch<AgentSummary>(`/api/agents/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: agentWriteHeaders(operatorToken),
    body: JSON.stringify(body),
  });
}
