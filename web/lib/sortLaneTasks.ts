// Per-lane task ordering helper (Kanban #772).
//
// Backend authoritative order for a lane: ORDER BY sort_order ASC NULLS LAST,
// created_at ASC. The frontend renders directly from `listTasks()` (which has
// no ORDER BY clause matching this contract), so we re-sort per lane on the
// client before handing the array to BoardColumn.
//
// Stability note: Array.prototype.sort is stable in V8/SpiderMonkey/JSC since
// ES2019. Ties on sort_order fall through to created_at ASC; ties on both
// preserve input order (which itself comes from a backend ORDER BY that
// stabilizes on id ASC in practice).

import type { TaskRead } from "./api";

export function sortLaneTasks(tasks: TaskRead[]): TaskRead[] {
  return [...tasks].sort((a, b) => {
    const ao = a.sort_order;
    const bo = b.sort_order;
    if (ao !== null && bo !== null) {
      if (ao !== bo) return ao - bo;
      // tie on sort_order → fall through to created_at
    } else if (ao !== null) {
      return -1; // a has order; b is NULL → a first
    } else if (bo !== null) {
      return 1; // b has order; a is NULL → b first
    }
    return new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
  });
}

/**
 * Done-lane comparator (Kanban #826) — newest-closed on top.
 *
 * Sort by `updated_at DESC` (the timestamp bumps on the lifecycle PATCH to
 * process_status=5, so this surfaces the most recently flipped-to-done task
 * at the top of the Done column). Stable tiebreaker on `id DESC` for
 * deterministic ordering when two tasks share an updated_at to the
 * microsecond (e.g. bulk-flip via script).
 */
export function sortDoneLane(tasks: TaskRead[]): TaskRead[] {
  return [...tasks].sort((a, b) => {
    const aT = new Date(a.updated_at).getTime();
    const bT = new Date(b.updated_at).getTime();
    if (aT !== bT) return bT - aT; // DESC
    return b.id - a.id; // tiebreaker DESC
  });
}
