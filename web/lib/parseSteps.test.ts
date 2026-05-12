// Unit tests for parseSteps.
//
// NOTE: web/ has no test runner installed (no vitest/jest/tsx — see #783 lint debt).
// This file documents the contract as executable code; assertions were verified manually
// via a one-shot Node script during #773 implementation (see session report).
// When a runner is added (proposed: vitest), this file should work as-is.

import { parseSteps } from "./parseSteps";

type Case = { name: string; input: string | null | undefined; expected: { done: number; total: number } | null };

export const CASES: Case[] = [
  { name: "null", input: null, expected: null },
  { name: "undefined", input: undefined, expected: null },
  { name: "empty string", input: "", expected: null },
  { name: "no checklist text", input: "Just a paragraph.\nAnother line.", expected: null },
  { name: "single unchecked", input: "- [ ] step", expected: { done: 0, total: 1 } },
  { name: "single checked", input: "- [x] step", expected: { done: 1, total: 1 } },
  { name: "uppercase X is done", input: "- [X] step", expected: { done: 1, total: 1 } },
  {
    name: "mixed lines",
    input: "Header\n- [x] a\n- [ ] b\n- [X] c\nplain line\n- [ ] d",
    expected: { done: 2, total: 4 },
  },
  { name: "indented checkboxes still count", input: "  - [ ] nested\n\t- [x] tabbed", expected: { done: 1, total: 2 } },
  { name: "asterisk list NOT counted", input: "* [ ] not a hyphen", expected: null },
  { name: "bracket without hyphen NOT counted", input: "[ ] orphan", expected: null },
  { name: "all checked", input: "- [x] a\n- [X] b\n- [x] c", expected: { done: 3, total: 3 } },
];

// Hand-runnable assertion helper (no test framework needed).
export function runAll() {
  let pass = 0;
  let fail = 0;
  for (const c of CASES) {
    const got = parseSteps(c.input);
    const ok = JSON.stringify(got) === JSON.stringify(c.expected);
    if (ok) pass++;
    else {
      fail++;
      console.error(`FAIL ${c.name}: expected ${JSON.stringify(c.expected)} got ${JSON.stringify(got)}`);
    }
  }
  console.log(`${pass}/${pass + fail} cases passed`);
  return fail === 0;
}
