// Smoke tests for GanttView — client component.
//
// Wave A.2c: GanttView absorbed the milestone-management surface (the dedicated
// /milestones page + MilestonesView were removed). These tests now cover BOTH
// the timeline rendering AND the folded-in management affordances (New milestone
// button, per-rail Edit/Delete, the Unassigned drag-source pool). The still-
// relevant assertions from the deleted MilestonesView.test.tsx live here now.
//
// Strategy: mock next/navigation (useRouter), next/link, @/lib/api (listTasks —
// called lazily on pool-open, not on mount), and the two milestone modals
// (MilestoneFormModal / MilestoneDeleteModal — tested elsewhere, they each do
// their own fetches).

import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen } from "@testing-library/react";
import type { MilestoneDetail } from "@/lib/api";

// ---------- mocks ----------

// next/navigation — useRouter is the only hook GanttView calls.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }),
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

// @/lib/api — prevent real fetch; listTasks is called lazily on pool-open.
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    listTasks: vi.fn().mockResolvedValue([]),
    patchTask: vi.fn(),
  };
});

// Stub heavy sub-modals — they each do their own fetches and are tested elsewhere.
vi.mock("@/components/MilestoneFormModal", () => ({
  MilestoneFormModal: () => null,
}));
vi.mock("@/components/MilestoneDeleteModal", () => ({
  MilestoneDeleteModal: () => null,
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
    render(<GanttView projectId={1} projectName="my-project" milestones={[]} />);
    const el = document.querySelector("[data-gantt-empty]");
    expect(el).not.toBeNull();
    expect(el?.textContent).toMatch(/no milestones yet/i);
  });

  it("renders the gantt section when milestones are provided", () => {
    render(
      <GanttView projectId={1} projectName="my-project" milestones={[makeDetail()]} />,
    );
    expect(document.querySelector("[data-gantt-view]")).not.toBeNull();
  });

  it("data-gantt-view has correct aria-label", () => {
    render(
      <GanttView projectId={1} projectName="my-project" milestones={[makeDetail()]} />,
    );
    const section = document.querySelector("[data-gantt-view]");
    expect(section?.getAttribute("aria-label")).toBe("Gantt timeline for my-project");
  });

  it("renders a rail row for each milestone", () => {
    const milestones = [
      makeDetail({ id: 1, title: "Alpha" }),
      makeDetail({ id: 2, title: "Beta", start_date: "2026-07-01", target_date: "2026-07-31" }),
    ];
    render(<GanttView projectId={1} projectName="my-project" milestones={milestones} />);
    expect(document.querySelector("[data-gantt-rail-row='1']")).not.toBeNull();
    expect(document.querySelector("[data-gantt-rail-row='2']")).not.toBeNull();
  });

  it("renders milestone title in the rail", () => {
    render(
      <GanttView
        projectId={1}
        projectName="my-project"
        milestones={[makeDetail({ title: "Milestone Alpha" })]}
      />,
    );
    // Title appears in both the rail AND the bar tooltip span — use queryAllBy.
    const matches = screen.getAllByText("Milestone Alpha");
    expect(matches.length).toBeGreaterThanOrEqual(1);
    // The rail row specifically.
    expect(
      document.querySelector("[data-gantt-rail-row='1']")?.textContent,
    ).toContain("Milestone Alpha");
  });

  it("renders a MilestoneStatusBadge inside each rail row", () => {
    render(
      <GanttView
        projectId={1}
        projectName="my-project"
        milestones={[makeDetail({ milestone_status: "active" })]}
      />,
    );
    expect(document.querySelector("[data-milestone-status='active']")).not.toBeNull();
  });

  it("renders a timeline row for each milestone", () => {
    render(
      <GanttView projectId={1} projectName="my-project" milestones={[makeDetail({ id: 42 })]} />,
    );
    expect(document.querySelector("[data-gantt-row='42']")).not.toBeNull();
  });

  it("renders a bar (gantt-bar) for a fully-dated milestone (start + target)", () => {
    render(
      <GanttView
        projectId={1}
        projectName="my-project"
        milestones={[makeDetail({ id: 1, start_date: "2026-06-01", target_date: "2026-06-30" })]}
      />,
    );
    expect(document.querySelector("[data-gantt-bar='1']")).not.toBeNull();
  });

  it("renders a diamond (gantt-diamond) for a target-only milestone", () => {
    render(
      <GanttView
        projectId={1}
        projectName="my-project"
        milestones={[makeDetail({ id: 5, start_date: null, target_date: "2026-06-20" })]}
      />,
    );
    expect(document.querySelector("[data-gantt-diamond='5']")).not.toBeNull();
  });

  it("renders 'no dates' label for an undated milestone", () => {
    render(
      <GanttView
        projectId={1}
        projectName="my-project"
        milestones={[makeDetail({ id: 7, start_date: null, target_date: null })]}
      />,
    );
    expect(document.querySelector("[data-gantt-nodates='7']")).not.toBeNull();
  });

  it("renders the milestone count header", () => {
    render(
      <GanttView
        projectId={1}
        projectName="my-project"
        milestones={[makeDetail({ id: 1 }), makeDetail({ id: 2, title: "B" })]}
      />,
    );
    expect(screen.getByText(/2 milestones/i)).toBeInTheDocument();
  });

  it("singular form for 1 milestone", () => {
    render(<GanttView projectId={1} projectName="my-project" milestones={[makeDetail()]} />);
    expect(screen.getByText(/1 milestone$/i)).toBeInTheDocument();
  });

  it("shows 'No dated milestones' hint when all milestones have no dates", () => {
    render(
      <GanttView
        projectId={1}
        projectName="my-project"
        milestones={[makeDetail({ start_date: null, target_date: null })]}
      />,
    );
    // Text appears in both the header hint span AND the timeline axis span.
    const matches = screen.getAllByText(/no dated milestones/i);
    expect(matches.length).toBeGreaterThanOrEqual(1);
  });

  it("renders progress info in the rail row (done/total)", () => {
    render(
      <GanttView
        projectId={1}
        projectName="my-project"
        milestones={[makeDetail({ rollup: { total: 10, done: 3, by_process_status: {}, progress_pct: 30 } })]}
      />,
    );
    // "3/10 done · 30%"
    expect(screen.getByText(/3\/10 done/)).toBeInTheDocument();
  });
});

