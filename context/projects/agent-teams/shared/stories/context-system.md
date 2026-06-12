---
story: context-system
version: 2
updated: 2026-06-12
updated_by: lead @ #2332
---

## Current state

- System FULLY ACTIVE: the "Context lifecycle + story records" section is IN CLAUDE.md
  (applied via operator ii 2026-06-12) — #2330 + #2332 both closed DONE. Canonical spec
  = CLAUDE.md section; decisions.md entry "#2330/#2332 story-based context system" =
  the lock record; Lead memory mirrors for cross-session recall.
- Surfaces live: `shared/stories/_template.md` + this doc (first dogfood);
  rail-mandatory checkpoints in effect since 2026-06-12.
- /tn-git-commit skill landed at `.claude/skills/tn-git-commit/` via operator ii,
  commit 472e4e5 (local; push held) — #2331 awaits only the next-session invokability smoke.

## Open threads

- #2331 — /tn-git-commit invokability smoke at next session start, then DONE
- Sunset evaluation due ~2026-07-03 (or ~30 chain pickups): story-doc read-rate +
  ~10-sample ground-truth audit; build /tn-task-context skill only if evaluation passes

## Gotchas

- Story versioning cannot rely on git alone: non-git `working_path` projects have no
  history, and batch-commit windows leave edits unversioned — hence in-file
  version+changelog as primary (this file's frontmatter).
- `tasks.resume_context` is HITL-flow server-written (api/src/services/content_moderation.py
  "No re-scan on resume_context") — do NOT overload it for handoffs.

## Decisions pointer

- "auto-run batch 2: #2104 audit truthfulness + #2155 interrupt usage metering + #1265
  consolidation" (2026-06-12) — known-gaps register pattern this system formalizes.
- decisions.md entry for #2332 (2026-06-12) — the lock record for this system.

## Changelog

v2 2026-06-12 #2332 — CLAUDE.md section applied (operator ii); #2330+#2332 closed; threads pruned to #2331 + sunset
v1 2026-06-12 #2332 — story opened (dogfood): design lock recorded; template landed; threads #2330/#2331/#2332 + sunset registered
