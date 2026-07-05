# Context lifecycle — mechanics (reference)

The compact core (rail mandatory · recording bright-line · contamination write/read · warm-vs-clear) lives in `CLAUDE.md` → "Context lifecycle + story records". This file holds the detailed mechanics pulled out of the hot path. Locked 2026-06-12 (#2330/#2332).

## Story doc shape

`<shared>/stories/<slug>.md` (see `shared/stories/_template.md`):

- **Frontmatter:** `story / version / updated / updated_by`.
- **Sections:** Current state · Open threads (→ #ids, "none" allowed) · Gotchas · Decisions pointer.
- **Changelog:** 1 line per edit, cap ~20.
- **Body cap:** ~150 lines.

## When to open a story

Open a story doc ONLY when a workstream meets ALL THREE (sharpened 2026-06-21, #2520 — replaces the old "≥2–3 related tasks" auto-trigger, which over-fired):

1. **Cross-session & ongoing** — actively resumed across ≥3 separate sessions AND still open (not a one-session/batch landing; not closed).
2. **Live NOW-state beyond Kanban / one entry** — accumulates a "what's-true-now" picture (what's LIVE vs pending · operational gotchas · open measurement-gates) that is NOT a Kanban field and is scattered across several `decisions.md` entries, so re-deriving it on each pickup is genuinely expensive.
3. **Re-read on pickup** — future sessions actually need that NOW-view to continue (real open threads/follow-ups), not just a closed historical record.

**A milestone/version bucket is NOT automatically story-worthy** — its Kanban rollup (done/total + task rows) + per-feature `decisions.md` entries already ARE the NOW-view. A story doc is reserved for a cross-cutting workstream whose live/operational state escapes BOTH the milestone view and a single `decisions.md` entry.

**Default** (everything else — anything that lands in one session/batch, or whose end-state fits one `decisions.md` entry): `decisions.md` (locked decision + reasoning) + live Kanban rows + `from #X` refs. The activity rail still carries per-task events. The operator may still NAME a workstream to force a story doc.

Tag story tasks with a `story: <slug>` line in the Kanban description (plus `from #X` refs); storyless tasks stay rail-checkpoint only.

**Sanity check (the criterion reproduces what actually worked):** `mode-a-cost` (multi-week ingest→capture→read→forecast across many sessions; NOW-state over ~6 `decisions.md` entries + gotchas + open threads) → **story doc** ✓. glassmorphism #2474-2479 (landed 06-18 in one `decisions.md` entry, closed) → **decisions.md-only** ✓. v0.7.0 / a remediation milestone like ms49 (the Kanban milestone rollup + per-feature entries ARE the NOW-view) → **no story doc** ✓. Retrospective false-positive of the old trigger: `ux-simplification` got a story doc but completed in ~1 session ("Open threads: none") — kept as a harmless closed record, but the sharpened criterion would not have opened it.

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

## Sunset — RESOLVED-FORWARD (2026-06-21, #2520)

The ~2026-07-03 evaluation was brought forward: #2520 IS the evidence it was meant to gather. Finding — the story-doc layer was OVER-TRIGGERED (the "≥2–3 tasks" rule fired on threads whose state lives fine in `decisions.md` + Kanban) and consequently BYPASSED (recent tasks stopped carrying `story:` tags while `decisions.md` stayed heavily maintained). Resolution = **sharpen, not remove** — `mode-a-cost` proves the layer has real value when scoped to a genuine cross-session workstream. The "When to open" criterion above is the sharpened rule.

Residual: a LIGHT "did the sharpened criterion stick" check at the next 2-3 story-eligible threads (does a new mode-a-cost-class workstream get a story doc, and does nothing else?) — then close. `/zb-task-context` automation stays UNBUILT: the trigger is a judgment call, and automating a deliberately-rare layer is the wrong move (#2520).
