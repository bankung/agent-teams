// Unit tests for computeBlockedByExclusionSet (Kanban #771 AC #4).
//
// NOTE: web/ has no test runner installed (no vitest/jest/tsx — see
// parseSteps.test.ts L4-6 for the prior-art convention). This file is
// contract-documentation-as-code. Assertions were verified manually via
// `docker compose exec -T web node -e "..."` during impl; see #771 session
// report for the verbatim PASS log.

import { computeBlockedByExclusionSet, MAX_DEPTH } from "./cycleExclusion";

type Stub = { id: number; blocked_by: number | null };

type Case = {
  name: string;
  tasks: Stub[];
  target: number;
  expected: number[]; // sorted asc
};

export const CASES: Case[] = [
  {
    name: "empty list → only self excluded",
    tasks: [],
    target: 5,
    expected: [5],
  },
  {
    name: "single isolated task → only self",
    tasks: [{ id: 5, blocked_by: null }],
    target: 5,
    expected: [5],
  },
  {
    name: "linear chain A(1)→B(2)→C(3), target=B → {1 ancestor, 2 self, 3 descendant}",
    // C(3).blocked_by = B(2); B(2).blocked_by = A(1). Picking B's blocker → exclude A (ancestor) + C (descendant).
    tasks: [
      { id: 1, blocked_by: null },
      { id: 2, blocked_by: 1 },
      { id: 3, blocked_by: 2 },
    ],
    target: 2,
    expected: [1, 2, 3],
  },
  {
    name: "branching: A(1) is blocker of B(2) and C(3); D(4) blocks A. target=A → {4 ancestor, 1 self, 2+3 descendants}",
    tasks: [
      { id: 1, blocked_by: 4 },
      { id: 2, blocked_by: 1 },
      { id: 3, blocked_by: 1 },
      { id: 4, blocked_by: null },
    ],
    target: 1,
    expected: [1, 2, 3, 4],
  },
  {
    name: "transitive descendants: A blocks B blocks C; target=A excludes B and C",
    tasks: [
      { id: 1, blocked_by: null },
      { id: 2, blocked_by: 1 },
      { id: 3, blocked_by: 2 },
    ],
    target: 1,
    expected: [1, 2, 3],
  },
  {
    name: "unrelated tasks NOT excluded",
    tasks: [
      { id: 1, blocked_by: null },
      { id: 2, blocked_by: 1 },
      { id: 99, blocked_by: null }, // unrelated
      { id: 100, blocked_by: 99 }, // unrelated chain
    ],
    target: 1,
    expected: [1, 2], // 99, 100 are reachable from neither direction
  },
  {
    name: "depth cap: 200-link forward chain bails at MAX_DEPTH",
    // Build chain 1 → 2 → 3 → ... → 200 (each blocked_by next).
    // target=1; ancestors chain is empty (1.blocked_by=2, walker goes 1→2→3→...)
    // Walker excludes target + MAX_DEPTH more ancestors then stops.
    tasks: Array.from({ length: 200 }, (_, i) => ({
      id: i + 1,
      blocked_by: i + 2 <= 200 ? i + 2 : null,
    })),
    target: 1,
    // self (1) + ancestors (2..101) = 101 items via forward walker.
    // Descendants of 1 via reverse-edge: nothing points to 1 (1 is the start).
    expected: Array.from({ length: MAX_DEPTH + 1 }, (_, i) => i + 1),
  },
];

export function runAll() {
  let pass = 0;
  let fail = 0;
  for (const c of CASES) {
    const got = Array.from(
      computeBlockedByExclusionSet(c.tasks, c.target),
    ).sort((a, b) => a - b);
    const ok =
      got.length === c.expected.length &&
      got.every((v, i) => v === c.expected[i]);
    if (ok) {
      pass++;
    } else {
      fail++;
      console.error(
        `FAIL ${c.name}\n  expected: ${JSON.stringify(c.expected)}\n  got:      ${JSON.stringify(got)}`,
      );
    }
  }
  console.log(`${pass}/${pass + fail} cycle-exclusion cases passed`);
  return fail === 0;
}
