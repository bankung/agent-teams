import type { TaskRead } from "./api";

export type ApprovalPolicyAction =
  | "auto_approve"
  | "auto_deny"
  | "require_attention";

export type ApprovalPolicyTrigger = "hitl_prompt" | "tool_call";
export type ConditionLogic = "all" | "any";

export type ConditionOperator =
  | "contains"
  | "equals"
  | "not_equals"
  | "lt"
  | "gt";

export type ConditionField =
  | "question_text"
  | "options"
  | "amount_usd"
  | "title"
  | "description"
  | "task_type"
  | "priority"
  | "project_id"
  | "age_hours"
  | "acceptance_criteria_count"
  | "operator_gate"
  | "run_mode"
  | "task_kind"
  | "assigned_role"
  | "tool_name"
  | "target_url_pattern"
  | "content_predicate";

export type PolicyCondition = {
  id: string;
  field: ConditionField;
  op: ConditionOperator;
  value: string;
};

export type ApprovalPolicyRule = {
  id?: string;
  name?: string;
  enabled?: boolean;
  action?: ApprovalPolicyAction | "requires_attention";
  default_answer?: string;
  reason?: string;
  route_label?: string;
  match?: Record<string, unknown>;
  match_any?: Array<Record<string, unknown>>;
  ui?: {
    trigger_event?: ApprovalPolicyTrigger;
    condition_logic?: ConditionLogic;
    conditions?: PolicyCondition[];
  };
  version?: number;
  created_at?: string;
  updated_at?: string;
  [key: string]: unknown;
};

export type ApprovalPoliciesDocument = {
  version?: number;
  rules?: ApprovalPolicyRule[];
  versions?: Array<{
    version: number;
    saved_at: string;
    rules: ApprovalPolicyRule[];
  }>;
  [key: string]: unknown;
};

export type ApprovalPolicyDraft = {
  id: string;
  name: string;
  enabled: boolean;
  trigger: ApprovalPolicyTrigger;
  logic: ConditionLogic;
  conditions: PolicyCondition[];
  action: ApprovalPolicyAction;
  label: string;
  defaultAnswer: string;
};

export type PolicyPreviewSample = {
  id: number;
  title: string;
  completed_at: string | null;
};

export type PolicyPreviewStats = {
  hitCount: number;
  lastMatchedAt: string | null;
  samples: PolicyPreviewSample[];
};

export const FIELD_DEFINITIONS: Array<{
  value: ConditionField;
  label: string;
  ops: ConditionOperator[];
  placeholder: string;
}> = [
  {
    value: "question_text",
    label: "Question text",
    ops: ["contains"],
    placeholder: "spend, deploy, commit",
  },
  {
    value: "options",
    label: "HITL options",
    ops: ["contains"],
    placeholder: "accept",
  },
  {
    value: "amount_usd",
    label: "Amount USD",
    ops: ["lt", "gt"],
    placeholder: "5",
  },
  {
    value: "title",
    label: "Task title",
    ops: ["contains"],
    placeholder: "release",
  },
  {
    value: "description",
    label: "Task description",
    ops: ["contains"],
    placeholder: "pytest",
  },
  {
    value: "task_type",
    label: "Task type",
    ops: ["equals", "not_equals"],
    placeholder: "feature",
  },
  {
    value: "priority",
    label: "Priority",
    ops: ["equals", "lt", "gt"],
    placeholder: "2",
  },
  {
    value: "project_id",
    label: "Project ID",
    ops: ["equals"],
    placeholder: "1",
  },
  {
    value: "age_hours",
    label: "Age hours",
    ops: ["lt", "gt"],
    placeholder: "24",
  },
  {
    value: "acceptance_criteria_count",
    label: "AC count",
    ops: ["equals", "lt", "gt"],
    placeholder: "3",
  },
  {
    value: "operator_gate",
    label: "Operator gate",
    ops: ["equals", "not_equals"],
    placeholder: "hitl",
  },
  {
    value: "run_mode",
    label: "Run mode",
    ops: ["equals", "not_equals"],
    placeholder: "manual",
  },
  {
    value: "task_kind",
    label: "Task kind",
    ops: ["equals", "not_equals"],
    placeholder: "ai",
  },
  {
    value: "assigned_role",
    label: "Assigned role",
    ops: ["equals"],
    placeholder: "2",
  },
  {
    value: "tool_name",
    label: "Tool name",
    ops: ["equals"],
    placeholder: "Bash",
  },
  {
    value: "target_url_pattern",
    label: "Target URL pattern",
    ops: ["contains"],
    placeholder: "linkedin\\.com",
  },
  {
    value: "content_predicate",
    label: "Tool input pattern",
    ops: ["contains"],
    placeholder: "git push",
  },
];

