// Kanban #2703 — board-card run-type toggle (human ↔ auto).
//
// Strategy: render TaskCard, mock @/lib/api (patchTask), and assert the toggle
// behaviour. @dnd-kit/sortable's useSortable is mocked to a static no-op return
// (the card only consumes its values; we don't exercise drag here). All async
// assertions use findBy*/waitFor (never sync querySelector on post-async state)
// per the project FE-determinism standard.
//
// Covered:
//   (a) human → auto sends ONE PATCH { task_kind:"ai", run_mode:"auto_pickup" }
//   (b) auto → human sends ONE PATCH { task_kind:"human", run_mode:"manual" }
//   (c) AC4 hidden: interaction_kind question/decision → no toggle
//   (d) AC4 hidden: process_status DONE(5) / CANCELLED(6) → no toggle
//   (e) hidden when onPatch / projectId not wired (read-only consumers)
//   (f) optimistic update + revert-on-error (aria-checked flips back, onError fires)
//   (g) clicking a segment does NOT bubble to the card's onOpenDetail

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, configure } from "@testing-library/react";
import type { TaskRead } from "@/lib/api";

configure({ asyncUtilTimeout: 5000 });

// ── api mock ──────────────────────────────────────────────────────────────────
const mockPatchTask = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    patchTask: (...args: Parameters<typeof actual.patchTask>) => mockPatchTask(...args),
  };
});

// ── dnd-kit stub ───────────────────────────────────────────────────────────────
// The card calls useSortable purely for transform/listeners; a static return
// lets us render it without a DndContext provider.
vi.mock("@dnd-kit/sortable", () => ({
  useSortable: () => ({
    attributes: {},
    listeners: {},
    setNodeRef: () => {},
    transform: null,
    transition: undefined,
    isDragging: false,
  }),
}));

// Heavy / unrelated child stubs.
vi.mock("@/components/TaskActivityStrip", () => ({
  TaskActivityStrip: () => <div data-testid="activity-strip-stub" />,
}));

// Imported AFTER mocks register.
import { TaskCard } from "@/components/TaskCard";

