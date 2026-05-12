export type StepCounts = { done: number; total: number };

/**
 * Parse GFM-style task-list checkboxes from a task description.
 * Spec: line starts with optional whitespace, `-` + space, then `[ ]` / `[x]` / `[X]`, then space.
 * Returns null when the description has no checklist lines (total === 0).
 */
export function parseSteps(description: string | null | undefined): StepCounts | null {
  if (!description) return null;
  const re = /^[ \t]*-\s\[( |x|X)\]\s/gm;
  let total = 0;
  let done = 0;
  for (const m of description.matchAll(re)) {
    total++;
    if (m[1] !== " ") done++;
  }
  if (total === 0) return null;
  return { done, total };
}
