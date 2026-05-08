# Tier-1 smoke checklist (live API probes)

> **Lead is the only writer of this file.** Updates come from Lead based on incident evidence (e.g., "Kanban #X escaped because we did not probe Y — add Y to the matrix"). Subagents read this; they do not edit it.

Tier-1 = scoped `curl localhost:8456` probes against the running container after a change has been applied to the working tree (or the running container — uvicorn `--reload` picks up source edits live). Run by dev-tester at lifecycle step 5b. Cost target: **1-3 probes per task, < 30 seconds total wall-clock**. Tier-2 (full API + security) is the release wrap-up flow — see Kanban #80 + `release-checklist.md` (forthcoming).

---

## When does Tier-1 apply? (decision matrix)

| Task touched | Tier-1 required? |
|---|---|
| `api/src/routers/**` (PATCH / POST / DELETE / GET semantics) | **YES** |
| `api/alembic/versions/**` (schema / constraint changes) | **YES** |
| `api/src/schemas/**` (Pydantic field changes — esp. `extra='ignore'`, validators) | **YES** |
| `api/src/models/**` (server defaults, onupdate, triggers) | **YES** |
| `api/src/templates/project_shared/**` (scaffold templates copied into new projects) | **YES** — POST a throwaway project, verify scaffold output, DELETE |
| `docker-compose.yml` / `api/Dockerfile` / env files | **YES** — verify health endpoint + at least one DB-touching endpoint |
| `api/src/main.py` (app wiring, middleware, exception handlers) | **YES** |
| `api/scripts/**` (seed, migration helpers) | **YES** if the script will be re-run |
| `api/tests/**` only | NO — pytest IS the smoke for tests |
| `.claude/**`, `context/projects/**`, `context/standards/**`, `README.md`, `CLAUDE.md` | NO — meta / docs only |
| Comments / docstrings / formatting only (no behavior change) | NO |

**Edge cases:**
- **Mixed touch** (e.g., router + tests in same task): apply Tier-1 on the router-touching part. The test changes are pytest-verified.
- **Cleanup pass with no behavior change** (renaming variables, fixing docstrings): NO Tier-1 unless a runtime-loaded asset changed (e.g., scaffold templates — those ARE behavior because new projects ingest them).
- **Migration that ALTERed an existing constraint:** YES — probe at least one happy-path INSERT/UPDATE/DELETE that exercises the new constraint shape.

When in doubt, run Tier-1. It is cheap; missing a regression is not.

---

## Probe template

dev-tester emits one section per probe in the final report. Each probe MUST have:

1. **Intent** (one line: which behavior is being verified)
2. **Command** (verbatim curl, copy-pasteable)
3. **Response** (verbatim JSON / status code, with timestamps preserved)
4. **Assertion** (PASS / FAIL with the exact comparison and observed values)

### Behavior shapes to lock (use both in every probe pair)

**POSITIVE** — "the mutation actually happens":
- Response field has the expected shape (e.g., `description="..."`).
- A side-effect-tracked field advances (e.g., `updated_at_after > updated_at_before`).
- Status code matches the documented contract (200 / 201 / 204 / 404 / 409 / 422).

**NEGATIVE** — "the no-op stays a no-op":
- Idempotent re-call does NOT advance side-effect-tracked fields (`updated_at_after_redelete == updated_at_after_first_delete`).
- Body that matches current values does NOT bump audit-style fields (N7 no-op skip).
- Forbidden field in body is silently ignored (`extra='ignore'` semantics).

> **Anti-pattern (load-bearing):** assertions of the shape `actual == baseline` where the baseline could be vacuously equal to actual on broken code. If the value is supposed to mutate on the positive path, you MUST also lock that the positive-path mutation does happen — otherwise the equality holds trivially. This is the Kanban #76 lesson: M9 asserted `updated_at_after_redelete == updated_at_before_redelete` but neither bumped, so the test was vacuous. Always pair a NEGATIVE assertion with a POSITIVE one against the same field.

### Boilerplate (bash)

