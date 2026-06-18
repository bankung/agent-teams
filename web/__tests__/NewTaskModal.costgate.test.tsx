// Kanban #1304 — cost-forecast gate flow inside NewTaskModal (Option A).
//
// Strategy: render the modal open (externalOpen=true) with a `project` carrying
// a `cost_forecast_threshold_usd`, mock @/lib/api (createTask + costForecast +
// patchTask + deleteTask + the two list fetches), fill the title, click Create,
// and assert whether the confirm sub-modal ([data-cost-gate]) appears and which
// follow-up API the three buttons call.
//
// Determinism: every post-await assertion goes through findBy/waitFor (never a
// sync querySelector after a click) per the repo's RTL flake rule (#1310).
// asyncUtilTimeout is raised to 5 s so waitFor survives full-suite CPU load.
// Queries use document.body (not the render container) because ModalShell
// portals to document.body.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor, within, configure } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ProjectRead, TaskRead, CostForecastResult } from "@/lib/api";

configure({ asyncUtilTimeout: 5000 });

// ---------- mocks ----------

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

// Stub heavy sub-pickers that do their own fetches and are tested elsewhere.
vi.mock("@/components/ActionTemplatePicker", () => ({
  ActionTemplatePicker: () => null,
}));
vi.mock("@/components/HandoffTemplatePicker", () => ({
  HandoffTemplatePicker: () => null,
}));

const mockCreateTask = vi.fn();
const mockCostForecast = vi.fn();
const mockPatchTask = vi.fn();
const mockDeleteTask = vi.fn();
const mockListTaskTemplates = vi.fn();
const mockListMilestones = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    createTask: (...a: Parameters<typeof actual.createTask>) => mockCreateTask(...a),
    costForecast: (...a: Parameters<typeof actual.costForecast>) =>
      mockCostForecast(...a),
    patchTask: (...a: Parameters<typeof actual.patchTask>) => mockPatchTask(...a),
    deleteTask: (...a: Parameters<typeof actual.deleteTask>) => mockDeleteTask(...a),
    listTaskTemplates: (...a: Parameters<typeof actual.listTaskTemplates>) =>
      mockListTaskTemplates(...a),
    listMilestones: (...a: Parameters<typeof actual.listMilestones>) =>
      mockListMilestones(...a),
  };
});

// ---------- fixtures ----------

function makeProject(threshold: number | null): ProjectRead {
  return {
    id: 42,
    name: "agent-teams",
    description: null,
    paths_web: "/web",
    paths_api: "/api",
    paths_db: "postgres",
    stack_web: "next",
    stack_api: "fastapi",
    stack_db: "postgres",
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
    is_paused: false,
    cost_forecast_threshold_usd: threshold,
  };
}

function makeCreatedTask(): TaskRead {
  return {
    id: 999,
    project_id: 42,
    parent_task_id: null,
    title: "Test task",
    description: null,
    process_status: 1,
    priority: 2,
    assigned_role: null,
    run_mode: "manual",
    task_kind: "ai",
    task_type: "feature",
    due_date: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    sort_order: null,
    milestone_id: null,
    acceptance_criteria: null,
    is_template: false,
  } as TaskRead;
}

function makeForecast(estimatedUsd: number): CostForecastResult {
  return {
    estimated_usd: estimatedUsd,
    estimated_tokens: 12345,
    breakdown: {
      prompt: 1000,
      role_brief: 2000,
      attached_resources: 9345,
      completion: 4000,
    },
    confidence: "high",
  };
}

// ---------- helper ----------

// Render the modal open + wait for the title field (shared TaskFormFields)
// to mount, then return onClose for call assertions.
async function renderOpen(project: ProjectRead) {
  const { NewTaskModal } = await import("@/components/NewTaskModal");
  const onClose = vi.fn();
  render(
    <NewTaskModal
      projectId={project.id}
      project={project}
      externalOpen={true}
      onExternalClose={onClose}
    />,
  );
  await waitFor(() => {
    if (!document.body.querySelector("[data-new-task-title]")) {
      throw new Error("title field not rendered yet");
    }
  });
  return { onClose };
}

