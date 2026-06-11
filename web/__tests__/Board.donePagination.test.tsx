// Board DONE server-pagination tests — Kanban #2112 AC2.
//
// Strategy:
// 1. Stub every heavy sub-component that Board imports (modals, charts, SSE,
//    next/dynamic boards) so the test suite stays fast and deterministic.
// 2. BoardDndCanvas is mocked to render a plain div that exposes the
//    onLoadMoreDone callback via a data-testid button — this avoids the
//    dnd-kit + next/dynamic(ssr:false) complexity in jsdom while still
//    exercising Board's handleLoadMoreDone state machine at the seam that
//    matters (cursor computation + listDoneLanePage call + dedup + doneHasMore).
//    See brief note: "if next/dynamic(ssr:false) is intractable in jsdom,
//    assert at the smallest testable seam".
// 3. All async assertions use findBy*/waitFor (RTL incident #1310 anti-pattern
//    guard: never sync querySelector on post-fetch state).
//
// Tests:
// A. Load-more calls listDoneLanePage with the correct cursor (last DONE row
//    updated_at + id) and appends the returned rows.
// B. doneHasMore flips false when the returned page has fewer rows than limit.
// C. Load-more button is absent when initialDoneHasMore=false.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, configure } from "@testing-library/react";
import type { TaskRead, ProjectRead, ProjectStatsEntry, ProgressStatsResponse } from "@/lib/api";
import { TaskStatus } from "@/lib/constants";

configure({ asyncUtilTimeout: 5000 });

// ---------------------------------------------------------------------------
// Mock: @/lib/api — capture listDoneLanePage calls; other helpers no-op.
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
// Mock: next/navigation — Board calls useRouter/usePathname/useSearchParams.
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
// Mock: next/dynamic — return a simple pass-through that renders a stub.
// Board uses: dynamic(() => import("@/components/BoardDndCanvas").then(...))
// We intercept next/dynamic so the returned component is our stub instead.
// The stub exposes the onLoadMoreDone handler via a data-testid button so
// tests can trigger it without needing actual dnd-kit drag interactions.
// ---------------------------------------------------------------------------
vi.mock("next/dynamic", () => ({
  default: (_factory: unknown, _opts?: unknown) => {
    // Return a stub BoardDndCanvas that surfaces the Load-more callback.
    return function StubBoardDndCanvas(props: Record<string, unknown>) {
      const onLoadMoreDone = props.onLoadMoreDone as (() => void) | undefined;
      const doneHasMore = props.doneHasMore as boolean | undefined;
      const doneLoadingMore = props.doneLoadingMore as boolean | undefined;
      const grouped = props.grouped as Map<number, TaskRead[]> | undefined;
      const doneTasks: TaskRead[] = grouped?.get(TaskStatus.DONE) ?? [];
      return (
        <div data-testid="stub-board-dnd-canvas">
          {/* Render DONE task titles so tests can assert appended rows */}
          {doneTasks.map((t) => (
            <div key={t.id} data-testid={`done-task-${t.id}`}>
              {t.title}
            </div>
          ))}
          {doneHasMore && (
            <button
              type="button"
              data-testid="load-more-done-btn"
              disabled={doneLoadingMore ?? false}
              onClick={() => onLoadMoreDone?.()}
            >
              {doneLoadingMore ? "Loading…" : "Load more"}
            </button>
          )}
        </div>
      );
    };
  },
}));

// ---------------------------------------------------------------------------
// Mock: SSE hook — prevents EventSource from being created in jsdom.
// ---------------------------------------------------------------------------
vi.mock("@/lib/useRowChangedEvents", () => ({
  useRowChangedEvents: () => ({
    connectionState: "open",
    lastEventAt: null,
  }),
}));

