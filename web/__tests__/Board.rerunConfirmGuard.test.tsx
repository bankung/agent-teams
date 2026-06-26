// Board — re-run confirm guard — Kanban #2664 AC4+AC5+AC6 (FE).
//
// Destructive drop = AI task (task_kind:'ai', started_at set) dragged to TODO.
// Guard must: hold optimistic move + PATCH until user confirms; cancel = no PATCH.
// Non-destructive paths (human task, never-run AI, non-TODO target) skip dialog.
//
// Strategy mirrors Board.operatorGate.test.tsx:
//   - Heavy sub-components stubbed for speed/determinism.
//   - StubBoardDndCanvas exposes onCrossLaneDrop via a synthetic button so tests
//     can trigger drops without dnd-kit.
//   - All async assertions use findBy*/waitFor (RTL #1310 anti-pattern guard).

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, configure } from "@testing-library/react";
import type { TaskRead, ProjectRead, ProgressStatsResponse } from "@/lib/api";
import { TaskStatus } from "@/lib/constants";

configure({ asyncUtilTimeout: 5000 });

// ---------------------------------------------------------------------------
// Mock: @/lib/api
// ---------------------------------------------------------------------------
const mockListDoneLanePage = vi.fn();
const mockListMilestones = vi.fn();
const mockPatchTask = vi.fn();
const mockReorderTask = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    listDoneLanePage: (...args: unknown[]) => mockListDoneLanePage(...args),
    listMilestones: (...args: unknown[]) => mockListMilestones(...args),
    patchTask: (...args: unknown[]) => mockPatchTask(...args),
    reorderTask: (...args: unknown[]) => mockReorderTask(...args),
  };
});

// ---------------------------------------------------------------------------
// Mock: next/navigation
// ---------------------------------------------------------------------------
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/p/test-project",
  useSearchParams: () => ({ get: () => null }),
}));

// ---------------------------------------------------------------------------
// Mock: next/link
// ---------------------------------------------------------------------------
vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: {
    href: string;
    children: React.ReactNode;
    [k: string]: unknown;
  }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

// ---------------------------------------------------------------------------
// Mock: next/dynamic — StubBoardDndCanvas exposes onCrossLaneDrop via buttons.
// Each task in non-TODO lanes gets a "drag to TODO" trigger button so tests
// can fire the drop handler without real dnd-kit.
// ---------------------------------------------------------------------------
vi.mock("next/dynamic", () => ({
  default: (_factory: unknown, _opts?: unknown) => {
    return function StubBoardDndCanvas(props: Record<string, unknown>) {
      const onCrossLaneDrop = props.onCrossLaneDrop as
        | ((taskId: number, newPs: number, original: TaskRead) => void)
        | undefined;
      const grouped = props.grouped as Map<number, TaskRead[]> | undefined;
      const allTasks: TaskRead[] = [];
      if (grouped) {
        for (const bucket of grouped.values()) allTasks.push(...bucket);
      }
      return (
        <div data-testid="stub-board-dnd-canvas">
          {allTasks.map((t) => (
            <div key={t.id} data-testid={`task-card-${t.id}`}>
              <span>{t.title}</span>
              {/* Simulate dragging this task to TODO lane */}
              <button
                type="button"
                data-testid={`drag-to-todo-${t.id}`}
                onClick={() => onCrossLaneDrop?.(t.id, TaskStatus.TODO, t)}
              >
                drag-to-todo
              </button>
              {/* Simulate dragging this task to IN_PROGRESS (non-destructive target) */}
              <button
                type="button"
                data-testid={`drag-to-inprogress-${t.id}`}
                onClick={() => onCrossLaneDrop?.(t.id, TaskStatus.IN_PROGRESS, t)}
              >
                drag-to-inprogress
              </button>
            </div>
          ))}
        </div>
      );
    };
  },
}));

// ---------------------------------------------------------------------------
// Mock: SSE hook
// ---------------------------------------------------------------------------
vi.mock("@/lib/useRowChangedEvents", () => ({
  useRowChangedEvents: () => ({ connectionState: "open", lastEventAt: null }),
}));

