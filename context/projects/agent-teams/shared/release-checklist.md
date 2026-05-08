# Tier-2 release wrap-up checklist

> **Lead is the only writer of this file.** Updates come from Lead based on incident evidence (e.g., "release vX.Y.Z found CVE Z in dep Q — add Q to the dep-audit matrix"). Subagents read this; they do not edit it.

Tier-2 = the EXPENSIVE gate that runs ONLY when a Kanban task titled `release wrap-up <version>` or `publish wrap-up <version>` is opened. Triggered manually by the user (semver bump, Phase milestone close, "we want to publish"). Cost target is intentionally high — full API matrix + security review + dep audit — because it runs maybe once per public release.

Tier-1 (per-task smoke; cheap; runs every applicable task) lives in `smoke-checklist.md`. Tier-2 builds on its conventions as the superset.

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
# Verify no tasks in-progress or blocked
curl --silent "http://localhost:8456/api/tasks?project_id=1&process_status=2"  # in_progress
curl --silent "http://localhost:8456/api/tasks?project_id=1&process_status=4"  # blocked
```

Both MUST return `[]`. If not, abort the wrap-up and tell the user which tasks need to close (or move to backlog) first. A wrap-up with in-flight work is meaningless.

---

## Step 1 — Full Tier-1 smoke matrix

Spawn dev-tester with **full smoke mode** (NOT scoped per task — covers every endpoint, every lifecycle path, every soft-delete + lead-bundle invariant). Use the same POSITIVE+NEGATIVE pair shape as Tier-1 (see `smoke-checklist.md`).

### Endpoint matrix (v0.x — extend as new endpoints land)

| Endpoint | POSITIVE probes | NEGATIVE probes |
|---|---|---|
| `GET /health` | 200 + `{"status":"ok","env":...}` | — |
| `GET /api/projects` | list returns active rows; `?include_deleted=true` includes soft-deleted | default list excludes `status=0` rows |
| `GET /api/projects/active` | returns the one `is_active=true` row | only one such row exists; partial unique enforced |
| `GET /api/projects/by-name/{name}` | 200 for existing name | 404 for unknown name |
| `POST /api/projects` | 201 + `ProjectRead` shape; auto-scaffolds `context/projects/<name>/` | 422 on missing `lead`; 422 on `lead` outside `{dev,novel}`; 409 on duplicate active name; scaffold dispatches per `lead` (dev → 5 role folders; novel → 2) |
| `PATCH /api/projects/{id}` | real change advances `updated_at`; 409 detail string on rename conflict | identical body = no-op (`updated_at` unchanged); 400 `Cannot activate a soft-deleted project` when flipping `is_active=true` on `status=0` |
| `DELETE /api/projects/{id}` | 204; `status=0`; first DELETE advances `updated_at`; clears `is_active` if true | re-DELETE returns 204 without bumping `updated_at`; folder NOT removed |
| `GET /api/tasks?project_id=<n>` | required `project_id`; default filters `status=1` | 422 missing `project_id`; `?include_deleted=true` exposes `status=0` |
| `GET /api/tasks/{id}` | 200 + `TaskRead` shape | 404 unknown id |
| `POST /api/tasks` | 201 + `TaskRead`; `started_at`/`completed_at` NULL on create | 400 FK violation (unknown `project_id`); 422 on bad code |
| `PATCH /api/tasks/{id}` | `process_status=2` → `started_at=now()` if NULL; `process_status=5` → `completed_at=now()`; real change advances `updated_at` | identical body = no-op; soft-delete `status` field silently ignored (extra='ignore'); 400 detail strings pinned (M5: `process_status violates ck_tasks_process_status_valid` etc) |
| `DELETE /api/tasks/{id}` | 204; `status=0`; advances `updated_at` (via task.updated_at = func.now()); writes `tasks_history` 'U' row | re-DELETE 204 without bumping `updated_at` and without writing extra `tasks_history` row (M9 task lock) |

### Output convention

dev-tester emits one section per endpoint with all probes inline. Aggregate at the end: total probes, PASSes, FAILs. Any FAIL → wrap-up RED on this step.

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

### Audit surface (this stack — extend as the codebase grows)

- **Input validation** — Pydantic schema constraints + DB CHECK constraints consistency. Flag any schema field whose Pydantic validator is weaker than the DB CHECK, or vice versa.
- **Authn / authz** — currently NONE in v0.x (auth is Phase 4). Flag as **SECURITY-KNOWN-GAP** in every wrap-up until Phase 4 ships. NOT a SECURITY-BLOCKER for v0.x because it is documented.
- **SQL injection** — verify all DB writes go through SQLAlchemy ORM or parameterised text(); flag any string-format SQL.
- **CSRF** — FastAPI default behaviour + CORS config (none currently — flag if CORS opens up).
- **Secret leakage** — env vars in logs / responses / git history. Grep `git log --all -p` for `password=`, `SECRET`, `KEY=`, `token=`. Grep current source for `print(os.environ)` style.
- **Dependency CVE** — defer to Step 4 (`pip-audit`); cross-reference findings.
- **Error-message info disclosure** — generic vs revealing PG internals. The M4/M5 detail-string hygiene is canonical; verify no new endpoints regress.
- **Secrets in error responses** — flag any `HTTPException(detail=str(exc))` that leaks raw exception text.

### Severity scale (DISTINCT from regular review BLOCKER/WARN/NIT)

- **SECURITY-BLOCKER** — release MUST NOT ship until fixed
- **SECURITY-WARN** — release CAN ship with explicit user accept + a follow-up Kanban task
- **SECURITY-NIT** — fix-when-convenient; no release impact
- **SECURITY-KNOWN-GAP** — documented in shared/decisions.md as deferred (e.g., auth = Phase 4); NOT a release blocker

### Output

`context/projects/<active>/dev-reviewer/security-mode-review-<YYYY-MM-DD>.md`. Standard review-report shape but with the SECURITY-* severity tags.

### Anti-pattern

Security mode running on a non-release task. dev-reviewer's prompt should refuse: "Security mode is for release wrap-up only. Use default review mode for per-task audits."

---

## Step 4 — Dependency CVE audit

```bash
docker compose exec -T api pip-audit
```

If `pip-audit` is not installed in the api container, install it as a dev dep first (this is a one-time setup; record it in dev-devops/current-state.md and propose a `pip-audit` line in `api/requirements-dev.txt` or pyproject.toml dev section).

**Severity gate:**
- ANY `HIGH` or `CRITICAL` CVE → wrap-up RED, must upgrade or pin before release
- `MEDIUM` → wrap-up YELLOW, address if upgrade is low-risk; otherwise document and accept
- `LOW` → fix-when-convenient

Capture the verbatim output. Do NOT auto-upgrade — Lead reviews and routes to dev-devops.

---

## Step 5 — Audit-log review

```bash
docker compose exec -T db psql -U postgres -d agent_teams -c "
  SELECT id, task_id, operation, changed_at, snapshot->>'process_status' AS process_status
  FROM tasks_history
  WHERE changed_at > '<last-release-date>'
  ORDER BY changed_at DESC LIMIT 100;
"
```

Read the rows. Anomalies to flag:
- `operation='D'` (hard delete) on a business row — should be rare. Hard deletes are reserved for manual psql cleanup; if any appeared via app code, the app violated soft-delete policy.
- Repeated rapid soft-delete/restore cycles on the same row — unusual user pattern; investigate.
- Bulk DELETE windows — many soft-deletes within a short timestamp range; usually a script ran. Verify it was intentional.

For projects, there is no `projects_history` table (per shared/decisions.md). Use `updated_at` differential queries on `projects` to spot mass-mutation windows:

```bash
docker compose exec -T db psql -U postgres -d agent_teams -c "
  SELECT id, name, status, is_active, created_at, updated_at
  FROM projects
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
- tasks_history rows since last release: <n>
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
- UX / visual regression — Phase 3 will introduce; not yet
- Localisation / i18n — out of scope for v0.x
- License audit — handle at Phase 4+