```bash
# capture baseline
before=$(curl --silent http://localhost:8456/api/projects/<id> | grep -o '"updated_at":"[^"]*"')

# perform real mutation
curl --silent -X PATCH http://localhost:8456/api/projects/<id> \
  -H "Content-Type: application/json" \
  -d '{"description":"smoke test"}' -o /dev/null

# capture after
after=$(curl --silent http://localhost:8456/api/projects/<id> | grep -o '"updated_at":"[^"]*"')

# assert
[ "$before" != "$after" ] && echo "POSITIVE PASS" || echo "POSITIVE FAIL"
```

### When the host has no Python/jq

This Windows host has no usable `python` / `python3` / `jq` — see memory `feedback_no_host_python.md`. Use one of:
- `docker compose exec -T api python -c "..."` — Python 3.12.13 lives in the api container
- PowerShell `... | ConvertFrom-Json | Select-Object ...` — native Windows shell
- Plain `curl ... | grep -o '"field":"[^"]*"'` — works for trivial probes (used in the boilerplate above)

### Restoration discipline

If a probe mutates a real production row (e.g., `paths_db` on the seeded `agent-teams` project), **restore it before returning**. Use the canonical seed value from `api/scripts/seed.py`. Capture the restore call as the final probe in the section so the working state is auditable.

If a probe creates a throwaway row (POSTs a test project / task), tag it with a unique-suffix name (`proj-<task#>-smoke-<timestamp>`) and DELETE it before returning. Soft-deleted is acceptable — hard cleanup is out of scope.

---

## Output convention

dev-tester's final report appends a section:

```markdown
## Tier-1 smoke probe results

### Probe A — <one-line intent>
**Command:** `curl ...`
**Response:** ` ... ` (verbatim, truncate non-relevant fields with `…`)
**Assertion:** POSITIVE PASS — `updated_at` advanced from `2026-05-08T08:08:52.138253Z` to `2026-05-08T08:08:52.240672Z`.

### Probe B — <intent>
...

### Restoration
- Restored `agent-teams.description` to canonical seed value (verified via GET).
- Throwaway project id=N soft-deleted.
```

**Lead's job:** read the section, verify each PASS aligns with the documented contract in `api-contracts.md`, treat any FAIL as a BLOCKER on the task (do not commit; route the failure to dev-backend / dev-frontend).

**dev-reviewer's job:** when auditing a task that touched routers / migrations / schemas, confirm the Tier-1 section is present with at least one POSITIVE + one NEGATIVE assertion. Missing on a router-touching task is a BLOCKER (escapes are how Kanban #76 happened).

---

## Worked example: Kanban #76 fix

The #76 fix bundle is the canonical reference. dev-backend ran probes A-E:
- **A (POSITIVE):** PATCH `/api/projects/1` with new description → `updated_at` advances.
- **B (NEGATIVE):** PATCH same body again → `updated_at` does NOT advance (N7 no-op skip).
- **C (POSITIVE):** PATCH different description → `updated_at` advances again.
- **D (restoration):** restore canonical seed description.
- **E (POSITIVE+NEGATIVE pair on DELETE):** POST throwaway, DELETE → `updated_at > created_at`; re-DELETE → `updated_at` unchanged. Soft-deleted row left in place.

Lead followed up with one independent probe (no-op PATCH, baseline + after) to confirm the live container reflected the uncommitted code. Both POSITIVE + NEGATIVE captured. Total wall-clock: ~20 seconds.

The test bundle (#76 dev-tester pass) added two pytest regression locks that mirror the same shape — Tier-1 catches the bug at deploy-verify, the regression test catches it forever after.

---

## Out of scope (NOT Tier-1)

- Full-API matrix sweeps (every endpoint × every code path) — that is Tier-2 release wrap-up.
- `/security-review` whole-branch security skill — Tier-2.
- Dependency CVE audit (`pip-audit`) — Tier-2.
- Audit-log inspection (`tasks_history` queries) — Tier-2.
- Performance / load probes — separate concern, not part of this checklist.

If a task is large enough to make Tier-1 cost more than ~30 seconds, that is a signal to split the task — not to expand Tier-1 scope.
