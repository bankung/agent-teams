---
name: tn-audit
description: >-
  Run an on-demand health audit of a project — spawn the read-only project-auditor for the 3 baseline
  metrics (budget burn, task failure rate, drift) + a continue/review/pause call, and show the recent
  audit-rollup trend. Use for a quick "how healthy is this project?" check.
argument-hint: "[project name or id]   (defaults to the bound project)"
allowed-tools:
  - Bash(curl:*)
  - Read
---

# /tn-audit — on-demand project health audit

`$ARGUMENTS` = optional project name or id. Default = the bound project (`_runtime/lead_project_id.txt`).
NOTE: there is NO "run audit" API endpoint — the audit RUN is the **project-auditor** agent. The
`/audit` router only exposes the historical rollup (read).

## Step 1 — resolve the target project
If a name is given, resolve via `GET /api/projects/by-name/<name>`; if an id, use it; else the bound id.

## Step 2 — run the audit (spawn the read-only agent)
Spawn the **project-auditor** subagent for the target project. It produces a structured report:
the 3 baseline metrics (budget burn rate, task failure rate, drift placeholder) + a
**continue / review / pause** recommendation. It is read-only — it proposes, never mutates.

## Step 3 — pull the trend (cross-project rollup; NO X-Project-Id)
```
curl --silent http://localhost:8456/api/audit/daily-rollup \
  -o _scratch/tn_audit_rollup.json -w "%{http_code}"
```
This endpoint is cross-project (takes NO X-Project-Id header; ordered project_id ASC, day DESC).
Filter the rows to the target project_id for the recent-days trend.

## Step 4 — report
Combine: the project-auditor's 3 metrics + recommendation, plus the recent rollup trend. Surface any
"review"/"pause" signal prominently. Keep proposals as proposals (operator decides on any action).

## Usage
```
/tn-audit
/tn-audit secretary
```
