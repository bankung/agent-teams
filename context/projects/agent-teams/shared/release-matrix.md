# Tier-2 release matrix — agent-teams

> **Project-specific Tier-2 config.** Lead is the only writer.
> **Cross-project methodology** (trigger conditions, pre-flight, Step 1-5 patterns, severity scales, wrap-up summary template) lives in `context/leads/dev/release-methodology.md`. Read both — methodology defines the flow, this file defines the agent-teams specifics.

## Step 1 — Endpoint matrix (v0.x — extend as new endpoints land)

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
| `POST /api/tasks` | 201 + `TaskRead`; `started_at`/`completed_at` NULL on create; `parent_task_id` accepted (Kanban #238 — null OK; FK-validated when set) | 400 FK violation (unknown `project_id` or `parent_task_id`); 422 on bad code; 422 on parent_task_id pointing to a different project's task |
| `PATCH /api/tasks/{id}` | `process_status=2` → `started_at=now()` if NULL; `process_status=5` → `completed_at=now()`; real change advances `updated_at` | identical body = no-op; soft-delete `status` field silently ignored (extra='ignore'); 400 detail strings pinned (M5: `process_status violates ck_tasks_process_status_valid` etc) |
| `DELETE /api/tasks/{id}` | 204; `status=0`; advances `updated_at` (via task.updated_at = func.now()); writes `tasks_history` 'U' row | re-DELETE 204 without bumping `updated_at` and without writing extra `tasks_history` row (M9 task lock) |

## Step 4 — Dependency audit context

`pip-audit` is a persisted dev dep in `api/pyproject.toml` (added 2026-05-08, Kanban #123) — available in `agent-teams-api` after every `docker compose build` without manual install. If a future image rebuild ever surfaces "command not found", verify the dev block in `api/pyproject.toml` still includes `pip-audit>=2.7,<3.0`.

## Step 5 — Audit-log queries (agent-teams DB)

```bash
# tasks_history (full audit trail; soft-delete-tracked via 'U' rows)
docker compose exec -T db psql -U postgres -d agent_teams -c "
  SELECT id, task_id, operation, changed_at, snapshot->>'process_status' AS process_status
  FROM tasks_history
  WHERE changed_at > '<last-release-date>'
  ORDER BY changed_at DESC LIMIT 100;
"

# projects (no projects_history table per shared/decisions.md — use updated_at differential)
docker compose exec -T db psql -U postgres -d agent_teams -c "
  SELECT id, name, status, is_active, created_at, updated_at
  FROM projects
  WHERE updated_at > '<last-release-date>'
  ORDER BY updated_at DESC;
"
```

DB connection: user `postgres`, database `agent_teams`. Container name: `db` (per `docker-compose.yml`).

## Past wrap-up references

- **2026-05-08 (Kanban #81)** — first Tier-2 dry-run on commit `f2edbae`. Status RED (#120 BLOCKER tasks-router updated_at parity; #121-#123 SECURITY-WARN/NIT bundle). Full report: `shared/backfill-2026-05-08.md`. Closed via `0546245` consolidation.
