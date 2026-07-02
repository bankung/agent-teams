// BoardDndCanvas re-render cascade — Kanban #2699 FE audit F2+F4 (High).
//
// Bug: every SSE tick that leaves the visible task data untouched (e.g. an
// SSE message that bumps `lastEventAt` before the debounced router.refresh()
// fires, or any other Board-local state change — toast, dialog, filter UI)
// still re-rendered BoardDndCanvas with no drag in progress, because two of
// its callback props (onSameLaneReorder, onLoadMoreDone) were rebuilt with
// `tasks` in their useCallback deps and BoardDndCanvas itself was not memo'd.
//
// Fix under test (Board.tsx + BoardDndCanvas.tsx):
//   1. onSameLaneReorder reads tasksRef.current (deps: [project.id, pushToast]).
//   2. handleLoadMoreDone (the done-lane load-more callback) reads
//      tasksRef.current too, and setVisibleDoneCount is now a stable
//      useCallback instead of a plain per-render const.
//   3. BoardDndCanvas is wrapped in React.memo.
//
// Strategy:
// - Mock @/components/BoardDndCanvas directly (not via next/dynamic) with a
//   small stub wrapped in the REAL `memo` from "react", so React's actual
//   shallow-compare bailout is what's under test — not a hand-rolled counter
//   condition. The stub renders enough of `grouped` to expose
//   onCrossLaneDrop/onSameLaneReorder trigger buttons (same pattern as
//   Board.rerunConfirmGuard.test.tsx's StubBoardDndCanvas).
// - Mock @/lib/useRowChangedEvents with a REAL useState-backed implementation
//   (mirrors the production hook's lastEventAt bump) so invoking the captured
//   onTaskChange from the test causes a genuine Board-local re-render without
//   touching `tasks` state — the actual "no-op SSE tick" scenario this fix
//   targets. router.refresh() is a mocked no-op in this harness, so this
//   faithfully reproduces "SSE message arrived, debounced refresh hasn't
//   landed yet" without needing a live EventSource in jsdom.
// - A real lane move (onCrossLaneDrop) goes through Board's applyLaneDrop,
//   which calls the real setTasks with a genuinely different array — this is
//   the DOES-re-render half of the assertion.
//
// NOT covered here (see final report residual note): a genuinely-new-but-
// content-identical `initialTasks` reference (a real production
// router.refresh() with unchanged DB rows) still produces new `tasks`/
// `grouped` object identities via Board's visibleTasks/grouped useMemo, so
// React.memo's default shallow-compare would NOT bail out for that specific
// case. That is a distinct, higher-risk value-equality question deliberately
// left out of this task's scope (see report).

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, configure, act } from "@testing-library/react";
import * as React from "react";
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
// Mock: next/navigation — router.refresh() is a no-op vi.fn() (as in every
// other Board test); this harness never re-executes RSC, so a captured SSE
// callback firing router.refresh() never itself touches `initialTasks`.
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
// Mock: next/dynamic — Board.tsx's factory already does
// `import("@/components/BoardDndCanvas").then((m) => m.BoardDndCanvas)`, i.e.
// the factory Promise resolves to the COMPONENT itself (not the module), so
// this wrapper just needs to render whatever the factory resolves to once
// it settles. Both @/components/BoardDndCanvas and @/components/TaskDetail
// are vi.mock'd below, so the factory resolves on the same microtask tick —
// one effect-driven re-render after mount is enough, no Suspense needed.
// ---------------------------------------------------------------------------
vi.mock("next/dynamic", () => ({
  default: (factory: () => Promise<React.ComponentType<Record<string, unknown>>>) => {
    return function DynamicPassThrough(props: Record<string, unknown>) {
      const [Resolved, setResolved] = React.useState<React.ComponentType<Record<string, unknown>> | null>(null);
      React.useEffect(() => {
        let cancelled = false;
        factory().then((Comp) => {
          if (!cancelled) setResolved(() => Comp);
        });
        return () => {
          cancelled = true;
        };
      }, []);
      if (!Resolved) return null;
      return <Resolved {...props} />;
    };
  },
}));

// ---------------------------------------------------------------------------
// Mock: @/components/BoardDndCanvas — render-counting stub wrapped in the
// REAL React `memo`, so the assertion exercises React's actual shallow-
// compare bailout (not a hand-rolled render guard). Exposes onCrossLaneDrop /
// onSameLaneReorder trigger buttons per task (mirrors
// Board.rerunConfirmGuard.test.tsx's StubBoardDndCanvas pattern) so tests can
// fire a real lane move without dnd-kit.
// ---------------------------------------------------------------------------
let renderCount = 0;
// Captures the onSameLaneReorder / onLoadMoreDone reference from EVERY render
// (including ones the memo blocks from committing new DOM — captured inside
// the function body, so it runs whenever React actually invokes the function,
// which for a memo'd component only happens when the shallow-compare fails).
const capturedCallbackRefs: { onSameLaneReorder: unknown; onLoadMoreDone: unknown }[] = [];