// ---------------------------------------------------------------------------
// Mock: heavy sub-components
// ---------------------------------------------------------------------------
vi.mock("@/components/ConnectionStateBadge", () => ({ ConnectionStateBadge: () => null }));
vi.mock("@/components/Icon", () => ({ Icon: () => null }));
vi.mock("@/components/AuditHistorySection", () => ({ AuditHistorySection: () => null }));
vi.mock("@/components/ResourcesPanel", () => ({ ResourcesPanel: () => null }));
vi.mock("@/components/CostSummary", () => ({ CostSummary: () => null }));
vi.mock("@/components/PnlSummaryCard", () => ({ PnlSummaryCard: () => null }));
vi.mock("@/components/ProgressChartsPanel", () => ({ ProgressChartsPanel: () => null }));
vi.mock("@/components/KilledBanner", () => ({ KilledBanner: () => null }));
vi.mock("@/components/KillProjectModal", () => ({ KillProjectModal: () => null }));
vi.mock("@/components/NewTaskDropdown", () => ({ NewTaskDropdown: () => null }));
vi.mock("@/components/PausedBanner", () => ({ PausedBanner: () => null }));
vi.mock("@/components/PauseProjectModal", () => ({ PauseProjectModal: () => null }));
vi.mock("@/components/ProjectConsentGrantModal", () => ({ ProjectConsentGrantModal: () => null }));
vi.mock("@/components/PlatformSettingsModal", () => ({ PlatformSettingsModal: () => null }));
vi.mock("@/components/ProductTourBoardResume", () => ({ ProductTourBoardResume: () => null }));
vi.mock("@/components/ProjectSwitcher", () => ({ ProjectSwitcher: () => null }));
vi.mock("@/components/SourcesBadge", () => ({ SourcesBadge: () => null }));
vi.mock("@/components/TaskDetail", () => ({ TaskDetail: () => null }));
vi.mock("@/components/ThemePicker", () => ({ ThemePicker: () => null }));
vi.mock("@/components/Toast", () => ({ ToastStack: () => null }));
vi.mock("@/components/ViewSwitcher", () => ({ ViewSwitcher: () => null }));
vi.mock("@/components/ListView", () => ({ ListView: () => null }));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
let nextId = 200;

function makeTask(overrides: Partial<TaskRead> = {}): TaskRead {
  return {
    id: nextId++,
    project_id: 1,
    parent_task_id: null,
    title: `task-${nextId}`,
    description: null,
    process_status: TaskStatus.IN_PROGRESS,
    priority: 2,
    assigned_role: null,
    run_mode: "auto",
    task_kind: "ai",
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
    ...overrides,
  } as TaskRead;
}

function makeProject(overrides: Partial<ProjectRead> = {}): ProjectRead {
  return {
    id: 1,
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
    is_killed: false,
    ...overrides,
  } as ProjectRead;
}

const EMPTY_PROGRESS: ProgressStatsResponse = { burndown: [], velocity: [] };

