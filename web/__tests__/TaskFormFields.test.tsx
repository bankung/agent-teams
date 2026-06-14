// #2373 R3 — focused tests for the shared <TaskFormFields> extraction.
//
// Two concerns:
//   (i)  The "Advanced details" disclosure is COLLAPSED by default and reveals
//        the rare fields (model tier / blocked-by / handoff) when expanded — for
//        BOTH consuming modals (data-new-task-* and data-ai-task-*).
//   (ii) The shared common fields render under EACH prefix so every consumer's
//        data-attr is preserved.
//
// Rendered in a separate file (not folded into NewTaskModal.template.test.tsx)
// because the concern is the SHARED component's prefix parametrization + the new
// disclosure behavior across BOTH modals — distinct from the #1310 template
// picker suite, which is NewTaskModal-specific. Keeping them apart keeps each
// file's intent legible.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor, configure } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ProjectRead, TaskRead, ParsedTaskProposal } from "@/lib/api";
import { TaskPriority } from "@/lib/constants";

configure({ asyncUtilTimeout: 5000 });

// ---------- mocks ----------

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

// Sub-pickers that do their own fetches — stub to keep the DOM deterministic.
vi.mock("@/components/ActionTemplatePicker", () => ({
  ActionTemplatePicker: () => null,
}));
vi.mock("@/components/HandoffTemplatePicker", () => ({
  HandoffTemplatePicker: () => (
    <div data-handoff-picker-stub>handoff picker</div>
  ),
}));
vi.mock("@/components/TaskTemplatePicker", () => ({
  TaskTemplatePicker: () => null,
}));

const mockCreateTask = vi.fn();
const mockListTaskTemplates = vi.fn();
const mockListMilestones = vi.fn();
const mockParseTaskText = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    listTaskTemplates: (...a: Parameters<typeof actual.listTaskTemplates>) =>
      mockListTaskTemplates(...a),
    createTask: (...a: Parameters<typeof actual.createTask>) =>
      mockCreateTask(...a),
    listMilestones: (...a: Parameters<typeof actual.listMilestones>) =>
      mockListMilestones(...a),
    parseTaskText: (...a: Parameters<typeof actual.parseTaskText>) =>
      mockParseTaskText(...a),
  };
});

// ---------- fixtures ----------

const MOCK_PROJECT: ProjectRead = {
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
};

const MOCK_PROPOSAL: ParsedTaskProposal = {
  title: "Parsed title",
  description: "Parsed description",
  task_type: "feature",
  priority: TaskPriority.NORMAL,
  assigned_role: null,
  blocked_by: null,
};

function makeCreatedTask(): TaskRead {
  return {
    id: 999,
    project_id: 42,
    parent_task_id: null,
    title: "t",
    description: null,
    process_status: 1,
    priority: 2,
    assigned_role: null,
    run_mode: "manual",
    task_kind: "ai",
    task_type: "feature",
    due_date: null,
    record_status: 1,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    sort_order: null,
    milestone_id: null,
    acceptance_criteria: null,
    is_template: false,
  } as TaskRead;
}

beforeEach(() => {
  vi.clearAllMocks();
  mockListTaskTemplates.mockResolvedValue([]);
  mockListMilestones.mockResolvedValue([]);
  mockCreateTask.mockResolvedValue(makeCreatedTask());
  mockParseTaskText.mockResolvedValue(MOCK_PROPOSAL);
});

// ---------- NewTaskModal (prefix=new-task) ----------

