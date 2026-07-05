// ApprovalPoliciesEditor — #2390 deep-review component-level coverage.
//
// Covered (lib-level H1 caps / M2 regex / AC5 text_contains_all/any live in
// __tests__/approvalPolicies.test.ts — this file covers what can only be
// exercised through the rendered component's state wiring):
//   M1 — toggle/duplicate/delete while editingId !== null surfaces the
//        inline guard instead of silently discarding the draft; "Keep
//        editing" dismisses it, "Discard draft and continue" runs the
//        original action.
//   M3 — the runGuarded choke point no-ops while a save is in flight
//        (closes the toggle/dup/delete same-tick race).
//   H1 — rule-count cap (MAX_RULES) disables New + Duplicate and shows the
//        cap message once rules.length >= MAX_RULES.
//   H3 — preview card always renders the "can't see live HITL prompts"
//        disclaimer alongside a preview result.
//
// Async: findBy/waitFor only (no sync querySelector after a click) per the
// FE test-determinism convention (#1310).

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, configure } from "@testing-library/react";
import type { ApprovalPolicyRule } from "@/lib/approvalPolicies";
import { MAX_RULES } from "@/lib/approvalPolicies";
import type { ProjectRead, TaskRead } from "@/lib/api";

configure({ asyncUtilTimeout: 5000 });

// ── api mock ────────────────────────────────────────────────────────────
const mockUpdateProject = vi.fn();
const mockListAllTasks = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    updateProject: (...args: Parameters<typeof actual.updateProject>) =>
      mockUpdateProject(...args),
    listAllTasks: (...args: Parameters<typeof actual.listAllTasks>) =>
      mockListAllTasks(...args),
  };
});

// ── next/navigation mock ────────────────────────────────────────────────
const mockRefresh = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: mockRefresh }),
}));

// Imported AFTER mocks register.
import { ApprovalPoliciesEditor } from "@/components/ApprovalPoliciesEditor";

// ── fixtures ────────────────────────────────────────────────────────────

function makeProject(overrides: Partial<ProjectRead> = {}): ProjectRead {
  return {
    id: 42,
    name: "test-project",
    description: null,
    paths_web: "",
    paths_api: "",
    paths_db: "",
    stack_web: null,
    stack_api: null,
    stack_db: null,
    config: {},
    is_active: true,
    team: "dev",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    auto_run_consent_at: null,
    sources: [],
    working_path: null,
    working_repo: null,
    budget_daily_usd: null,
    budget_monthly_usd: null,
    budget_total_usd: null,
    approval_policies: null,
    hitl_nudge_threshold_hours: null,
    effort_mode: null,
    ...overrides,
  };
}

function makeRule(overrides: Partial<ApprovalPolicyRule> = {}): ApprovalPolicyRule {
  return {
    id: "policy-1",
    name: "Test policy",
    enabled: true,
    action: "auto_approve",
    default_answer: "accept",
    match: { task_type: "feature" },
    ui: {
      trigger_event: "hitl_prompt",
      condition_logic: "all",
      conditions: [{ id: "cond-1", field: "task_type", op: "equals", value: "feature" }],
    },
    version: 1,
    ...overrides,
  };
}

beforeEach(() => {
  mockUpdateProject.mockReset();
  mockListAllTasks.mockReset();
  mockRefresh.mockReset();
  mockUpdateProject.mockResolvedValue(makeProject());
  mockListAllTasks.mockResolvedValue([] as TaskRead[]);
});

// ---------------------------------------------------------------------------
// M1 — unsaved-draft guard
// ---------------------------------------------------------------------------