import { Board } from "@/components/Board";

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("Board — re-run confirm guard (Kanban #2664 AC4+AC5+AC6)", () => {
  beforeEach(() => {
    mockListDoneLanePage.mockReset();
    mockListMilestones.mockResolvedValue([]);
    mockPatchTask.mockReset();
    mockReorderTask.mockReset();
    nextId = 200;
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue(null);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => undefined);
  });

  // -------------------------------------------------------------------------
  // 1. Destructive drop — dialog appears, NO PATCH, NO optimistic move yet.
  // -------------------------------------------------------------------------
  it("1. dragging a ran AI task to TODO shows confirm dialog and withholds PATCH", async () => {
    const ranAi = makeTask({
      title: "ran-ai-task",
      task_kind: "ai",
      started_at: "2026-01-02T00:00:00Z",
      process_status: TaskStatus.IN_PROGRESS,
    });

    render(
      <Board
        initialTasks={[ranAi]}
        initialDoneHasMore={false}
        hasHeadlessTask={false}
        project={makeProject()}
        projectStats={[]}
        progressStats={EMPTY_PROGRESS}
      />,
    );

    // Trigger the cross-lane drop into TODO.
    const dragBtn = await screen.findByTestId(`drag-to-todo-${ranAi.id}`);
    fireEvent.click(dragBtn);

    // Dialog must appear.
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveTextContent(/re-run/i);
    expect(dialog).toHaveTextContent(/discards its previous run/i);

    // No PATCH sent yet.
    expect(mockPatchTask).not.toHaveBeenCalled();
  });

  // -------------------------------------------------------------------------
  // 2. Confirm → PATCH sent + dialog closes.
  // -------------------------------------------------------------------------
  it("2. confirming re-run dialog sends PATCH and closes dialog", async () => {
    mockPatchTask.mockResolvedValue(
      makeTask({ task_kind: "ai", started_at: "2026-01-02T00:00:00Z", process_status: TaskStatus.TODO }),
    );

    const ranAi = makeTask({
      title: "ran-ai-task",
      task_kind: "ai",
      started_at: "2026-01-02T00:00:00Z",
      process_status: TaskStatus.IN_PROGRESS,
    });

    render(
      <Board
        initialTasks={[ranAi]}
        initialDoneHasMore={false}
        hasHeadlessTask={false}
        project={makeProject()}
        projectStats={[]}
        progressStats={EMPTY_PROGRESS}
      />,
    );

    const dragBtn = await screen.findByTestId(`drag-to-todo-${ranAi.id}`);
    fireEvent.click(dragBtn);

    // Dialog shown.
    await screen.findByRole("dialog");

    // Click "Re-run" confirm button.
    const okBtn = screen.getByRole("button", { name: /re-run/i });
    fireEvent.click(okBtn);

    // PATCH must be called.
    await waitFor(() => {
      expect(mockPatchTask).toHaveBeenCalledTimes(1);
      expect(mockPatchTask).toHaveBeenCalledWith(
        1,
        ranAi.id,
        expect.objectContaining({ process_status: TaskStatus.TODO }),
      );
    });

    // Dialog dismissed.
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });

  // -------------------------------------------------------------------------
  // 3. Cancel → NO PATCH, dialog closes, card stays in source lane.
  // -------------------------------------------------------------------------
  it("3. cancelling re-run dialog sends NO PATCH", async () => {
    const ranAi = makeTask({
      title: "ran-ai-task",
      task_kind: "ai",
      started_at: "2026-01-02T00:00:00Z",
      process_status: TaskStatus.IN_PROGRESS,
    });

    render(
      <Board
        initialTasks={[ranAi]}
        initialDoneHasMore={false}
        hasHeadlessTask={false}
        project={makeProject()}
        projectStats={[]}
        progressStats={EMPTY_PROGRESS}
      />,
    );

    const dragBtn = await screen.findByTestId(`drag-to-todo-${ranAi.id}`);
    fireEvent.click(dragBtn);

    await screen.findByRole("dialog");

    const cancelBtn = screen.getByRole("button", { name: /cancel/i });
    fireEvent.click(cancelBtn);

    // Dialog dismissed.
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });

    // No PATCH sent.
    expect(mockPatchTask).not.toHaveBeenCalled();
  });

  // -------------------------------------------------------------------------
  // 4. Human task dragged to TODO — NO dialog, PATCH fires immediately.
  // -------------------------------------------------------------------------
  it("4. dragging a human task to TODO skips dialog and PATCHes immediately", async () => {
    mockPatchTask.mockResolvedValue(
      makeTask({ task_kind: "human", started_at: null, process_status: TaskStatus.TODO }),
    );

    const humanTask = makeTask({
      title: "human-task",
      task_kind: "human",
      started_at: "2026-01-02T00:00:00Z",
      process_status: TaskStatus.IN_PROGRESS,
    });

    render(
      <Board
        initialTasks={[humanTask]}
        initialDoneHasMore={false}
        hasHeadlessTask={false}
        project={makeProject()}
        projectStats={[]}
        progressStats={EMPTY_PROGRESS}
      />,
    );

    const dragBtn = await screen.findByTestId(`drag-to-todo-${humanTask.id}`);
    fireEvent.click(dragBtn);

    // No dialog.
    expect(screen.queryByRole("dialog")).toBeNull();

    // PATCH fires immediately.
    await waitFor(() => {
      expect(mockPatchTask).toHaveBeenCalledTimes(1);
    });
  });

  // -------------------------------------------------------------------------
  // 5. Never-run AI task (started_at null) → NO dialog, PATCH fires immediately.
  // -------------------------------------------------------------------------
  it("5. dragging a never-run AI task (started_at null) to TODO skips dialog", async () => {
    mockPatchTask.mockResolvedValue(
      makeTask({ task_kind: "ai", started_at: null, process_status: TaskStatus.TODO }),
    );

    const neverRun = makeTask({
      title: "never-run-ai",
      task_kind: "ai",
      started_at: null,
      process_status: TaskStatus.IN_PROGRESS,
    });

    render(
      <Board
        initialTasks={[neverRun]}
        initialDoneHasMore={false}
        hasHeadlessTask={false}
        project={makeProject()}
        projectStats={[]}
        progressStats={EMPTY_PROGRESS}
      />,
    );

    const dragBtn = await screen.findByTestId(`drag-to-todo-${neverRun.id}`);
    fireEvent.click(dragBtn);

    expect(screen.queryByRole("dialog")).toBeNull();

    await waitFor(() => {
      expect(mockPatchTask).toHaveBeenCalledTimes(1);
    });
  });

  // -------------------------------------------------------------------------
  // 6. Ran AI task dragged to non-TODO target → NO dialog, PATCH fires immediately.
  // -------------------------------------------------------------------------
  it("6. dragging a ran AI task to IN_PROGRESS (non-TODO) skips dialog", async () => {
    mockPatchTask.mockResolvedValue(
      makeTask({ task_kind: "ai", started_at: "2026-01-02T00:00:00Z", process_status: TaskStatus.IN_PROGRESS }),
    );

    const ranAi = makeTask({
      title: "ran-ai-task",
      task_kind: "ai",
      started_at: "2026-01-02T00:00:00Z",
      process_status: TaskStatus.REVIEW,
    });

    render(
      <Board
        initialTasks={[ranAi]}
        initialDoneHasMore={false}
        hasHeadlessTask={false}
        project={makeProject()}
        projectStats={[]}
        progressStats={EMPTY_PROGRESS}
      />,
    );

    const dragBtn = await screen.findByTestId(`drag-to-inprogress-${ranAi.id}`);
    fireEvent.click(dragBtn);

    expect(screen.queryByRole("dialog")).toBeNull();

    await waitFor(() => {
      expect(mockPatchTask).toHaveBeenCalledTimes(1);
    });
  });
});
