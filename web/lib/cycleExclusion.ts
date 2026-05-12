// computeBlockedByExclusionSet — Kanban #771 AC #4.
//
// Given a project's full task list and a target task id T, return the set of
// task ids that must be EXCLUDED from T's "blocked_by" picker:
//
//   1. T itself (self-reference is invalid — API returns 422, but hide it pre-flight).
//   2. Ancestors of T — every task T transitively depends on via its own
//      blocked_by chain. (Not strictly a cycle, but UX-noisy: a task already
//      "above" T in the chain shouldn't be re-picked.)
//   3. Descendants of T — every task X such that X.blocked_by transitively
//      traces back to T. Picking one of these would create a cycle.
//
// Pure function — no React, no I/O. Walks the in-memory graph already held
// by Board. Cap at MAX_DEPTH=100 defensively; real chains are 1-3 deep
// (the API enforces depth ≤ 10 on writes).

import type { TaskRead } from "./api";

export const MAX_DEPTH = 100;

type IdAccessor = Pick<TaskRead, "id" | "blocked_by">;

export function computeBlockedByExclusionSet(
  tasks: ReadonlyArray<IdAccessor>,
  targetId: number,
): Set<number> {
  const excluded = new Set<number>();
  excluded.add(targetId);

  // Index by id for O(1) ancestor lookups (forward edge: task → blocker).
  const byId = new Map<number, IdAccessor>();
  for (const t of tasks) byId.set(t.id, t);

  // Ancestors: walk forward via blocked_by.
  let cursor: number | null | undefined = byId.get(targetId)?.blocked_by;
  let steps = 0;
  while (cursor != null && steps < MAX_DEPTH) {
    if (excluded.has(cursor)) break; // already seen — defensive cycle break
    excluded.add(cursor);
    cursor = byId.get(cursor)?.blocked_by ?? null;
    steps++;
  }

  // Descendants: BFS over reverse edges. A task X is a descendant of T iff
  // X.blocked_by chains back to T. Build the reverse adjacency on the fly.
  const reverse = new Map<number, number[]>();
  for (const t of tasks) {
    if (t.blocked_by != null) {
      const bucket = reverse.get(t.blocked_by) ?? [];
      bucket.push(t.id);
      reverse.set(t.blocked_by, bucket);
    }
  }
  const queue: Array<{ id: number; depth: number }> = [
    { id: targetId, depth: 0 },
  ];
  while (queue.length > 0) {
    const { id, depth } = queue.shift()!;
    if (depth >= MAX_DEPTH) continue;
    const children = reverse.get(id) ?? [];
    for (const cid of children) {
      if (excluded.has(cid)) continue;
      excluded.add(cid);
      queue.push({ id: cid, depth: depth + 1 });
    }
  }

  return excluded;
}