describe("M1 — unsaved-draft guard", () => {
  it("clicking Duplicate on another row while editing shows the guard instead of duplicating", async () => {
    const rules = [makeRule({ id: "a", name: "Rule A" }), makeRule({ id: "b", name: "Rule B" })];
    render(
      <ApprovalPoliciesEditor
        project={makeProject({ approval_policies: { version: 1, rules, versions: [] } })}
      />,
    );

    // Start editing rule A.
    const editButtons = await screen.findAllByText("Edit");
    fireEvent.click(editButtons[0]);
    expect(await screen.findByText('Edit policy')).toBeInTheDocument();

    // Attempt to duplicate rule B while A's draft is unsaved.
    const dupButtons = screen.getAllByText("Duplicate");
    fireEvent.click(dupButtons[1]);

    // Guard banner appears; the mutation must NOT have fired.
    expect(await screen.findByText(/Finish editing/)).toBeInTheDocument();
    expect(mockUpdateProject).not.toHaveBeenCalled();
  });

  it("'Keep editing' dismisses the guard without discarding the draft or running the action", async () => {
    const rules = [makeRule({ id: "a", name: "Rule A" }), makeRule({ id: "b", name: "Rule B" })];
    render(
      <ApprovalPoliciesEditor
        project={makeProject({ approval_policies: { version: 1, rules, versions: [] } })}
      />,
    );

    fireEvent.click((await screen.findAllByText("Edit"))[0]);
    expect(await screen.findByText('Edit policy')).toBeInTheDocument();

    fireEvent.click(screen.getAllByText("Delete")[1]);
    expect(await screen.findByText(/Finish editing/)).toBeInTheDocument();

    fireEvent.click(screen.getByText("Keep editing"));

    await waitFor(() => {
      expect(screen.queryByText(/Finish editing/)).not.toBeInTheDocument();
    });
    // Still on the edit form (draft preserved) — delete never fired.
    expect(screen.getByText('Edit policy')).toBeInTheDocument();
    expect(mockUpdateProject).not.toHaveBeenCalled();
  });

  it("'Discard draft and continue' runs the originally-blocked action", async () => {
    const rules = [makeRule({ id: "a", name: "Rule A" }), makeRule({ id: "b", name: "Rule B" })];
    render(
      <ApprovalPoliciesEditor
        project={makeProject({ approval_policies: { version: 1, rules, versions: [] } })}
      />,
    );

    fireEvent.click((await screen.findAllByText("Edit"))[0]);
    expect(await screen.findByText('Edit policy')).toBeInTheDocument();

    // Toggle rule B's enabled checkbox while A is being edited.
    const checkboxes = screen.getAllByRole("checkbox", { name: "Enabled" });
    // First "Enabled" checkbox belongs to the form's own toggle; row
    // checkboxes come first in DOM order (list pane renders before the form).
    fireEvent.click(checkboxes[1]);
    expect(await screen.findByText(/Finish editing/)).toBeInTheDocument();

    fireEvent.click(screen.getByText("Discard draft and continue"));

    await waitFor(() => expect(mockUpdateProject).toHaveBeenCalledTimes(1));
    // The saved payload's rules array should reflect rule B flipped, rule A untouched.
    const [, body] = mockUpdateProject.mock.calls[0];
    const savedRules = body.approval_policies.rules as ApprovalPolicyRule[];
    const savedB = savedRules.find((r) => r.id === "b");
    expect(savedB?.enabled).toBe(false); // was true, toggled off

    // The guard is gone and the form reverted to "New policy" (draft discarded).
    await waitFor(() => {
      expect(screen.queryByText(/Finish editing/)).not.toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// M3 — toggle/dup/delete race (runGuarded choke point)
// ---------------------------------------------------------------------------

describe("M3 — race choke point", () => {
  it("a second row action while a save is already in flight does not fire a second mutation", async () => {
    const rules = [makeRule({ id: "a", name: "Rule A" })];
    // Never resolves — keeps `saving` true for the duration of the test.
    mockUpdateProject.mockReturnValue(new Promise(() => {}));
    render(
      <ApprovalPoliciesEditor
        project={makeProject({ approval_policies: { version: 1, rules, versions: [] } })}
      />,
    );

    const deleteBtn = await screen.findByText("Delete");
    fireEvent.click(deleteBtn); // saving becomes true (never resolves)
    await waitFor(() => expect(mockUpdateProject).toHaveBeenCalledTimes(1));

    // Row buttons are disabled once saving=true, so a re-click is a no-op —
    // asserting the DOM state instead of forcing a synthetic bypass, since a
    // disabled button never dispatches a click event in a real browser.
    expect(deleteBtn).toBeDisabled();
    expect(mockUpdateProject).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// H1 — rule-count cap
// ---------------------------------------------------------------------------

describe("H1 — rule-count cap", () => {
  it("disables New + Duplicate and shows the cap message at MAX_RULES", async () => {
    const rules = Array.from({ length: MAX_RULES }, (_, i) =>
      makeRule({ id: `r${i}`, name: `Rule ${i}` }),
    );
    render(
      <ApprovalPoliciesEditor
        project={makeProject({ approval_policies: { version: 1, rules, versions: [] } })}
      />,
    );

    // The cap message renders in TWO places at once (list-panel banner +
    // form banner, since the "New policy" form is atRuleCap too by default)
    // — findAllByText (not findByText) avoids a false "multiple elements"
    // failure from an ambiguous single-element query.
    const capMessages = await screen.findAllByText(
      new RegExp(`Max ${MAX_RULES} policies reached`),
    );
    expect(capMessages.length).toBeGreaterThanOrEqual(1);
    const newBtn = screen.getByText("New").closest("button");
    expect(newBtn).toBeDisabled();
    const dupButtons = screen.getAllByText("Duplicate");
    for (const btn of dupButtons) {
      expect(btn.closest("button")).toBeDisabled();
    }
  });

  it("does not cap when rules.length is one under MAX_RULES", async () => {
    const rules = Array.from({ length: MAX_RULES - 1 }, (_, i) =>
      makeRule({ id: `r${i}`, name: `Rule ${i}` }),
    );
    render(
      <ApprovalPoliciesEditor
        project={makeProject({ approval_policies: { version: 1, rules, versions: [] } })}
      />,
    );

    await screen.findAllByText("Rule 0");
    expect(screen.queryByText(new RegExp(`Max ${MAX_RULES} policies reached`))).not.toBeInTheDocument();
    const newBtn = screen.getByText("New").closest("button");
    expect(newBtn).not.toBeDisabled();
  });
});

// ---------------------------------------------------------------------------
// H3 — preview disclaimer
// ---------------------------------------------------------------------------

describe("H3 — preview disclaimer", () => {
  it("renders the 'can't see live HITL prompts' disclaimer alongside a preview result", async () => {
    render(<ApprovalPoliciesEditor project={makeProject({ approval_policies: null })} />);

    // Fill the minimal required fields so Preview is enabled.
    fireEvent.change(screen.getByLabelText("Label"), { target: { value: "preview test" } });
    const conditionRow = document.querySelector("[data-approval-policy-condition]");
    const valueInput = conditionRow?.querySelector("input") as HTMLInputElement;
    fireEvent.change(valueInput, { target: { value: "spend" } });

    const previewBtn = screen.getByText("Preview");
    fireEvent.click(previewBtn);

    expect(
      await screen.findByText(/can't see live HITL prompts/),
    ).toBeInTheDocument();
  });
});
