// Tests for TaskActivityStrip — Kanban #2334.
//
// Covers:
//   1. Running vs idle threshold (≤5 min → running; staler → idle)
//   2. Zero rows → no strip markup rendered
//   3. Non-IN_PROGRESS → TaskCard renders no strip at all and makes no fetch
//   4. Polling pause on document.hidden (fake timers)
//
// Uses waitFor/findBy* throughout — async component, never sync querySelector.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act, configure } from "@testing-library/react";
import type { ToolCallRead } from "@/lib/api";
import { TaskStatus } from "@/lib/constants";
import type { TaskRead } from "@/lib/api";

configure({ asyncUtilTimeout: 5000 });

const mockGetTaskToolCalls = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getTaskToolCalls: (...args: Parameters<typeof actual.getTaskToolCalls>) =>
      mockGetTaskToolCalls(...args),
  };
});

// dnd-kit sortable stub — TaskCard uses useSortable which requires the
// DndContext provider; stub it to a no-op for unit tests.
vi.mock("@dnd-kit/sortable", () => ({
  useSortable: () => ({
    attributes: {},
    listeners: {},
    setNodeRef: () => undefined,
    transform: null,
    transition: undefined,
    isDragging: false,
  }),
  SortableContext: ({ children }: { children: React.ReactNode }) => children,
  verticalListSortingStrategy: {},
}));
vi.mock("@dnd-kit/utilities", () => ({
  CSS: { Transform: { toString: () => undefined } },
}));

import { TaskActivityStrip } from "@/components/TaskActivityStrip";
import { TaskCard } from "@/components/TaskCard";

// ---- helpers ----------------------------------------------------------------

function makeRow(over: Partial<ToolCallRead> = {}): ToolCallRead {
  return {
    id: 1,
    task_id: 99,
    invoked_at: new Date().toISOString(),
    tool_name: "SomeTool",
    source: "engine",
    kind: null,
    summary: null,
    tier: "read",
    input_json: null,
    success: true,
    error_code: null,
    error_msg: null,
    output_summary: null,
    duration_ms: 10,
    permission_decision: "auto_allow",
    ...over,
  };
}

function makeLeadRow(over: Partial<ToolCallRead> = {}): ToolCallRead {
  return makeRow({
    source: "lead",
    kind: "spawn",
    summary: "Spawned agent",
    tier: null,
    input_json: null,
    duration_ms: null,
    permission_decision: null,
    ...over,
  });
}

function makeTask(overrides: Partial<TaskRead> = {}): TaskRead {
  return {
    id: 99,
    project_id: 1,
    parent_task_id: null,
    title: "Test task",
    description: null,
    process_status: TaskStatus.IN_PROGRESS,
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
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    started_at: null,
    completed_at: null,
    ...overrides,
  };
}

beforeEach(() => {
  mockGetTaskToolCalls.mockReset();
});

// ---- AC1: running vs idle threshold -----------------------------------------

