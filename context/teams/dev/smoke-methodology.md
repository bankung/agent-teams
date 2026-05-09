# Tier-1 smoke methodology (dev lead)

> **Scope:** cross-project — applies to every `lead='dev'` project. Lead is the only writer of this file.
> **Project-specific endpoint matrix / config** lives in each project's `context/projects/<active>/shared/smoke-matrix.md`.

Tier-1 = scoped `curl localhost:<api-port>` probes against the running container after a change has been applied to the working tree (uvicorn `--reload` picks up source edits live). Run by dev-tester at lifecycle step 5b. Cost target: **1-3 probes per task, < 30 seconds total wall-clock**. Tier-2 (full API + security) is the release wrap-up flow — see `release-methodology.md` in this folder.

---

## When does Tier-1 apply? (decision matrix)

| Task touched | Tier-1 required? |
|---|---|
| `api/src/routers/**` (PATCH / POST / DELETE / GET semantics) | **YES** |
| `api/alembic/versions/**` (schema / constraint changes) | **YES** |
| `api/src/schemas/**` (Pydantic field changes — esp. `extra='ignore'`, validators) | **YES** |
| `api/src/models/**` (server defaults, onupdate, triggers) | **YES** |
| `api/src/templates/**` (scaffold templates copied into new projects) | **YES** — POST a throwaway project, verify scaffold output, DELETE |
| `docker-compose.yml` / `api/Dockerfile` / env files | **YES** — verify health endpoint + at least one DB-touching endpoint |
| `api/src/main.py` (app wiring, middleware, exception handlers) | **YES** |
| `api/scripts/**` (seed, migration helpers) | **YES** if the script will be re-run |
| `api/tests/**` only | NO — pytest IS the smoke for tests |
| `.claude/**`, `context/**`, `README.md`, `CLAUDE.md` | NO — meta / docs only |
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
- Body that matches current values does NOT bump audit-style fields (no-op skip).
- Forbidden field in body is silently ignored (`extra='ignore'` semantics).

> **Anti-pattern (load-bearing):** assertions of the shape `actual == baseline` where the baseline could be vacuously equal to actual on broken code. If the value is supposed to mutate on the positive path, you MUST also lock that the positive-path mutation does happen — otherwise the equality holds trivially. The canonical worked example is the Kanban #76 escape (see "Worked example" below). Always pair a NEGATIVE assertion with a POSITIVE one against the same field.

### Boilerplate (bash)

```bash
# capture baseline
before=$(curl --silent http://localhost:<api-port>/api/<entity>/<id> | grep -o '"updated_at":"[^"]*"')

# perform real mutation
curl --silent -X PATCH http://localhost:<api-port>/api/<entity>/<id> \
  -H "Content-Type: application/json" \
  -d '{"description":"smoke test"}' -o /dev/null

# capture after
after=$(curl --silent http://localhost:<api-port>/api/<entity>/<id> | grep -o '"updated_at":"[^"]*"')

# assert
[ "$before" != "$after" ] && echo "POSITIVE PASS" || echo "POSITIVE FAIL"
```

For larger payload bodies, write to `_scratch/` and `--data-binary @<path>`:

```bash
cat > _scratch/probe-<intent>.json <<'EOF'
{"name":"_smoke-<timestamp>","lead":"dev","description":"...",...}
EOF

# Note the `_` prefix in the project name — required so .gitignore catches the
# scaffold folder at context/projects/_smoke-<timestamp>/
curl --silent -X POST http://localhost:<api-port>/api/projects \
  -H "Content-Type: application/json" \
  --data-binary "@_scratch/probe-<intent>.json"

# cleanup before return
rm _scratch/probe-<intent>.json
```

### When the host has no Python/jq

Some hosts have no usable `python` / `python3` / `jq` (e.g., Windows with Store stubs). Use one of:
- `docker compose exec -T api python -c "..."` — Python is available inside the api container
- PowerShell `... | ConvertFrom-Json | Select-Object ...` — native Windows shell
- Plain `curl ... | grep -o '"field":"[^"]*"'` — works for trivial probes (used in the boilerplate above)

### Restoration discipline

