// Tests for #2122 N1: buildColumnPs derives the column-key→process_status map
// from the columns prop instead of a hardcoded literal.
//
// buildColumnPs is a pure exported helper — no React, no DOM, no mocks needed.

import { describe, it, expect } from "vitest";
import { buildColumnPs } from "@/components/BoardDndCanvas";
import { TaskStatus } from "@/lib/constants";

describe("buildColumnPs (#2122 N1)", () => {
  it("maps each column key to its first status for the current 5-column set (#2477)", () => {
    // #2477 — ps=8 merged into Blocked (key "4"); no standalone key "8" column.
    const columns = [
      { key: "1", statuses: [TaskStatus.TODO], label: "New tasks" },
      { key: "2", statuses: [TaskStatus.IN_PROGRESS], label: "In progress" },
      { key: "3", statuses: [TaskStatus.REVIEW], label: "Review" },
      { key: "4", statuses: [TaskStatus.BLOCKED, TaskStatus.HALTED_PENDING_USER], label: "Blocked" },
      { key: "5", statuses: [TaskStatus.DONE], label: "Done" },
    ];
    const map = buildColumnPs(columns);
    expect(map["1"]).toBe(TaskStatus.TODO);
    expect(map["2"]).toBe(TaskStatus.IN_PROGRESS);
    expect(map["3"]).toBe(TaskStatus.REVIEW);
    // statuses[0] is BLOCKED — drop onto Blocked column → ps=4 (ps=8 is engine-set).
    expect(map["4"]).toBe(TaskStatus.BLOCKED);
    expect(map["5"]).toBe(TaskStatus.DONE);
    // No standalone "8" key.
    expect("8" in map).toBe(false);
  });

  it("uses statuses[0]=BLOCKED (not HALTED_PENDING_USER) for the merged Blocked column (#2477)", () => {
    const columns = [
      { key: "4", statuses: [TaskStatus.BLOCKED, TaskStatus.HALTED_PENDING_USER], label: "Blocked" },
    ];
    const map = buildColumnPs(columns);
    expect(map["4"]).toBe(TaskStatus.BLOCKED);
  });

  it("produces exactly as many entries as columns with non-empty statuses", () => {
    const columns = [
      { key: "1", statuses: [TaskStatus.TODO], label: "New tasks" },
      { key: "2", statuses: [TaskStatus.IN_PROGRESS], label: "In progress" },
    ];
    const map = buildColumnPs(columns);
    expect(Object.keys(map)).toHaveLength(2);
  });

  it("skips columns with empty statuses array (guard against malformed input)", () => {
    const columns = [
      { key: "1", statuses: [TaskStatus.TODO], label: "New tasks" },
      { key: "bad", statuses: [], label: "Empty" },
    ];
    const map = buildColumnPs(columns);
    expect("bad" in map).toBe(false);
    expect(map["1"]).toBe(TaskStatus.TODO);
  });

  it("returns empty object for empty columns array", () => {
    expect(buildColumnPs([])).toEqual({});
  });

  it("uses statuses[0] (not later elements) when a column has multiple statuses", () => {
    const columns = [
      { key: "x", statuses: [TaskStatus.REVIEW, TaskStatus.BLOCKED], label: "Multi" },
    ];
    const map = buildColumnPs(columns);
    expect(map["x"]).toBe(TaskStatus.REVIEW);
  });
});
