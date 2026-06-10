// Kanban #2181 — editable description + acceptance criteria in TaskDetail.
//
// Strategy: render AcEditor (AC editing) and a minimal TaskDetail wrapper for
// description editing, mock @/lib/api (patchTask), and assert all AC cases.
// All async assertions use findBy*/waitFor (never sync querySelector on
// post-async state) per project standard.
//
// Covered:
//   (a) read-only for ps=5 and ps=6 — no edit buttons
//   (b) description edit → save calls patchTask with new text; cancel restores
//   (c) AC: pending→passed stamps operator/verified_at; →pending clears them;
//       add + remove item; empty-text blocked
//   (d) SSE-style props refresh during edit does NOT clobber the draft

import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  fireEvent,
  configure,
} from "@testing-library/react";
import type { AcceptanceCriterion, TaskRead } from "@/lib/api";

configure({ asyncUtilTimeout: 5000 });

// ── api mock ──────────────────────────────────────────────────────────────────
const mockPatchTask = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    patchTask: (...args: Parameters<typeof actual.patchTask>) =>
      mockPatchTask(...args),
    // TaskDetail also calls these — stub them so the component mounts cleanly
    getTaskBlocks: vi.fn().mockResolvedValue([]),
    listMilestones: vi.fn().mockResolvedValue([]),
  };
});

// ── heavy child stubs ─────────────────────────────────────────────────────────
vi.mock("@/components/TaskComments", () => ({
  TaskComments: () => <div data-testid="task-comments-stub" />,
}));
vi.mock("@/components/TaskToolCalls", () => ({
  TaskToolCalls: () => <div data-testid="task-tool-calls-stub" />,
}));
vi.mock("@/components/TaskMuteToggle", () => ({
  TaskMuteToggle: () => <div data-testid="task-mute-stub" />,
}));
vi.mock("@/components/ModelTierSelect", () => ({
  ModelTierSelect: (props: Record<string, unknown>) => (
    <select data-testid="model-tier-select" {...props} />
  ),
}));
vi.mock("@/components/MilestoneCombobox", () => ({
  MilestoneCombobox: () => <div data-testid="milestone-combobox-stub" />,
}));
vi.mock("@/components/DatePicker", () => ({
  DatePicker: () => <div data-testid="date-picker-stub" />,
}));
vi.mock("@/components/DecisionInteractionView", () => ({
  DecisionInteractionView: () => <div />,
}));

// Imported AFTER mocks register
import { AcEditor } from "@/components/AcEditor";
import { TaskDetail } from "@/components/TaskDetail";

// ── helpers ────────────────────────────────────────────────────────────────────
function makeCriterion(over: Partial<AcceptanceCriterion> = {}): AcceptanceCriterion {
  return {
    text: "criterion text",
    status: "pending",
    verified_by: null,
    verified_at: null,
    notes: null,
    ...over,
  };
}

function makeTask(over: Partial<TaskRead> = {}): TaskRead {
  return {
    id: 42,
    project_id: 1,
    parent_task_id: null,
    title: "Test task",
    description: "initial description",
    process_status: 1, // TODO
    priority: 3,
    assigned_role: null,
    run_mode: "manual",
    task_kind: "human",
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
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    started_at: null,
    completed_at: null,
    ...over,
  };
}

beforeEach(() => {
  mockPatchTask.mockReset();
  // Default: resolve with the patched task (caller overrides per test)
  mockPatchTask.mockResolvedValue(makeTask());
});

