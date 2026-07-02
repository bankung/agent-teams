// Kanban #2409 — extend the #1304 cost-forecast gate to CalendarView's
// "New task on this date" path.
//
// Prior to this change CalendarView's NewTaskModal mount (the one keyed on
// `createForDay`, opened from a day cell's "+" button / context menu) did NOT
// receive `project`, so `project?.cost_forecast_threshold_usd` resolved
// undefined and the gate never fired from the calendar. This file proves the
// fix: mirrors NewTaskModal.costgate.test.tsx's mock/assert approach (same
// @/lib/api mocks, same makeProject/makeForecast fixture shape) but drives
// the calendar's own trigger — the always-visible per-day "+" button
// (aria-label `New task on ${dayKey}`) — rather than NewTaskModal's own open
// prop, so the assertion covers the actual wiring gap (#2409), not a re-test
// of #1304's gate logic itself (already covered exhaustively by that file).
//
// Two cases only (per the brief): forecast-over-threshold fires the gate;
// under-threshold passes through ungated. The other #1304 branches (null
// threshold, Use Sample, Cancel, forecast-throws) are NewTaskModal-internal
// behaviour already covered by NewTaskModal.costgate.test.tsx and unaffected
// by this prop-threading fix — mirroring them here would just be a source
// duplicate, not new coverage (Karpathy lane: fold near-duplicates).
//
// Determinism: every post-await assertion goes through findBy/waitFor per the
// repo's RTL flake rule (#1310). asyncUtilTimeout raised to 5s to survive
// full-suite CPU load, matching the sibling gate test.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor, within, configure } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ProjectRead, TaskRead, CostForecastResult } from "@/lib/api";

configure({ asyncUtilTimeout: 5000 });

// ---------- mocks ----------

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
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

// Stub heavy sub-pickers NewTaskModal mounts internally — same as the sibling
// gate test, irrelevant to the gate path itself.
vi.mock("@/components/ActionTemplatePicker", () => ({
  ActionTemplatePicker: () => null,
}));
vi.mock("@/components/HandoffTemplatePicker", () => ({
  HandoffTemplatePicker: () => null,
}));

const mockCreateTask = vi.fn();
const mockCostForecast = vi.fn();
const mockPatchTask = vi.fn();
const mockDeleteTask = vi.fn();
const mockListTaskTemplates = vi.fn();
const mockListMilestones = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    createTask: (...a: Parameters<typeof actual.createTask>) => mockCreateTask(...a),
    costForecast: (...a: Parameters<typeof actual.costForecast>) =>
      mockCostForecast(...a),
    patchTask: (...a: Parameters<typeof actual.patchTask>) => mockPatchTask(...a),
    deleteTask: (...a: Parameters<typeof actual.deleteTask>) => mockDeleteTask(...a),
    listTaskTemplates: (...a: Parameters<typeof actual.listTaskTemplates>) =>
      mockListTaskTemplates(...a),
    listMilestones: (...a: Parameters<typeof actual.listMilestones>) =>
      mockListMilestones(...a),
  };
});

// ---------- fixtures (mirrors NewTaskModal.costgate.test.tsx) ----------

function makeProject(threshold: number | null): ProjectRead {
  return {
    id: 42,
    name: "agent-teams",
    description: null,
    paths_web: "/web",
    paths_api: "/api",
    paths_db: "postgres",
    stack_web: "next",
    stack_api: "fastapi",
    stack_db: "postgres",
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
    is_paused: false,
    cost_forecast_threshold_usd: threshold,
  };
}

function makeCreatedTask(): TaskRead {
  return {
    id: 999,
    project_id: 42,
    parent_task_id: null,
    title: "Test task",
    description: null,
    process_status: 1,
    priority: 2,
    assigned_role: null,
    run_mode: "manual",
    task_kind: "ai",
    task_type: "feature",
    due_date: "2026-06-15",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    sort_order: null,
    milestone_id: null,
    acceptance_criteria: null,
    is_template: false,
  } as TaskRead;
}

