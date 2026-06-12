# Runbook — running the langgraph test suite (test deps NOT baked in the image)

> Kanban #2155 item 2 decision (2026-06-12): **document, don't bake.** Image rebuild is
> operator-tier (machine load + container recreate); ephemeral pip installs persist across
> same-container restarts. Re-evaluate baking only after the dep set is stable ≥2 weeks
> AND a `docker compose build` cadence exists.

## (a) One-off docker run — canonical (works even when the service container is down/looping)

The service container may be in a retry-boot loop (e.g., host Ollama off) — this path
doesn't touch it. Run from **PowerShell, not git-bash** (bash mangles `/repo` into a
`C:/Program Files/Git/...` path).

```powershell
docker run --rm `
  --env-file "<PROJECT_ROOT>\.env" `
  -e HITL_DEMO_ENABLED=1 `
  -v "<PROJECT_ROOT>:/repo" `
  -w /repo/langgraph `
  --entrypoint sh agent-teams-langgraph `
  -c "pip install -q pytest pytest-asyncio respx && python -m pytest tests -q"
```

**Footguns (both observed 2026-06-12):**
- `-e HITL_DEMO_ENABLED=1` is REQUIRED: docker-compose sets it (`${HITL_DEMO_ENABLED:-1}`,
  compose line ~271) but it is NOT in `.env`, so `--env-file` alone misses it →
  5 `test_auditor_demo_branches.py` tests fail spuriously and look "pre-existing".
- git-bash path conversion breaks `-w /repo/langgraph` → exit 125 "working directory invalid".

Expected baseline 2026-06-12: **635 passed / 15 skipped** (~10s after deps install).

## (b) In-container quick path — when the service is healthy

```bash
docker exec agent-teams-langgraph pip install -q pytest pytest-asyncio respx
docker exec agent-teams-langgraph python -m pytest /repo/langgraph/tests -q
```

Installs persist until the container is RECREATED (`docker compose up` / image change);
a plain `docker restart` keeps them. Re-install after any recreate.

## (c) Proposed `.claude/teams/dev.md` line (humans apply — Lead may not edit .claude/)

> **Langgraph test suite:** run via one-off container (`docker run --rm … agent-teams-langgraph`,
> with `-e HITL_DEMO_ENABLED=1`) rather than `docker exec` into the service container, which may
> be in a retry-boot loop when host Ollama is off. Canonical command + footguns:
> `shared/runbooks/langgraph-test-deps.md`. Do NOT bake test deps into the image (#2155 decision).

## (d) Future dev-image layer (NOT now)

```dockerfile
# Test deps — bake only after #2155 re-evaluation (stable deps + rebuild cadence).
RUN pip install --no-cache-dir pytest pytest-asyncio respx
```
