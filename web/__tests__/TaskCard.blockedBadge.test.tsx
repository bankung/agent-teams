// Tests for #2412: TaskCard suppresses the "blocked by" chip when the
// blocker is terminal (DONE/CANCELLED) or absent from the loaded set.
//
// TaskCard is a pure presentational component once useSortable is mocked
// (no network, no context) — tests run without DOM complexity.

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { TaskRead } from "@/lib/api";
import { TaskStatus } from "@/lib/constants";
import { TaskCard } from "@/components/TaskCard";

// ---------------------------------------------------------------------------
// Mocks — isolate dnd-kit and Icon (no SVG stubs needed in jsdom).
// ---------------------------------------------------------------------------
vi.mock("@dnd-kit/sortable", () => ({
  useSortable: () => ({
    attributes: {},
    listeners: {},
    setNodeRef: () => undefined,
    transform: null,
    transition: undefined,
    isDragging: false,
  }),
}));

vi.mock("@dnd-kit/utilities", () => ({
  CSS: { Transform: { toString: () => "" } },
}));

vi.mock("@/components/Icon", () => ({ Icon: () => null }));
vi.mock("@/components/RunModeBadge", () => ({ RunModeBadge: () => null }));
vi.mock("@/components/TaskKindBadge", () => ({ TaskKindBadge: () => null }));
vi.mock("@/components/PendingBadge", () => ({ PendingBadge: () => null }));
vi.mock("@/components/RecurrenceIndicator", () => ({ RecurrenceIndicator: () => null }));
vi.mock("@/components/StepCounter", () => ({ StepCounter: () => null }));
vi.mock("@/components/TaskActivityStrip", () => ({ TaskActivityStrip: () => null }));

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

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("TaskCard — blocked badge (#2412)", () => {
  it("shows chip when blocker is non-terminal and present in blockingTaskIds", () => {
    const blockerTask = makeTask({ id: 200, process_status: TaskStatus.IN_PROGRESS });
    const blocked = makeTask({ blocked_by: blockerTask.id });
    const blockingTaskIds = new Set([blockerTask.id]);

    render(<TaskCard task={blocked} blockingTaskIds={blockingTaskIds} />);

    expect(document.querySelector("[data-blocked-by-chip]")).not.toBeNull();
  });

  it("shows chip when blocker is TODO and present in blockingTaskIds", () => {
    const blockerTask = makeTask({ id: 201, process_status: TaskStatus.TODO });
    const blocked = makeTask({ blocked_by: blockerTask.id });
    const blockingTaskIds = new Set([blockerTask.id]);

    render(<TaskCard task={blocked} blockingTaskIds={blockingTaskIds} />);

    expect(document.querySelector("[data-blocked-by-chip]")).not.toBeNull();
  });

  it("suppresses chip when blocker is DONE (absent from blockingTaskIds)", () => {
    // DONE tasks are not added to blockingTaskIds — so the set does NOT contain
    // the blocker id, which means it is treated as terminal.
    const blockerDoneId = 300;
    const blocked = makeTask({ blocked_by: blockerDoneId });
    // blockingTaskIds only contains non-terminal tasks; 300 is not in it.
    const blockingTaskIds = new Set<number>([999]);

    render(<TaskCard task={blocked} blockingTaskIds={blockingTaskIds} />);

    expect(document.querySelector("[data-blocked-by-chip]")).toBeNull();
  });

  it("suppresses chip when blocker is CANCELLED (absent from blockingTaskIds)", () => {
    const blockerCancelledId = 400;
    const blocked = makeTask({ blocked_by: blockerCancelledId });
    const blockingTaskIds = new Set<number>();

    render(<TaskCard task={blocked} blockingTaskIds={blockingTaskIds} />);

    expect(document.querySelector("[data-blocked-by-chip]")).toBeNull();
  });

  it("suppresses chip when blocker id is absent from loaded set (terminal beyond first-50 DONE)", () => {
    // Absent id => blocker loaded nowhere => treat as terminal.
    const absentBlockerId = 500;
    const blocked = makeTask({ blocked_by: absentBlockerId });
    const blockingTaskIds = new Set<number>([1, 2, 3]); // doesn't include 500

    render(<TaskCard task={blocked} blockingTaskIds={blockingTaskIds} />);

    expect(document.querySelector("[data-blocked-by-chip]")).toBeNull();
  });

  it("shows chip when blockingTaskIds is undefined (backwards compat — old callers)", () => {
    // When blockingTaskIds is not provided, chip is shown unconditionally (old behaviour).
    const blocked = makeTask({ blocked_by: 600 });

    render(<TaskCard task={blocked} />);

    expect(document.querySelector("[data-blocked-by-chip]")).not.toBeNull();
  });

  it("does not render chip when blocked_by is null, regardless of blockingTaskIds", () => {
    const notBlocked = makeTask({ blocked_by: null });
    const blockingTaskIds = new Set<number>([1, 2, 3]);

    render(<TaskCard task={notBlocked} blockingTaskIds={blockingTaskIds} />);

    expect(document.querySelector("[data-blocked-by-chip]")).toBeNull();
  });

  it("data-blocked-by attribute is preserved even when chip is suppressed", () => {
    const blockerDoneId = 700;
    const blocked = makeTask({ blocked_by: blockerDoneId });
    const blockingTaskIds = new Set<number>(); // empty => blocker absent

    render(<TaskCard task={blocked} blockingTaskIds={blockingTaskIds} />);

    const article = document.querySelector("article[data-blocked-by]");
    expect(article).not.toBeNull();
    expect(article?.getAttribute("data-blocked-by")).toBe(String(blockerDoneId));
    // But the visual chip is gone.
    expect(document.querySelector("[data-blocked-by-chip]")).toBeNull();
  });
});