describe("TaskActivityStrip — running/idle indicator", () => {
  it("shows data-activity-state=running when newest row is ≤5 min old", async () => {
    const recentIso = new Date(Date.now() - 60_000).toISOString(); // 1 min ago
    mockGetTaskToolCalls.mockResolvedValue([makeRow({ invoked_at: recentIso })]);

    render(<TaskActivityStrip projectId={1} taskId={99} />);

    await waitFor(() => {
      const el = document.querySelector("[data-activity-state]");
      expect(el?.getAttribute("data-activity-state")).toBe("running");
    });
  });

  it("shows data-activity-state=idle when newest row is >5 min old", async () => {
    const staleIso = new Date(Date.now() - 10 * 60_000).toISOString(); // 10 min ago
    mockGetTaskToolCalls.mockResolvedValue([makeRow({ invoked_at: staleIso })]);

    render(<TaskActivityStrip projectId={1} taskId={99} />);

    await waitFor(() => {
      const el = document.querySelector("[data-activity-state]");
      expect(el?.getAttribute("data-activity-state")).toBe("idle");
    });
  });

  it("shows data-activity-state=idle initially (SSR default) before first fetch resolves", async () => {
    // Never resolves in this test — we just check initial render.
    mockGetTaskToolCalls.mockReturnValue(new Promise(() => undefined));

    const { container } = render(<TaskActivityStrip projectId={1} taskId={99} />);

    const el = container.querySelector("[data-activity-state]");
    expect(el).not.toBeNull();
    expect(el?.getAttribute("data-activity-state")).toBe("idle");
  });

  it("shows aria-label=running when running", async () => {
    const recentIso = new Date(Date.now() - 30_000).toISOString();
    mockGetTaskToolCalls.mockResolvedValue([makeRow({ invoked_at: recentIso })]);

    render(<TaskActivityStrip projectId={1} taskId={99} />);

    await waitFor(() => {
      const el = document.querySelector('[aria-label="running"]');
      expect(el).not.toBeNull();
    });
  });

  it("shows aria-label=idle when idle", async () => {
    const staleIso = new Date(Date.now() - 20 * 60_000).toISOString();
    mockGetTaskToolCalls.mockResolvedValue([makeRow({ invoked_at: staleIso })]);

    render(<TaskActivityStrip projectId={1} taskId={99} />);

    await waitFor(() => {
      const el = document.querySelector('[aria-label="idle"]');
      expect(el).not.toBeNull();
    });
  });
});

// ---- AC2: zero rows → no strip rows ----------------------------------------

describe("TaskActivityStrip — zero rows", () => {
  it("renders no activity rows when fetch returns empty array", async () => {
    mockGetTaskToolCalls.mockResolvedValue([]);

    render(<TaskActivityStrip projectId={1} taskId={99} />);

    // Dot is always present; rows list must be absent.
    await waitFor(() => {
      expect(document.querySelector("[data-activity-state]")).not.toBeNull();
    });
    expect(document.querySelector("[data-activity-rows]")).toBeNull();
  });

  it("railless card still shows idle dot", async () => {
    mockGetTaskToolCalls.mockResolvedValue([]);

    render(<TaskActivityStrip projectId={1} taskId={99} />);

    await waitFor(() => {
      const el = document.querySelector("[data-activity-state]");
      expect(el?.getAttribute("data-activity-state")).toBe("idle");
    });
  });
});

// ---- AC3: non-IN_PROGRESS → no fetch ----------------------------------------

describe("TaskCard — non-IN_PROGRESS cards make no tool-calls fetch", () => {
  it("does not call getTaskToolCalls for TODO task", () => {
    const task = makeTask({ process_status: TaskStatus.TODO });
    render(<TaskCard task={task} projectId={1} />);
    expect(mockGetTaskToolCalls).not.toHaveBeenCalled();
  });

  it("does not call getTaskToolCalls for DONE task", () => {
    const task = makeTask({ process_status: TaskStatus.DONE });
    render(<TaskCard task={task} projectId={1} />);
    expect(mockGetTaskToolCalls).not.toHaveBeenCalled();
  });

  it("does not call getTaskToolCalls for BLOCKED task", () => {
    const task = makeTask({ process_status: TaskStatus.BLOCKED });
    render(<TaskCard task={task} projectId={1} />);
    expect(mockGetTaskToolCalls).not.toHaveBeenCalled();
  });

  it("does not call getTaskToolCalls for REVIEW task", () => {
    const task = makeTask({ process_status: TaskStatus.REVIEW });
    render(<TaskCard task={task} projectId={1} />);
    expect(mockGetTaskToolCalls).not.toHaveBeenCalled();
  });

  it("does not call getTaskToolCalls for CANCELLED task", () => {
    const task = makeTask({ process_status: TaskStatus.CANCELLED });
    render(<TaskCard task={task} projectId={1} />);
    expect(mockGetTaskToolCalls).not.toHaveBeenCalled();
  });

  it("does not render data-activity-strip for DONE task", () => {
    const task = makeTask({ process_status: TaskStatus.DONE });
    render(<TaskCard task={task} projectId={1} />);
    expect(document.querySelector("[data-activity-strip]")).toBeNull();
  });

  it("calls getTaskToolCalls for IN_PROGRESS task when projectId provided", async () => {
    mockGetTaskToolCalls.mockResolvedValue([]);
    const task = makeTask({ process_status: TaskStatus.IN_PROGRESS });
    render(<TaskCard task={task} projectId={1} />);
    await waitFor(() => {
      expect(mockGetTaskToolCalls).toHaveBeenCalledWith(1, 99, 3);
    });
  });

  it("does NOT render data-activity-strip when projectId is undefined (even if IN_PROGRESS)", () => {
    const task = makeTask({ process_status: TaskStatus.IN_PROGRESS });
    render(<TaskCard task={task} />); // no projectId
    expect(document.querySelector("[data-activity-strip]")).toBeNull();
    expect(mockGetTaskToolCalls).not.toHaveBeenCalled();
  });
});