const FIELD_BY_VALUE = new Map(FIELD_DEFINITIONS.map((f) => [f.value, f]));

export function newPolicyId(prefix = "policy"): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  return `${prefix}-${Math.random().toString(36).slice(2, 10)}`;
}

export function newCondition(field: ConditionField = "question_text"): PolicyCondition {
  const def = FIELD_BY_VALUE.get(field) ?? FIELD_DEFINITIONS[0];
  return {
    id: newPolicyId("cond"),
    field: def.value,
    op: def.ops[0],
    value: "",
  };
}

export function blankDraft(): ApprovalPolicyDraft {
  return {
    id: newPolicyId(),
    name: "",
    enabled: true,
    trigger: "hitl_prompt",
    logic: "all",
    conditions: [newCondition()],
    action: "auto_approve",
    label: "",
    defaultAnswer: "accept",
  };
}

export function normalizePolicies(raw: Record<string, unknown> | null | undefined): ApprovalPoliciesDocument {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return { version: 0, rules: [], versions: [] };
  }
  const doc = raw as ApprovalPoliciesDocument;
  return {
    ...doc,
    version: typeof doc.version === "number" ? doc.version : 0,
    rules: Array.isArray(doc.rules) ? doc.rules : [],
    versions: Array.isArray(doc.versions) ? doc.versions : [],
  };
}

export function draftFromRule(rule: ApprovalPolicyRule): ApprovalPolicyDraft {
  const ui = rule.ui ?? {};
  const conditions =
    Array.isArray(ui.conditions) && ui.conditions.length > 0
      ? ui.conditions
      : conditionsFromMatch(rule);
  return {
    id: typeof rule.id === "string" ? rule.id : newPolicyId(),
    name: typeof rule.name === "string" ? rule.name : "",
    enabled: rule.enabled !== false,
    trigger: ui.trigger_event ?? "hitl_prompt",
    logic: ui.condition_logic ?? (rule.match_any ? "any" : "all"),
    conditions,
    action: normalizeAction(rule.action),
    label:
      typeof rule.route_label === "string"
        ? rule.route_label
        : typeof rule.reason === "string"
          ? rule.reason
          : "",
    defaultAnswer:
      typeof rule.default_answer === "string" && rule.default_answer.trim()
        ? rule.default_answer
        : "accept",
  };
}

function normalizeAction(action: ApprovalPolicyRule["action"]): ApprovalPolicyAction {
  if (action === "auto_deny") return "auto_deny";
  if (action === "require_attention" || action === "requires_attention") {
    return "require_attention";
  }
  return "auto_approve";
}

function conditionsFromMatch(rule: ApprovalPolicyRule): PolicyCondition[] {
  const groups = rule.match_any ?? (rule.match ? [rule.match] : []);
  const conditions: PolicyCondition[] = [];
  for (const group of groups) {
    for (const [key, value] of Object.entries(group)) {
      const condition = conditionFromPredicate(key, value);
      if (condition) conditions.push(condition);
    }
  }
  return conditions.length > 0 ? conditions : [newCondition()];
}

