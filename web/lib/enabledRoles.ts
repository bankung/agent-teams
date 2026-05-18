// Kanban #7 Section A AC#3 — per-project role whitelist.
// Reads project.config.enabled_roles: number[] (TaskRole codes) and narrows
// dropdown option lists in NewTaskModal + AiTaskModal.
//
// Semantics (per spawn brief):
//   - missing  (undefined / not in config)   → show ALL roles (current behaviour)
//   - null                                   → show ALL roles
//   - []       (empty array)                 → show ALL roles
//   - [1,2]    (non-empty number[])          → show ONLY those role codes
//
// Anything not a number-array → ALL roles (defensive: malformed config never
// hides options from the user). Non-number entries inside the array are
// silently dropped; remaining numeric codes drive the filter.
//
// The unassigned sentinel ("" / null role value) is NEVER filtered out —
// only the typed-role entries are touched.

import type { TaskRoleValue } from "./constants";

export type RoleOption<V> = { value: V; label: string };

/**
 * Read a number[] under `config.enabled_roles`. Returns null when the entry
 * is missing, null, an empty array, or any non-number-array shape — caller
 * uses null as "no filter, show all roles".
 */
export function readEnabledRoles(
  config: Record<string, unknown> | null | undefined,
): number[] | null {
  if (!config) return null;
  const raw = config["enabled_roles"];
  if (!Array.isArray(raw)) return null;
  const nums = raw.filter((v): v is number => typeof v === "number");
  if (nums.length === 0) return null;
  return nums;
}

/**
 * Filter a list of role options against an optional whitelist. Preserves the
 * unassigned sentinel option (value === "") unconditionally — only typed roles
 * are subject to the filter.
 *
 * `enabledRoles` null / undefined / empty → returns the input list unchanged.
 */
export function filterRoleOptions<V extends "" | TaskRoleValue>(
  options: ReadonlyArray<RoleOption<V>>,
  enabledRoles: number[] | null | undefined,
): RoleOption<V>[] {
  if (!enabledRoles || enabledRoles.length === 0) {
    return [...options];
  }
  const allowed = new Set(enabledRoles);
  return options.filter((o) => o.value === "" || allowed.has(o.value as number));
}
