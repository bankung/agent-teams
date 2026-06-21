// Board — milestone-filter <select> hides cancelled milestones — Kanban #2519.
//
// Strategy mirrors Board.operatorGate.test.tsx:
//   - All heavy sub-components stubbed for speed/determinism.
//   - listMilestones resolves with a fixture that includes a cancelled entry.
//   - After mount, the data-milestone-filter <select> must NOT contain an
//     <option> for the cancelled milestone.
//   - Non-cancelled milestones (active, planned, released) must be present.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor, configure } from "@testing-library/react";
import type { TaskRead, MilestoneRead, ProjectRead, ProgressStatsResponse } from "@/lib/api";
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
let nextId = 200;

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

function makeMilestone(id: number, title: string, status: string): MilestoneRead {
  return {
    id,
    project_id: 1,
    title,
    milestone_status: status,
    description: null,
    start_date: null,
    target_date: null,
    sort_order: id,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  } as MilestoneRead;
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

describe("Board — milestone-filter <select> (#2519)", () => {
  beforeEach(() => {
    mockListDoneLanePage.mockReset();
    mockPatchTask.mockReset();
    mockReorderTask.mockReset();
    nextId = 200;
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue(null);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => undefined);
  });

  it("cancelled milestone is absent from data-milestone-filter options", async () => {
    const milestones = [
      makeMilestone(10, "Active Sprint", "active"),
      makeMilestone(11, "SMOKE #1924", "cancelled"),
      makeMilestone(12, "Next Quarter", "planned"),
    ];
    mockListMilestones.mockResolvedValue(milestones);

    render(
      <Board
        initialTasks={[makeTask()]}
        initialDoneHasMore={false}
        hasHeadlessTask={false}
        project={makeProject()}
        projectStats={[]}
        progressStats={EMPTY_PROGRESS}
      />,
    );

    // Wait for listMilestones to resolve and the select to appear.
    await waitFor(() => {
      expect(document.querySelector("[data-milestone-filter]")).not.toBeNull();
    });

    const select = document.querySelector("[data-milestone-filter]") as HTMLSelectElement;
    const optionValues = Array.from(select.options).map((o) => o.value);

    // Sentinel options always present.
    expect(optionValues).toContain("all");
    expect(optionValues).toContain("none");

    // Non-cancelled milestones present.
    expect(optionValues).toContain("10");
    expect(optionValues).toContain("12");

    // Cancelled milestone must be absent.
    expect(optionValues).not.toContain("11");
  });

  it("filter select hidden when only cancelled milestones exist", async () => {
    const milestones = [makeMilestone(20, "SMOKE #1924", "cancelled")];
    mockListMilestones.mockResolvedValue(milestones);

    render(
      <Board
        initialTasks={[makeTask()]}
        initialDoneHasMore={false}
        hasHeadlessTask={false}
        project={makeProject()}
        projectStats={[]}
        progressStats={EMPTY_PROGRESS}
      />,
    );

    // Give the milestone effect time to resolve.
    await waitFor(() => expect(mockListMilestones).toHaveBeenCalled());

    // Select should not be rendered (filterMilestones.length === 0).
    expect(document.querySelector("[data-milestone-filter]")).toBeNull();
  });
});