function makeForecast(estimatedUsd: number): CostForecastResult {
  return {
    estimated_usd: estimatedUsd,
    estimated_tokens: 12345,
    breakdown: {
      prompt: 1000,
      role_brief: 2000,
      attached_resources: 9345,
      completion: 4000,
    },
    confidence: "high",
  };
}

// ---------- helper ----------

// Render CalendarView for a fixed, deterministic month (June 2026) carrying
// `project`, then open the "new task on this date" modal via the day cell's
// always-visible "+" trigger (data-calendar-new-task) — the calendar-native
// path this task is gating, as opposed to NewTaskModal's own externalOpen.
async function renderAndOpenNewTask(project: ProjectRead) {
  const { CalendarView } = await import("@/components/CalendarView");
  const user = userEvent.setup();
  render(
    <CalendarView
      projectId={project.id}
      projectName={project.name}
      year={2026}
      month0={5} // June (0-indexed) — day 15 is safely in-month
      tasks={[]}
      milestones={[]}
      project={project}
    />,
  );

  const dayTrigger = await waitFor(() => {
    const el = document.body.querySelector(
      '[data-calendar-new-task="2026-06-15"]',
    );
    if (!el) throw new Error("day '+' trigger not rendered yet");
    return el as HTMLButtonElement;
  });
  await user.click(dayTrigger);

  const title = await waitFor(() => {
    const el = document.body.querySelector("[data-new-task-title]");
    if (!el) throw new Error("title field not rendered yet");
    return el as HTMLInputElement;
  });
  return { user, title };
}

// ---------- tests ----------

describe("CalendarView — 'new task on this date' inherits the #1304 cost-forecast gate (#2409)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockListTaskTemplates.mockResolvedValue([]);
    mockListMilestones.mockResolvedValue([]);
    mockCreateTask.mockResolvedValue(makeCreatedTask());
    mockPatchTask.mockResolvedValue(makeCreatedTask());
    mockDeleteTask.mockResolvedValue(undefined);
  });

  // (a) estimated_usd > threshold -> confirm modal appears; create is deferred
  // (no PATCH/DELETE follow-up fired yet — gate is reached, not resolved).
  it("shows the confirm modal when the calendar-created task's forecast exceeds the threshold", async () => {
    mockCostForecast.mockResolvedValue(makeForecast(3.5)); // > 1.00
    const { user, title } = await renderAndOpenNewTask(makeProject(1.0));

    await user.type(title, "Forecast me from the calendar");
    const submit = document.body.querySelector(
      "[data-new-task-submit]",
    ) as HTMLButtonElement;
    await user.click(submit);

    await waitFor(() => {
      expect(mockCostForecast).toHaveBeenCalledWith(42, 999);
    });
    const gate = await waitFor(() => {
      const el = document.body.querySelector("[data-cost-gate]");
      if (!el) throw new Error("cost gate not rendered yet");
      return el as HTMLElement;
    });
    const heading = within(gate).getByText(/estimated cost \$3\.50/i);
    expect(heading).toBeInTheDocument();
    expect(mockPatchTask).not.toHaveBeenCalled();
    expect(mockDeleteTask).not.toHaveBeenCalled();
  });

  // (b) estimated_usd <= threshold -> NO modal, passes through (create not
  // blocked). Proves the wiring doesn't over-gate the under-threshold case.
  it("does NOT show the modal when the calendar-created task's forecast is at/below the threshold", async () => {
    mockCostForecast.mockResolvedValue(makeForecast(1.0)); // == 1.00 (not >)
    const { user, title } = await renderAndOpenNewTask(makeProject(1.0));

    await user.type(title, "Cheap calendar task");
    const submit = document.body.querySelector(
      "[data-new-task-submit]",
    ) as HTMLButtonElement;
    await user.click(submit);

    await waitFor(() => {
      expect(mockCostForecast).toHaveBeenCalledWith(42, 999);
    });
    expect(document.body.querySelector("[data-cost-gate]")).toBeNull();
  });
});