function BoardDndCanvasStubImpl(props: Record<string, unknown>) {
  // Test-only render-count instrumentation: the whole point of this stub is
  // to observe how many times React actually INVOKES this function body (the
  // thing memo() is supposed to prevent). There is no React-idiomatic way to
  // count a component's own render invocations without mutating a
  // module-level variable — useState here would itself add an extra render.
  // Confined to this test file; the stub never ships.
  // eslint-disable-next-line react-hooks/globals
  renderCount += 1;
  capturedCallbackRefs.push({
    onSameLaneReorder: props.onSameLaneReorder,
    onLoadMoreDone: props.onLoadMoreDone,
  });
  const onCrossLaneDrop = props.onCrossLaneDrop as
    | ((taskId: number, newPs: number, original: TaskRead) => void)
    | undefined;
  const onSameLaneReorder = props.onSameLaneReorder as
    | ((taskId: number, overTaskId: number, laneIds: number[]) => void)
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
          <button
            type="button"
            data-testid={`drag-to-inprogress-${t.id}`}
            onClick={() => onCrossLaneDrop?.(t.id, TaskStatus.IN_PROGRESS, t)}
          >
            drag-to-inprogress
          </button>
        </div>
      ))}
      {/* Same-lane reorder trigger — fires with the first two TODO ids. */}
      <button
        type="button"
        data-testid="reorder-trigger"
        onClick={() => {
          const todoIds = allTasks
            .filter((t) => t.process_status === TaskStatus.TODO)
            .map((t) => t.id);
          if (todoIds.length >= 2) {
            onSameLaneReorder?.(todoIds[0], todoIds[1], todoIds);
          }
        }}
      >
        reorder
      </button>
    </div>
  );
}

vi.mock("@/components/BoardDndCanvas", () => ({
  BoardDndCanvas: React.memo(BoardDndCanvasStubImpl),
}));

// ---------------------------------------------------------------------------
// Mock: @/lib/useRowChangedEvents — REAL useState-backed lastEventAt bump
// (mirrors the production hook) so invoking the captured onTaskChange from a
// test causes a genuine Board-local re-render, without touching `tasks`
// state. This is the "SSE tick arrived, debounced router.refresh() hasn't
// landed yet" scenario — the actual no-op-tick trigger in production.
// ---------------------------------------------------------------------------
let capturedOnTaskChange: (() => void) | undefined;

vi.mock("@/lib/useRowChangedEvents", () => ({
  useRowChangedEvents: (args: { onTaskChange?: () => void }) => {
    const [lastEventAt, setLastEventAt] = React.useState<Date | null>(null);
    capturedOnTaskChange = () => {
      setLastEventAt(new Date());
      args.onTaskChange?.();
    };
    return { connectionState: "open" as const, lastEventAt };
  },
}));

// ---------------------------------------------------------------------------
// Mock: heavy sub-components (same list as every other Board test).
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
    task_kind: "human",
    task_type: "feature",
    due_date: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    sort_order: null,
    milestone_id: null,
    acceptance_criteria: null,
    is_template: false,
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