function conditionFromPredicate(key: string, value: unknown): PolicyCondition | null {
  const text = typeof value === "string" ? value : String(value ?? "");
  const numberText = typeof value === "number" ? String(value) : text;
  const make = (
    field: ConditionField,
    op: ConditionOperator,
    raw: string,
  ): PolicyCondition => ({ id: newPolicyId("cond"), field, op, value: raw });
  if (key === "text_contains") return make("question_text", "contains", text);
  if (key === "options_include") return make("options", "contains", text);
  if (key === "amount_usd_lt") return make("amount_usd", "lt", numberText);
  if (key === "amount_usd_gt") return make("amount_usd", "gt", numberText);
  if (key === "task_title_contains") return make("title", "contains", text);
  if (key === "task_description_contains") return make("description", "contains", text);
  if (key === "task_type") return make("task_type", "equals", text);
  if (key === "task_type_not") return make("task_type", "not_equals", text);
  if (key === "priority") return make("priority", "equals", numberText);
  if (key === "priority_lt") return make("priority", "lt", numberText);
  if (key === "priority_gt") return make("priority", "gt", numberText);
  if (key === "project_id") return make("project_id", "equals", numberText);
  if (key === "age_hours_lt") return make("age_hours", "lt", numberText);
  if (key === "age_hours_gt") return make("age_hours", "gt", numberText);
  if (key === "acceptance_criteria_count") return make("acceptance_criteria_count", "equals", numberText);
  if (key === "acceptance_criteria_count_lt") return make("acceptance_criteria_count", "lt", numberText);
  if (key === "acceptance_criteria_count_gt") return make("acceptance_criteria_count", "gt", numberText);
  if (key === "operator_gate") return make("operator_gate", "equals", text);
  if (key === "operator_gate_not") return make("operator_gate", "not_equals", text);
  if (key === "run_mode") return make("run_mode", "equals", text);
  if (key === "run_mode_not") return make("run_mode", "not_equals", text);
  if (key === "task_kind") return make("task_kind", "equals", text);
  if (key === "task_kind_not") return make("task_kind", "not_equals", text);
  if (key === "assigned_role") return make("assigned_role", "equals", numberText);
  if (key === "tool_name") return make("tool_name", "equals", text);
  if (key === "target_url_pattern") return make("target_url_pattern", "contains", text);
  if (key === "content_predicate") return make("content_predicate", "contains", text);
  return null;
}

export function conditionError(condition: PolicyCondition): string | null {
  const def = FIELD_BY_VALUE.get(condition.field);
  if (!def) return "Choose a field from the task schema.";
  if (!def.ops.includes(condition.op)) return "Choose a valid operator for this field.";
  if (condition.value.trim().length === 0) return "Enter a value.";
  if (needsNumber(condition.field, condition.op) && Number.isNaN(Number(condition.value))) {
    return "Enter a numeric value.";
  }
  return null;
}

function needsNumber(field: ConditionField, op: ConditionOperator): boolean {
  if (op === "lt" || op === "gt") return true;
  return ["priority", "project_id", "acceptance_criteria_count", "assigned_role"].includes(field);
}

export function draftErrors(draft: ApprovalPolicyDraft): string[] {
  const errors: string[] = [];
  if (draft.name.trim().length === 0) errors.push("Name is required.");
  if (draft.conditions.length === 0) errors.push("At least one condition is required.");
  draft.conditions.forEach((condition, index) => {
    const error = conditionError(condition);
    if (error) errors.push(`Condition ${index + 1}: ${error}`);
  });
  if (draft.action === "require_attention" && draft.label.trim().length === 0) {
    errors.push("Route-to-user label is required.");
  }
  return errors;
}

export function ruleFromDraft(draft: ApprovalPolicyDraft, version: number, nowIso: string): ApprovalPolicyRule {
  const predicates = draft.conditions
    .map(conditionToPredicate)
    .filter((p): p is Record<string, unknown> => p !== null);
  const base: ApprovalPolicyRule = {
    id: draft.id,
    name: draft.name.trim(),
    enabled: draft.enabled,
    action: draft.action,
    version,
    updated_at: nowIso,
    ui: {
      trigger_event: draft.trigger,
      condition_logic: draft.logic,
      conditions: draft.conditions,
    },
  };
  if (draft.action === "auto_approve" && draft.defaultAnswer.trim()) {
    base.default_answer = draft.defaultAnswer.trim();
  }
  if (draft.label.trim()) {
    base.route_label = draft.label.trim();
    base.reason = draft.label.trim();
  }
  if (draft.logic === "any") {
    base.match_any = predicates;
  } else {
    base.match = Object.assign({}, ...predicates);
  }
  return base;
}

