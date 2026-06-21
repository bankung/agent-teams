// Pure unit tests for lib/milestoneOrder.ts — no RTL, no mocks.
// Two separate orderings by design; covered independently.

import { describe, it, expect } from "vitest";
import {
  orderGanttMilestones,
  orderMilestonesForPicker,
} from "@/lib/milestoneOrder";

// ---------------------------------------------------------------------------
// Shared minimal fixture type
// ---------------------------------------------------------------------------
type GanttRow = { id: number; milestone_status: string; start_date: string | null };
type PickerRow = { id: number; milestone_status: string };

// ---------------------------------------------------------------------------
// orderGanttMilestones
// ---------------------------------------------------------------------------
describe("orderGanttMilestones", () => {
  it("group order is active → released → planned → cancelled", () => {
    const rows: GanttRow[] = [
      { id: 1, milestone_status: "cancelled", start_date: null },
      { id: 2, milestone_status: "planned", start_date: null },
      { id: 3, milestone_status: "released", start_date: "2026-01-01" },
      { id: 4, milestone_status: "active", start_date: null },
    ];
    const result = orderGanttMilestones(rows);
    expect(result.map((r) => r.milestone_status)).toEqual([
      "active",
      "released",
      "planned",
      "cancelled",
    ]);
  });

  it("within released, orders by start_date ascending", () => {
    const rows: GanttRow[] = [
      { id: 1, milestone_status: "released", start_date: "2026-06-01" },
      { id: 2, milestone_status: "released", start_date: "2026-01-01" },
      { id: 3, milestone_status: "released", start_date: "2025-12-01" },
    ];
    const result = orderGanttMilestones(rows);
    expect(result.map((r) => r.id)).toEqual([3, 2, 1]);
  });

  it("within released, null start_date sorts last", () => {
    const rows: GanttRow[] = [
      { id: 1, milestone_status: "released", start_date: null },
      { id: 2, milestone_status: "released", start_date: "2026-03-01" },
      { id: 3, milestone_status: "released", start_date: null },
      { id: 4, milestone_status: "released", start_date: "2026-01-01" },
    ];
    const result = orderGanttMilestones(rows);
    // dated ones first (4, 2), then null ones (1, 3 — stable input order)
    expect(result[0].id).toBe(4);
    expect(result[1].id).toBe(2);
    expect(result.slice(2).map((r) => r.start_date)).toEqual([null, null]);
  });

  it("non-released groups preserve input order (stable)", () => {
    const rows: GanttRow[] = [
      { id: 10, milestone_status: "planned", start_date: null },
      { id: 11, milestone_status: "planned", start_date: "2026-01-01" },
      { id: 12, milestone_status: "planned", start_date: null },
    ];
    const result = orderGanttMilestones(rows);
    expect(result.map((r) => r.id)).toEqual([10, 11, 12]);
  });

  it("within active, orders by start_date ascending", () => {
    const rows: GanttRow[] = [
      { id: 1, milestone_status: "active", start_date: "2026-06-01" },
      { id: 2, milestone_status: "active", start_date: "2026-01-01" },
      { id: 3, milestone_status: "active", start_date: "2025-12-01" },
    ];
    const result = orderGanttMilestones(rows);
    expect(result.map((r) => r.id)).toEqual([3, 2, 1]);
  });

  it("within active, null start_date sorts last", () => {
    const rows: GanttRow[] = [
      { id: 1, milestone_status: "active", start_date: null },
      { id: 2, milestone_status: "active", start_date: "2026-03-01" },
      { id: 3, milestone_status: "active", start_date: null },
      { id: 4, milestone_status: "active", start_date: "2026-01-01" },
    ];
    const result = orderGanttMilestones(rows);
    // dated ones first (4, 2), then null ones (1, 3 — stable input order)
    expect(result[0].id).toBe(4);
    expect(result[1].id).toBe(2);
    expect(result.slice(2).map((r) => r.start_date)).toEqual([null, null]);
  });

  it("unknown status ranks last (after cancelled)", () => {
    const rows: GanttRow[] = [
      { id: 1, milestone_status: "unknown", start_date: null },
      { id: 2, milestone_status: "cancelled", start_date: null },
      { id: 3, milestone_status: "active", start_date: null },
    ];
    const result = orderGanttMilestones(rows);
    expect(result[0].milestone_status).toBe("active");
    expect(result[1].milestone_status).toBe("cancelled");
    expect(result[2].milestone_status).toBe("unknown");
  });

  it("returns empty array for empty input", () => {
    expect(orderGanttMilestones([])).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// orderMilestonesForPicker
// ---------------------------------------------------------------------------
describe("orderMilestonesForPicker", () => {
  it("removes cancelled rows", () => {
    const rows: PickerRow[] = [
      { id: 1, milestone_status: "cancelled" },
      { id: 2, milestone_status: "active" },
      { id: 3, milestone_status: "cancelled" },
    ];
    const result = orderMilestonesForPicker(rows);
    expect(result.every((r) => r.milestone_status !== "cancelled")).toBe(true);
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe(2);
  });

  it("orders active → planned → released", () => {
    const rows: PickerRow[] = [
      { id: 1, milestone_status: "released" },
      { id: 2, milestone_status: "planned" },
      { id: 3, milestone_status: "active" },
    ];
    const result = orderMilestonesForPicker(rows);
    expect(result.map((r) => r.milestone_status)).toEqual([
      "active",
      "planned",
      "released",
    ]);
  });

  it("is stable within each rank (preserves input order)", () => {
    const rows: PickerRow[] = [
      { id: 10, milestone_status: "planned" },
      { id: 11, milestone_status: "planned" },
      { id: 12, milestone_status: "active" },
      { id: 13, milestone_status: "active" },
    ];
    const result = orderMilestonesForPicker(rows);
    // active first (12, 13 in original order), then planned (10, 11)
    expect(result.map((r) => r.id)).toEqual([12, 13, 10, 11]);
  });

  it("unknown status ranks after released (rank 3)", () => {
    const rows: PickerRow[] = [
      { id: 1, milestone_status: "released" },
      { id: 2, milestone_status: "unknown" },
      { id: 3, milestone_status: "active" },
    ];
    const result = orderMilestonesForPicker(rows);
    expect(result.map((r) => r.milestone_status)).toEqual([
      "active",
      "released",
      "unknown",
    ]);
  });

  it("returns empty array for empty input", () => {
    expect(orderMilestonesForPicker([])).toEqual([]);
  });

  it("returns empty array when all rows are cancelled", () => {
    const rows: PickerRow[] = [
      { id: 1, milestone_status: "cancelled" },
      { id: 2, milestone_status: "cancelled" },
    ];
    expect(orderMilestonesForPicker(rows)).toEqual([]);
  });
});
