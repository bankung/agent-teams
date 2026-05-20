# Data Analytics Integration Policy (v1 — read-only)

**Status:** locked 2026-05-20 (Kanban #1271 AC5 — followup of #997).
**Scope:** v1 = analytics agents are read-only recommenders; they don't auto-connect to platforms. This doc captures the credential / scope / audit posture that v2 (active integration) will inherit.
**Connects to:** [Mode B authorization-chain doctrine](design/mode-b-authorization-chain.md) (Kanban #1205) — v2 live writes / queries against external platforms go through the same approval-chain primitives as other classifier-gated actions.

---

## Scope of v1 (today)

The 4 Data Analytics agents (`bi-analyst` Opus / `sql-optimizer` Sonnet / `dashboard-designer` Sonnet / `analytics-platform-integrator` Sonnet) currently:

- READ project context, prior dashboard specs, prior query plans
- RECOMMEND queries, dashboards, integration architectures
- WRITE markdown specs to `_scratch/` or `context/projects/<p>/shared/`

They DO NOT today: execute live queries against external warehouses, mutate dashboards in BI tools, rotate credentials, or persist secrets anywhere.

The v1 hard rule: **no live API mutations**. If an agent's brief requires "fetch the actual GA4 numbers", that's v2 territory. v1 stops at "here's the query + the projection of what the answer shape would be."

This is the same v1 posture as the SEO + SEM teams; the integration-policy concerns below are pre-staged so v2 lands quickly when ready.

## Credential storage — where secrets actually live

**Rule 0 — secrets NEVER in chat / Kanban / git.** Per the standing hidden-agenda + lock-code-hygiene discipline + `feedback_recommend_not_execute.md`: a subagent that needs a credential value asks the operator inline; the operator pastes it into an environment variable; the subagent reads `$env:VAR_NAME` and never echoes it back into output. Logs / Kanban-task descriptions / spec docs reference credential NAMES only (`ANALYTICS_GA4_SERVICE_ACCOUNT_JSON_PATH`), never values.

**Rule 1 — credentials live in one of three places, ranked by risk:**

| Tier | Location | When to use | Rotation cadence |
|---|---|---|---|
| **T1 — host env via `.env`** | `.env` file at repo root (gitignored) → docker compose loads into containers | v2 default for non-mutating reads (GA4 / GSC / Mixpanel keys). Operator manages by hand. | 90 days or on operator job-change |
| **T2 — local file under `_runtime/secrets/`** | Untracked dir per-machine, never synced. Service-account JSON files (BigQuery, GCS) land here. | When a platform needs a JSON-key file path (BigQuery service accounts). | 90 days; immediately on suspected leak |
| **T3 — Tailscale / vault-like service** | Operator's vault-of-choice (Bitwarden / 1Password Connect / HashiCorp Vault). Surfaced into containers via runtime fetch at startup. | When v2 introduces write-capable creds (Looker embed API, Tableau dashboard publish, Snowflake warehouses with $ blast radius). | 30 days; on every contractor offboard |

Today's agent-teams repo lives at T1 (.env + docker compose). T2 is staged but unused. T3 is operator's call when v2 lands.

**Rule 2 — env var naming convention:** `ANALYTICS_<PLATFORM>_<KIND>`. Examples:
- `ANALYTICS_GA4_SERVICE_ACCOUNT_JSON_PATH=/run/secrets/ga4-svc.json` (T2 path)
- `ANALYTICS_GSC_SERVICE_ACCOUNT_JSON_PATH=/run/secrets/gsc-svc.json`
- `ANALYTICS_MIXPANEL_PROJECT_TOKEN=<token>` (T1)
- `ANALYTICS_AMPLITUDE_API_KEY=<key>` + `ANALYTICS_AMPLITUDE_SECRET_KEY=<secret>` (T1)
- `ANALYTICS_BIGQUERY_PROJECT_ID=<project>` + `ANALYTICS_BIGQUERY_SERVICE_ACCOUNT_JSON_PATH=/run/secrets/bq-svc.json` (T1+T2)
- `ANALYTICS_SNOWFLAKE_ACCOUNT=<acct>` + `ANALYTICS_SNOWFLAKE_USER=<user>` + `ANALYTICS_SNOWFLAKE_PRIVATE_KEY_PATH=/run/secrets/sf.pem` (T1+T2; never password — key-pair only)
- `ANALYTICS_REDSHIFT_HOST=<host>` + `ANALYTICS_REDSHIFT_DB=<db>` + `ANALYTICS_REDSHIFT_USER=<user>` + `ANALYTICS_REDSHIFT_PASSWORD=<pw>` (T1; IAM role preferred on AWS)
- `ANALYTICS_LOOKER_BASE_URL=<host>` + `ANALYTICS_LOOKER_CLIENT_ID=<id>` + `ANALYTICS_LOOKER_CLIENT_SECRET=<secret>` (T1)
- `ANALYTICS_TABLEAU_SERVER=<url>` + `ANALYTICS_TABLEAU_PERSONAL_ACCESS_TOKEN_NAME=<name>` + `ANALYTICS_TABLEAU_PAT_SECRET=<secret>` (T1)
- `ANALYTICS_POWERBI_TENANT_ID=<tenant>` + `ANALYTICS_POWERBI_CLIENT_ID=<id>` + `ANALYTICS_POWERBI_CLIENT_SECRET=<secret>` (T1)
- `ANALYTICS_GENERIC_SQL_<DBNAME>_DSN=<DSN>` — for ad-hoc reads against operator-controlled SQL warehouses (Postgres / MySQL / SQLite local).

## Per-platform minimum-scope guidance

### GA4 (Google Analytics 4)

- **OAuth scope** (user-creds path): `https://www.googleapis.com/auth/analytics.readonly` only. Never request `analytics.edit` for v1.
- **Service-account path (preferred for unattended reads):** create svc-account in GCP project, grant `Viewer` on the GA4 property only. JSON key path stored at T2.
- **Cost surface:** GA4 Data API is free at small scale; quota = 25k requests / day per project. Track via the integrator agent's audit-log emit.
- **Audit trail:** every query produces a row in `_scratch/data-audit-trail.log` (today; future = `/api/audit-events`).

### GSC (Google Search Console)

- **OAuth scope:** `https://www.googleapis.com/auth/webmasters.readonly`. Never `webmasters` (full write).
- **Service-account path:** add the svc-account as a verified user on the property in GSC; grant restricted-view role.
- **Cost surface:** free; 1200 queries / minute soft limit.

### Mixpanel

- **API key model:** project-scoped service-account API keys. Two keys (api_key + secret) — use the SECRET only for export queries.
- **Scope:** read-only by default (admin permission separately gated).
- **Cost surface:** rate-limit 60 req/hour for export API; engagement API higher. Track via integrator.

### Amplitude

- **API key model:** project-scoped api_key + secret_key. Use secret_key for analytics queries.
- **Scope:** read-only by default; mutation endpoints require admin-tier user creds (defer to v2).
- **Cost surface:** rate-limit 1 req/sec for the analytics API; cohorts have stricter limits.

### BigQuery

- **Service-account:** dedicated svc-account per project. Grant `BigQuery Data Viewer` + `BigQuery Job User` (the latter so queries can run; no `BigQuery Admin`). Key file at T2.
- **Cost surface:** **THIS IS WHERE MONEY HAPPENS.** Pricing = $5 / TB scanned. A naive `SELECT *` on a 10TB table = $50.
  - **Hard rule:** sql-optimizer agent MUST run `EXPLAIN` first + report estimated bytes-scanned before recommending a query (enforced via the PreToolUse hook from #1271 AC3 — query-perf-gate.ps1).
  - **Soft rule:** prefer partitioned tables + `WHERE _PARTITIONTIME ...` clauses. The `sql-optimizer` agent's playbook teaches this.
- **Audit:** every query logs project_id + bytes_processed + cost_usd_estimate.

### Snowflake

- **Auth:** key-pair authentication only (no password). Private key at T2; public key registered on the Snowflake user. Optional MFA on the user account.
- **Role:** dedicated `ANALYTICS_READER` role with `USAGE` on the warehouse + `SELECT` on the target schemas. No `OWNERSHIP`, no `INSERT/UPDATE/DELETE`.
- **Cost surface:** **EVEN MORE MONEY THAN BIGQUERY.** Compute cost on warehouse-uptime; a 5-min `SELECT *` on an XL warehouse with auto-suspend off = $$$.
  - Hard rule: pin the warehouse to MEDIUM or smaller for v1; auto-suspend ≤ 60s.
  - Hard rule: same EXPLAIN-first discipline as BigQuery.
- **Audit:** every query logs warehouse + credits_used + runtime_seconds.

### Redshift

- **Auth (AWS):** IAM role preferred (no static creds); fallback = master user / password at T1 if IAM is unavailable.
- **Permissions:** read-only role with `SELECT` on the target schemas; `USAGE` on the necessary schemas.
- **Cost surface:** Redshift is reserved-capacity-priced (cluster runs whether or not anyone queries) — no per-query $ blast, but query latency burns time. Per-query monitoring still useful.

### Looker

- **Auth:** API3 credentials (client_id + client_secret) — per-user; treat like a personal secret.
- **Scope:** the user behind the creds should be a `viewer` role + access to the relevant dashboards / models only.
- **Use case for v2:** embed dashboards / export look results — read-only.
- **Cost surface:** Looker licenses are per-seat; API doesn't generate new billing.

### Tableau

- **Auth:** Personal Access Tokens (PAT) — name + secret. PATs are user-scoped; create a dedicated PAT for the integrator agent.
- **Scope:** PAT inherits the underlying user's permissions; pair it with a "Viewer" role on a dedicated user account.
- **Use case for v2:** publish dashboard specs / export reports.

### Power BI

- **Auth:** Azure AD app registration with `client_id` + `client_secret` + `tenant_id`. Service principal pattern.
- **Scope:** workspace-scoped — grant the SP `Viewer` on relevant workspaces only.
- **Use case for v2:** read datasets, refresh schedules. Write capability gated on v2 review.

### Generic SQL DBs (Postgres / MySQL / SQLite / DuckDB / MS SQL)

- **Auth:** DSN string (`postgresql://user:pw@host:5432/db?sslmode=require`) at T1.
- **DB-side enforcement:** create a **dedicated read-only role** on the warehouse (`CREATE ROLE analytics_reader WITH LOGIN PASSWORD ...; GRANT CONNECT, USAGE, SELECT ON ALL TABLES ...`). The DSN points at this role; the read-only enforcement lives in the DB, not in the agent's discipline.
- **Cost surface:** zero direct $ on self-hosted; cloud-managed (RDS / Aurora / Cloud SQL) charges per-cluster runtime — same as Redshift posture.

## Audit trail — what gets logged

Every integrator-agent invocation produces an audit row with these fields (today: append to `_scratch/data-audit-trail.log`; v2: `POST /api/audit-events`):

```
<iso-ts>\tproject_id=<id>\tagent=<agent>\tplatform=<platform>\tquery_kind=<read|metadata|estimate>\tbytes_scanned=<N>\tcost_usd_estimate=<F>\trows_returned=<N>\tlatency_ms=<N>
```

Three fields are load-bearing for v2 review:
- `cost_usd_estimate` — operator's primary tripwire (BigQuery / Snowflake especially)
- `query_kind` — `read` = pulled data; `metadata` = schema/permission check; `estimate` = EXPLAIN-only (pre-flight)
- `rows_returned` — sanity check vs estimate; a 10× delta flags caching or filter drift

The PostToolUse dashboard-publish hook (#1271 AC3 — dashboard-publish.ps1) emits to the same log file when an agent writes a dashboard spec touching sensitive-keyword data.

## Rotation cadence + offboarding

- **Quarterly (every 90 days):** rotate T1 + T2 secrets via operator-led runbook. Touch every `ANALYTICS_*` env var.
- **On operator job-change** (a Thanit-search target lands → company / wallet / data sources shift): rotate all T1+T2 secrets immediately + re-register service accounts on the new GCP/AWS/Azure tenants.
- **On suspected leak:** revoke the affected key + rotate; audit `_scratch/data-audit-trail.log` for last 30 days for anomalous usage.

## v2 hooks — what changes when active integration lands

When v2 (live writes / queries) ships, this doc gets extended with:

1. **Authorization chain (Mode B integration):** each platform → which Pattern (1=last-click / 2=narrow-allowlist / 3=pre-signed-token / 4=Kanban-audit-trail / 5=approval_policies-harness) gates its write actions. E.g., a Snowflake `INSERT` to a curated table → Pattern 5 with text_contains_all=`["snowflake","insert"]` + amount_usd_lt=`5.00`.
2. **Budget gate:** `cost_usd_estimate` aggregated daily against `projects.daily_budget_usd` (per #1194). Spawn-time 429 gate fires before any agent emits a $-blast query.
3. **Approval-policies JSONB (per #957 + #1274):** Layer A worker matchers for HITL routing + Layer B hook matchers for tool-gating (per `decisions-approval-policies-schema.md` Kanban #1279). Sample rule shape gets a section here.

These are intentionally OUT of v1 scope. The operator should review this doc before unlocking v2 for any platform.

## Cross-references

- Kanban #997 — Data Analytics team Phase 1 (parent)
- Kanban #1271 AC5 — this doc
- Kanban #1205 — Mode B authorization-chain design
- Kanban #1194 — Hard cost cap + budget gate (composes with `cost_usd_estimate` audit field)
- Kanban #1279 — approval_policies coexistence schema
- `context/projects/agent-teams/shared/design/mode-b-authorization-chain.md`
- `context/projects/agent-teams/shared/decisions-approval-policies-schema.md`
- `_scratch/draft-data-query-perf-gate.ps1` + `.sh` (PreToolUse — query-perf gate per #1271 AC3)
- `_scratch/draft-data-dashboard-publish.ps1` + `.sh` (PostToolUse — sensitive-data audit per #1271 AC3)