describe("TaskFormFields — NewTaskModal (data-new-task-*)", () => {
  async function renderNew() {
    const { NewTaskModal } = await import("@/components/NewTaskModal");
    const { container } = render(
      <NewTaskModal
        projectId={42}
        project={MOCK_PROJECT}
        externalOpen={true}
        onExternalClose={vi.fn()}
      />,
    );
    // Wait for the title field (shared component) to render.
    await waitFor(() => {
      expect(container.querySelector("[data-new-task-title]")).not.toBeNull();
    });
    return container;
  }

  it("renders the common fields under the new-task prefix", async () => {
    const container = await renderNew();
    for (const suffix of [
      "title",
      "type",
      "priority",
      "role",
      "milestone",
      "due-date",
      "description",
    ]) {
      expect(
        container.querySelector(`[data-new-task-${suffix}]`),
        `data-new-task-${suffix} should render`,
      ).not.toBeNull();
    }
  });

  it("Advanced disclosure is collapsed by default and hides the rare fields' summary state", async () => {
    const container = await renderNew();
    const details = container.querySelector(
      "[data-new-task-advanced]",
    ) as HTMLDetailsElement;
    expect(details).not.toBeNull();
    // Collapsed = the <details> has no `open` attribute.
    expect(details.open).toBe(false);
  });

  it("expanding Advanced reveals model tier, blocked-by, and handoff picker", async () => {
    const container = await renderNew();
    const user = userEvent.setup();

    // The rare fields exist in the DOM (details renders children) — assert the
    // disclosure toggles open on summary click (the user-facing reveal).
    const summary = container.querySelector(
      "[data-new-task-advanced-summary]",
    ) as HTMLElement;
    await user.click(summary);

    const details = container.querySelector(
      "[data-new-task-advanced]",
    ) as HTMLDetailsElement;
    await waitFor(() => expect(details.open).toBe(true));

    expect(
      container.querySelector("[data-new-task-blocked-by]"),
    ).not.toBeNull();
    expect(
      container.querySelector("[data-new-task-model-override]"),
    ).not.toBeNull();
    // Handoff picker (stubbed) lives inside the disclosure.
    expect(container.querySelector("[data-handoff-picker-stub]")).not.toBeNull();
  });
});

// ---------- AiTaskModal (prefix=ai-task) ----------

describe("TaskFormFields — AiTaskModal (data-ai-task-*)", () => {
  // The shared fields live in the PREVIEW phase — parse first to reach it.
  async function renderAiPreview() {
    const { AiTaskModal } = await import("@/components/AiTaskModal");
    const { container } = render(
      <AiTaskModal
        projectId={42}
        project={MOCK_PROJECT}
        externalOpen={true}
        onExternalClose={vi.fn()}
      />,
    );
    const user = userEvent.setup();

    // Phase 1 — type a prompt + parse.
    const textArea = container.querySelector(
      "[data-ai-task-text]",
    ) as HTMLTextAreaElement;
    await user.type(textArea, "make a backend bug task");
    const parseBtn = container.querySelector(
      "[data-ai-task-parse]",
    ) as HTMLButtonElement;
    await user.click(parseBtn);

    // Phase 2 — preview renders the shared fields.
    await waitFor(() => {
      expect(container.querySelector("[data-ai-task-title]")).not.toBeNull();
    });
    return { container, user };
  }

  it("renders the common fields under the ai-task prefix", async () => {
    const { container } = await renderAiPreview();
    for (const suffix of [
      "title",
      "type",
      "priority",
      "role",
      "milestone",
      "due-date",
      "description",
    ]) {
      expect(
        container.querySelector(`[data-ai-task-${suffix}]`),
        `data-ai-task-${suffix} should render`,
      ).not.toBeNull();
    }
  });

  it("Advanced disclosure is collapsed by default", async () => {
    const { container } = await renderAiPreview();
    const details = container.querySelector(
      "[data-ai-task-advanced]",
    ) as HTMLDetailsElement;
    expect(details).not.toBeNull();
    expect(details.open).toBe(false);
  });

  it("expanding Advanced reveals model tier, blocked-by, and handoff picker", async () => {
    const { container, user } = await renderAiPreview();

    const summary = container.querySelector(
      "[data-ai-task-advanced-summary]",
    ) as HTMLElement;
    await user.click(summary);

    const details = container.querySelector(
      "[data-ai-task-advanced]",
    ) as HTMLDetailsElement;
    await waitFor(() => expect(details.open).toBe(true));

    expect(container.querySelector("[data-ai-task-blocked-by]")).not.toBeNull();
    expect(
      container.querySelector("[data-ai-task-model-override]"),
    ).not.toBeNull();
    expect(container.querySelector("[data-handoff-picker-stub]")).not.toBeNull();
  });
});
