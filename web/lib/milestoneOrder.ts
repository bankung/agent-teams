// Pure milestone ordering helpers (extracted for deterministic unit tests).
// TWO different orderings by design:
//  - Gantt (orderGanttMilestones): status rank active>released>planned>cancelled;
//    within the active AND released groups, by start_date asc (nulls last). #2496/#2519.
//  - Picker (orderMilestonesForPicker): active>planned>released; cancelled hidden.

const GANTT_RANK: Record<string, number> = {
  active: 0,
  released: 1,
  planned: 2,
  cancelled: 3,
};

export function orderGanttMilestones<
  T extends { milestone_status: string; start_date: string | null },
>(rows: T[]): T[] {
  return rows
    .map((m, i) => ({ m, i }))
    .sort((a, b) => {
      const rankDiff =
        (GANTT_RANK[a.m.milestone_status] ?? 4) -
        (GANTT_RANK[b.m.milestone_status] ?? 4);
      if (rankDiff !== 0) return rankDiff;
      // Active and released groups → start_date asc, nulls last. Other groups: stable index.
      const g = a.m.milestone_status;
      if (g === b.m.milestone_status && (g === "active" || g === "released")) {
        const sa = a.m.start_date;
        const sb = b.m.start_date;
        if (sa !== sb) {
          if (sa == null) return 1;
          if (sb == null) return -1;
          return sa < sb ? -1 : 1; // ISO date strings sort chronologically
        }
      }
      return a.i - b.i; // stable tiebreak: preserve incoming order
    })
    .map(({ m }) => m);
}

const PICKER_RANK: Record<string, number> = {
  active: 0,
  planned: 1,
  released: 2,
};

export function orderMilestonesForPicker<
  T extends { milestone_status: string },
>(rows: T[]): T[] {
  return rows
    .filter((m) => m.milestone_status !== "cancelled")
    .map((m, i) => ({ m, i }))
    .sort(
      (a, b) =>
        (PICKER_RANK[a.m.milestone_status] ?? 3) -
          (PICKER_RANK[b.m.milestone_status] ?? 3) || a.i - b.i,
    )
    .map(({ m }) => m);
}
