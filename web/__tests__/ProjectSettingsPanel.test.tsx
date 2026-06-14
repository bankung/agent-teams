// ProjectSettingsPanel — Thinking effort section tests (Kanban #2300).
//
// Covered:
//   (1) renders "Default (off)" selected when effort_mode is null
//   (2) selecting "auto" + save fires PATCH with {effort_mode:"auto"}
//   (3) selecting "Default (off)" (i.e. null) + save fires PATCH with {effort_mode:null}
//
// Async: findBy/waitFor only (no sync querySelector on post-async state).

import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  configure,
} from "@testing-library/react";
import type { ProjectRead } from "@/lib/api";

configure({ asyncUtilTimeout: 5000 });

// ── api mock ──────────────────────────────────────────────────────────────────
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

// ── next/navigation mock ──────────────────────────────────────────────────────
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn() }),
}));

// Imported AFTER mocks register.
import { ProjectSettingsPanel } from "@/components/ProjectSettingsPanel";

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

beforeEach(() => {
  mockUpdateProject.mockReset();
  mockListAllTasks.mockReset();
  mockUpdateProject.mockResolvedValue(makeProject());
  mockListAllTasks.mockResolvedValue([]);
});

describe("ProjectSettingsPanel — Thinking effort", () => {
  it("renders 'Default (off)' selected when effort_mode is null", async () => {
    render(<ProjectSettingsPanel project={makeProject({ effort_mode: null })} />);
    const select = document.querySelector(
      "[data-project-effort-mode-select]",
    ) as HTMLSelectElement;
    expect(select).not.toBeNull();
    // The selected option text should match "Default (off)".
    const selectedOption = select.options[select.selectedIndex];
    expect(selectedOption.text).toBe("Default (off)");
  });

  it("selecting 'auto' and saving fires PATCH with {effort_mode:'auto'}", async () => {
    render(<ProjectSettingsPanel project={makeProject({ effort_mode: null })} />);
    const select = document.querySelector(
      "[data-project-effort-mode-select]",
    ) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "auto" } });

    const saveBtn = document.querySelector(
      "[data-project-effort-mode-save]",
    ) as HTMLButtonElement;
    fireEvent.click(saveBtn);

    await waitFor(() => expect(mockUpdateProject).toHaveBeenCalledTimes(1));
    expect(mockUpdateProject).toHaveBeenCalledWith(42, { effort_mode: "auto" });
  });

  it("selecting 'Default (off)' and saving fires PATCH with {effort_mode:null}", async () => {
    // Start with a non-null value so switching to null makes the form dirty.
    render(
      <ProjectSettingsPanel project={makeProject({ effort_mode: "high" })} />,
    );
    const select = document.querySelector(
      "[data-project-effort-mode-select]",
    ) as HTMLSelectElement;
    // "__null__" is the internal encoding for null (see component source).
    fireEvent.change(select, { target: { value: "__null__" } });

    const saveBtn = document.querySelector(
      "[data-project-effort-mode-save]",
    ) as HTMLButtonElement;
    fireEvent.click(saveBtn);

    await waitFor(() => expect(mockUpdateProject).toHaveBeenCalledTimes(1));
    expect(mockUpdateProject).toHaveBeenCalledWith(42, { effort_mode: null });
  });
});

describe("ProjectSettingsPanel — Approval policies", () => {
  it("saves a form-authored policy as evaluator JSON", async () => {
    render(<ProjectSettingsPanel project={makeProject()} />);

    fireEvent.change(document.querySelector("[data-approval-policy-name]") as HTMLInputElement, {
      target: { value: "feature auto approve" },
    });
    const condition = document.querySelector("[data-approval-policy-condition]") as HTMLElement;
    const inputs = condition.querySelectorAll("input");
    fireEvent.change(inputs[0], { target: { value: "task_type" } });
    fireEvent.change(inputs[1], { target: { value: "feature" } });

    fireEvent.click(document.querySelector("[data-approval-policy-save]") as HTMLButtonElement);

    await waitFor(() => expect(mockUpdateProject).toHaveBeenCalledTimes(1));
    const body = mockUpdateProject.mock.calls[0][1];
    expect(body.approval_policies.version).toBe(1);
    expect(body.approval_policies.rules[0]).toMatchObject({
      name: "feature auto approve",
      enabled: true,
      action: "auto_approve",
      match: { task_type: "feature" },
    });
    expect(body.approval_policies.rules[0].ui.conditions[0]).toMatchObject({
      field: "task_type",
      op: "equals",
      value: "feature",
    });
  });

  it("previews against DONE tasks closed in the last seven days", async () => {
    const now = new Date();
    mockListAllTasks.mockResolvedValue([
      makeTask({
        id: 10,
        title: "release polish",
        completed_at: new Date(now.getTime() - 60 * 60 * 1000).toISOString(),
      }),
      makeTask({
        id: 11,
        title: "old release",
        completed_at: new Date(now.getTime() - 10 * 24 * 60 * 60 * 1000).toISOString(),
      }),
      makeTask({
        id: 12,
        title: "fresh bug",
        completed_at: new Date(now.getTime() - 30 * 60 * 1000).toISOString(),
      }),
    ]);

    render(<ProjectSettingsPanel project={makeProject()} />);
    fireEvent.change(document.querySelector("[data-approval-policy-name]") as HTMLInputElement, {
      target: { value: "release preview" },
    });
    const condition = document.querySelector("[data-approval-policy-condition]") as HTMLElement;
    const inputs = condition.querySelectorAll("input");
    fireEvent.change(inputs[0], { target: { value: "title" } });
    fireEvent.change(inputs[1], { target: { value: "release" } });

    fireEvent.click(document.querySelector("[data-approval-policy-preview]") as HTMLButtonElement);

    await screen.findByText("1");
    expect(screen.getByText(/#10 release polish/)).toBeInTheDocument();
    expect(screen.queryByText(/#11 old release/)).not.toBeInTheDocument();
  });
});

function makeTask(overrides: Partial<import("@/lib/api").TaskRead> = {}): import("@/lib/api").TaskRead {
  return {
    id: 1,
    project_id: 42,
    parent_task_id: null,
    title: "task",
    description: null,
    process_status: 5,
    priority: 2,
    assigned_role: null,
    run_mode: "manual",
    task_kind: "ai",
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
    created_at: "2026-06-01T00:00:00Z",
    updated_at: "2026-06-01T00:00:00Z",
    started_at: null,
    completed_at: "2026-06-01T01:00:00Z",
    ...overrides,
  };
}
