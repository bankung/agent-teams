// Kanban #2827: ListView re-declares its own STATUS_LABEL / STATUS_CLASS /
// STATUS_OPTIONS maps (rather than importing a shared one) and all three used
// to OMIT TaskStatus.CANCELLED(6) — a cancelled task rendered the literal
// numeric fallback "6" in the Status column, and the status filter row had no
// way to select cancelled tasks. This locks: (a) CANCELLED renders "Cancelled"
// specifically, and (b) every TaskStatus value renders its expected label —
// so a future status added to lib/constants.ts without a matching ListView
// entry fails this test instead of leaking a raw number into the UI.
//
// NOTE: CrossProjectActiveTasksList's narrower StatusChip map is intentionally
// NOT touched/asserted here — it's a documented, test-locked exception (#2429)
// that only covers IN_PROGRESS/REVIEW/BLOCKED by design.

import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import type { TaskRead } from "@/lib/api";
import { TaskStatus } from "@/lib/constants";
import { ListView } from "@/components/ListView";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
let nextId = 1;

function makeTask(overrides: Partial<TaskRead> = {}): TaskRead {
  return {
    id: nextId++,
    project_id: 1,
    parent_task_id: null,
    title: "test task",
    description: null,
    process_status: TaskStatus.TODO,
    priority: 2,
    assigned_role: null,
    run_mode: "manual",
    task_kind: "human",
    task_type: "feature",
    due_date: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
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
    ...overrides,
  } as TaskRead;
}

// Mirrors ListView's own (private, unexported) STATUS_LABEL map. Duplicated
// here deliberately — the point of this test is to lock the exact label per
// status value, so every TaskStatus member must be listed explicitly.
const EXPECTED_STATUS_LABEL: Record<number, string> = {
  [TaskStatus.TODO]: "TODO",
  [TaskStatus.IN_PROGRESS]: "In progress",
  [TaskStatus.REVIEW]: "Review",
  [TaskStatus.BLOCKED]: "Blocked",
  [TaskStatus.DONE]: "Done",
  [TaskStatus.CANCELLED]: "Cancelled",
  [TaskStatus.HALTED_PENDING_USER]: "Halted / Pending user",
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ListView — status maps cover every TaskStatus value (#2827)", () => {
  it("renders 'Cancelled' — not the literal '6' — for a CANCELLED task", () => {
    const task = makeTask({ id: 900, title: "cancelled row", process_status: TaskStatus.CANCELLED });
    render(<ListView tasks={[task]} onOpenDetail={() => {}} />);

    const row = screen.getByText("cancelled row").closest("tr");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText("Cancelled")).toBeInTheDocument();
    expect(within(row as HTMLElement).queryByText("6")).not.toBeInTheDocument();
  });

  it("every TaskStatus value renders its expected label (no numeric fallback leaks)", () => {
    const statusValues = Object.values(TaskStatus);
    // Sanity: this suite's expectation table must itself cover every value —
    // otherwise the test below would silently pass with gaps.
    expect(Object.keys(EXPECTED_STATUS_LABEL).map(Number).sort()).toEqual(
      [...statusValues].sort((a, b) => a - b),
    );

    const tasks = statusValues.map((status) =>
      makeTask({ title: `status task ${status}`, process_status: status }),
    );
    render(<ListView tasks={tasks} onOpenDetail={() => {}} />);

    for (const status of statusValues) {
      const row = screen.getByText(`status task ${status}`).closest("tr");
      expect(row).not.toBeNull();
      const expectedLabel = EXPECTED_STATUS_LABEL[status];
      expect(within(row as HTMLElement).getByText(expectedLabel)).toBeInTheDocument();
      // The raw numeric fallback (`String(status)`) must never appear as the
      // status pill's own text — HALTED_PENDING_USER=8 vs "8" etc.
      expect(within(row as HTMLElement).queryByText(String(status))).not.toBeInTheDocument();
    }
  });

  it("status filter row includes a 'Cancelled' chip", () => {
    render(<ListView tasks={[]} onOpenDetail={() => {}} />);
    expect(screen.getByRole("button", { name: "Cancelled" })).toBeInTheDocument();
  });
});
