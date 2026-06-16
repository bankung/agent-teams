// Tests for #2416: FE board lane for process_status=8 (HALTED_PENDING_USER).
//
// Verifies:
// 1. buildColumnPs maps key "8" → TaskStatus.HALTED_PENDING_USER (8).
// 2. groupByStatus buckets a ps=8 task into the halted bucket (not dropped).
// 3. Board renders ps=8 tasks via the stub BoardDndCanvas grouped map.
// 4. Board includes HALTED_PENDING_USER in ALL_STATUSES so ps=8 tasks aren't
//    silently dropped by groupByStatus.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, configure } from "@testing-library/react";
import type { TaskRead, ProjectRead, ProgressStatsResponse } from "@/lib/api";
import { TaskStatus } from "@/lib/constants";
import { buildColumnPs } from "@/components/BoardDndCanvas";

configure({ asyncUtilTimeout: 5000 });

// ---------------------------------------------------------------------------
// buildColumnPs — pure unit test (no React, no mocks needed).
// ---------------------------------------------------------------------------
describe("buildColumnPs — halted lane key (#2416)", () => {
  it("maps key '8' to TaskStatus.HALTED_PENDING_USER (8)", () => {
    const columns = [
      { key: "1", statuses: [TaskStatus.TODO], label: "New tasks" },
      { key: "2", statuses: [TaskStatus.IN_PROGRESS], label: "In progress" },
      { key: "3", statuses: [TaskStatus.REVIEW], label: "Review" },
      { key: "4", statuses: [TaskStatus.BLOCKED], label: "Blocked" },
      { key: "8", statuses: [TaskStatus.HALTED_PENDING_USER], label: "Halted / Pending user" },
      { key: "5", statuses: [TaskStatus.DONE], label: "Done" },
    ];
    const map = buildColumnPs(columns);
    expect(map["8"]).toBe(TaskStatus.HALTED_PENDING_USER);
    expect(map["8"]).toBe(8);
  });

  it("full 6-column set produces 6 entries", () => {
    const columns = [
      { key: "1", statuses: [TaskStatus.TODO], label: "New tasks" },
      { key: "2", statuses: [TaskStatus.IN_PROGRESS], label: "In progress" },
      { key: "3", statuses: [TaskStatus.REVIEW], label: "Review" },
      { key: "4", statuses: [TaskStatus.BLOCKED], label: "Blocked" },
      { key: "8", statuses: [TaskStatus.HALTED_PENDING_USER], label: "Halted / Pending user" },
      { key: "5", statuses: [TaskStatus.DONE], label: "Done" },
    ];
    const map = buildColumnPs(columns);
    expect(Object.keys(map)).toHaveLength(6);
  });
});

// ---------------------------------------------------------------------------
// Board integration — ps=8 tasks routed into grouped map and rendered.
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

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/p/test-project",
  useSearchParams: () => ({ get: () => null }),
}));

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

// Stub BoardDndCanvas: renders all tasks from grouped map with their status
// so tests can verify ps=8 tasks are present in the halted bucket.
vi.mock("next/dynamic", () => ({
  default: (_factory: unknown, _opts?: unknown) => {
    return function StubBoardDndCanvas(props: Record<string, unknown>) {
      const grouped = props.grouped as Map<number, TaskRead[]> | undefined;
      const allTasks: TaskRead[] = [];
      if (grouped) {
        for (const bucket of grouped.values()) allTasks.push(...bucket);
      }
      return (
        <div data-testid="stub-board-dnd-canvas">
          {allTasks.map((t) => (
            <div
              key={t.id}
              data-testid={`task-${t.id}`}
              data-process-status={t.process_status}
            >
              {t.title}
            </div>
          ))}
        </div>
      );
    };
  },
}));

vi.mock("@/lib/useRowChangedEvents", () => ({
  useRowChangedEvents: () => ({ connectionState: "open", lastEventAt: null }),
}));

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
let nextId = 500;

function makeTask(overrides: Partial<TaskRead> = {}): TaskRead {
  return {
    id: nextId++,
    project_id: 1,
    parent_task_id: null,
    title: `task-${nextId}`,
    description: null,
    process_status: TaskStatus.TODO,
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

describe("Board — halted-pending-user lane (#2416)", () => {
  beforeEach(() => {
    mockListDoneLanePage.mockReset();
    mockListMilestones.mockResolvedValue([]);
    mockPatchTask.mockReset();
    mockReorderTask.mockReset();
    nextId = 500;
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue(null);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => undefined);
  });

  it("ps=8 task is grouped into the halted bucket and rendered in the board", async () => {
    const haltedTask = makeTask({
      title: "halted-task",
      process_status: TaskStatus.HALTED_PENDING_USER,
    });
    const todoTask = makeTask({ title: "todo-task", process_status: TaskStatus.TODO });

    render(
      <Board
        initialTasks={[haltedTask, todoTask]}
        initialDoneHasMore={false}
        hasHeadlessTask={false}
        project={makeProject()}
        projectStats={[]}
        progressStats={EMPTY_PROGRESS}
      />,
    );

    // Both tasks should be visible (not dropped by groupByStatus).
    expect(await screen.findByTestId(`task-${haltedTask.id}`)).toBeInTheDocument();
    expect(screen.getByTestId(`task-${todoTask.id}`)).toBeInTheDocument();

    // The halted task renders with process_status=8.
    const haltedEl = screen.getByTestId(`task-${haltedTask.id}`);
    expect(haltedEl.getAttribute("data-process-status")).toBe(
      String(TaskStatus.HALTED_PENDING_USER),
    );
  });

  it("ps=8 task is NOT dropped when only ps=8 tasks are provided (no regressions on groupByStatus)", async () => {
    const haltedTask = makeTask({
      title: "only-halted",
      process_status: TaskStatus.HALTED_PENDING_USER,
    });

    render(
      <Board
        initialTasks={[haltedTask]}
        initialDoneHasMore={false}
        hasHeadlessTask={false}
        project={makeProject()}
        projectStats={[]}
        progressStats={EMPTY_PROGRESS}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId(`task-${haltedTask.id}`)).toBeInTheDocument();
    });
  });

  it("TaskStatus.HALTED_PENDING_USER equals 8", () => {
    expect(TaskStatus.HALTED_PENDING_USER).toBe(8);
  });
});