function conditionToPredicate(condition: PolicyCondition): Record<string, unknown> | null {
  if (conditionError(condition)) return null;
  const raw = condition.value.trim();
  const num = Number(raw);
  switch (condition.field) {
    case "question_text":
      return { text_contains: raw };
    case "options":
      return { options_include: raw };
    case "amount_usd":
      return condition.op === "gt" ? { amount_usd_gt: num } : { amount_usd_lt: num };
    case "title":
      return { task_title_contains: raw };
    case "description":
      return { task_description_contains: raw };
    case "task_type":
      return condition.op === "not_equals" ? { task_type_not: raw } : { task_type: raw };
    case "priority":
      if (condition.op === "lt") return { priority_lt: num };
      if (condition.op === "gt") return { priority_gt: num };
      return { priority: num };
    case "project_id":
      return { project_id: num };
    case "age_hours":
      return condition.op === "gt" ? { age_hours_gt: num } : { age_hours_lt: num };
    case "acceptance_criteria_count":
      if (condition.op === "lt") return { acceptance_criteria_count_lt: num };
      if (condition.op === "gt") return { acceptance_criteria_count_gt: num };
      return { acceptance_criteria_count: num };
    case "operator_gate":
      return condition.op === "not_equals" ? { operator_gate_not: raw } : { operator_gate: raw };
    case "run_mode":
      return condition.op === "not_equals" ? { run_mode_not: raw } : { run_mode: raw };
    case "task_kind":
      return condition.op === "not_equals" ? { task_kind_not: raw } : { task_kind: raw };
    case "assigned_role":
      return { assigned_role: num };
    case "tool_name":
      return { tool_name: raw };
    case "target_url_pattern":
      return { target_url_pattern: raw };
    case "content_predicate":
      return { content_predicate: raw };
    default:
      return null;
  }
}

export function buildPoliciesDocument(
  currentRaw: Record<string, unknown> | null | undefined,
  nextRules: ApprovalPolicyRule[],
  nowIso: string,
): ApprovalPoliciesDocument {
  const current = normalizePolicies(currentRaw);
  const previousVersion = current.version ?? 0;
  const nextVersion = previousVersion + 1;
  const history = current.rules && current.rules.length > 0
    ? [
        ...(current.versions ?? []),
        { version: previousVersion, saved_at: nowIso, rules: current.rules },
      ]
    : current.versions ?? [];
  return {
    ...current,
    version: nextVersion,
    rules: nextRules.map((rule) => ({ ...rule, version: nextVersion, updated_at: nowIso })),
    versions: history.slice(-20),
  };
}

export function previewRule(
  rule: ApprovalPolicyRule,
  tasks: TaskRead[],
  now: Date = new Date(),
): PolicyPreviewStats {
  const cutoff = now.getTime() - 7 * 24 * 60 * 60 * 1000;
  const matched = tasks
    .filter((task) => {
      const completedMs = task.completed_at ? Date.parse(task.completed_at) : Number.NaN;
      return !Number.isNaN(completedMs) && completedMs >= cutoff;
    })
    .filter((task) => evaluateRuleAgainstTask(rule, task, now))
    .sort((a, b) => {
      const aDone = a.completed_at ? Date.parse(a.completed_at) : 0;
      const bDone = b.completed_at ? Date.parse(b.completed_at) : 0;
      return bDone - aDone;
    });
  return {
    hitCount: matched.length,
    lastMatchedAt: matched[0]?.completed_at ?? null,
    samples: matched.slice(0, 5).map((task) => ({
      id: task.id,
      title: task.title,
      completed_at: task.completed_at,
    })),
  };
}

export function evaluateRuleAgainstTask(
  rule: ApprovalPolicyRule,
  task: TaskRead,
  now: Date = new Date(),
): boolean {
  if (rule.enabled === false) return false;
  const hasMatch = rule.match && Object.keys(rule.match).length > 0;
  const hasAny = Array.isArray(rule.match_any) && rule.match_any.length > 0;
  if (!hasMatch && !hasAny) return false;
  if (hasMatch && !matchesPredicateGroup(rule.match ?? {}, task, now)) return false;
  if (hasAny) {
    return (rule.match_any ?? []).some((group) => matchesPredicateGroup(group, task, now));
  }
  return true;
}

