// Tests for #2419: CrossProjectActiveTasksList suppresses the "blocked by"
// chip when blocked_by_terminal=true (server signals blocker is DONE/CANCELLED).

import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import type { DashboardActiveTasks, DashboardActiveTaskRow } from "@/lib/api";
import { CrossProjectActiveTasksList } from "@/components/CrossProjectActiveTasksList";

// ---------------------------------------------------------------------------
// Fixture helpers
// ---------------------------------------------------------------------------

function makeRow(overrides: Partial<DashboardActiveTaskRow>): DashboardActiveTaskRow {
  return {
    task_id: 1,
    title: "test task",
    project_id: 10,
    project_name: "test-project",
    team: "dev",
    process_status: 2,
    run_mode: "manual",
    task_kind: "feature",
    assigned_role: null,
    priority: 2,
    updated_at: "2026-01-01T00:00:00Z",
    blocked_by: null,
    blocked_by_terminal: false,
    ...overrides,
  } as DashboardActiveTaskRow;
}

function makeData(rows: DashboardActiveTaskRow[]): DashboardActiveTasks {
  return { rows, total_count: rows.length };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("CrossProjectActiveTasksList — blocked chip (#2419)", () => {
  const rows = [
    // Row A: blocked, blocker is terminal → chip MUST be hidden
    makeRow({ task_id: 1, blocked_by: 999, blocked_by_terminal: true }),
    // Row B: blocked, blocker is NOT terminal → chip MUST be shown
    makeRow({ task_id: 2, blocked_by: 888, blocked_by_terminal: false }),
    // Row C: not blocked at all → no chip
    makeRow({ task_id: 3, blocked_by: null, blocked_by_terminal: false }),
  ];

  it("hides chip when blocked_by=999, blocked_by_terminal=true", () => {
    render(<CrossProjectActiveTasksList data={makeData(rows)} />);
    const link = document.querySelector<HTMLAnchorElement>('a[href="/tasks/999"]');
    expect(link).toBeNull();
  });

  it("shows chip when blocked_by=888, blocked_by_terminal=false", () => {
    render(<CrossProjectActiveTasksList data={makeData(rows)} />);
    const link = document.querySelector<HTMLAnchorElement>('a[href="/tasks/888"]');
    expect(link).not.toBeNull();
    expect(link?.textContent).toContain("888");
  });

  it("shows no chip when blocked_by=null", () => {
    // Render only the null-blocked row to isolate the assertion.
    const nullRow = makeRow({ task_id: 3, blocked_by: null, blocked_by_terminal: false });
    render(<CrossProjectActiveTasksList data={makeData([nullRow])} />);
    // No BlockedByChip renders when blocked_by is null — no blocker link at all
    // beyond the task's own self-navigation link.
    const selfLink = document.querySelector<HTMLAnchorElement>('a[href="/tasks/3"]');
    expect(selfLink).not.toBeNull(); // self-link present
    // The only /tasks/* links are the two self-links (task id in the row); no blocker chip.
    const allTaskLinks = document.querySelectorAll<HTMLAnchorElement>('a[href^="/tasks/"]');
    // Both self-links go to /tasks/3; no extra blocker link exists.
    for (const link of Array.from(allTaskLinks)) {
      expect(link.getAttribute("href")).toBe("/tasks/3");
    }
  });
});
