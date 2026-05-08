# Tier-2 release wrap-up methodology (dev lead)

> **Scope:** cross-project — applies to every `lead='dev'` project. Lead is the only writer of this file.
> **Project-specific endpoint matrix / dep config / DB-name** lives in each project's `context/projects/<active>/shared/release-matrix.md`.

Tier-2 = the EXPENSIVE gate that runs ONLY when a Kanban task titled `release wrap-up <version>` or `publish wrap-up <version>` is opened. Triggered manually by the user (semver bump, Phase milestone close, "we want to publish"). Cost target is intentionally high — full API matrix + security review + dep audit — because it runs maybe once per public release.

Tier-1 (per-task smoke; cheap; runs every applicable task) lives in `smoke-methodology.md` in this folder. Tier-2 builds on its conventions as the superset.

---

## Trigger conditions

The user opens a Kanban task whose title matches:
- `release wrap-up <version>` — e.g., `release wrap-up v0.3.0`
- `publish wrap-up <version>`

OR a manual user signal in chat ("we want to publish v0.3.0", "do release wrap-up").

If neither applies → Tier-2 does NOT run. Per-task work uses Tier-1 only.

---

## Pre-flight queue check

Before running any Tier-2 step:

```bash
# Verify no tasks in-progress or blocked (substitute project_id for the active project)
curl --silent "http://localhost:<api-port>/api/tasks?project_id=<id>&process_status=2"  # in_progress
curl --silent "http://localhost:<api-port>/api/tasks?project_id=<id>&process_status=4"  # blocked
```

Both MUST return `[]`. If not, abort the wrap-up and tell the user which tasks need to close (or move to backlog) first. A wrap-up with in-flight work is meaningless.

---

## Step 1 — Full Tier-1 smoke matrix

Spawn dev-tester with **full smoke mode** (NOT scoped per task — covers every endpoint, every lifecycle path, every soft-delete + lead-bundle invariant). Use the same POSITIVE+NEGATIVE pair shape as Tier-1 (see `smoke-methodology.md` in this folder).

The **endpoint matrix is project-specific** and lives in the active project's `shared/release-matrix.md`. Lead injects that matrix into the dev-tester spawn prompt.

### Output convention

dev-tester emits one section per endpoint with all probes inline. Aggregate at the end: total probes, PASSes, FAILs. Any FAIL → wrap-up RED on this step.

### Probe artifact discipline

All throwaway rows POSTed during the matrix MUST use the `_` prefix convention (`_release-<version>-<n>`, `_smoke-<timestamp>`, etc) — `.gitignore` excludes `context/projects/_*/` so scaffold folders don't pollute working tree. Tempfiles for payload bodies go in `_scratch/` at repo root (also gitignored, with `.gitkeep` keeping the dir in the index). See `smoke-methodology.md` "Restoration discipline" + "Tempfile location" for full convention.

---

## Step 2 — `/security-review` slash command (USER-TRIGGERED)

The built-in Claude Code `/security-review` skill is a whole-branch security pass. Lead **cannot** fire it programmatically — only the user can.

**Lead's job:**
1. Document the request in the wrap-up Kanban task description: "User: please run `/security-review` from the project root and paste the output below."
2. Wait for the user to fire and paste results.
3. Treat any HIGH/CRITICAL finding as wrap-up RED.

If the user opts to skip `/security-review` for cost reasons, document the skip with explicit reasoning in the wrap-up summary (this is a YELLOW, not GREEN — release ships with documented exception).

---

## Step 3 — dev-reviewer security mode

Spawn dev-reviewer with `mode: security` in the prompt body (the default review mode is correctness/style — security mode is a separate clause in `.claude/agents/dev-reviewer.md`).

### Audit surface (extend per project as the codebase grows)

- **Input validation** — Pydantic schema constraints + DB CHECK constraints consistency. Flag any schema field whose Pydantic validator is weaker than the DB CHECK, or vice versa.
- **Authn / authz** — flag whatever auth posture is documented in the project's `shared/decisions.md`. If auth is deferred to a future Phase, tag findings as **SECURITY-KNOWN-GAP** (not SECURITY-BLOCKER) referencing the deferred decision.
- **SQL injection** — verify all DB writes go through SQLAlchemy ORM or parameterised text(); flag any string-format SQL.
- **CSRF / CORS** — FastAPI default behaviour + CORS config drift.
- **Secret leakage** — env vars in logs / responses / git history. Grep `git log --all -p` for `password=`, `SECRET`, `KEY=`, `token=`. Grep current source for `print(os.environ)` style.
- **Dependency CVE** — defer to Step 4 (`pip-audit`); cross-reference findings.
- **Error-message info disclosure** — generic vs revealing PG internals. Detail-string hygiene is canonical; verify no new endpoints regress.
- **Secrets in error responses** — flag any `HTTPException(detail=str(exc))` that leaks raw exception text.

