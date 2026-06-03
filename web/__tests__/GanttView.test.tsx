// Smoke tests for GanttView — client component.
// Strategy: mock next/link; pass representative MilestoneDetail props and
// assert key structural elements (empty state, rail rows, bars, diamonds).

import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen } from "@testing-library/react";
import type { MilestoneDetail } from "@/lib/api";

// ---------- mocks ----------

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

function makeDetail(overrides: Partial<MilestoneDetail> = {}): MilestoneDetail {
  return {
    id: 1,
    project_id: 1,
    title: "v1.0 Release",
    description: null,
    milestone_status: "planned",
    start_date: "2026-06-01",
    target_date: "2026-06-30",
    sort_order: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    released_at: null,
    rollup: { total: 5, done: 2, by_process_status: {}, progress_pct: 40 },
    ...overrides,
  } as MilestoneDetail;
}

// ---------- tests ----------

describe("GanttView — smoke", () => {
  let GanttView: typeof import("@/components/GanttView").GanttView;

  beforeAll(async () => {
    const mod = await import("@/components/GanttView");
    GanttView = mod.GanttView;
  });

  it("renders the empty-state when milestones=[]", () => {
    render(<GanttView projectName="my-project" milestones={[]} />);
    const el = document.querySelector("[data-gantt-empty]");
    expect(el).not.toBeNull();
    expect(el?.textContent).toMatch(/no milestones yet/i);
  });

  it("renders the gantt section when milestones are provided", () => {
    render(
      <GanttView
        projectName="my-project"
        milestones={[makeDetail()]}
      />,
    );
    expect(document.querySelector("[data-gantt-view]")).not.toBeNull();
  });

  it("data-gantt-view has correct aria-label", () => {
    render(<GanttView projectName="my-project" milestones={[makeDetail()]} />);
    const section = document.querySelector("[data-gantt-view]");
    expect(section?.getAttribute("aria-label")).toBe("Gantt timeline for my-project");
  });

  it("renders a rail row for each milestone", () => {
    const milestones = [
      makeDetail({ id: 1, title: "Alpha" }),
      makeDetail({ id: 2, title: "Beta", start_date: "2026-07-01", target_date: "2026-07-31" }),
    ];
    render(<GanttView projectName="my-project" milestones={milestones} />);
    expect(document.querySelector("[data-gantt-rail-row='1']")).not.toBeNull();
    expect(document.querySelector("[data-gantt-rail-row='2']")).not.toBeNull();
  });

  it("renders milestone title in the rail", () => {
    render(
      <GanttView
        projectName="my-project"
        milestones={[makeDetail({ title: "Milestone Alpha" })]}
      />,
    );
    // Title appears in both the rail link AND the bar tooltip span — use queryAllBy.
    const matches = screen.getAllByText("Milestone Alpha");
    expect(matches.length).toBeGreaterThanOrEqual(1);
    // The rail link specifically
    expect(document.querySelector("[data-gantt-rail-row='1'] a")).toHaveTextContent("Milestone Alpha");
  });

  it("renders a MilestoneStatusBadge inside each rail row", () => {
    render(
      <GanttView
        projectName="my-project"
        milestones={[makeDetail({ milestone_status: "active" })]}
      />,
    );
    expect(document.querySelector("[data-milestone-status='active']")).not.toBeNull();
  });

  it("renders a timeline row for each milestone", () => {
    render(
      <GanttView
        projectName="my-project"
        milestones={[makeDetail({ id: 42 })]}
      />,
    );
    expect(document.querySelector("[data-gantt-row='42']")).not.toBeNull();
  });

  it("renders a bar (gantt-bar) for a fully-dated milestone (start + target)", () => {
    render(
      <GanttView
        projectName="my-project"
        milestones={[makeDetail({ id: 1, start_date: "2026-06-01", target_date: "2026-06-30" })]}
      />,
    );
    expect(document.querySelector("[data-gantt-bar='1']")).not.toBeNull();
  });

  it("renders a diamond (gantt-diamond) for a target-only milestone", () => {
    render(
      <GanttView
        projectName="my-project"
        milestones={[makeDetail({ id: 5, start_date: null, target_date: "2026-06-20" })]}
      />,
    );
    expect(document.querySelector("[data-gantt-diamond='5']")).not.toBeNull();
  });

  it("renders 'no dates' label for an undated milestone", () => {
    render(
      <GanttView
        projectName="my-project"
        milestones={[makeDetail({ id: 7, start_date: null, target_date: null })]}
      />,
    );
    expect(document.querySelector("[data-gantt-nodates='7']")).not.toBeNull();
  });

  it("renders the milestone count header", () => {
    render(
      <GanttView
        projectName="my-project"
        milestones={[makeDetail({ id: 1 }), makeDetail({ id: 2, title: "B" })]}
      />,
    );
    expect(screen.getByText(/2 milestones/i)).toBeInTheDocument();
  });

  it("singular form for 1 milestone", () => {
    render(<GanttView projectName="my-project" milestones={[makeDetail()]} />);
    expect(screen.getByText(/1 milestone$/i)).toBeInTheDocument();
  });

  it("shows 'No dated milestones' hint when all milestones have no dates", () => {
    render(
      <GanttView
        projectName="my-project"
        milestones={[makeDetail({ start_date: null, target_date: null })]}
      />,
    );
    // Text appears in both the header hint span AND the timeline axis span — at least one must be present.
    const matches = screen.getAllByText(/no dated milestones/i);
    expect(matches.length).toBeGreaterThanOrEqual(1);
  });

  it("renders progress info in the rail row (done/total)", () => {
    render(
      <GanttView
        projectName="my-project"
        milestones={[makeDetail({ rollup: { total: 10, done: 3, by_process_status: {}, progress_pct: 30 } })]}
      />,
    );
    // "3/10 done · 30%"
    expect(screen.getByText(/3\/10 done/)).toBeInTheDocument();
  });
});