describe("BoardDndCanvas re-render cascade (#2699 FE audit F2+F4)", () => {
  beforeEach(() => {
    mockListDoneLanePage.mockReset();
    mockListMilestones.mockResolvedValue([]);
    mockPatchTask.mockReset();
    mockReorderTask.mockReset();
    renderCount = 0;
    capturedCallbackRefs.length = 0;
    capturedOnTaskChange = undefined;
    nextId = 500;
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue(null);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => undefined);
  });

  // -------------------------------------------------------------------------
  // 1. A no-op SSE tick (Board-local re-render, `tasks` state untouched)
  //    does NOT re-render the memo'd BoardDndCanvas.
  // -------------------------------------------------------------------------
  it("1. a no-op SSE tick (lastEventAt bump, tasks unchanged) does not re-render BoardDndCanvas", async () => {
    const todoTasks = [
      makeTask({ title: "todo-a", process_status: TaskStatus.TODO }),
      makeTask({ title: "todo-b", process_status: TaskStatus.TODO }),
    ];

    render(
      <Board
        initialTasks={todoTasks}
        initialDoneHasMore={false}
        hasHeadlessTask={false}
        project={makeProject()}
        projectStats={[]}
        progressStats={EMPTY_PROGRESS}
      />,
    );

    // Let the initial mount (+ dynamic() effect-driven resolution) settle.
    await screen.findByTestId("stub-board-dnd-canvas");
    const countAfterMount = renderCount;
    expect(countAfterMount).toBeGreaterThan(0);

    // Fire the captured SSE onTaskChange 3x — each bumps Board's lastEventAt
    // (a genuine Board-local re-render) without ever calling setTasks
    // (router.refresh() is a no-op in this harness, mirroring "message
    // arrived, debounced refresh hasn't landed yet").
    expect(capturedOnTaskChange).toBeDefined();
    act(() => {
      capturedOnTaskChange?.();
    });
    act(() => {
      capturedOnTaskChange?.();
    });
    act(() => {
      capturedOnTaskChange?.();
    });

    // Board re-rendered (connectionState badge etc. would reflect it in prod);
    // BoardDndCanvas must NOT have re-rendered — its render count is frozen.
    expect(renderCount).toBe(countAfterMount);
  });

  // -------------------------------------------------------------------------
  // 2. A real task-lane move (setTasks with a genuinely different array) DOES
  //    re-render BoardDndCanvas — the memo must not stale-freeze a real change.
  // -------------------------------------------------------------------------
  it("2. a real cross-lane task move re-renders BoardDndCanvas", async () => {
    mockPatchTask.mockResolvedValue(
      makeTask({ process_status: TaskStatus.IN_PROGRESS }),
    );

    const todoTasks = [makeTask({ title: "todo-a", process_status: TaskStatus.TODO })];

    render(
      <Board
        initialTasks={todoTasks}
        initialDoneHasMore={false}
        hasHeadlessTask={false}
        project={makeProject()}
        projectStats={[]}
        progressStats={EMPTY_PROGRESS}
      />,
    );

    await screen.findByTestId("stub-board-dnd-canvas");
    const countAfterMount = renderCount;

    const dragBtn = await screen.findByTestId(`drag-to-inprogress-${todoTasks[0].id}`);
    fireEvent.click(dragBtn);

    // The optimistic setTasks (before the PATCH resolves) is a genuinely new
    // `tasks` array → grouped recomputes → BoardDndCanvas must re-render.
    await waitFor(() => {
      expect(renderCount).toBeGreaterThan(countAfterMount);
    });
  });

  // -------------------------------------------------------------------------
  // 2b. Even when `tasks` genuinely changes (so BoardDndCanvas correctly DOES
  //     re-render — see test 2), onSameLaneReorder / onLoadMoreDone must keep
  //     the SAME function reference. This is the specific, narrow claim the
  //     tasksRef refactor makes: Board.tsx's useCallback deps no longer list
  //     `tasks`, so these two callbacks don't churn on data changes elsewhere
  //     in the board (only project.id/pushToast changing would rebuild them,
  //     and neither does across a task move). Proven by strict reference
  //     equality (toBe), not the render-count proxy tests 1/2 use.
  // -------------------------------------------------------------------------
  it("2b. onSameLaneReorder and onLoadMoreDone keep the same reference across a real tasks change", async () => {
    mockPatchTask.mockResolvedValue(
      makeTask({ process_status: TaskStatus.IN_PROGRESS }),
    );

    const todoTasks = [makeTask({ title: "todo-a", process_status: TaskStatus.TODO })];

    render(
      <Board
        initialTasks={todoTasks}
        initialDoneHasMore={false}
        hasHeadlessTask={false}
        project={makeProject()}
        projectStats={[]}
        progressStats={EMPTY_PROGRESS}
      />,
    );

    await screen.findByTestId("stub-board-dnd-canvas");
    const refsBefore = capturedCallbackRefs[capturedCallbackRefs.length - 1];

    const dragBtn = await screen.findByTestId(`drag-to-inprogress-${todoTasks[0].id}`);
    fireEvent.click(dragBtn);

    // Wait for the optimistic setTasks to land (a real re-render + re-capture).
    await waitFor(() => {
      expect(capturedCallbackRefs.length).toBeGreaterThan(1);
    });
    const refsAfter = capturedCallbackRefs[capturedCallbackRefs.length - 1];

    expect(refsAfter.onSameLaneReorder).toBe(refsBefore.onSameLaneReorder);
    expect(refsAfter.onLoadMoreDone).toBe(refsBefore.onLoadMoreDone);
  });

  // -------------------------------------------------------------------------
  // 3. Same-lane reorder still calls reorderTask with the correct args after
  //    the tasksRef refactor — the ref-read must not break the reorder math.
  //    (Existing reorder math coverage; the refactor moved tasks.find() to
  //    tasksRef.current.find() only.)
  // -------------------------------------------------------------------------
  it("3. onSameLaneReorder (tasksRef-backed) still computes before/after_id correctly", async () => {
    mockReorderTask.mockResolvedValue(makeTask({ process_status: TaskStatus.TODO }));

    const todoTasks = [
      makeTask({ title: "todo-first", process_status: TaskStatus.TODO }),
      makeTask({ title: "todo-second", process_status: TaskStatus.TODO }),
    ];

    render(
      <Board
        initialTasks={todoTasks}
        initialDoneHasMore={false}
        hasHeadlessTask={false}
        project={makeProject()}
        projectStats={[]}
        progressStats={EMPTY_PROGRESS}
      />,
    );

    const reorderBtn = await screen.findByTestId("reorder-trigger");
    fireEvent.click(reorderBtn);

    await waitFor(() => {
      expect(mockReorderTask).toHaveBeenCalledTimes(1);
    });

    // taskId = todoTasks[0].id (oldIndex 0) moved to overTaskId = todoTasks[1].id
    // (newIndex 1); oldIndex < newIndex → { after_id: overTask.id }.
    const [calledProjectId, calledTaskId, calledBody] = mockReorderTask.mock.calls[0];
    expect(calledProjectId).toBe(1);
    expect(calledTaskId).toBe(todoTasks[0].id);
    expect(calledBody).toEqual({ after_id: todoTasks[1].id });
  });

  // -------------------------------------------------------------------------
  // 4. onSameLaneReorder reads the CURRENT tasks at call time (via the ref),
  //    not a stale snapshot from when the callback was first created. Proof:
  //    the SAME onSameLaneReorder function identity (captured once, before
  //    any tasks change — see test 2b) is called AFTER a real cross-lane
  //    mutation (moves the 3rd TODO task out of the lane); the reorder math
  //    must reflect the POST-mutation 2-task TODO lane, not the original
  //    3-task snapshot the closure was created against.
  // -------------------------------------------------------------------------
  it("4. onSameLaneReorder reads current tasks via the ref, not a stale closure", async () => {
    mockPatchTask.mockResolvedValue(makeTask({ process_status: TaskStatus.IN_PROGRESS }));
    mockReorderTask.mockResolvedValue(makeTask({ process_status: TaskStatus.TODO }));

    const toMoveOut = makeTask({ title: "todo-third", process_status: TaskStatus.TODO });
    const todoTasks = [
      makeTask({ title: "todo-first", process_status: TaskStatus.TODO }),
      makeTask({ title: "todo-second", process_status: TaskStatus.TODO }),
      toMoveOut,
    ];

    render(
      <Board
        initialTasks={todoTasks}
        initialDoneHasMore={false}
        hasHeadlessTask={false}
        project={makeProject()}
        projectStats={[]}
        progressStats={EMPTY_PROGRESS}
      />,
    );

    await screen.findByTestId("stub-board-dnd-canvas");
    // Capture the closure identity BEFORE the mutation — same object the
    // reorder click below will invoke (proves it's the SAME callback, reading
    // fresh data, not a rebuilt one).
    const reorderRefBefore = capturedCallbackRefs[capturedCallbackRefs.length - 1].onSameLaneReorder;

    // Real cross-lane mutation: move the 3rd TODO task to IN_PROGRESS. This
    // is a genuine setTasks with a different array (test 2's scenario).
    const dragBtn = await screen.findByTestId(`drag-to-inprogress-${toMoveOut.id}`);
    fireEvent.click(dragBtn);
    await waitFor(() => {
      // Card for the moved task must be gone from the TODO-lane render (the
      // stub renders every task in `grouped`, keyed by lane membership via
      // the optimistic process_status update).
      expect(screen.queryByTestId(`drag-to-inprogress-${toMoveOut.id}`)).toBeNull();
    });

    const reorderRefAfter = capturedCallbackRefs[capturedCallbackRefs.length - 1].onSameLaneReorder;
    expect(reorderRefAfter).toBe(reorderRefBefore); // same function identity, per test 2b

    // Fire the SAME reorder trigger — must now compute against the 2
    // remaining TODO tasks (todo-first, todo-second), NOT the original
    // 3-task snapshot.
    const reorderBtn = await screen.findByTestId("reorder-trigger");
    fireEvent.click(reorderBtn);

    await waitFor(() => {
      expect(mockReorderTask).toHaveBeenCalledTimes(1);
    });
    const [, calledTaskId, calledBody] = mockReorderTask.mock.calls[0];
    expect(calledTaskId).toBe(todoTasks[0].id);
    expect(calledBody).toEqual({ after_id: todoTasks[1].id });
  });
});