// ─────────────────────────────────────────────────────────────────────────────
// (a) Read-only for terminal tasks
// ─────────────────────────────────────────────────────────────────────────────
describe("AcEditor — terminal read-only (ps=5 and ps=6)", () => {
  it("renders no edit button when isTerminal=true", () => {
    render(
      <AcEditor
        criteria={[makeCriterion({ text: "ship it", status: "passed" })]}
        isTerminal={true}
        onSave={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: /edit acceptance criteria/i })).toBeNull();
    expect(screen.getByText("ship it")).toBeInTheDocument();
  });

  it("renders no edit button for ps=6 via TaskDetail", async () => {
    const task = makeTask({ process_status: 6, acceptance_criteria: [makeCriterion()] });
    render(
      <TaskDetail
        task={task}
        allTasks={[task]}
        projectId={1}
        onClose={vi.fn()}
        onPatch={vi.fn()}
        onError={vi.fn()}
      />,
    );
    // No edit trigger for AC
    await waitFor(() => {
      expect(document.querySelector("[data-ac-edit-trigger]")).toBeNull();
      expect(document.querySelector("[data-description-edit-trigger]")).toBeNull();
    });
  });

  it("renders no edit button for ps=5 via TaskDetail", async () => {
    const task = makeTask({ process_status: 5, acceptance_criteria: [makeCriterion()] });
    render(
      <TaskDetail
        task={task}
        allTasks={[task]}
        projectId={1}
        onClose={vi.fn()}
        onPatch={vi.fn()}
        onError={vi.fn()}
      />,
    );
    await waitFor(() => {
      expect(document.querySelector("[data-ac-edit-trigger]")).toBeNull();
      expect(document.querySelector("[data-description-edit-trigger]")).toBeNull();
    });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// (b) Description editing
// ─────────────────────────────────────────────────────────────────────────────
describe("TaskDetail — description edit", () => {
  it("save calls patchTask with the new text", async () => {
    const task = makeTask({ description: "old description" });
    const patchedTask = makeTask({ description: "new description" });
    mockPatchTask.mockResolvedValue(patchedTask);
    const onPatch = vi.fn();

    render(
      <TaskDetail
        task={task}
        allTasks={[task]}
        projectId={1}
        onClose={vi.fn()}
        onPatch={onPatch}
        onError={vi.fn()}
      />,
    );

    // Click Edit
    const editBtn = await screen.findByRole("button", { name: /edit description/i });
    fireEvent.click(editBtn);

    // Change text
    const textarea = document.querySelector("[data-description-textarea]") as HTMLTextAreaElement;
    expect(textarea).not.toBeNull();
    fireEvent.change(textarea, { target: { value: "new description" } });

    // Save
    fireEvent.click(document.querySelector("[data-description-save]")!);

    await waitFor(() => {
      expect(mockPatchTask).toHaveBeenCalledWith(1, 42, {
        description: "new description",
      });
    });
    expect(onPatch).toHaveBeenCalledWith(patchedTask);
  });

  it("cancel restores the original description", async () => {
    const task = makeTask({ description: "original" });
    render(
      <TaskDetail
        task={task}
        allTasks={[task]}
        projectId={1}
        onClose={vi.fn()}
        onPatch={vi.fn()}
        onError={vi.fn()}
      />,
    );

    const editBtn = await screen.findByRole("button", { name: /edit description/i });
    fireEvent.click(editBtn);

    const textarea = document.querySelector("[data-description-textarea]") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "discard me" } });

    fireEvent.click(document.querySelector("[data-description-cancel]")!);

    // Edit mode closed; original text shown
    await waitFor(() => {
      expect(document.querySelector("[data-description-textarea]")).toBeNull();
    });
    expect(screen.getByText("original")).toBeInTheDocument();
    expect(mockPatchTask).not.toHaveBeenCalled();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// (c) Acceptance criteria editing
// ─────────────────────────────────────────────────────────────────────────────
describe("AcEditor — AC edit interactions", () => {
  it("pending→passed stamps operator/verified_at in save payload", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const criterion = makeCriterion({ text: "do the thing", status: "pending" });

    render(
      <AcEditor
        criteria={[criterion]}
        isTerminal={false}
        onSave={onSave}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /edit acceptance criteria/i }));

    // Change status to "passed"
    const select = document.querySelector("[data-ac-status-select='0']") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "passed" } });

    fireEvent.click(document.querySelector("[data-ac-save]")!);

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const [saved] = onSave.mock.calls[0] as [AcceptanceCriterion[]];
    expect(saved[0].status).toBe("passed");
    expect(saved[0].verified_by).toBe("operator");
    expect(saved[0].verified_at).toBeTruthy();
  });

  it("→pending clears verified_by and verified_at", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const criterion = makeCriterion({
      text: "already passed",
      status: "passed",
      verified_by: "operator",
      verified_at: "2026-01-01T00:00:00.000Z",
    });

    render(
      <AcEditor
        criteria={[criterion]}
        isTerminal={false}
        onSave={onSave}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /edit acceptance criteria/i }));

    const select = document.querySelector("[data-ac-status-select='0']") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "pending" } });

    fireEvent.click(document.querySelector("[data-ac-save]")!);

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const [saved] = onSave.mock.calls[0] as [AcceptanceCriterion[]];
    expect(saved[0].status).toBe("pending");
    expect(saved[0].verified_by).toBeNull();
    expect(saved[0].verified_at).toBeNull();
  });

  it("adds an item that is reflected in the save payload", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);

    render(
      <AcEditor
        criteria={[makeCriterion({ text: "existing" })]}
        isTerminal={false}
        onSave={onSave}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /edit acceptance criteria/i }));
    fireEvent.click(screen.getByRole("button", { name: /add acceptance criterion/i }));

    // Fill in new item text (index 1)
    const newInput = document.querySelector("[data-ac-text-input='1']") as HTMLTextAreaElement;
    fireEvent.change(newInput, { target: { value: "new criterion" } });

    fireEvent.click(document.querySelector("[data-ac-save]")!);

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const [saved] = onSave.mock.calls[0] as [AcceptanceCriterion[]];
    expect(saved).toHaveLength(2);
    expect(saved[1].text).toBe("new criterion");
    expect(saved[1].status).toBe("pending");
  });

  it("removes an item after confirm — save payload has one fewer entry", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("confirm", () => true);

    render(
      <AcEditor
        criteria={[
          makeCriterion({ text: "keep me" }),
          makeCriterion({ text: "remove me" }),
        ]}
        isTerminal={false}
        onSave={onSave}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /edit acceptance criteria/i }));

    // Remove the second item
    fireEvent.click(document.querySelector("[data-ac-remove='1']")!);

    fireEvent.click(document.querySelector("[data-ac-save]")!);

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const [saved] = onSave.mock.calls[0] as [AcceptanceCriterion[]];
    expect(saved).toHaveLength(1);
    expect(saved[0].text).toBe("keep me");

    vi.unstubAllGlobals();
  });

  it("blocks save when any item has empty text, shows inline hint", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);

    render(
      <AcEditor
        criteria={[makeCriterion({ text: "valid" })]}
        isTerminal={false}
        onSave={onSave}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /edit acceptance criteria/i }));
    // Add an empty item
    fireEvent.click(screen.getByRole("button", { name: /add acceptance criterion/i }));

    // Attempt save without filling the new item
    fireEvent.click(document.querySelector("[data-ac-save]")!);

    // Save was NOT called
    expect(onSave).not.toHaveBeenCalled();
    // Inline hint visible
    expect(await screen.findByText(/text is required/i)).toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// (d) SSE-refresh safety: props update during edit must not clobber draft
// ─────────────────────────────────────────────────────────────────────────────
describe("AcEditor — SSE-refresh safety", () => {
  it("does not overwrite draft when criteria prop changes while editing", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const initial = [makeCriterion({ text: "original" })];

    const { rerender } = render(
      <AcEditor criteria={initial} isTerminal={false} onSave={onSave} />,
    );

    // Enter edit mode
    fireEvent.click(screen.getByRole("button", { name: /edit acceptance criteria/i }));

    // Type new text
    const input = document.querySelector("[data-ac-text-input='0']") as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: "my draft" } });

    // Simulate SSE refresh: parent re-renders with updated criteria from server
    const serverRefreshed = [makeCriterion({ text: "server updated" })];
    rerender(
      <AcEditor criteria={serverRefreshed} isTerminal={false} onSave={onSave} />,
    );

    // Draft should still show our in-progress text
    const inputAfter = document.querySelector("[data-ac-text-input='0']") as HTMLTextAreaElement;
    expect(inputAfter.value).toBe("my draft");
  });
});
