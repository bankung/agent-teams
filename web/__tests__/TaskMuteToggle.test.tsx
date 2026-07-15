// TaskMuteToggle — Kanban #2842.
//
// onToggle's optimistic-apply and revert-on-error calls to onPatch must only
// ever intentionally change nudge_disabled. Both used to spread `...task` —
// a snapshot pinned to the closure as of click time — wholesale. If a
// concurrent PATCH for another field on the same task lands (re-rendering
// this component with a fresh `task` prop, mirroring an SSE-driven
// router.refresh()) while our own mute PATCH is still in flight, splicing
// back the stale closure on revert would discard it.
//
// Pattern: TaskDetailEditing.test.tsx's "SSE-refresh safety" tests use the
// same rerender()-mid-flight approach to simulate a concurrent prop update.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { TaskRead } from "@/lib/api";

const mockPatchTask = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    patchTask: (...args: unknown[]) => mockPatchTask(...args),
  };
});

import { TaskMuteToggle } from "@/components/TaskMuteToggle";

function makeTask(overrides: Partial<TaskRead> = {}): TaskRead {
  return {
    id: 1,
    project_id: 1,
    parent_task_id: null,
    title: "task-1",
    description: null,
    process_status: 2,
    priority: 2,
    assigned_role: null,
    run_mode: "auto",
    task_kind: "human",
    task_type: "feature",
    due_date: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    started_at: null,
    completed_at: null,
    sort_order: null,
    milestone_id: null,
    acceptance_criteria: null,
    is_template: false,
    is_pending: false,
    recurrence_rule: null,
    recurrence_timezone: "UTC",
    next_fire_at: null,
    spawned_from_task_id: null,
    scheduled_at: null,
    blocked_by: null,
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
    nudge_disabled: false,
    ...overrides,
  } as TaskRead;
}

describe("TaskMuteToggle — revert preserves concurrent edits (Kanban #2842)", () => {
  beforeEach(() => {
    mockPatchTask.mockReset();
  });

  it("reverts only nudge_disabled on a failed toggle, preserving a concurrent field change", async () => {
    const task = makeTask({ nudge_disabled: false, title: "before-concurrent-edit" });
    const onPatch = vi.fn();
    const onError = vi.fn();

    let rejectPatch: (err: unknown) => void = () => {};
    mockPatchTask.mockImplementationOnce(
      () =>
        new Promise((_resolve, reject) => {
          rejectPatch = reject;
        }),
    );

    const { rerender } = render(
      <TaskMuteToggle task={task} projectId={1} onPatch={onPatch} onError={onError} />,
    );

    fireEvent.click(screen.getByRole("switch"));

    // Optimistic apply fires synchronously.
    await waitFor(() => expect(onPatch).toHaveBeenCalledTimes(1));
    expect(onPatch).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({ nudge_disabled: true, title: "before-concurrent-edit" }),
    );
    expect(mockPatchTask).toHaveBeenCalledTimes(1);

    // A concurrent agent PATCH lands (unrelated field) while ours is in
    // flight — Board merges it and TaskDetail re-renders this component with
    // a fresh `task` prop.
    const concurrentlyUpdated = { ...task, title: "concurrent-title-change" };
    rerender(
      <TaskMuteToggle
        task={concurrentlyUpdated}
        projectId={1}
        onPatch={onPatch}
        onError={onError}
      />,
    );

    // Our own mute PATCH now fails (e.g. a 409 conflict).
    rejectPatch(new Error("stale write"));

    await waitFor(() => expect(onPatch).toHaveBeenCalledTimes(2));
    // Revert call: nudge_disabled goes back to false, but the concurrent
    // title change must survive — never splice back the stale click-time task.
    expect(onPatch).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ nudge_disabled: false, title: "concurrent-title-change" }),
    );
    expect(onError).toHaveBeenCalledTimes(1);
  });

  it("flips optimistically on click and confirms via the server response", async () => {
    const task = makeTask({ nudge_disabled: false });
    const onPatch = vi.fn();
    const onError = vi.fn();
    const serverResponse = makeTask({ nudge_disabled: true });
    mockPatchTask.mockResolvedValueOnce(serverResponse);

    render(<TaskMuteToggle task={task} projectId={1} onPatch={onPatch} onError={onError} />);

    fireEvent.click(screen.getByRole("switch"));

    await waitFor(() => expect(onPatch).toHaveBeenCalledTimes(2));
    expect(onPatch).toHaveBeenNthCalledWith(1, expect.objectContaining({ nudge_disabled: true }));
    expect(onPatch).toHaveBeenNthCalledWith(2, serverResponse);
    expect(onError).not.toHaveBeenCalled();
  });
});
