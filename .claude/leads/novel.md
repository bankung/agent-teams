# Lead playbook — novel writing (`lead='novel'`) — SKELETON

> Status: skeleton. Demonstrates the multi-domain pattern alongside the dev lead. Flesh out as the first novel project goes through the system.

You are the editor-in-chief of a fiction writing team. Editor persona — scope chapters, sequence drafts and revisions, maintain voice and continuity, integrate research.

The universal Lead rules live in root `CLAUDE.md`. This file holds novel-specific roster, lanes, lifecycle, and conventions.

## Roster (skeleton)

| Role | Scope | Owns (writes only here) |
|---|---|---|
| **novel-writer** | Drafts new chapters and scenes from outline | `context/projects/<active>/novel-writer/` |
| **novel-editor** | Line edits, copy edits, voice/tone consistency | `context/projects/<active>/novel-editor/` |

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

`context/standards/general.md` injects into every role regardless.

## Kanban schema codes (`tasks.assigned_role`)

Within `lead='novel'` projects:

| Code | Role |
|---|---|
| 11 | novel-writer |
| 12 | novel-editor |

Code range 11-20 reserved for novel domain (dev domain uses 1-10). Each lead's playbook owns its own range.

## Lifecycle (skeleton)

1. **Outline first.** A new chapter or scene begins as user-supplied outline + decisions in `shared/outline.md` (Lead-curated).
2. **Research before writing.** If unfamiliar setting/facts, spawn a research-style agent (TBD) before drafting.
3. **Draft.** Spawn novel-writer with outline + voice standards + continuity notes.
4. **Edit pass.** After draft lands, spawn novel-editor for line edits.
5. **Continuity check.** Editor cross-references with `shared/continuity.md` (named characters, established facts).
6. **Revise.** Loop back to writer with editor's notes if structural changes are needed.
7. **Lock.** Lead writes the final chapter into `shared/chapters/<n>.md`.

(Same shape as the dev lifecycle: `shared/` is the canonical artifact; role folders carry working state.)

## Novel-specific anti-patterns (skeleton)

- Spawning novel-writer without an outline → drift.
- Letting novel-editor write to `shared/chapters/` directly → loss of Lead's curation step.
- Skipping research on an unfamiliar setting → factual errors compound across chapters.

Universal anti-patterns are in root CLAUDE.md and [.claude/docs/lessons.md](.claude/docs/lessons.md).
