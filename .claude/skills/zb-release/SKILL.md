---
name: zb-release
description: Run the weekly agent-teams release flow (dev → main) end-to-end — Tier-2 gate, merge, version bump, annotated tag, push, MILESTONE FLIPS (released + activate next), and resume dev. Encodes shared/release-workflow.md (#1646) so the milestone-status step (the gap behind #2056) can never be skipped. Use for a normal weekly release or an explicit "release vX.Y.0".
argument-hint: "[vX.Y.Z]"
allowed-tools:
  - Bash(git:*)
  - Bash(curl:*)
  - Bash(gh:*)
metadata:
  version: 1.0.0
  category: platform
  tags: [platform, git, release, mutate]
---

# /zb-release — weekly release flow (dev → main) with milestone flips

> Source of truth: `context/projects/agent-teams/shared/release-workflow.md`.
> HARD RULES: never force-push `main`; AA→GOV lock-code scan before any push;
> a version milestone is released ONLY when its children are all DONE/CANCELLED.

Args: optional `vX.Y.Z` (else compute the next MINOR from the latest tag).

## Step 0 — resolve target version
- Latest tag: `git tag -l | sort -V | tail -1`. Next weekly = `MINOR += 1, PATCH = 0`
  (MAJOR bumps only on explicit operator command). Hotfix = `PATCH += 1`.

## Step 1 — Tier-2 gate (on dev)
- On `dev`, run the full smoke superset (`.claude/teams/dev.md` "Release wrap-up" +
  `shared/release-matrix.md`). Full test suite must be GREEN. Fix red on `dev` first.

## Step 2 — merge dev → main
- `git checkout main && git merge --no-ff dev -m "release: vX.Y.0"`. NEVER force-push main.

## Step 3 — bump version (on main)
- Set `api/pyproject.toml` + `web/package.json` `version` = `X.Y.0`. Commit.

## Step 4 — annotated tag (message = the week's changelog)
- `git tag -a vX.Y.0 -m "vX.Y.0 — <summary>"` (body = `git log --oneline <prev-tag>..HEAD`).

## Step 5 — pre-push lock-code scan, then push
- Scan staged/tree for the lifecycle lock-codes and substitute to the committed `GOV`
  names per `_scratch/.lifecycle-mapping.md` (the `pre-push` hook carries the forbidden
  term list and enforces this).
- `git push origin main && git push origin vX.Y.0`

## Step 6 — milestone flips (Kanban) ← the #2056 step
- `GET /api/milestones` (header `X-Project-Id: <id>`) to find the version milestones.
- RELEASE the just-shipped version milestone: FIRST verify its children are all
  DONE/CANCELLED (rollup / `/zb-milestone-done` logic) — warn + halt if open work
  remains. Then `PATCH /api/milestones/{id}` `{"milestone_status":"released"}`.
- ACTIVATE the next version milestone (`vX.Y.0`) + any newly-focused milestones the
  operator names: `PATCH /api/milestones/{id}` `{"milestone_status":"active"}`.

## Step 7 — GitHub Release (optional) + resume dev
- If `gh` installed: `gh release create vX.Y.0 --notes-from-tag`.
- `git checkout dev && git merge main` (keep dev caught up).

## Report
- Tag created, `main HEAD == tag`, images CI (`release-images.yml`) triggered by the tag,
  milestone flips applied, dev resumed. List anything that needs the operator (gh install, etc.).

## Related skills

- **zb-git-commit** — handles the scoped-staging + scan + commit mechanics that zb-release composes into its merge and version-bump steps.
- **zb-milestone-done** — the milestone-completion check zb-release calls in Step 6 before flipping a milestone to released.
