---
story: context-system
version: 5
updated: 2026-06-23
updated_by: lead @ #2520 (maintenance)
---

## Current state

- **System ACTIVE but TRIGGER SHARPENED (#2520, 2026-06-21).** The "Context lifecycle +
  story records" section is IN CLAUDE.md (#2330/#2332, applied via operator ii 2026-06-12).
  The broad "≥2-3 related tasks" story-trigger was found REDUNDANT in practice (state lives
  fine in decisions.md + live Kanban for most threads) and was being BYPASSED — recent tasks
  #2474/#2473/#2482/#2487 carry NO `story:` tag while decisions.md stayed heavily maintained
  06-16→21. Root cause = **redundancy, NOT neglect**. → Sharpened: a story doc is reserved
  for a workstream that is (a) cross-session & ongoing AND (b) carries a live NOW-state spread
  across several decisions.md entries (exemplar: mode-a-cost); a milestone/version bucket is
  NOT story-worthy by itself. Full decision: decisions.md "#2520 story-doc layer was REDUNDANT"
  (2026-06-21).
- **What stays UNCHANGED:** activity rail (mandatory), recording bright-line, contamination
  write/read, warm-vs-clear. Existing good story docs kept — mode-a-cost (exemplary multi-week
  arc) + ux-simplification (closed record, harmless). This very doc stays a valid story: a
  cross-session methodology arc #2330/#2332 → #2520.
- **Methodology docs:** CLAUDE.md (story-trigger bullet) + .claude/docs/context-lifecycle.md
  (When-to-open + Sunset) **APPLIED via operator `ii`** (commit 388e96f; verified live 2026-06-23
  — context-lifecycle.md L16 "ALL THREE … sharpened 2026-06-21, #2520" + L48 sunset). decisions.md
  entry + this story doc landed in 7bc724f; both commits are in origin/dev (pushed). The two
  `_scratch/story-trigger-DRAFT-*.md` drafts were deleted (residue after apply).
- /zb-git-commit live (#2331, commit 472e4e5, smoke-passed). `/zb-task-context` automation:
  deliberately NOT built (#2520 — automating a bypassed/judgment-call layer is the wrong move).

## Open threads

- **Sunset eval — RESOLVED-FORWARD (#2520).** The ~2026-07-03 eval was exactly the
  evidence-gathering #2520 completed. **ONLY remaining residual** = a LIGHT "did the sharpened
  criterion stick" check at the next 2-3 story-eligible threads (a new mode-a-cost-class
  workstream gets a story; nothing else does), then close. No /zb-task-context build.
- Operator-side residuals **CLOSED 2026-06-23**: (a) the 2 .claude edits applied via `ii`
  (388e96f); (b) the #2519 b87c201 + #2520 7bc724f/388e96f commits are in origin/dev. The
  `_scratch/story-trigger-DRAFT-*.md` drafts were deleted.

## Gotchas

- Story versioning cannot rely on git alone: non-git `working_path` projects have no
  history, and batch-commit windows leave edits unversioned — hence in-file
  version+changelog as primary (this file's frontmatter).
- SKILLS hot-load mid-session (observed 2026-06-12: /zb-git-commit became invokable
  minutes after landing, no restart) — unlike `.claude/agents/*.md` which still load
  only at session start. Don't defer skill smokes to the next session by default.
- `tasks.resume_context` is HITL-flow server-written (api/src/services/content_moderation.py
  "No re-scan on resume_context") — do NOT overload it for handoffs.
- **A milestone is NOT a story (#2520).** The old trigger conflated "multi-task thread" with
  "story-worthy". A version/capability/domain milestone is tracked by its Kanban rollup +
  per-feature decisions.md entries — that IS its NOW-view. Reserve story docs for workstreams
  whose live/operational state escapes both the milestone view and a single decisions.md entry.

## Decisions pointer

- decisions.md "#2520 story-doc layer was REDUNDANT, not neglected — sharpened story-vs-decisions
  trigger" (2026-06-21) — root cause + sharpened criterion + 3-thread sanity check.
- decisions.md "#2330/#2332 story-based context system" (2026-06-12) — the original design lock.

## Changelog

v5 2026-06-23 — MAINTENANCE: operator-side residuals closed — .claude edits applied via ii (388e96f; CLAUDE.md + context-lifecycle.md verified live) + decisions/story landed (7bc724f), b87c201/7bc724f/388e96f all in origin/dev; stale _scratch DRAFT files deleted. Only the sunset light-check residual remains.
v4 2026-06-21 #2520 — story-trigger SHARPENED: redundancy root cause verified (untagged #2474/2473/2482/2487; story docs stale since 06-15); "≥2-3 tasks" retired → cross-session + live-NOW-state criterion (milestone ≠ story; exemplar mode-a-cost). decisions.md entry + 2 _scratch .claude drafts (operator ii). Sunset resolved-forward; /zb-task-context stays unbuilt.
v3 2026-06-12 #2331 — /zb-git-commit smoke-passed same-session (skills hot-load gotcha recorded); #2331 closed; threads = sunset + push-signal
v2 2026-06-12 #2332 — CLAUDE.md section applied (operator ii); #2330+#2332 closed; threads pruned to #2331 + sunset
v1 2026-06-12 #2332 — story opened (dogfood): design lock recorded; template landed; threads #2330/#2331/#2332 + sunset registered
