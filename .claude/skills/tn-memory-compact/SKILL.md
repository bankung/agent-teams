---
name: tn-memory-compact
description: >-
  Propose a COMPACTED, de-duplicated version of the two long-lived knowledge stores — the operator
  auto-memory dir (MEMORY.md + memory/*.md) and a project's shared/decisions.md — as a review-ready
  draft, never an in-place rewrite. Use when the operator says "compact memory", "dedupe the memory",
  "memory is bloated / getting long", "consolidate decisions", "clean up decisions.md", "merge
  duplicate memories", "tidy the memory", or asks to shrink / consolidate the memory or decision log.
argument-hint: "[memory | decisions | both] [project-name]   (default: both, active project)"
allowed-tools:
  - Read
  - Glob
  - Grep
  - Write
  - Bash(date:*)
metadata:
  version: 1.0.0
  category: platform
  tags: [memory, decisions, compaction, context-hygiene, hitl]
---

# /tn-memory-compact — propose a compacted memory + decisions draft (HITL, propose-only)

`$ARGUMENTS` = `[memory | decisions | both] [project-name]` — which store(s) to compact (default
`both`) and an optional project override for the `decisions.md` target (default = the session-bound
active project in `_runtime/lead_project_id.txt`).

This skill REASONS over two append-mostly knowledge stores and proposes how to shrink them without
losing information. It is **Lead-run**: only the Lead has host filesystem access to the operator
memory dir — the api container bind-mounts only `/repo` (verified `docker-compose.yml`), so an
in-container auditor cannot reach the memory dir and must not own this job.

## 1. Propose-only — the hard boundary (NON-NEGOTIABLE)

A knowledge store is operator-owned and lossy to merge wrong. This skill NEVER edits the live stores.
Every byte it writes goes under `_scratch/memory-compact-<YYYY-MM-DD>/`.

- ZERO writes to the operator memory dir (`~/.claude/projects/<proj>/memory/`).
- ZERO writes to `context/` (any `decisions.md` or other shared file).
- The operator reviews the draft + change-report and applies accepted merges by hand.
- A merge that drops a fact is a data-loss event: when two entries are not PROVABLY redundant, KEEP
  both and flag for operator judgement — never silently drop. Propose, never auto-apply.

If you cannot satisfy "all output under `_scratch/`", STOP and report — do not partially apply.

## Procedure

1. **Resolve targets.** Read `_runtime/lead_project_id.txt` → active project id (and name, via
   `GET /api/projects/<id>` if needed). A `project-name` arg overrides the decisions target. Resolve:
   memory dir = the operator auto-memory path (`MEMORY.md` + `memory/*.md`); decisions =
   `context/projects/<project>/shared/decisions.md`. Make the output dir
   `_scratch/memory-compact-<YYYY-MM-DD>/` (`date -u +%Y-%m-%d`).

2. **Enumerate + read (READ-ONLY).** `Glob` the memory dir for `*.md`; read `MEMORY.md` (the index).
   `Read` `decisions.md`. For a large store, read the index first and deep-read only the files a
   candidate cluster touches (token economy) — but every MERGE/DROP proposal MUST be backed by having
   read the full text of every entry it touches.

3. **Detect candidates — three signal classes.**
   - **Duplicate / overlapping** — entries asserting the same rule (e.g. an "add-task = open, not
     implement" note that is a sub-case of a broader "recommend-not-execute" note). Propose a MERGE
     into the strongest-named entry; fold the others in as sub-sections so no trigger/nuance is lost.
   - **Superseded** — `Grep` for `REPLACES | SUPERSEDES | CANCELLED | changed-to | LOCKED <date> |
     DEPRECATED` and stale "as of <date>" counts / stop-lists; superseded text → DROP or fold into
     its successor. In `decisions.md`, collapse an entry that a later entry explicitly replaces.
   - **Broken / stale links** — `Grep` for `[[name]]` refs; flag any pointing to a slug with no
     matching `memory/<slug>.md`, any slug-style mismatch (hyphen vs underscore vs `name:` field),
     and any file orphaned from the `MEMORY.md` index. Propose a REWRITE that normalises slugs.

4. **Write the proposal (NOT the live files)** into `_scratch/memory-compact-<YYYY-MM-DD>/`:
   - the proposed compacted store files (e.g. `memory/<merged-slug>.md`, `decisions.compacted.md`),
   - a regenerated `MEMORY.md` index consistent with the proposed merges/drops (at minimum the
     affected index lines, before→after),
   - `CHANGE-REPORT.md`: one row per proposed MERGE / DROP / REWRITE / KEEP-flag with a one-line
     reason + source slugs/line refs, plus a top tally (before→after entry count).

5. **STOP — hand to operator.** Print the change-report path + the tally. Do NOT apply. The operator
   diffs the draft and moves what they accept into the live stores by hand.

## Footgun guards

| Step | Incident class / why it exists |
|---|---|
| 1 | Lead-run only — the memory dir is host-side; the api container mounts only `/repo` and cannot reach it (verified `docker-compose.yml`). An in-container pass would silently see zero memory files. |
| 1,4 | All output under `_scratch/memory-compact-<date>/` — writing the memory dir or `context/` bypasses the operator-owned + humans-commit boundary (CLAUDE.md storage zones; `feedback_claude_dir_humans_only`). |
| 3 | When two entries are not provably redundant, KEEP both + flag — a wrong merge is permanent data loss. Fold, don't drop, on a MERGE so each entry's trigger survives. |
| 2,4 | Non-ASCII safe: memory/decisions hold Thai + arrows + emoji; read/write as UTF-8 via the Write tool, never round-trip through an inline shell `echo` (`feedback_nonascii_api_write_utf8`). |
| 5 | Propose-only: the skill ends at a draft and has NO apply step by design — the operator is the apply gate. |

## Usage

```
/tn-memory-compact                       # both stores, active project
/tn-memory-compact memory                # operator memory dir only
/tn-memory-compact decisions agent-teams # decisions.md for a named project only
```

## Related skills

- **tn-audit** — on-demand project *health* audit (budget/failure/drift via the project-auditor).
  Adjacent auditor-family skill; tn-memory-compact is the knowledge-store hygiene counterpart.
- **skill-authoring standard** — `context/standards/skills/skill-authoring.md` — the frontmatter +
  eval rubric (E1–E10) this skill targets.
- **tn-task-done** — close the build task only after the smoke (≥1 proposed merge/drop, zero writes
  outside `_scratch/`) is verified.
