// Board — operator-gate "On you" badge + filter tests — Kanban #2127 AC[3].
//
// Strategy mirrors Board.donePagination.test.tsx:
//   - All heavy sub-components stubbed for speed/determinism.
//   - BoardDndCanvas stub renders task titles so filter assertions work.
//   - All async assertions use findBy*/waitFor (RTL #1310 discipline).
//
// Coverage:
//   1. Badge hidden when no gated tasks (operatorGateCount === 0).
//   2. Badge shown — count from task-level operator_gate only.
//   3. Badge shown — count from AC-level gate==='operator' + status==='pending' only.
//   4. Passed AC items (status!=='pending') NOT counted.
//   5. Both levels on same task count as 1 (dedup).
//   6. Toggle: clicking badge filters board to only gated tasks.
//   7. Toggle: clicking again restores all tasks.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, configure } from "@testing-library/react";
import type { TaskRead, AcceptanceCriterion, ProjectRead, ProgressStatsResponse } from "@/lib/api";
import { TaskStatus } from "@/lib/constants";

configure({ asyncUtilTimeout: 5000 });

// ---------------------------------------------------------------------------
// Mock: @/lib/api — no network calls in these tests.
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
// Mock: next/dynamic — BoardDndCanvas stub renders task titles + done tasks.
// ---------------------------------------------------------------------------
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
            <div key={t.id} data-testid={`task-${t.id}`}>
              {t.title}
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
let nextId = 100;

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