### Severity scale (DISTINCT from regular review BLOCKER/WARN/NIT)

- **SECURITY-BLOCKER** — release MUST NOT ship until fixed
- **SECURITY-WARN** — release CAN ship with explicit user accept + a follow-up Kanban task
- **SECURITY-NIT** — fix-when-convenient; no release impact
- **SECURITY-KNOWN-GAP** — documented in shared/decisions.md as deferred; NOT a release blocker

### Output

`context/projects/<active>/dev-reviewer/security-mode-review-<YYYY-MM-DD>.md`. Standard review-report shape but with the SECURITY-* severity tags.

### Anti-pattern

Security mode running on a non-release task. dev-reviewer's prompt should refuse: "Security mode is for release wrap-up only. Use default review mode for per-task audits."

---

## Step 4 — Dependency CVE audit

```bash
docker compose exec -T api pip-audit
```

`pip-audit` should be a persisted dev dep in the project's `api/pyproject.toml` so it's available after every `docker compose build` without manual install. Project-specific install context (when added, which Kanban task) is recorded in the project's `release-matrix.md`.

**Severity gate:**
- ANY `HIGH` or `CRITICAL` CVE → wrap-up RED, must upgrade or pin before release
- `MEDIUM` → wrap-up YELLOW, address if upgrade is low-risk; otherwise document and accept
- `LOW` → fix-when-convenient

Capture the verbatim output. Do NOT auto-upgrade — Lead reviews and routes to dev-devops.

---

## Step 5 — Audit-log review

```bash
docker compose exec -T db psql -U <db-user> -d <db-name> -c "
  SELECT id, <entity>_id, operation, changed_at, snapshot->>'<field>' AS <field>
  FROM <history-table>
  WHERE changed_at > '<last-release-date>'
  ORDER BY changed_at DESC LIMIT 100;
"
```

Substitute `<db-user>`, `<db-name>`, `<history-table>`, and the relevant snapshot fields from the project's `release-matrix.md` (each project documents its own audit-log schema).

### Anomalies to flag

- `operation='D'` (hard delete) on a business row — should be rare. Hard deletes are reserved for manual psql cleanup; if any appeared via app code, the app violated soft-delete policy.
- Repeated rapid soft-delete/restore cycles on the same row — unusual user pattern; investigate.
- Bulk DELETE windows — many soft-deletes within a short timestamp range; usually a script ran. Verify it was intentional.

For tables without a history table, use `updated_at` differential queries on the live table to spot mass-mutation windows:

```bash
docker compose exec -T db psql -U <db-user> -d <db-name> -c "
  SELECT id, <name>, status, is_active, created_at, updated_at
  FROM <table>
  WHERE updated_at > '<last-release-date>'
  ORDER BY updated_at DESC;
"
```

---

## Wrap-up summary template

Lead `PATCH`es the release-wrap-up Kanban task description with:

```markdown
## Release wrap-up <version> — <date>
**Branch / commit:** `<branch>` / `<short-sha>`
**Last release reference:** <prior version + date>

### Step 1 — Tier-1 full smoke
- Endpoints probed: <n> / <total>
- POSITIVE PASS: <n>; NEGATIVE PASS: <n>; FAIL: <n>
- Status: GREEN / YELLOW / RED
- Findings: <list or "none">

### Step 2 — /security-review (user-triggered)
- Status: GREEN / YELLOW / RED / SKIPPED-WITH-REASON
- Findings: <paste user-provided summary>

### Step 3 — dev-reviewer security mode
- Report: `context/projects/<active>/dev-reviewer/security-mode-review-<date>.md`
- SECURITY-BLOCKER: <n>; SECURITY-WARN: <n>; SECURITY-NIT: <n>; SECURITY-KNOWN-GAP: <n>
- Status: GREEN / YELLOW / RED

### Step 4 — Dependency audit
- `pip-audit` output verbatim
- HIGH/CRITICAL: <n>; MEDIUM: <n>; LOW: <n>
- Status: GREEN / YELLOW / RED

### Step 5 — Audit-log review
- <history-table> rows since last release: <n>
- Anomalies: <list or "none">
- Status: GREEN / YELLOW / RED

### Overall sign-off
- All GREEN → ship
- Any RED → DO NOT SHIP; route to fix tasks
- YELLOWs documented with user accept → ship with caveats
```

Move the wrap-up task to `process_status=5` only when overall sign-off is reached.

---

## Out of scope (NOT Tier-2)

- Performance / load tests — separate concern, not part of release-blocker checklist
- UX / visual regression — handled by web smoke patterns or e2e tests if the project has them
- Localisation / i18n — out of scope for default Tier-2
- License audit — handle as a separate workflow if/when project requires it
