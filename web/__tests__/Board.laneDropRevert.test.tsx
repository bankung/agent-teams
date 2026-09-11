// Board — lane-drop revert preserves concurrent edits — Kanban #2842.
//
// applyLaneDrop's failure path must revert ONLY the optimistically-changed
// process_status field, not splice back the whole `original` snapshot
// captured at drop time. A whole-object revert would silently discard any
// field change that arrived (via an SSE-driven router.refresh() → fresh SSR
// `initialTasks`) from a concurrent agent PATCH while this task's own PATCH
// was in flight — the platform's normal autonomous-agent operating mode.
//
// Strategy mirrors Board.rerunConfirmGuard.test.tsx:
//   - Heavy sub-components stubbed for speed/determinism.
//   - StubBoardDndCanvas exposes onCrossLaneDrop via a synthetic button, and
//     renders each task's title + process_status as text so the test can
//     assert on exactly which fields survived the revert.
//   - patchTask is manually deferred (a controlled Promise) so the test can
//     interleave a concurrent SSR refresh (TaskDetailEditing.test.tsx's
//     "SSE-refresh safety" pattern, via rerender()) before rejecting it.

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
// Mock: next/dynamic — StubBoardDndCanvas exposes onCrossLaneDrop via a
// button, and renders title + process_status as text so revert-scope can be
// asserted precisely.
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
              <span data-testid={`task-title-${t.id}`}>{t.title}</span>
              <span data-testid={`task-ps-${t.id}`}>{t.process_status}</span>
              {/* Simulate dragging this task to IN_PROGRESS. */}
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
let nextId = 300;

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

describe("Board — lane-drop revert preserves concurrent edits (Kanban #2842)", () => {
  beforeEach(() => {
    mockListDoneLanePage.mockReset();
    mockListMilestones.mockResolvedValue([]);
    mockPatchTask.mockReset();
    mockReorderTask.mockReset();
    nextId = 300;
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue(null);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => undefined);
  });

  it("reverts only process_status on a failed lane move, preserving a concurrent field change", async () => {
    const task = makeTask({
      title: "before-concurrent-edit",
      process_status: TaskStatus.TODO,
    });

    let rejectPatch: (err: unknown) => void = () => {};
    mockPatchTask.mockImplementationOnce(
      () =>
        new Promise((_resolve, reject) => {
          rejectPatch = reject;
        }),
    );

    const { rerender } = render(
      <Board
        initialTasks={[task]}
        initialDoneHasMore={false}
        hasHeadlessTask={false}
        project={makeProject()}
        projectStats={[]}
        progressStats={EMPTY_PROGRESS}
      />,
    );

    // Drag to IN_PROGRESS — optimistic move fires + PATCH sent (held pending).
    const dragBtn = await screen.findByTestId(`drag-to-inprogress-${task.id}`);
    fireEvent.click(dragBtn);

    await waitFor(() => {
      expect(mockPatchTask).toHaveBeenCalledTimes(1);
    });

    // Concurrent agent PATCH lands via SSE → router.refresh() → fresh SSR
    // props (TaskDetailEditing.test.tsx's "SSE-refresh safety" pattern).
    // process_status is still TODO server-side (our own PATCH hasn't landed
    // yet), but `title` now reflects the other agent's already-committed
    // change.
    rerender(
      <Board
        initialTasks={[{ ...task, title: "concurrent-title-change" }]}
        initialDoneHasMore={false}
        hasHeadlessTask={false}
        project={makeProject()}
        projectStats={[]}
        progressStats={EMPTY_PROGRESS}
      />,
    );

    await screen.findByText("concurrent-title-change");

    // Our own PATCH now fails (e.g. a 409 conflict).
    rejectPatch(new Error("stale write"));

    // The failed lane-move reverts process_status back to TODO...
    await waitFor(() => {
      expect(screen.getByTestId(`task-ps-${task.id}`)).toHaveTextContent(
        String(TaskStatus.TODO),
      );
    });

    // ...but must NOT discard the concurrent title change picked up above.
    expect(screen.getByTestId(`task-title-${task.id}`)).toHaveTextContent(
      "concurrent-title-change",
    );
  });
});
