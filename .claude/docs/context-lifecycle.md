# Context lifecycle — mechanics (reference)

The compact core (rail mandatory · recording bright-line · contamination write/read · warm-vs-clear) lives in `CLAUDE.md` → "Context lifecycle + story records". This file holds the detailed mechanics pulled out of the hot path. Locked 2026-06-12 (#2330/#2332).

## Story doc shape

`<shared>/stories/<slug>.md` (see `shared/stories/_template.md`):

- **Frontmatter:** `story / version / updated / updated_by`.
- **Sections:** Current state · Open threads (→ #ids, "none" allowed) · Gotchas · Decisions pointer.
- **Changelog:** 1 line per edit, cap ~20.
- **Body cap:** ~150 lines.

## When to open a story

Only when a thread reaches ≥2–3 related tasks or the operator names a workstream. Tag tasks with a `story: <slug>` line in the Kanban description (plus `from #X` refs). One-off tasks stay storyless (rail checkpoint only).

## Versioning (git alone is NOT enough — non-git working_paths + uncommitted batch windows)

- Bump `version` on every edit.
- **Optimistic lock:** re-read and compare `version` immediately before writing; mismatch → re-read, merge, then bump — never blind-overwrite.
- The task's rail close-checkpoint cross-stamps `story <slug> → vN`.
- In git repos the story doc rides the SAME docs commit as its task.

## Pickup reads (resuming a thread)

- **Story-tagged task** → read its story doc first (O(1)).
- **Storyless** → 4-layer fallback:
  1. FK + `#refs` already in the fetched task.
  2. Rails of ≤3–5 referenced tasks — COLD pickups only, one hop.
  3. `decisions.md` grep + `git log -- <paths>` (commits carry `#id:`).
  4. Nothing found → fresh start.
- **Proportionality:** tiny chores may skip layers 2–3 even when refs exist.
- **Write-side duty:** every derived task cites `from #X` (+ FK when a real dependency exists) at creation — the links ARE the index.

## Sunset

Evaluate ~2026-07-03 (or ~30 chain pickups): were story docs / handoffs actually read; sample ~10 records against ground truth (artifact-backed → mechanically checkable). Unused or unread → trim the rule set. The `/tn-task-context` convenience skill is built only AFTER this evaluation passes.