function matchesPredicateGroup(group: Record<string, unknown>, task: TaskRead, now: Date): boolean {
  const entries = Object.entries(group);
  if (entries.length === 0) return false;
  return entries.every(([key, value]) => matchPredicate(key, value, task, now));
}

function matchPredicate(key: string, value: unknown, task: TaskRead, now: Date): boolean {
  const textValue = typeof value === "string" ? value : String(value ?? "");
  const numberValue = typeof value === "number" ? value : Number(value);
  const question = String(task.question_payload?.question ?? "");
  switch (key) {
    case "text_contains":
      return question.toLowerCase().includes(textValue.toLowerCase());
    case "text_contains_all":
      return Array.isArray(value) && value.length > 0 && value.every((v) => (
        typeof v === "string" && question.toLowerCase().includes(v.toLowerCase())
      ));
    case "text_contains_any":
      return Array.isArray(value) && value.length > 0 && value.some((v) => (
        typeof v === "string" && question.toLowerCase().includes(v.toLowerCase())
      ));
    case "options_include": {
      const options = task.question_payload?.options ?? [];
      return options.some((option) => typeof option === "string" && option === textValue);
    }
    case "amount_usd_lt": {
      const amount = extractAmountUsd(question);
      return amount !== null && !Number.isNaN(numberValue) && amount < numberValue;
    }
    case "amount_usd_gt": {
      const amount = extractAmountUsd(question);
      return amount !== null && !Number.isNaN(numberValue) && amount > numberValue;
    }
    case "task_title_contains":
      return task.title.toLowerCase().includes(textValue.toLowerCase());
    case "task_description_contains":
      return String(task.description ?? "").toLowerCase().includes(textValue.toLowerCase());
    case "task_type":
      return (task.task_type ?? "") === textValue;
    case "task_type_not": {
      const a = task.task_type ?? "";
      return a !== "" && a !== textValue;
    }
    case "priority":
      return task.priority === numberValue;
    case "priority_lt":
      return task.priority < numberValue;
    case "priority_gt":
      return task.priority > numberValue;
    case "project_id":
      return task.project_id === numberValue;
    case "age_hours_lt": {
      const age = ageHours(task, now);
      return age !== null && age < numberValue;
    }
    case "age_hours_gt": {
      const age = ageHours(task, now);
      return age !== null && age > numberValue;
    }
    case "acceptance_criteria_count": {
      const count = task.acceptance_criteria?.length ?? 0;
      return count === numberValue;
    }
    case "acceptance_criteria_count_lt": {
      const count = task.acceptance_criteria?.length ?? 0;
      return count < numberValue;
    }
    case "acceptance_criteria_count_gt": {
      const count = task.acceptance_criteria?.length ?? 0;
      return count > numberValue;
    }
    case "operator_gate":
      return (task.operator_gate ?? "") === textValue;
    case "operator_gate_not": {
      const a = task.operator_gate ?? "";
      return a !== "" && a !== textValue;
    }
    case "run_mode":
      return task.run_mode === textValue;
    case "run_mode_not": {
      const a = task.run_mode ?? "";
      return a !== "" && a !== textValue;
    }
    case "task_kind":
      return task.task_kind === textValue;
    case "task_kind_not": {
      const a = task.task_kind ?? "";
      return a !== "" && a !== textValue;
    }
    case "assigned_role":
      return task.assigned_role === numberValue;
    default:
      return false;
  }
}

function ageHours(task: TaskRead, now: Date): number | null {
  const createdMs = Date.parse(task.created_at);
  if (Number.isNaN(createdMs)) return null;
  return (now.getTime() - createdMs) / (60 * 60 * 1000);
}

function extractAmountUsd(text: string): number | null {
  const match = text.match(/\$\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*USD\b/i);
  if (!match) return null;
  const value = match[1] ?? match[2];
  const parsed = Number(value);
  return Number.isNaN(parsed) ? null : parsed;
}
