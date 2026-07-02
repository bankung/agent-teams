/**
 * Tests for approvalPolicies.ts — _not predicate fail-closed coverage (#2389)
 * + #2390 deep-review findings (H1 caps, M2 regex-field alignment).
 *
 * Strategy: test via the public `evaluateRuleAgainstTask` surface so no exports
 * need to be added to the module. A minimal TaskRead stub (taskWith) provides
 * only the fields each test case cares about; remaining required fields are
 * filled from `BASE_TASK` defaults so TypeScript is satisfied.
 */
import { describe, it, expect } from "vitest";
import {
  MAX_CONDITIONS_PER_RULE,
  NAME_MAX_LENGTH,
  blankDraft,
  draftErrors,
  evaluateRuleAgainstTask,
  isRegexField,
  newCondition,
  regexPatternError,
} from "@/lib/approvalPolicies";
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

// ---------------------------------------------------------------------------
// #2390 H1 — editor-side caps (draftErrors-surfaced)
// ---------------------------------------------------------------------------

describe("H1 caps — draftErrors", () => {
  it("accepts a name at exactly NAME_MAX_LENGTH", () => {
    const draft = { ...blankDraft(), name: "a".repeat(NAME_MAX_LENGTH) };
    draft.conditions = [{ ...newCondition(), value: "x" }];
    expect(draftErrors(draft).some((e) => e.includes("200 characters"))).toBe(false);
  });

  it("rejects a name one character over NAME_MAX_LENGTH", () => {
    const draft = { ...blankDraft(), name: "a".repeat(NAME_MAX_LENGTH + 1) };
    draft.conditions = [{ ...newCondition(), value: "x" }];
    expect(draftErrors(draft)).toContain(
      `Name must be ${NAME_MAX_LENGTH} characters or fewer.`,
    );
  });

  it("accepts exactly MAX_CONDITIONS_PER_RULE conditions", () => {
    const draft = blankDraft();
    draft.name = "cap test";
    draft.conditions = Array.from({ length: MAX_CONDITIONS_PER_RULE }, () => ({
      ...newCondition(),
      value: "x",
    }));
    expect(
      draftErrors(draft).some((e) => e.includes("conditions")),
    ).toBe(false);
  });

  it("rejects MAX_CONDITIONS_PER_RULE + 1 conditions", () => {
    const draft = blankDraft();
    draft.name = "cap test";
    draft.conditions = Array.from({ length: MAX_CONDITIONS_PER_RULE + 1 }, () => ({
      ...newCondition(),
      value: "x",
    }));
    expect(draftErrors(draft)).toContain(
      `A rule can have at most ${MAX_CONDITIONS_PER_RULE} conditions.`,
    );
  });

  it("rejects a defaultAnswer over its cap", () => {
    const draft = blankDraft();
    draft.name = "answer cap";
    draft.conditions = [{ ...newCondition(), value: "x" }];
    draft.defaultAnswer = "a".repeat(201);
    expect(draftErrors(draft).some((e) => e.includes("Default answer"))).toBe(true);
  });

  it("rejects a route label over its cap on require_attention", () => {
    const draft = blankDraft();
    draft.name = "label cap";
    draft.conditions = [{ ...newCondition(), value: "x" }];
    draft.action = "require_attention";
    draft.label = "a".repeat(201);
    expect(draftErrors(draft).some((e) => e.includes("Route label"))).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// #2390 M2 — target_url_pattern / content_predicate are regex, not substring
// ---------------------------------------------------------------------------

describe("M2 — regex-field alignment", () => {
  it("isRegexField is true only for target_url_pattern / content_predicate", () => {
    expect(isRegexField("target_url_pattern")).toBe(true);
    expect(isRegexField("content_predicate")).toBe(true);
    expect(isRegexField("question_text")).toBe(false);
    expect(isRegexField("title")).toBe(false);
  });

  it("regexPatternError accepts a valid regex", () => {
    expect(regexPatternError("linkedin\\.com")).toBeNull();
    expect(regexPatternError("^git push.*main$")).toBeNull();
  });

  it("regexPatternError accepts an empty string (required-ness is a separate check)", () => {
    expect(regexPatternError("")).toBeNull();
  });

  it("regexPatternError flags an unbalanced group as invalid — mirrors the .ps1 gate's try/catch [regex]::IsMatch fail path", () => {
    // _shared.ps1 Test-PolicyRule wraps [regex]::IsMatch in try/catch and
    // treats a throw as no-match (fail-closed); this is the TS-side
    // equivalent input-time check using the same "new RegExp throws" signal.
    expect(regexPatternError("(unclosed")).toBe("Invalid regex pattern.");
  });

  it("draftErrors surfaces an invalid regex on a target_url_pattern condition", () => {
    const draft = blankDraft();
    draft.name = "bad regex";
    draft.conditions = [
      { ...newCondition("target_url_pattern"), value: "(unclosed" },
    ];
    expect(draftErrors(draft)).toContain("Condition 1: Invalid regex pattern.");
  });

  it("draftErrors accepts a valid regex on a content_predicate condition", () => {
    const draft = blankDraft();
    draft.name = "good regex";
    draft.conditions = [
      { ...newCondition("content_predicate"), value: "git push.*main" },
    ];
    expect(draftErrors(draft).some((e) => e.includes("regex"))).toBe(false);
  });

  it("target_url_pattern/content_predicate always report 0 hits in the task-list preview — no URL/tool_input field exists on TaskRead to test against (H3)", () => {
    const urlRule = {
      name: "url rule",
      match: { target_url_pattern: "linkedin\\.com" },
    };
    const contentRule = {
      name: "content rule",
      match: { content_predicate: "git push" },
    };
    const task = taskWith({ title: "Deploy to linkedin.com via git push" });
    expect(evaluateRuleAgainstTask(urlRule, task, NOW)).toBe(false);
    expect(evaluateRuleAgainstTask(contentRule, task, NOW)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// AC5 — text_contains_all / text_contains_any: engine supports them, but the
// editor UI has no FIELD_DEFINITIONS entry or conditionToPredicate case that
// produces them — confirms the deferral is a real UI feature-gap, not a bug
// in what's reachable through the form.
// ---------------------------------------------------------------------------

describe("AC5 — text_contains_all/any (deferred, UI-unreachable)", () => {
  it("the evaluator DOES support text_contains_all (multi-value AND)", () => {
    const rule = { name: "all", match: { text_contains_all: ["spend", "deploy"] } };
    const matches = taskWith({
      interaction_kind: "question",
      question_payload: { question: "spend to deploy the release" },
    });
    const missesOne = taskWith({
      interaction_kind: "question",
      question_payload: { question: "spend only" },
    });
    expect(evaluateRuleAgainstTask(rule, matches, NOW)).toBe(true);
    expect(evaluateRuleAgainstTask(rule, missesOne, NOW)).toBe(false);
  });

  it("the evaluator DOES support text_contains_any (multi-value OR)", () => {
    const rule = { name: "any", match: { text_contains_any: ["spend", "deploy"] } };
    const matchesEither = taskWith({
      interaction_kind: "question",
      question_payload: { question: "deploy only, no spend word" },
    });
    const matchesNeither = taskWith({
      interaction_kind: "question",
      question_payload: { question: "unrelated question" },
    });
    expect(evaluateRuleAgainstTask(rule, matchesEither, NOW)).toBe(true);
    expect(evaluateRuleAgainstTask(rule, matchesNeither, NOW)).toBe(false);
  });
});