// ---------------------------------------------------------------------------
// Mock: heavy sub-components that would pull in complex dependencies.
// ---------------------------------------------------------------------------
vi.mock("@/components/ConnectionStateBadge", () => ({
  ConnectionStateBadge: () => null,
}));
vi.mock("@/components/Icon", () => ({
  Icon: () => null,
}));
vi.mock("@/components/AuditHistorySection", () => ({
  AuditHistorySection: () => null,
}));
// #1315 — Resources footer. Stubbed like every other Board child so its
// own mount effects (localStorage hydrate + lazy fetch) don't interfere with
// the DONE-pagination state-machine assertions. Covered by its own test.
vi.mock("@/components/ResourcesPanel", () => ({
  ResourcesPanel: () => null,
}));
vi.mock("@/components/CostSummary", () => ({
  CostSummary: () => null,
}));
vi.mock("@/components/PnlSummaryCard", () => ({
  PnlSummaryCard: () => null,
}));
vi.mock("@/components/ProgressChartsPanel", () => ({
  ProgressChartsPanel: () => null,
}));
vi.mock("@/components/KilledBanner", () => ({
  KilledBanner: () => null,
}));
vi.mock("@/components/KillProjectModal", () => ({
  KillProjectModal: () => null,
}));
vi.mock("@/components/NewTaskDropdown", () => ({
  NewTaskDropdown: () => null,
}));
vi.mock("@/components/PausedBanner", () => ({
  PausedBanner: () => null,
}));
vi.mock("@/components/PauseProjectModal", () => ({
  PauseProjectModal: () => null,
}));
vi.mock("@/components/ProjectConsentGrantModal", () => ({
  ProjectConsentGrantModal: () => null,
}));
vi.mock("@/components/PlatformSettingsModal", () => ({
  PlatformSettingsModal: () => null,
}));
vi.mock("@/components/ProductTourBoardResume", () => ({
  ProductTourBoardResume: () => null,
}));
vi.mock("@/components/ProjectSwitcher", () => ({
  ProjectSwitcher: () => null,
}));
vi.mock("@/components/SourcesBadge", () => ({
  SourcesBadge: () => null,
}));
vi.mock("@/components/TaskDetail", () => ({
  TaskDetail: () => null,
}));
vi.mock("@/components/ThemePicker", () => ({
  ThemePicker: () => null,
}));
vi.mock("@/components/Toast", () => ({
  ToastStack: () => null,
}));
vi.mock("@/components/ViewSwitcher", () => ({
  ViewSwitcher: () => null,
}));
vi.mock("@/components/ListView", () => ({
  ListView: () => null,
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeTask(overrides: Partial<TaskRead> = {}): TaskRead {
  return {
    id: 1,
    project_id: 1,
    parent_task_id: null,
    title: "Test task",
    description: null,
    process_status: TaskStatus.TODO,
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
    ...overrides,
  } as TaskRead;
}

/** Build 50 DONE tasks with descending updated_at timestamps. */
function make50DoneTasks(): TaskRead[] {
  return Array.from({ length: 50 }, (_, i) => {
    // Descending timestamps: newest first (i=0 → most recent)
    const ts = new Date(Date.UTC(2026, 2, 50 - i, 12, 0, 0)).toISOString();
    return makeTask({
      id: 1000 + i,
      title: `done-task-${i}`,
      process_status: TaskStatus.DONE,
      updated_at: ts,
    });
  });
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

const EMPTY_PROGRESS: ProgressStatsResponse = {
  burndown: [],
  velocity: [],
};

// Import Board AFTER all mocks are registered.
import { Board } from "@/components/Board";

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("Board — DONE server pagination (#2112 AC2)", () => {
  beforeEach(() => {
    mockListDoneLanePage.mockReset();
    mockListMilestones.mockResolvedValue([]);
    mockPatchTask.mockReset();
    mockReorderTask.mockReset();
    // Stub localStorage to avoid "window.localStorage is undefined" noise in jsdom.
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue(null);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => undefined);
  });

  // -------------------------------------------------------------------------
  // A. Load-more calls listDoneLanePage with the correct cursor and appends rows.
  // -------------------------------------------------------------------------
  it("A. calls listDoneLanePage with cursor = last DONE row's updated_at + id and appends", async () => {
    const doneTasks = make50DoneTasks();
    const activeTasks = [
      makeTask({ id: 1, title: "active-todo", process_status: TaskStatus.TODO }),
    ];
    const allTasks = [...activeTasks, ...doneTasks];

    // Page 2 returns 3 new rows.
    const page2Tasks = [
      makeTask({ id: 2000, title: "new-done-1", process_status: TaskStatus.DONE, updated_at: "2026-01-15T12:00:00Z" }),
      makeTask({ id: 2001, title: "new-done-2", process_status: TaskStatus.DONE, updated_at: "2026-01-14T12:00:00Z" }),
      makeTask({ id: 2002, title: "new-done-3", process_status: TaskStatus.DONE, updated_at: "2026-01-13T12:00:00Z" }),
    ];
    mockListDoneLanePage.mockResolvedValueOnce(page2Tasks);

    render(
      <Board
        initialTasks={allTasks}
        initialDoneHasMore={true}
        hasHeadlessTask={false}
        project={makeProject()}
        projectStats={[]}
        progressStats={EMPTY_PROGRESS}
      />,
    );

    // The Load-more button must be present because initialDoneHasMore=true.
    const loadMoreBtn = await screen.findByTestId("load-more-done-btn");
    expect(loadMoreBtn).toBeInTheDocument();

    // Click it.
    fireEvent.click(loadMoreBtn);

    // Wait for listDoneLanePage to be called.
    await waitFor(() => {
      expect(mockListDoneLanePage).toHaveBeenCalledTimes(1);
    });

    // The cursor must use the last DONE row in sortDoneLane order.
    // doneTasks are already in DESC order (i=49 is the oldest → smallest id/ts).
    // sortDoneLane sorts by updated_at DESC, id DESC → last row = doneTasks[49].
    const lastDone = doneTasks[doneTasks.length - 1]; // id=1049, oldest ts
    const [calledProjectId, calledOpts] = mockListDoneLanePage.mock.calls[0];

    expect(calledProjectId).toBe(1);
    expect(calledOpts).toMatchObject({
      limit: 50,
      before_updated_at: lastDone.updated_at,
      before_id: lastDone.id,
    });

    // Appended rows should appear in the stub canvas.
    expect(await screen.findByTestId("done-task-2000")).toBeInTheDocument();
    expect(await screen.findByTestId("done-task-2001")).toBeInTheDocument();
    expect(await screen.findByTestId("done-task-2002")).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // B. doneHasMore flips false when returned page is shorter than limit.
  // -------------------------------------------------------------------------
  it("B. doneHasMore flips false when page returned has fewer rows than limit", async () => {
    const doneTasks = make50DoneTasks();
    const allTasks = [...doneTasks];

    // A short page (3 < 50) signals "last page".
    const shortPage = [
      makeTask({ id: 3000, title: "short-done-1", process_status: TaskStatus.DONE, updated_at: "2026-01-05T12:00:00Z" }),
      makeTask({ id: 3001, title: "short-done-2", process_status: TaskStatus.DONE, updated_at: "2026-01-04T12:00:00Z" }),
    ];
    mockListDoneLanePage.mockResolvedValueOnce(shortPage);

    render(
      <Board
        initialTasks={allTasks}
        initialDoneHasMore={true}
        hasHeadlessTask={false}
        project={makeProject()}
        projectStats={[]}
        progressStats={EMPTY_PROGRESS}
      />,
    );

    const loadMoreBtn = await screen.findByTestId("load-more-done-btn");
    fireEvent.click(loadMoreBtn);

    await waitFor(() => {
      expect(mockListDoneLanePage).toHaveBeenCalledTimes(1);
    });

    // After a short page, doneHasMore must become false → Load-more button disappears.
    await waitFor(() => {
      expect(screen.queryByTestId("load-more-done-btn")).toBeNull();
    });
  });

  // -------------------------------------------------------------------------
  // C. Load-more button is absent when initialDoneHasMore=false.
  // -------------------------------------------------------------------------
  it("C. Load-more button absent when initialDoneHasMore=false", async () => {
    const doneTasks = make50DoneTasks().slice(0, 5);
    render(
      <Board
        initialTasks={doneTasks}
        initialDoneHasMore={false}
        hasHeadlessTask={false}
        project={makeProject()}
        projectStats={[]}
        progressStats={EMPTY_PROGRESS}
      />,
    );

    // Give time for any async effects to settle.
    await waitFor(() => {
      // listMilestones is always called on mount; wait for it to settle.
      expect(mockListMilestones).toHaveBeenCalled();
    });

    // NEGATIVE: button must not appear.
    expect(screen.queryByTestId("load-more-done-btn")).toBeNull();
    // POSITIVE: listDoneLanePage never called (no request without the button).
    expect(mockListDoneLanePage).not.toHaveBeenCalled();
  });
});
