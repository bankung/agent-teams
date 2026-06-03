// Smoke tests for CalendarView — client component.
// Strategy: mock next/navigation (useRouter) and next/link; pass representative
// props (tasks + milestones) and assert key structural elements.

import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen } from "@testing-library/react";
import type { TaskRead, MilestoneRead } from "@/lib/api";
import { TaskStatus } from "@/lib/constants";

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

// ---------- helpers ----------

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
    due_date: "2026-06-15",
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

function makeMilestone(overrides: Partial<MilestoneRead> = {}): MilestoneRead {
  return {
    id: 1,
    project_id: 1,
    title: "v1.0 Release",
    description: null,
    milestone_status: "planned",
    start_date: "2026-06-01",
    target_date: "2026-06-20",
    sort_order: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    released_at: null,
    ...overrides,
  } as MilestoneRead;
}

// ---------- tests ----------

describe("CalendarView — smoke", () => {
  let CalendarView: typeof import("@/components/CalendarView").CalendarView;

  beforeAll(async () => {
    const mod = await import("@/components/CalendarView");
    CalendarView = mod.CalendarView;
  });

  it("renders without crashing with empty tasks and milestones", () => {
    const { container } = render(
      <CalendarView
        projectName="my-project"
        year={2026}
        month0={5}
        tasks={[]}
        milestones={[]}
      />,
    );
    expect(container.firstChild).not.toBeNull();
  });

  it("renders the month label (June 2026)", () => {
    render(
      <CalendarView
        projectName="my-project"
        year={2026}
        month0={5}
        tasks={[]}
        milestones={[]}
      />,
    );
    expect(screen.getByText("June 2026")).toBeInTheDocument();
  });

  it("renders all 7 weekday headers", () => {
    render(
      <CalendarView
        projectName="my-project"
        year={2026}
        month0={5}
        tasks={[]}
        milestones={[]}
      />,
    );
    for (const label of ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("renders the calendar grid container", () => {
    render(
      <CalendarView
        projectName="my-project"
        year={2026}
        month0={5}
        tasks={[]}
        milestones={[]}
      />,
    );
    expect(document.querySelector("[data-calendar-grid]")).not.toBeNull();
  });

  it("renders Prev / Today / Next navigation buttons", () => {
    render(
      <CalendarView
        projectName="my-project"
        year={2026}
        month0={5}
        tasks={[]}
        milestones={[]}
      />,
    );
    expect(document.querySelector("[data-calendar-prev]")).not.toBeNull();
    expect(document.querySelector("[data-calendar-today]")).not.toBeNull();
    expect(document.querySelector("[data-calendar-next]")).not.toBeNull();
  });

  it("shows empty-state message when no tasks and no milestone deadlines", () => {
    render(
      <CalendarView
        projectName="my-project"
        year={2026}
        month0={5}
        tasks={[]}
        milestones={[]}
      />,
    );
    expect(document.querySelector("[data-calendar-empty]")).not.toBeNull();
  });

  it("does NOT show empty-state when there is a task", () => {
    render(
      <CalendarView
        projectName="my-project"
        year={2026}
        month0={5}
        tasks={[makeTask({ due_date: "2026-06-15" })]}
        milestones={[]}
      />,
    );
    expect(document.querySelector("[data-calendar-empty]")).toBeNull();
  });

  it("renders a task chip with the task title", () => {
    render(
      <CalendarView
        projectName="my-project"
        year={2026}
        month0={5}
        tasks={[makeTask({ title: "Launch feature", due_date: "2026-06-15" })]}
        milestones={[]}
      />,
    );
    expect(screen.getByText("Launch feature")).toBeInTheDocument();
  });

  it("renders a task chip correctly for different statuses", () => {
    const statuses = [
      TaskStatus.TODO,
      TaskStatus.IN_PROGRESS,
      TaskStatus.REVIEW,
      TaskStatus.DONE,
    ] as const;
    for (const status of statuses) {
      const { unmount } = render(
        <CalendarView
          projectName="my-project"
          year={2026}
          month0={5}
          tasks={[makeTask({ title: `task-${status}`, due_date: "2026-06-10", process_status: status })]}
          milestones={[]}
        />,
      );
      expect(screen.getByText(`task-${status}`)).toBeInTheDocument();
      unmount();
    }
  });

  it("renders multiple tasks on the same day", () => {
    const tasks = [
      makeTask({ id: 1, title: "Task A", due_date: "2026-06-10" }),
      makeTask({ id: 2, title: "Task B", due_date: "2026-06-10" }),
    ];
    render(
      <CalendarView
        projectName="my-project"
        year={2026}
        month0={5}
        tasks={tasks}
        milestones={[]}
      />,
    );
    expect(screen.getByText("Task A")).toBeInTheDocument();
    expect(screen.getByText("Task B")).toBeInTheDocument();
  });

  it("does NOT render a task due outside the current month's grid", () => {
    // A task due in August should not appear in a June grid render.
    // (The server already filters, but we verify the component drops them too.)
    render(
      <CalendarView
        projectName="my-project"
        year={2026}
        month0={5}
        tasks={[makeTask({ title: "Far future task", due_date: "2026-08-01" })]}
        milestones={[]}
      />,
    );
    // The component only renders chips for cells in the visible grid; an August
    // date is outside the 35-cell grid — it won't be in a bucket.
    expect(screen.queryByText("Far future task")).toBeNull();
  });

  it("renders with milestones provided (no crash)", () => {
    render(
      <CalendarView
        projectName="my-project"
        year={2026}
        month0={5}
        tasks={[]}
        milestones={[makeMilestone({ target_date: "2026-06-20" })]}
      />,
    );
    // The section wrapper should still render
    expect(document.querySelector("[data-calendar-view]")).not.toBeNull();
  });

  it("data-calendar-view section has correct aria-label", () => {
    render(
      <CalendarView
        projectName="my-project"
        year={2026}
        month0={5}
        tasks={[]}
        milestones={[]}
      />,
    );
    const section = document.querySelector("[data-calendar-view]");
    expect(section?.getAttribute("aria-label")).toBe("Calendar for my-project");
  });
});