// ── helpers ────────────────────────────────────────────────────────────────────
function makeTask(over: Partial<TaskRead> = {}): TaskRead {
  return {
    id: 42,
    project_id: 1,
    parent_task_id: null,
    title: "Test task",
    description: null,
    process_status: 1, // TODO
    priority: 2,
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

function renderCard(over: Partial<TaskRead> = {}, props: Record<string, unknown> = {}) {
  const task = makeTask(over);
  const onPatch = vi.fn();
  const onError = vi.fn();
  const onOpenDetail = vi.fn();
  render(
    <TaskCard
      task={task}
      projectId={1}
      onPatch={onPatch}
      onError={onError}
      onOpenDetail={onOpenDetail}
      {...props}
    />,
  );
  return { task, onPatch, onError, onOpenDetail };
}

beforeEach(() => {
  mockPatchTask.mockReset();
  mockPatchTask.mockResolvedValue(makeTask());
});

// ─────────────────────────────────────────────────────────────────────────────
// (a)/(b) Atomic PATCH payload — both fields together, each direction
// ─────────────────────────────────────────────────────────────────────────────
describe("TaskCard run-type toggle — atomic PATCH payload", () => {
  it("human → auto sends ONE PATCH { task_kind:'ai', run_mode:'auto_pickup' }", async () => {
    mockPatchTask.mockResolvedValue(makeTask({ task_kind: "ai", run_mode: "auto_pickup" }));
    const { onPatch } = renderCard({ task_kind: "human", run_mode: "manual" });

    fireEvent.click(screen.getByRole("radio", { name: /auto/i }));

    await waitFor(() => expect(mockPatchTask).toHaveBeenCalledTimes(1));
    expect(mockPatchTask).toHaveBeenCalledWith(1, 42, {
      task_kind: "ai",
      run_mode: "auto_pickup",
    });
    await waitFor(() => expect(onPatch).toHaveBeenCalledTimes(1));
  });

  it("auto → human sends ONE PATCH { task_kind:'human', run_mode:'manual' }", async () => {
    mockPatchTask.mockResolvedValue(makeTask({ task_kind: "human", run_mode: "manual" }));
    const { onPatch } = renderCard({ task_kind: "ai", run_mode: "auto_pickup" });

    fireEvent.click(screen.getByRole("radio", { name: /human/i }));

    await waitFor(() => expect(mockPatchTask).toHaveBeenCalledTimes(1));
    expect(mockPatchTask).toHaveBeenCalledWith(1, 42, {
      task_kind: "human",
      run_mode: "manual",
    });
    await waitFor(() => expect(onPatch).toHaveBeenCalledTimes(1));
  });

  it("clicking the already-active segment is a no-op (no PATCH)", async () => {
    renderCard({ task_kind: "human", run_mode: "manual" });
    fireEvent.click(screen.getByRole("radio", { name: /human/i }));
    // give any async a tick; nothing should fire
    await Promise.resolve();
    expect(mockPatchTask).not.toHaveBeenCalled();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// (c)/(d)/(e) AC4 hidden / disabled predicate
// ─────────────────────────────────────────────────────────────────────────────
describe("TaskCard run-type toggle — hidden states (AC4)", () => {
  it("hidden for interaction_kind=question", () => {
    renderCard({ interaction_kind: "question" });
    expect(document.querySelector("[data-run-type-toggle]")).toBeNull();
  });

  it("hidden for interaction_kind=decision", () => {
    renderCard({ interaction_kind: "decision" });
    expect(document.querySelector("[data-run-type-toggle]")).toBeNull();
  });

  it("hidden for process_status=5 (DONE)", () => {
    renderCard({ process_status: 5 });
    expect(document.querySelector("[data-run-type-toggle]")).toBeNull();
  });

  it("hidden for process_status=6 (CANCELLED)", () => {
    renderCard({ process_status: 6 });
    expect(document.querySelector("[data-run-type-toggle]")).toBeNull();
  });

  it("hidden when onPatch is not wired (read-only consumer)", () => {
    const task = makeTask();
    render(<TaskCard task={task} projectId={1} />);
    expect(document.querySelector("[data-run-type-toggle]")).toBeNull();
  });

  it("hidden when projectId is not wired", () => {
    const task = makeTask();
    render(<TaskCard task={task} onPatch={vi.fn()} />);
    expect(document.querySelector("[data-run-type-toggle]")).toBeNull();
  });

  it("visible for a work task in a non-terminal status", () => {
    renderCard({ interaction_kind: "work", process_status: 1 });
    expect(document.querySelector("[data-run-type-toggle]")).not.toBeNull();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// (f) Optimistic update + revert-on-error
// ─────────────────────────────────────────────────────────────────────────────
describe("TaskCard run-type toggle — optimistic update + revert", () => {
  it("optimistically flips aria-checked, then reverts + surfaces error on PATCH failure", async () => {
    mockPatchTask.mockRejectedValue(new Error("boom"));
    const { onPatch, onError } = renderCard({ task_kind: "human", run_mode: "manual" });

    const autoBtn = screen.getByRole("radio", { name: /auto/i });
    expect(autoBtn).toHaveAttribute("aria-checked", "false");

    fireEvent.click(autoBtn);

    // Optimistic: auto becomes checked immediately.
    await waitFor(() =>
      expect(screen.getByRole("radio", { name: /auto/i })).toHaveAttribute(
        "aria-checked",
        "true",
      ),
    );

    // Revert: after the rejection settles, human is checked again.
    await waitFor(() =>
      expect(screen.getByRole("radio", { name: /human/i })).toHaveAttribute(
        "aria-checked",
        "true",
      ),
    );
    expect(screen.getByRole("radio", { name: /auto/i })).toHaveAttribute(
      "aria-checked",
      "false",
    );
    expect(onError).toHaveBeenCalledWith(expect.stringContaining("Task #42"));
    expect(onPatch).not.toHaveBeenCalled();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// (g) Event isolation — toggle click must not open the detail drawer
// ─────────────────────────────────────────────────────────────────────────────
describe("TaskCard run-type toggle — event isolation", () => {
  it("clicking a segment does not call onOpenDetail", async () => {
    const { onOpenDetail } = renderCard({ task_kind: "human", run_mode: "manual" });

    fireEvent.click(screen.getByRole("radio", { name: /auto/i }));

    await waitFor(() => expect(mockPatchTask).toHaveBeenCalledTimes(1));
    expect(onOpenDetail).not.toHaveBeenCalled();
  });
});
