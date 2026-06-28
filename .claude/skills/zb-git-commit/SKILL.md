---
name: zb-git-commit
description: >-
  Paved-path local commit — scoped staging + keyword scan + goal-driven verify; NEVER pushes.
  Use when committing scoped task work locally: "commit this", "stage and commit", "commit #<id>",
  "save the work", or any request to create a git commit for a specific task or file list.
  NOT for pushing, releasing, or merging (use zb-release for those).
argument-hint: "<task-id> [file1 file2 …] [-- \"<commit message>\"]"
allowed-tools:
  - Bash(git:*)
  - Bash(grep:*)
  - Bash(curl:*)
metadata:
  version: 1.0.1
  category: platform
  tags: [platform, git, commit, mutate]
---

# /zb-git-commit — paved-path commit (scoped staging + scan + verify; NEVER pushes)

You are committing work on the agent-teams platform (or the bound project's repo). The
arguments may name a task id, a file list, and a message. Follow this procedure exactly —
each step encodes a recurring footgun so it cannot recur. **This skill NEVER pushes.**

## Step 0 — resolve the repo root (worktree CWD trap)

The session CWD may be a `.claude/worktrees/*` directory that is NOT a real git worktree
(git would resolve to the main repo anyway, or fail). ALWAYS run git with an explicit
`-C "<main-repo-absolute-path>"` and absolute pathspecs. For agent-teams:
`git -C "C:\Users\banku\Documents\Personal\Projects\GitHub\agent-teams" ...`

## Step 1 — build the EXPLICIT file list (no wildcards, no -A)

- List exactly the files THIS task touched (from the work just done / the task's report).
- `git add -A`, `git add .`, and directory-level adds are **FORBIDDEN** — they sweep in
  operator debris. Known never-stage zones: `.codex/*` (operator-managed, perpetually
  dirty), `context/projects/<dead-project-debris>/`, `_scratch/*`, `_runtime/*`.
- Run `git -C <root> status --short -- <each path>` first; confirm every path is `M`/`??`
  as expected. A path you did not expect = STOP and reconcile before staging.

## Step 2 — stage + keyword scan the STAGED diff (before commit, every time)

```
git -C <root> add <file1> <file2> ...
git -C <root> diff --cached | grep -in "<forbidden-term-list>"
```

- The forbidden terms are the lifecycle lock-codes — the canonical list lives in the
  pre-push hook (`.git/hooks/pre-push`) and `_scratch/.lifecycle-mapping.md` (substitution
  table: internal code → committed GOV-name). Scan for the internal codes; any hit =
  UNSTAGE, substitute per the mapping, re-stage.
- Committed text uses committed names ONLY (e.g. `GOV1..GOV5`, `project-auditor`,
  `lifecycle program`). This applies to code, comments, tests, docs, and commit messages.

## Step 3 — commit message convention

- Shape: `#<task-id>: <what + why-it-matters, terse>` — or `release: vX.Y.Z` /
  `chore: <summary>` for non-task commits. Multi-task batches: `#A+#B: ...`.
- **`Co-Authored-By` trailer is FORBIDDEN** (operator rule: personal repos show only the
  operator). Do not add any AI-attribution trailer.
- Multi-line messages via a single-quoted here-string (PowerShell) or `-m` with embedded
  newlines from bash — never interpolate `$` carelessly.

## Step 4 — commit + verify (goal-driven, not claim-driven)

```
git -C <root> commit -m "<message>"
git -C <root> log --oneline -1
git -C <root> status --short -- <the file list>
```

- Confirm the new hash exists and the listed files left the dirty set.
- CRLF warnings ("LF will be replaced by CRLF") are benign on this repo — not an error.

## Step 5 — PUSH IS NOT PART OF THIS SKILL

- Default posture: **commit local only.** Push requires an explicit operator go-signal
  given in the CURRENT session ("push ได้", "push เลย", a /zb-release flow, etc.).
  A go-signal from a previous session does NOT carry over.
- When a push IS authorized, it is a separate command with its own gate: the `pre-push`
  hook re-scans for lock-codes; `main` additionally requires the ruleset bypass and the
  /zb-release procedure. Never `--force` on main, never `--no-verify` anywhere.

## Step 6 — rail checkpoint (activity-rail rule, 2026-06-12)

Post the commit checkpoint on the task's activity rail in the same working stretch:

```
curl --silent -X POST -H "X-Project-Id: <id>" -H "Content-Type: application/json" \
  -d '{"source":"lead","kind":"commit","summary":"Committed <hash> on <branch> (local; push held): <one-line> . Gates: <evidence>."}' \
  http://localhost:8456/api/tasks/<task_id>/tool-calls
```

EXCEPTION: if the FULL api suite is running, HOLD this post until it finishes (live-DB
sentinel trips on tool_calls deltas) — held queue, not a backfill.

## Footgun index (why each step exists)

| Step | Incident class |
|---|---|
| 0 | worktree CWD ≠ repo; git resolves to main repo or fails |
| 1 | `git add -A` swept `.codex/*` + debris into a scoped commit |
| 2 | lock-code term reached a committed file; caught only at push-time before |
| 3 | AI trailer appeared in a personal-repo commit |
| 5 | push without operator signal; trailing pushes during batch-hold windows |
| 6 | empty activity rail discovered end-of-day (2026-06-12) — recording is mandatory |

## Usage

```
# Simplest — task id + single file, message auto-derived from task title
/zb-git-commit 2155 langgraph/worker.py

# Multi-file explicit message
/zb-git-commit 2155 langgraph/worker.py langgraph/tests/test_worker_hitl_usage.py -- "#2155: HITL-resume usage PATCH"

# Non-task commit (chore / release prep) — no task id, explicit message required
/zb-git-commit context/standards/skills/skill-authoring.md -- "chore: update skill-authoring E10 usage examples"
```

(Free-form arguments are fine — the procedure above is the contract, not the arg syntax.)

## Related skills

- **zb-release** — the release/push flow that composes this skill's staging + scan + commit mechanics into its merge and version-bump steps; this skill never pushes, zb-release owns that gate.
- **zb-report** — the activity-rail checkpoint that Step 6 posts to; rail entries created here become the immutable event record for the task.
- **zb-task-done** — typically run immediately after the commit to close the task with AC verification; this skill commits, zb-task-done closes.