If a probe mutates a real production row (e.g., a seeded project's `paths_db`), **restore it before returning**. Use the canonical seed value from the project's seed script. Capture the restore call as the final probe in the section so the working state is auditable.

If a probe creates a throwaway row (POSTs a test project / task), tag it with the **`_` prefix convention** (e.g., `_smoke-<timestamp>`, `_probe-<reason>-<timestamp>`) and DELETE it before returning. Soft-deleted is acceptable — hard cleanup is out of scope. **The `_` prefix is mandatory** — `.gitignore` excludes `context/projects/_*/` so the scaffold folder doesn't pollute the working tree on `git status`. Probe rows that don't follow the convention WILL pollute (Kanban #81 backfill originally used `backfill-<timestamp>` without the underscore — those folders had to be manually `rm -r`d).

### Tempfile location

POST payloads, JSON drafts, ad-hoc probe scripts go in `_scratch/` at the repo root (gitignored). The dir is tracked via `.gitkeep` so it always exists. Use absolute paths so tools that don't honour `--cwd` find the file. Clean up with `rm _scratch/<name>` before return — leftover files in `_scratch/` are visible on `git status` (the dir is tracked even if its contents are ignored).

---

## Output convention

dev-tester's final report appends a section:

```markdown
## Tier-1 smoke probe results

### Probe A — <one-line intent>
**Command:** `curl ...`
**Response:** ` ... ` (verbatim, truncate non-relevant fields with `…`)
**Assertion:** POSITIVE PASS — `updated_at` advanced from `<ts-before>` to `<ts-after>`.

### Probe B — <intent>
...

### Restoration
- Restored `<entity>.<field>` to canonical seed value (verified via GET).
- Throwaway <entity> id=N soft-deleted.
```

**Lead's job:** read the section, verify each PASS aligns with the documented contract in the project's `api-contracts.md`, treat any FAIL as a BLOCKER on the task (do not commit; route the failure to dev-backend / dev-frontend).

**dev-reviewer's job:** when auditing a task that touched routers / migrations / schemas, confirm the Tier-1 section is present with at least one POSITIVE + one NEGATIVE assertion. Missing on a router-touching task is a BLOCKER (escapes are how Kanban #76 happened — see worked example).

---

## Worked example: Kanban #76 (canonical lesson)

This is the canonical lesson for the vacuous-shape anti-pattern. It is referenced from every dev project; the lesson is universal even though the specific Kanban id and endpoint paths come from the agent-teams repo.

The fix bundle ran probes A-E:
- **A (POSITIVE):** PATCH `/api/projects/1` with new description → `updated_at` advances.
- **B (NEGATIVE):** PATCH same body again → `updated_at` does NOT advance (no-op skip).
- **C (POSITIVE):** PATCH different description → `updated_at` advances again.
- **D (restoration):** restore canonical seed description.
- **E (POSITIVE+NEGATIVE pair on DELETE):** POST throwaway, DELETE → `updated_at > created_at`; re-DELETE → `updated_at` unchanged. Soft-deleted row left in place.

Lead followed up with one independent probe (no-op PATCH, baseline + after) to confirm the live container reflected the uncommitted code. Both POSITIVE + NEGATIVE captured. Total wall-clock: ~20 seconds.

The accompanying pytest regression added two tests that mirror the same shape — Tier-1 catches the bug at deploy-verify, the regression test catches it forever after. **The original M9 test passed for the wrong reason** because it asserted `updated_at_after_redelete == updated_at_before_redelete` without a sibling POSITIVE assertion proving the first DELETE actually mutated the field — both sides were vacuously equal on the broken code.

---

## Web smoke matrix (localhost:<web-port>)

When a task touches `web/**`, `docker-compose.yml`'s `web` service, or `.env.example`'s web vars (`WEB_PORT`, `NEXT_PUBLIC_API_URL`), Tier-1 also covers the Next.js surface. The probe shape diverges from the api side because there is no `updated_at` to advance — instead lock:

| Touched | Probe | Assertion |
|---|---|---|
| New page (`web/app/**/page.tsx`) | `curl -fsS http://localhost:<web-port><route>` + grep for a known marker string | HTTP 200 AND grep count >= 1 (POSITIVE) |
| App Router wiring | `curl -s -w "%{http_code}" http://localhost:<web-port>/<unknown-route>` | HTTP 404 (NEGATIVE — confirms App Router default 404 still wired; catches accidental catch-all routes) |
| `docker-compose.yml` web service / Dockerfile | `docker compose ps web --format json` | Contains `"Health":"healthy"` |
| New API client (`web/lib/api.ts` and consumers) | `curl http://localhost:<web-port>/<page-that-calls-api>` AND inspect rendered output | Client round-trips `NEXT_PUBLIC_API_URL` and surfaces api data (POSITIVE — cross-container FE→BE) |
| Next.js form / mutation | Submit via `curl -X POST` against the page's server action endpoint, then GET the api row | Side-effect lands in DB AND identical resubmit is no-op (mirrors the api POSITIVE+NEGATIVE pair) |

The api-side Kanban #76 lesson still applies on the web side: never assert `actual == baseline` where baseline could vacuously match. If you assert that an unknown route returns 404, also assert that a KNOWN route returns 200 in the same probe pass — otherwise a totally broken `next start` (returning 404 on every URL) would falsely pass the negative probe.

Cost target unchanged: 1-3 probes, < 30 seconds.

---

## Out of scope (NOT Tier-1)

- Full-API matrix sweeps (every endpoint × every code path) — that is Tier-2 release wrap-up.
- `/security-review` whole-branch security skill — Tier-2.
- Dependency CVE audit (`pip-audit`) — Tier-2.
- Audit-log inspection (`tasks_history` queries) — Tier-2.
- Performance / load probes — separate concern, not part of this checklist.

If a task is large enough to make Tier-1 cost more than ~30 seconds, that is a signal to split the task — not to expand Tier-1 scope.