// Fill the required title then click Create. Description optional (used by the
// Use-Sample assertion to verify the directive appends to operator text).
async function fillAndSubmit(
  user: ReturnType<typeof userEvent.setup>,
  opts: { title?: string; description?: string } = {},
) {
  const title = document.body.querySelector(
    "[data-new-task-title]",
  ) as HTMLInputElement;
  await user.type(title, opts.title ?? "Forecast me");
  if (opts.description !== undefined) {
    const desc = document.body.querySelector(
      "[data-new-task-description]",
    ) as HTMLTextAreaElement | null;
    if (desc) await user.type(desc, opts.description);
  }
  const submit = document.body.querySelector(
    "[data-new-task-submit]",
  ) as HTMLButtonElement;
  await user.click(submit);
}

// ---------- tests ----------

describe("NewTaskModal — cost-forecast gate (#1304)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockListTaskTemplates.mockResolvedValue([]);
    mockListMilestones.mockResolvedValue([]);
    mockCreateTask.mockResolvedValue(makeCreatedTask());
    mockPatchTask.mockResolvedValue(makeCreatedTask());
    mockDeleteTask.mockResolvedValue(undefined);
  });

  // (a) estimated_usd > threshold -> confirm modal appears.
  it("shows the confirm modal when the forecast exceeds the threshold", async () => {
    const user = userEvent.setup();
    mockCostForecast.mockResolvedValue(makeForecast(3.5)); // > 1.00
    await renderOpen(makeProject(1.0));

    await fillAndSubmit(user);

    // Forecast was requested for the freshly-created task id.
    await waitFor(() => {
      expect(mockCostForecast).toHaveBeenCalledWith(42, 999);
    });
    // The gate sub-modal renders with the $ heading (light-tech, no "tokens").
    const gate = await waitFor(() => {
      const el = document.body.querySelector("[data-cost-gate]");
      if (!el) throw new Error("cost gate not rendered yet");
      return el as HTMLElement;
    });
    const heading = within(gate).getByText(/estimated cost \$3\.50/i);
    expect(heading).toBeInTheDocument();
    // No tokens leak into operator-facing copy.
    expect(gate.textContent ?? "").not.toMatch(/token/i);
    // All three actions present.
    expect(document.body.querySelector("[data-cost-gate-runfull]")).not.toBeNull();
    expect(document.body.querySelector("[data-cost-gate-sample]")).not.toBeNull();
    expect(document.body.querySelector("[data-cost-gate-cancel]")).not.toBeNull();
    // Gate is reached only AFTER create succeeded — no follow-up writes yet.
    expect(mockPatchTask).not.toHaveBeenCalled();
    expect(mockDeleteTask).not.toHaveBeenCalled();
  });

  // (b) threshold === null -> NO modal, closes normally.
  it("does NOT show the modal (and never forecasts) when the threshold is null", async () => {
    const user = userEvent.setup();
    mockCostForecast.mockResolvedValue(makeForecast(99));
    const { onClose } = await renderOpen(makeProject(null));

    await fillAndSubmit(user);

    // Create fired; the modal closed via onExternalClose.
    await waitFor(() => {
      expect(mockCreateTask).toHaveBeenCalledTimes(1);
      expect(onClose).toHaveBeenCalled();
    });
    // No gate, and the forecast endpoint is never even called when ungated.
    expect(document.body.querySelector("[data-cost-gate]")).toBeNull();
    expect(mockCostForecast).not.toHaveBeenCalled();
  });

  // (c) estimated_usd <= threshold -> NO modal.
  it("does NOT show the modal when the forecast is at/below the threshold", async () => {
    const user = userEvent.setup();
    mockCostForecast.mockResolvedValue(makeForecast(1.0)); // == 1.00 (not >)
    const { onClose } = await renderOpen(makeProject(1.0));

    await fillAndSubmit(user);

    // Forecast ran, but the equal-to-ceiling case does NOT gate.
    await waitFor(() => {
      expect(mockCostForecast).toHaveBeenCalledWith(42, 999);
      expect(onClose).toHaveBeenCalled();
    });
    expect(document.body.querySelector("[data-cost-gate]")).toBeNull();
  });

  // (d) Use Sample -> PATCH called with the appended directive.
  it("Use Sample PATCHes the task description with the sample directive", async () => {
    const user = userEvent.setup();
    mockCostForecast.mockResolvedValue(makeForecast(5));
    await renderOpen(makeProject(1.0));

    await fillAndSubmit(user, { description: "Crunch the dataset" });

    const sampleBtn = await waitFor(() => {
      const el = document.body.querySelector("[data-cost-gate-sample]");
      if (!el) throw new Error("sample button not rendered yet");
      return el as HTMLButtonElement;
    });
    await user.click(sampleBtn);

    await waitFor(() => {
      expect(mockPatchTask).toHaveBeenCalledTimes(1);
    });
    const [pid, tid, body] = mockPatchTask.mock.calls[0];
    expect(pid).toBe(42);
    expect(tid).toBe(999);
    // Directive appended on top of the operator's typed description.
    expect(body.description).toContain("Crunch the dataset");
    expect(body.description).toContain("first 100 rows of attached files only");
    // Use Sample is not a delete.
    expect(mockDeleteTask).not.toHaveBeenCalled();
  });

  // (e) Cancel -> DELETE called on the new task id.
  it("Cancel soft-deletes the just-created task", async () => {
    const user = userEvent.setup();
    mockCostForecast.mockResolvedValue(makeForecast(5));
    await renderOpen(makeProject(1.0));

    await fillAndSubmit(user);

    const cancelBtn = await waitFor(() => {
      const el = document.body.querySelector("[data-cost-gate-cancel]");
      if (!el) throw new Error("cancel button not rendered yet");
      return el as HTMLButtonElement;
    });
    await user.click(cancelBtn);

    await waitFor(() => {
      expect(mockDeleteTask).toHaveBeenCalledWith(42, 999);
    });
    // Cancel does not patch.
    expect(mockPatchTask).not.toHaveBeenCalled();
  });

  // (f) Use Sample with EMPTY description -> directive must NOT start with "\n".
  it("Use Sample sends the directive without a leading newline when description is empty", async () => {
    const user = userEvent.setup();
    mockCostForecast.mockResolvedValue(makeForecast(5));
    await renderOpen(makeProject(1.0));

    // Submit with no description typed (title only).
    await fillAndSubmit(user);

    const sampleBtn = await waitFor(() => {
      const el = document.body.querySelector("[data-cost-gate-sample]");
      if (!el) throw new Error("sample button not rendered yet");
      return el as HTMLButtonElement;
    });
    await user.click(sampleBtn);

    await waitFor(() => {
      expect(mockPatchTask).toHaveBeenCalledTimes(1);
    });
    const [, , body] = mockPatchTask.mock.calls[0];
    // Must contain the directive text.
    expect(body.description).toContain("first 100 rows of attached files only");
    // Must NOT start with a newline (the empty-base bug).
    expect(body.description).not.toMatch(/^\n/);
  });

  // Resilience: a forecast error AFTER create must NOT trap the operator or lose
  // the task — it falls through to the normal close (no gate).
  it("falls through to a normal close when the forecast call throws", async () => {
    const user = userEvent.setup();
    mockCostForecast.mockRejectedValue(new Error("boom"));
    const { onClose } = await renderOpen(makeProject(1.0));

    await fillAndSubmit(user);

    await waitFor(() => {
      expect(mockCreateTask).toHaveBeenCalledTimes(1);
      expect(onClose).toHaveBeenCalled();
    });
    expect(document.body.querySelector("[data-cost-gate]")).toBeNull();
  });
});
