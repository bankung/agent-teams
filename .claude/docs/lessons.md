# Lessons — anti-patterns with detail

CLAUDE.md's "Critical anti-patterns" lists the rules. This file holds the reasoning and incident context behind each one.

## Lead never edits target-project code
If the user says "fix a small bug in api/main.py" — spawn `backend`. Do not open `Edit` yourself. The only Lead-writable paths are `context/projects/<active>/shared/*` and API calls.

## shared/ is Lead-only
If a subagent reports "I updated api-contracts.md" — check `git diff`. The permission model should have stopped them, but if a write slipped through, revert it and have Lead rewrite from the proposal.

## standards/ is human-only
`context/standards/*` is human-maintained because the blast radius crosses every project. If Lead or a subagent feels the urge to change it — stop and surface it to the user. Exception: explicit user instruction.

## DB writes go through the API
No `psql`, no `python -c "..."` that touches the DB directly. Routing through FastAPI keeps validation and the audit triggers intact.

## Verify, don't trust
A subagent saying "done" is not the same as it being done. Open the files it claims to have modified before reporting completion to the user.

## Parallel only when independent
- Frontend + backend on the same feature with an unstable contract → sequential, backend first.
- Frontend on feature A while backend works on feature B → safe to parallelize.

## Commit scope
On user-requested commits, stage only the files this task touched. Never `git add -A` — it picks up unrelated work or secrets.

## Multi-project context separation
If the user switches project mid-session, re-resolve the active project (call API) and re-read `context/projects/<new>/shared/`. Do not carry context from the previous project.

## Bootstrap fallback can go stale
The DB is the single source of truth. If pre-scaffold or hardcoded fallbacks linger in CLAUDE.md after the API + seed are healthy, remove them — otherwise Lead will use stale paths instead of the live DB.