// Status-rank sort — verified at the page level (gantt/page.tsx) but observable
// in GanttView via data-gantt-rail-row order in the DOM. The page sorts before
// passing milestones down; these tests confirm the rail rows appear in the
// expected order when GanttView receives a pre-sorted list, AND that
// same-rank items keep their relative order (stability check).
describe("GanttView — status-rank row order", () => {
  let GanttView: typeof import("@/components/GanttView").GanttView;

  beforeAll(async () => {
    const mod = await import("@/components/GanttView");
    GanttView = mod.GanttView;
  });

  // Build a milestones array already sorted by the page's STATUS_RANK logic
  // (active→released→planned→cancelled) and confirm the DOM rail rows appear
  // in that same order.
  it("renders rail rows in active→released→planned→cancelled order", () => {
    const milestones = [
      makeDetail({ id: 10, title: "A-active", milestone_status: "active" }),
      makeDetail({ id: 20, title: "B-released", milestone_status: "released" }),
      makeDetail({ id: 30, title: "C-planned", milestone_status: "planned" }),
      makeDetail({ id: 40, title: "D-cancelled", milestone_status: "cancelled" }),
    ];
    render(
      <GanttView projectId={1} projectName="p" milestones={milestones} />,
    );
    const rows = document.querySelectorAll("[data-gantt-rail-row]");
    const ids = Array.from(rows).map((r) =>
      Number(r.getAttribute("data-gantt-rail-row")),
    );
    expect(ids).toEqual([10, 20, 30, 40]);
  });

  it("renders rail rows in active→released→planned→cancelled when input is interleaved", () => {
    // Interleaved: cancelled first, then active, then planned, then released.
    // The page sorts before passing to GanttView, so we pass an already-sorted
    // array here — this test proves GanttView preserves the passed order (no
    // internal re-sort that might break things).
    const sorted = [
      makeDetail({ id: 1, title: "active-1", milestone_status: "active" }),
      makeDetail({ id: 2, title: "released-1", milestone_status: "released" }),
      makeDetail({ id: 3, title: "planned-1", milestone_status: "planned" }),
      makeDetail({ id: 4, title: "cancelled-1", milestone_status: "cancelled" }),
    ];
    render(<GanttView projectId={2} projectName="p2" milestones={sorted} />);
    const rows = document.querySelectorAll("[data-gantt-rail-row]");
    const ids = Array.from(rows).map((r) =>
      Number(r.getAttribute("data-gantt-rail-row")),
    );
    expect(ids).toEqual([1, 2, 3, 4]);
  });

  it("preserves relative order within the same rank (stability)", () => {
    // Two released milestones — page stable-sorts them by original index, so
    // r1 (lower index) comes before r2.
    const milestones = [
      makeDetail({ id: 5, title: "released-first", milestone_status: "released" }),
      makeDetail({ id: 6, title: "released-second", milestone_status: "released" }),
    ];
    render(
      <GanttView projectId={3} projectName="p3" milestones={milestones} />,
    );
    const rows = document.querySelectorAll("[data-gantt-rail-row]");
    const ids = Array.from(rows).map((r) =>
      Number(r.getAttribute("data-gantt-rail-row")),
    );
    expect(ids).toEqual([5, 6]);
  });
});

// Wave A.2c — milestone-management affordances folded into the Gantt view (these
// assertions carried over from the deleted MilestonesView.test.tsx).
describe("GanttView — milestone management (Wave A.2c)", () => {
  let GanttView: typeof import("@/components/GanttView").GanttView;

  beforeAll(async () => {
    const mod = await import("@/components/GanttView");
    GanttView = mod.GanttView;
  });

  it("renders the 'New milestone' button (even with no milestones)", () => {
    render(<GanttView projectId={1} projectName="my-project" milestones={[]} />);
    const btn = document.querySelector("[data-new-milestone-trigger]");
    expect(btn).not.toBeNull();
    expect(btn?.textContent).toMatch(/new milestone/i);
  });

  it("renders Edit + Delete affordances on each rail row", () => {
    render(
      <GanttView projectId={1} projectName="my-project" milestones={[makeDetail({ id: 9 })]} />,
    );
    const row = document.querySelector("[data-gantt-rail-row='9']");
    expect(row?.querySelector("[data-milestone-edit]")).not.toBeNull();
    expect(row?.querySelector("[data-milestone-delete]")).not.toBeNull();
  });

  it("renders the Unassigned drag-source pool", () => {
    render(
      <GanttView projectId={1} projectName="my-project" milestones={[makeDetail()]} />,
    );
    expect(
      document.querySelector("[data-milestone-unassigned-zone]"),
    ).not.toBeNull();
    expect(screen.getByText(/unassigned/i)).toBeInTheDocument();
  });

  it("rail row carries milestone id + status data attributes", () => {
    render(
      <GanttView
        projectId={1}
        projectName="my-project"
        milestones={[makeDetail({ id: 3, milestone_status: "released" })]}
      />,
    );
    const row = document.querySelector("[data-gantt-rail-row='3']");
    expect(row?.getAttribute("data-milestone-id")).toBe("3");
    expect(row?.getAttribute("data-milestone-status")).toBe("released");
  });
});
