// Tests for #2429: CrossProjectActiveTasksList StatusChip fallback for
// unmapped process_status values (e.g. HALTED_PENDING_USER=8 or future codes).

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import type { DashboardActiveTasks, DashboardActiveTaskRow } from "@/lib/api";
import { CrossProjectActiveTasksList } from "@/components/CrossProjectActiveTasksList";

// ---------------------------------------------------------------------------
// Fixture helpers (mirrors CrossProjectActiveTasksList.blockedChip.test.tsx)
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

describe("CrossProjectActiveTasksList — StatusChip fallback (#2429)", () => {
  it("renders fallback label ps99 for unmapped process_status=99 without throwing", () => {
    const row = makeRow({
      task_id: 42,
      // process_status is typed 2|3|4 on DashboardActiveTaskRow (documents BE contract);
      // cast to exercise the contract-violation defense path.
      process_status: 99 as unknown as DashboardActiveTaskRow["process_status"],
    });

    // Must not throw.
    expect(() =>
      render(<CrossProjectActiveTasksList data={makeData([row])} />),
    ).not.toThrow();

    // The chip should render the fallback label "ps99".
    expect(screen.getByText("ps99")).toBeInTheDocument();
  });

  it("renders fallback label ps8 for process_status=8 (HALTED_PENDING_USER)", () => {
    const row = makeRow({
      task_id: 43,
      process_status: 8 as unknown as DashboardActiveTaskRow["process_status"],
    });

    render(<CrossProjectActiveTasksList data={makeData([row])} />);

    expect(screen.getByText("ps8")).toBeInTheDocument();
  });
});
