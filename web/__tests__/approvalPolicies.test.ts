/**
 * Tests for approvalPolicies.ts — _not predicate fail-closed coverage (#2389).
 *
 * Strategy: test via the public `evaluateRuleAgainstTask` surface so no exports
 * need to be added to the module. A minimal TaskRead stub (taskWith) provides
 * only the fields each test case cares about; remaining required fields are
 * filled from `BASE_TASK` defaults so TypeScript is satisfied.
 */
import { describe, it, expect } from "vitest";
import { evaluateRuleAgainstTask } from "@/lib/approvalPolicies";
import type { TaskRead } from "@/lib/api";

// Minimal required-field defaults for TaskRead so tests only set what they care about.
const BASE_TASK: TaskRead = {
  id: 1,
  project_id: 1,
  parent_task_id: null,
  title: "Test task",
  description: null,
  process_status: 1,
  priority: 2,
  assigned_role: null,
  run_mode: "manual",
  task_kind: "human",
  task_type: "feature",
  is_template: false,
  is_pending: false,
  recurrence_rule: null,
  recurrence_timezone: "UTC",
  next_fire_at: null,
  spawned_from_task_id: null,
  scheduled_at: null,
  blocked_by: null,
  sort_order: null,
  acceptance_criteria: null,
  interaction_kind: "work",
  question_payload: null,
  resume_context: null,
  status_change_reason: null,
  estimated_input_tokens: null,
  estimated_output_tokens: null,
  estimated_cost_usd: null,
  model_override: null,
  halt_reason: null,
  operator_gate: null,
  operator_gate_note: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  started_at: null,
  completed_at: "2026-01-02T00:00:00Z",
};

function taskWith(overrides: Partial<TaskRead>): TaskRead {
  return { ...BASE_TASK, ...overrides };
}

const NOW = new Date("2026-01-03T00:00:00Z");

// ---------------------------------------------------------------------------
// _not predicates — fail-closed on absent/null field
// ---------------------------------------------------------------------------

describe("task_type_not predicate", () => {
  const ruleNotChore = {
    name: "not-chore",
    match: { task_type_not: "chore" },
  };

  it("MATCHES when task_type is present and differs from predicate value", () => {
    const task = taskWith({ task_type: "feature" });
    expect(evaluateRuleAgainstTask(ruleNotChore, task, NOW)).toBe(true);
  });

  it("FAILS CLOSED when task_type equals predicate value", () => {
    const task = taskWith({ task_type: "chore" });
    expect(evaluateRuleAgainstTask(ruleNotChore, task, NOW)).toBe(false);
  });

  it("FAILS CLOSED when task_type is absent (undefined)", () => {
    // task_type is optional on TaskRead — simulate absent field
    const task = taskWith({ task_type: undefined });
    expect(evaluateRuleAgainstTask(ruleNotChore, task, NOW)).toBe(false);
  });

  it("FAILS CLOSED when task_type is null-ish empty string", () => {
    // The TS evaluator uses `task.task_type ?? ""` so empty string = absent.
    // Cast required because the type says TaskTypeValue | undefined, not "".
    const task = taskWith({ task_type: "" as TaskRead["task_type"] });
    expect(evaluateRuleAgainstTask(ruleNotChore, task, NOW)).toBe(false);
  });
});

describe("operator_gate_not predicate", () => {
  const ruleNotHitl = {
    name: "not-hitl",
    match: { operator_gate_not: "hitl" },
  };

  it("MATCHES when operator_gate is present and differs", () => {
    const task = taskWith({ operator_gate: "review" });
    expect(evaluateRuleAgainstTask(ruleNotHitl, task, NOW)).toBe(true);
  });

  it("FAILS CLOSED when operator_gate is null (absent)", () => {
    const task = taskWith({ operator_gate: null });
    expect(evaluateRuleAgainstTask(ruleNotHitl, task, NOW)).toBe(false);
  });

  it("FAILS CLOSED when operator_gate is undefined", () => {
    const task = taskWith({ operator_gate: undefined });
    expect(evaluateRuleAgainstTask(ruleNotHitl, task, NOW)).toBe(false);
  });
});

describe("run_mode_not predicate", () => {
  const ruleNotManual = {
    name: "not-manual",
    match: { run_mode_not: "manual" },
  };

  it("MATCHES when run_mode is present and differs", () => {
    const task = taskWith({ run_mode: "auto" as TaskRead["run_mode"] });
    expect(evaluateRuleAgainstTask(ruleNotManual, task, NOW)).toBe(true);
  });

  it("FAILS CLOSED when run_mode matches predicate value", () => {
    const task = taskWith({ run_mode: "manual" });
    expect(evaluateRuleAgainstTask(ruleNotManual, task, NOW)).toBe(false);
  });
});

describe("task_kind_not predicate", () => {
  const ruleNotHuman = {
    name: "not-human",
    match: { task_kind_not: "human" },
  };

  it("MATCHES when task_kind is present and differs", () => {
    const task = taskWith({ task_kind: "ai" as TaskRead["task_kind"] });
    expect(evaluateRuleAgainstTask(ruleNotHuman, task, NOW)).toBe(true);
  });

  it("FAILS CLOSED when task_kind matches predicate value", () => {
    const task = taskWith({ task_kind: "human" });
    expect(evaluateRuleAgainstTask(ruleNotHuman, task, NOW)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Positive sanity — existing predicates still work
// ---------------------------------------------------------------------------

describe("positive predicates sanity", () => {
  it("task_type equals match", () => {
    const rule = { name: "feature-type", match: { task_type: "feature" } };
    const task = taskWith({ task_type: "feature" });
    expect(evaluateRuleAgainstTask(rule, task, NOW)).toBe(true);
  });

  it("task_type equals miss", () => {
    const rule = { name: "feature-type", match: { task_type: "feature" } };
    const task = taskWith({ task_type: "chore" });
    expect(evaluateRuleAgainstTask(rule, task, NOW)).toBe(false);
  });

  it("enabled:false rule never matches", () => {
    const rule = { name: "disabled", enabled: false, match: { task_type: "feature" } };
    const task = taskWith({ task_type: "feature" });
    expect(evaluateRuleAgainstTask(rule, task, NOW)).toBe(false);
  });

  it("task_title_contains match", () => {
    const rule = { name: "deploy rule", match: { task_title_contains: "Deploy" } };
    const task = taskWith({ title: "Deploy to production" });
    expect(evaluateRuleAgainstTask(rule, task, NOW)).toBe(true);
  });
});