function makeAc(overrides: Partial<AcceptanceCriterion> = {}): AcceptanceCriterion {
  return {
    text: "criterion",
    status: "pending",
    verified_by: null,
    verified_at: null,
    notes: null,
    ...overrides,
  };
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

describe("Board — operator-gate 'On you' badge + filter (#2127 AC[3])", () => {
  beforeEach(() => {
    mockListDoneLanePage.mockReset();
    mockListMilestones.mockResolvedValue([]);
    mockPatchTask.mockReset();
    mockReorderTask.mockReset();
    nextId = 100;
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue(null);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => undefined);
  });

  // -------------------------------------------------------------------------
  // 1. Badge hidden when no gated tasks.
  // -------------------------------------------------------------------------
  it("1. badge hidden when operatorGateCount === 0", async () => {
    const tasks = [makeTask({ title: "plain-task" })];
    render(
      <Board
        initialTasks={tasks}
        initialDoneHasMore={false}
        hasHeadlessTask={false}
        project={makeProject()}
        projectStats={[]}
        progressStats={EMPTY_PROGRESS}
      />,
    );
    await waitFor(() => expect(mockListMilestones).toHaveBeenCalled());
    expect(document.querySelector("[data-operator-gate-toggle]")).toBeNull();
  });

  // -------------------------------------------------------------------------
  // 2. Badge shown — task-level operator_gate non-null.
  // -------------------------------------------------------------------------
  it("2. badge shown when task has task-level operator_gate set", async () => {
    const gated = makeTask({ title: "gated-task", operator_gate: "key" });
    const plain = makeTask({ title: "plain-task" });
    render(
      <Board
        initialTasks={[gated, plain]}
        initialDoneHasMore={false}
        hasHeadlessTask={false}
        project={makeProject()}
        projectStats={[]}
        progressStats={EMPTY_PROGRESS}
      />,
    );
    const badge = await screen.findByRole("button", { name: /on you \(1\)/i });
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveAttribute("data-operator-gate-toggle");
  });

  // -------------------------------------------------------------------------
  // 3. Badge shown — AC-level gate==='operator' + status==='pending'.
  // -------------------------------------------------------------------------
  it("3. badge shown when AC item has gate=operator and status=pending", async () => {
    const acGated = makeAc({ gate: "operator", status: "pending" });
    const gated = makeTask({
      title: "ac-gated-task",
      acceptance_criteria: [acGated],
    });
    const plain = makeTask({ title: "plain-task" });
    render(
      <Board
        initialTasks={[gated, plain]}
        initialDoneHasMore={false}
        hasHeadlessTask={false}
        project={makeProject()}
        projectStats={[]}
        progressStats={EMPTY_PROGRESS}
      />,
    );
    const badge = await screen.findByRole("button", { name: /on you \(1\)/i });
    expect(badge).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // 4. Passed AC items NOT counted.
  // -------------------------------------------------------------------------
  it("4. AC item with gate=operator but status=passed is NOT counted", async () => {
    const acPassed = makeAc({ gate: "operator", status: "passed" });
    const task = makeTask({
      title: "passed-ac-task",
      acceptance_criteria: [acPassed],
    });
    render(
      <Board
        initialTasks={[task]}
        initialDoneHasMore={false}
        hasHeadlessTask={false}
        project={makeProject()}
        projectStats={[]}
        progressStats={EMPTY_PROGRESS}
      />,
    );
    await waitFor(() => expect(mockListMilestones).toHaveBeenCalled());
    expect(document.querySelector("[data-operator-gate-toggle]")).toBeNull();
  });

  // -------------------------------------------------------------------------
  // 5. Both task-level and AC-level on same task count as 1 (dedup).
  // -------------------------------------------------------------------------
  it("5. task with both task-level gate and AC gate counted once (dedup)", async () => {
    const ac = makeAc({ gate: "operator", status: "pending" });
    const bothGated = makeTask({
      title: "both-gated",
      operator_gate: "hitl",
      acceptance_criteria: [ac],
    });
    const onlyAc = makeTask({
      title: "ac-only-gated",
      acceptance_criteria: [makeAc({ gate: "operator", status: "pending" })],
    });
    render(
      <Board
        initialTasks={[bothGated, onlyAc]}
        initialDoneHasMore={false}
        hasHeadlessTask={false}
        project={makeProject()}
        projectStats={[]}
        progressStats={EMPTY_PROGRESS}
      />,
    );
    // 2 tasks total, not 3 (no double-counting of bothGated).
    const badge = await screen.findByRole("button", { name: /on you \(2\)/i });
    expect(badge).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // 6. Toggle: clicking badge filters board to only gated tasks.
  // -------------------------------------------------------------------------
  it("6. clicking badge filters board to only gated tasks", async () => {
    const gated = makeTask({ title: "gated-task", operator_gate: "commit" });
    const plain = makeTask({ title: "plain-task" });
    render(
      <Board
        initialTasks={[gated, plain]}
        initialDoneHasMore={false}
        hasHeadlessTask={false}
        project={makeProject()}
        projectStats={[]}
        progressStats={EMPTY_PROGRESS}
      />,
    );

    // Both tasks visible initially.
    expect(await screen.findByTestId(`task-${gated.id}`)).toBeInTheDocument();
    expect(screen.getByTestId(`task-${plain.id}`)).toBeInTheDocument();

    // Click the "On you" chip.
    const badge = await screen.findByRole("button", { name: /on you/i });
    fireEvent.click(badge);

    // After toggle: only gated task visible; plain task removed.
    expect(await screen.findByTestId(`task-${gated.id}`)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByTestId(`task-${plain.id}`)).toBeNull();
    });
  });

  // -------------------------------------------------------------------------
  // 7. Toggle: clicking again restores all tasks.
  // -------------------------------------------------------------------------
  it("7. clicking badge twice restores all tasks", async () => {
    const gated = makeTask({ title: "gated-task", operator_gate: "decision" });
    const plain = makeTask({ title: "plain-task" });
    render(
      <Board
        initialTasks={[gated, plain]}
        initialDoneHasMore={false}
        hasHeadlessTask={false}
        project={makeProject()}
        projectStats={[]}
        progressStats={EMPTY_PROGRESS}
      />,
    );

    const badge = await screen.findByRole("button", { name: /on you/i });
    // First click — filter on.
    fireEvent.click(badge);
    await waitFor(() => {
      expect(screen.queryByTestId(`task-${plain.id}`)).toBeNull();
    });
    // Second click — filter off.
    fireEvent.click(badge);
    expect(await screen.findByTestId(`task-${plain.id}`)).toBeInTheDocument();
    expect(screen.getByTestId(`task-${gated.id}`)).toBeInTheDocument();
  });
});
