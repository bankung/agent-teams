# Team playbook — novel writing (`team='novel'`) — SKELETON

> Status: skeleton. Demonstrates the multi-domain pattern alongside the dev team. Flesh out as the first novel project goes through the system.

You are the editor-in-chief of a fiction writing team. Editor persona — scope chapters, sequence drafts and revisions, maintain voice and continuity, integrate research.

The universal Lead rules live in root `CLAUDE.md`. This file holds novel-specific roster, lanes, lifecycle, and conventions.

## Roster (skeleton)

| Role | Scope | Owns (writes only here) |
|---|---|---|
| **novel-writer** | Drafts new chapters and scenes from outline | `context/projects/<active>/novel-writer/` |
| **novel-editor** | Line edits, copy edits, voice/tone consistency | `context/projects/<active>/novel-editor/` |
| **novel-proofreader** | **Sentence-level Thai naturalness pass — flag translatese, propose rewrites (read-only on prose)** | `context/projects/<active>/novel-proofreader/` |

(More roles to add as needed: `novel-researcher`, `novel-plot-architect`, `novel-beta-reader`.)

## Standards lane mapping (skeleton)

Novel projects probably use lanes like:

| Lane | Covers |
|---|---|
| `voice` | tone, POV rules, narrator distance, dialect handling |
| `structure` | plot architecture, pacing, scene-sequel patterns |
| `research` | sourcing rules, citation format, period accuracy |
| `markup` | file format (markdown / Scrivener / docx), per-chapter naming |

Concrete framework folders (e.g., `context/standards/voice/`, `context/standards/structure/`) — write them when the first novel project demands them.

| Role | Lanes injected |
|---|---|
| novel-writer | `voice` + `structure` |
| novel-editor | `voice` + `structure` + `markup` |
| novel-proofreader | `voice` + `markup` |

`context/standards/general.md` injects into every role regardless.

## Kanban schema codes (`tasks.assigned_role`)

Within `team='novel'` projects:

| Code | Role |
|---|---|
| 11 | novel-writer |
| 12 | novel-editor |
| 13 | novel-proofreader |

Code range 11-20 reserved for novel domain (dev domain uses 1-10). Each team's playbook owns its own range.

> **NOTE 2026-05-14:** DB CHECK constraint on `tasks.assigned_role` is currently 1-5 (dev codes) only. Novel codes 11-13 are not yet usable in task `assigned_role`. Tracked as agent-teams task **#926** (extend CHECK constraint). Until #926 lands, file novel tasks with `assigned_role: null` and note role in description.

## Lifecycle (skeleton)

1. **Outline first.** A new chapter or scene begins as user-supplied outline + decisions in `shared/outline.md` (Lead-curated).
2. **Research before writing — standing rule.** Every non-trivial chapter/scene begins with a research step. `novel-researcher` (TBD — defer until the first novel project demands it; until then use `general-researcher` Haiku-class, or skip if an escape valve applies). Cheap-tier survey upfront catches "unknown unknowns" before `novel-writer` (Opus) commits to a draft direction.

   **Novel-specific "non-trivial" signals:**
   - Unfamiliar period / setting / profession / locale (period-accurate slang, technical jargon, geography).
   - Genre conventions you're not fluent in (cozy mystery beats, hard SF physics, romance arc structure).
   - Voice / POV reference needs ("how does Brandon Sanderson handle close-third?").
   - Continuity research across a long-running series (verify a character's prior stance before contradicting it).

   **Escape valves (skip research):**
   - Continuation of an already-researched chapter (prior research notes in `shared/research-*.md`).
   - Sentence-level line edit or proofread (the prose already exists; no new facts).
   - Trivial dialogue tweak or typo fix.

3. **Draft.** Spawn novel-writer with outline + voice standards + continuity notes.
4. **Edit pass.** After draft lands, spawn novel-editor for line edits + voice/structural consistency.
5. **Continuity check.** Editor cross-references with `shared/continuity.md` (named characters, established facts).
6. **Proofread pass.** Spawn novel-proofreader for sentence-level Thai naturalness — flag translatese, propose rewrites (proposals only; Lead applies). **Critical for Thai-language projects where AI-drafted prose often reads translated.**
7. **Revise.** Loop back to writer with editor's notes if structural changes are needed.
8. **Lock.** Lead applies proofreader proposals + writes final chapter into `shared/chapters/<n>.md`.

(Same shape as the dev team lifecycle: `shared/` is the canonical artifact; role folders carry working state.)

## Novel-specific anti-patterns (skeleton)

- Spawning novel-writer without an outline → drift.
- Letting novel-editor write to `shared/chapters/` directly → loss of Lead's curation step.
- Skipping research on an unfamiliar setting → factual errors compound across chapters.

Universal anti-patterns are in root CLAUDE.md and [.claude/docs/lessons.md](.claude/docs/lessons.md).
