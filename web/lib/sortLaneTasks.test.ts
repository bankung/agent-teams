// Unit tests for sortLaneTasks (Kanban #772 AC #4 helper).
//
// NOTE: web/ has no test runner installed — same NO-RUNNER convention as
// parseSteps.test.ts and cycleExclusion.test.ts. This file is
// contract-documentation-as-code; runAll() exits 0 on PASS, 1 on FAIL via
// `docker compose exec -T web sh -c "cd /app && npx tsx -e '...'"`.

import type { TaskRead } from "./api";
import { sortLaneTasks } from "./sortLaneTasks";

type StubArgs = {
  id: number;
  sort_order: number | null;
  created_at: string;
};

function stub({ id, sort_order, created_at }: StubArgs): TaskRead {
  // Minimal TaskRead — only fields read by sortLaneTasks matter. Cast to keep
  // the test stub small; production callers always supply a real TaskRead.
  return {
    id,
    sort_order,
    created_at,
  } as unknown as TaskRead;
}

type Case = {
  name: string;
  input: StubArgs[];
  expected: number[]; // ids in expected output order
};

export const CASES: Case[] = [
  {
    name: "all NULL sort_orders → ordered by created_at ASC",
    input: [
      { id: 3, sort_order: null, created_at: "2026-05-12T03:00:00Z" },
      { id: 1, sort_order: null, created_at: "2026-05-12T01:00:00Z" },
      { id: 2, sort_order: null, created_at: "2026-05-12T02:00:00Z" },
    ],
    expected: [1, 2, 3],
  },
  {
    name: "all non-null → ordered by sort_order ASC",
    input: [
      { id: 3, sort_order: 3.5, created_at: "2026-05-12T01:00:00Z" },
      { id: 1, sort_order: 1.0, created_at: "2026-05-12T03:00:00Z" },
      { id: 2, sort_order: 2.25, created_at: "2026-05-12T02:00:00Z" },
    ],
    expected: [1, 2, 3],
  },
  {
    name: "mixed: non-nulls first (sort_order ASC), then NULLs (created_at ASC)",
    input: [
      { id: 4, sort_order: null, created_at: "2026-05-12T02:00:00Z" },
      { id: 2, sort_order: 2.0, created_at: "2026-05-12T05:00:00Z" },
      { id: 3, sort_order: null, created_at: "2026-05-12T01:00:00Z" },
      { id: 1, sort_order: 1.0, created_at: "2026-05-12T06:00:00Z" },
    ],
    expected: [1, 2, 3, 4],
  },
  {
    name: "duplicate sort_order values → fall back to created_at ASC",
    input: [
      { id: 2, sort_order: 1.0, created_at: "2026-05-12T02:00:00Z" },
      { id: 1, sort_order: 1.0, created_at: "2026-05-12T01:00:00Z" },
      { id: 3, sort_order: 2.0, created_at: "2026-05-12T03:00:00Z" },
    ],
    expected: [1, 2, 3],
  },
  {
    name: "empty input → empty output",
    input: [],
    expected: [],
  },
  {
    name: "single task → unchanged",
    input: [{ id: 7, sort_order: null, created_at: "2026-05-12T00:00:00Z" }],
    expected: [7],
  },
];

export function runAll(): boolean {
  let pass = 0;
  let fail = 0;
  for (const c of CASES) {
    const got = sortLaneTasks(c.input.map(stub)).map((t) => t.id);
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
  console.log(`${pass}/${pass + fail} sort-lane-tasks cases passed`);
  return fail === 0;
}
