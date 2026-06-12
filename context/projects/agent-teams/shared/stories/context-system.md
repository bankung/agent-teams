---
story: context-system
version: 1
updated: 2026-06-12
updated_by: lead @ #2332
---

## Current state

- Story-based context system design LOCKED by operator 2026-06-12 (2 pushback rounds +
  versioning round). Full spec: Lead memory `project_story_context_system.md` until the
  CLAUDE.md section lands (draft in `_scratch/draft-claudemd-context-lifecycle.md`).
- Core split shipped conceptually: activity rail = immutable per-task EVENTS (#2330 rule,
  mandatory during work) · `shared/stories/<slug>.md` = mutable thread STATE (this file
  format; `_template.md` alongside).
- Activity-rail-mandatory rule active since 2026-06-12 (Lead memory
  `feedback_activity_rail_mandatory.md`); doc placement tracked in #2330 (TODO, gate=commit).
- /tn-git-commit skill landed at `.claude/skills/tn-git-commit/` via operator ii,
  commit 472e4e5 (local; push held) — #2331 awaits only the next-session invokability smoke.

## Open threads

- #2330 — encode rail-mandatory rule into CLAUDE.md/teams-doc/SKILL.md (operator applies; gate=commit)
- #2332 — THIS task: operator must apply the CLAUDE.md section from `_scratch/draft-claudemd-context-lifecycle.md` (gate=commit); then DONE
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

v1 2026-06-12 #2332 — story opened (dogfood): design lock recorded; template landed; threads #2330/#2331/#2332 + sunset registered
