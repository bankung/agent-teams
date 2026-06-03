// Smoke tests for MilestonesView — client component.
// Strategy: mock next/navigation (useRouter) and @/lib/api (listTasks) so the
// component renders in jsdom without a Next.js runtime. We also stub the
// sub-modal components (MilestoneFormModal, MilestoneDeleteModal) to avoid
// pulling in their full fetch dependencies.

import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen } from "@testing-library/react";
import type { MilestoneDetail } from "@/lib/api";

// ---------- mocks ----------

// next/navigation — useRouter is the only hook MilestonesView calls.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }),
}));

// next/link — render as a plain <a> so RTL doesn't complain about missing context.
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode; [k: string]: unknown }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

// @/lib/api — prevent real fetch; listTasks is called lazily on expand, not on mount.
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    listTasks: vi.fn().mockResolvedValue([]),
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

function makeMilestone(overrides: Partial<MilestoneDetail> = {}): MilestoneDetail {
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
  };
}

// ---------- tests ----------

describe("MilestonesView — smoke", () => {
  // Import lazily so mocks are applied first (vi.mock is hoisted, but using
  // dynamic import keeps the pattern explicit).
  let MilestonesView: typeof import("@/components/MilestonesView").MilestonesView;

  beforeAll(async () => {
    const mod = await import("@/components/MilestonesView");
    MilestonesView = mod.MilestonesView;
  });

  it("renders empty-state message when milestones=[]", () => {
    render(<MilestonesView projectId={1} projectName="my-project" milestones={[]} />);
    // The empty-state paragraph has data-milestones-empty
    const el = document.querySelector("[data-milestones-empty]");
    expect(el).not.toBeNull();
    expect(el?.textContent).toMatch(/no milestones yet/i);
  });

  it("renders the milestone list container when milestones are provided", () => {
    const milestones = [makeMilestone(), makeMilestone({ id: 2, title: "Beta" })];
    render(<MilestonesView projectId={1} projectName="my-project" milestones={milestones} />);
    expect(document.querySelector("[data-milestones-list]")).not.toBeNull();
  });

  it("renders a card for each milestone", () => {
    const milestones = [
      makeMilestone({ id: 1, title: "Milestone Alpha" }),
      makeMilestone({ id: 2, title: "Milestone Beta" }),
    ];
    render(<MilestonesView projectId={1} projectName="my-project" milestones={milestones} />);
    expect(screen.getByText("Milestone Alpha")).toBeInTheDocument();
    expect(screen.getByText("Milestone Beta")).toBeInTheDocument();
  });

  it("renders milestone title inside a card", () => {
    render(
      <MilestonesView
        projectId={1}
        projectName="my-project"
        milestones={[makeMilestone({ title: "v1.0 Release" })]}
      />,
    );
    expect(screen.getByText("v1.0 Release")).toBeInTheDocument();
  });

  it("renders the MilestoneStatusBadge (visible status text) inside each card", () => {
    render(
      <MilestonesView
        projectId={1}
        projectName="my-project"
        milestones={[makeMilestone({ milestone_status: "active" })]}
      />,
    );
    // The badge renders the status label as text
    expect(screen.getByText("active")).toBeInTheDocument();
  });

  it("renders a progress bar with correct aria-valuenow", () => {
    render(
      <MilestonesView
        projectId={1}
        projectName="my-project"
        milestones={[makeMilestone({ rollup: { total: 10, done: 4, by_process_status: {}, progress_pct: 40 } })]}
      />,
    );
    const bar = document.querySelector("[role='progressbar']");
    expect(bar).not.toBeNull();
    expect(bar?.getAttribute("aria-valuenow")).toBe("40");
  });

  it("renders the 'New milestone' button", () => {
    render(<MilestonesView projectId={1} projectName="my-project" milestones={[]} />);
    expect(screen.getByText(/new milestone/i)).toBeInTheDocument();
  });

  it("header count reflects number of milestones", () => {
    const milestones = [makeMilestone(), makeMilestone({ id: 2, title: "B" })];
    render(<MilestonesView projectId={1} projectName="my-project" milestones={milestones} />);
    // "2 milestones"
    expect(screen.getByText(/2 milestones/i)).toBeInTheDocument();
  });

  it("singular form for 1 milestone", () => {
    render(
      <MilestonesView
        projectId={1}
        projectName="my-project"
        milestones={[makeMilestone()]}
      />,
    );
    expect(screen.getByText(/1 milestone$/i)).toBeInTheDocument();
  });

  it("renders date range string in the card", () => {
    render(
      <MilestonesView
        projectId={1}
        projectName="my-project"
        milestones={[makeMilestone({ start_date: "2026-06-01", target_date: "2026-06-30" })]}
      />,
    );
    expect(screen.getByText(/2026-06-01/)).toBeInTheDocument();
    expect(screen.getByText(/2026-06-30/)).toBeInTheDocument();
  });

  it("renders 'no dates set' when both dates are null", () => {
    render(
      <MilestonesView
        projectId={1}
        projectName="my-project"
        milestones={[makeMilestone({ start_date: null, target_date: null })]}
      />,
    );
    expect(screen.getByText(/no dates set/i)).toBeInTheDocument();
  });

  it("renders milestone description when present", () => {
    render(
      <MilestonesView
        projectId={1}
        projectName="my-project"
        milestones={[makeMilestone({ description: "This release ships the calendar." })]}
      />,
    );
    expect(screen.getByText("This release ships the calendar.")).toBeInTheDocument();
  });
});