// ---- AC4: polling pause on document.hidden ----------------------------------

describe("TaskActivityStrip — polling pauses while document.hidden", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("does not re-fetch while document.hidden is true", async () => {
    mockGetTaskToolCalls.mockResolvedValue([]);

    // Simulate hidden tab.
    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => true,
    });

    render(<TaskActivityStrip projectId={1} taskId={99} />);

    // The initial fetch fires synchronously on mount (before timers).
    // Advance past one polling interval.
    await act(async () => {
      vi.advanceTimersByTime(11_000);
    });

    // Only the initial call fired, NOT an extra poll tick while hidden.
    expect(mockGetTaskToolCalls).toHaveBeenCalledTimes(1);

    // Restore.
    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => false,
    });
  });

  it("triggers a fetch when visibilitychange fires while document is visible", async () => {
    // Use real promises (resolvedValue) but fake timers for the interval.
    mockGetTaskToolCalls.mockResolvedValue([]);

    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => false,
    });

    render(<TaskActivityStrip projectId={1} taskId={99} />);

    // Flush the initial fetch microtask.
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    const countAfterMount = (mockGetTaskToolCalls as ReturnType<typeof vi.fn>).mock.calls.length;
    expect(countAfterMount).toBeGreaterThanOrEqual(1);

    // Fire visibilitychange — should trigger another call synchronously.
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });

    // Give the new async call time to register.
    await act(async () => {
      await Promise.resolve();
    });

    expect(mockGetTaskToolCalls.mock.calls.length).toBeGreaterThan(countAfterMount);
  });
});

// ---- Activity strip shows rows ----------------------------------------------

describe("TaskActivityStrip — rows rendered", () => {
  it("renders up to 3 activity rows", async () => {
    const rows = [
      makeLeadRow({ id: 1, invoked_at: new Date(Date.now() - 1000).toISOString() }),
      makeLeadRow({ id: 2, invoked_at: new Date(Date.now() - 2000).toISOString() }),
      makeLeadRow({ id: 3, invoked_at: new Date(Date.now() - 3000).toISOString() }),
    ];
    mockGetTaskToolCalls.mockResolvedValue(rows);

    render(<TaskActivityStrip projectId={1} taskId={99} />);

    await waitFor(() => {
      const rowEls = document.querySelectorAll("[data-activity-row]");
      expect(rowEls.length).toBe(3);
    });
  });

  it("passes limit=3 to getTaskToolCalls", async () => {
    mockGetTaskToolCalls.mockResolvedValue([]);
    render(<TaskActivityStrip projectId={1} taskId={99} />);
    await waitFor(() => {
      expect(mockGetTaskToolCalls).toHaveBeenCalledWith(1, 99, 3);
    });
  });
});
