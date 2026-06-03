# Release workflow — agent-teams (weekly cadence)

> Adopted 2026-05-29 (Kanban #1646, trial). Develop on `dev`; publish to `main`
> once a week. `main` = the curated, released version a recruiter / user sees;
> `dev` = the working line. For THIS repo this supersedes the old
> "always-main / no-branch" solo-dev default.

## Branch model
- **`dev`** — all daily development. Every task commits + pushes to `dev`. All
  parallel sessions / worktrees target `dev`, **never `main` directly**.
- **`main`** — the published release. Updated ONLY by a weekly merge from `dev`
  (or a hotfix merge). `main` HEAD always == the latest release tag.
- Never force-push `main` (it's the published line).

## Versioning — `vMAJOR.MINOR.PATCH`
- **MAJOR** — starts at 0; bumped ONLY on explicit operator command.
- **MINOR** — running number per *normal* (weekly) release. Each weekly release:
  `MINOR += 1`, `PATCH` reset to 0.
- **PATCH** — quick-fix number. Resets to 0 at each normal release; `+= 1` per
  hotfix between weekly releases.
- Examples: `v0.1.0` (baseline) → `v0.1.1` (hotfix) → `v0.2.0` (next weekly) →
  … → `v1.0.0` (operator says bump major).
- **Version of record = the annotated git tag** `vX.Y.Z`. `gh` CLI is not
  installed; install it for formal GitHub Releases — until then tags appear on
  GitHub `/tags`. Keep `api/pyproject.toml` + `web/package.json` `version` in
  sync (MAJOR.MINOR) at each weekly release.

## Weekly release (cadence) — triggered by recurring Kanban task #1647
1. **Gate — Tier-2 wrap-up.** On `dev`, run the full smoke superset (see
   `.claude/teams/dev.md` "Release wrap-up flow" + `shared/release-matrix.md` +
   `context/teams/dev/release-methodology.md`). Fix anything red on `dev` first.
2. **Merge `dev` → `main`:**
   `git checkout main && git merge --no-ff dev -m "release: vX.Y.0"`
3. **Bump version** (on `main`): set `api/pyproject.toml` + `web/package.json`
   `version` to `X.Y.0` (MINOR += 1, PATCH 0). Commit.
4. **Tag** (annotated; message = the week's changelog —
   `git log --oneline <prev-tag>..HEAD`):
   `git tag -a vX.Y.0 -m "vX.Y.0 — <summary> + changelog"`
5. **Push:** `git push origin main && git push origin vX.Y.0`
6. **(Optional)** GitHub Release once `gh` is installed:
   `gh release create vX.Y.0 --notes-from-tag` (or via the web UI).
7. **Resume `dev`:** `git checkout dev && git merge main` (keep `dev` caught up).

## Hotfix (between weekly releases)
1. Make the small/urgent fix on `dev`.
2. `git checkout main && git merge --no-ff dev -m "hotfix: vX.Y.Z"`
   — OR `git cherry-pick <sha>` onto `main` if `dev` has unreleased work you do
   NOT want to ship in the hotfix.
3. Tag `vX.Y.(PATCH+1)` (annotated). Push `main` + the tag.
4. `git checkout dev && git merge main`.

## Guardrails
- A recruiter sees `main` (the curated weekly release), not `dev`'s churn.
- Trial status: this is a first run (Kanban #1646). Promote to dev-team
  methodology (`context/teams/dev/`) only if it proves out across a few weeks.
