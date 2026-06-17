# Code-leanness — baseline + lean-test rubric

Makes repo leanness **measurable** before anything is cut (de-bloat chain #2433–#2436).
Tool: [`scripts/loc-report.mjs`](../../../../scripts/loc-report.mjs) — thin node script, no
new deps, counts raw newlines across `git ls-files` (excludes lockfiles + binary/asset
extensions), buckets by path into 5 categories. Re-run any time: `node scripts/loc-report.mjs`.

## (A) Baseline — 2026-06-16

| Category | Files | Lines | Notes |
|---|---:|---:|---|
| migrations | 70 | 7,531 | `api/alembic/versions/` |
| tests | 237 | 90,252 | ~1.45× backend `src/`; the de-bloat focus of #2434 |
| methodology | 239 | 30,355 | `.claude/` + `context/` |
| generated (.codex) | 115 | 13,300 | **GENERATED from `.claude/` — NOT a de-bloat target; never hand-edit** |
| production | 423 | 97,316 | app/source/config/doc |
| **TOTAL** | **1,084** | **238,754** | |

`.codex/` is regenerated from `.claude/` by the operator → excluded from every reduction
target. Reduction work touches the other four categories only.

Sanity vs the chain's ballpark (~199K total / ~90K tests): tests match (~90K); the 238K
total runs ~20% above 199K because methodology + `.codex` (~43K combined) were likely outside
the original estimate, or the repo grew since. **238,754 is the authoritative baseline** that
later steps report their deltas against.

## (B) Lean-test rubric (applied by #2434)

This is **redundancy / brittleness removal, NOT coverage reduction.** Coverage must not drop
and the suite must stay green. When in doubt → **protect**.

### Decision checklist — per test function
1. Identify the test's primary assertion(s).
2. Does a **behavioral** test (hitting the live endpoint / full call stack) already assert
   this exact observable outcome elsewhere? No → **protected**.
3. Is the test asserting **source text** (reading a `.py`/`.ts` file as a string) rather than
   behavior? Yes + a behavioral twin covers the same contract → **removable (i)**. Yes but no
   twin → **protected** (the lock is the only coverage).
4. Could ≥2 near-identical functions fold into one `@pytest.mark.parametrize`, differing only
   in 1–2 literal scalars with no distinct edge? Yes → **removable (ii)** — fold, don't delete.
5. Otherwise → **protected**.

### Removable
- **(i) Source-text-lock duplicating a behavioral assertion.** A test opens a production source
  file and asserts a substring is present, while a separate behavioral test already asserts the
  same observable (status code + body). **Exemplar: `api/tests/test_routes_smoke.py:96`**
  (`test_get_active_project_410_detail_pinned_in_router_source`) reads `routers/projects.py` as
  text to pin the 410 detail string — but the behavioral test directly above it (lines 79–92)
  already asserts `status_code == 410` **and** the exact `resp.json()` detail end-to-end. The
  source-lock is redundant → removable.
- **(ii) Un-parametrized near-duplicates.** ≥2 functions sharing ~80%+ identical lines, differing
  only in 1–2 literals, exercising the same logical rule with no distinct edge (no different
  error code / DB state / auth role). Fold into one parametrized test, keep one.

### Protected (never cut)
Coverage-unique · auth / trust-boundary · input-validation (422/400) · edge + error-path
(404/410/409, empty, null) · data-loss / idempotency · concurrency / ordering · regression tests
pinning a real past bug (`# Regression:`) · any source-lock with **no** behavioral twin (the lock
*is* the coverage). The cost of missing a real bug exceeds the cost of one extra test.

## (C) api.ts codegen decision — #2435 (2026-06-17, dev-frontend spike)

**Decision: KEEP hand-written.** openapi-typescript / orval are net-negative for this file.

Composition of `web/lib/api.ts` (2,635 LOC): ~785 comment/doc (30% — Kanban refs + wire-format
gotchas + defensive-resilience notes), ~755 type-def code (29%), ~744 field/import/infra (28%),
~163 fetch-helper logic (6%, 59 `async function`), ~189 blank. Exports: 94 `type` · 1 `class`
(HttpError) · 59 `async function`.

| Dimension | Adopt openapi-typescript | Keep hand-written |
|---|---|---|
| Net LOC | −450–500 (after enum-override layer); **785 doc lines lost** | 0 |
| Drift | Partial: types auto-sync; fetch helpers stay manual; integer enums degrade to `number` | Manual; ~20-field additive gap |
| Dep + toolchain | new devDep + codegen build step + per-schema commit churn | zero |
| Docs preserved | no | yes (referenced by >40 components) |
| Enum type safety (`TaskStatusValue=1\|2\|…`) | lost w/o +50–80 LOC override | preserved in `constants.ts` |

orval rejected outright — adds a **runtime** dep to a deliberate 7-runtime-dep FE and imposes
react-query (FE is RSC + direct-fetch). Net: ~450–500 LOC saved costs the WHY-annotated type
docs + an enum-safety regression → negative for a solo-operator tool. **Reconsider if:** API
grows >10 top-level schemas, more contributors join, or FastAPI ships enum-aware TS generation.

**Drift surfaced (real, separate finding):** api.ts is a strict subset of the live OpenAPI (zero
phantom FE fields) but lags the backend — TaskRead missing 11 (`halted_at`,
`max_active_children`, `template_auto_run_confirmed_at`, `requires_human_review`,
`subagent_models`, `effort_override`, `forecast_cost_usd`, `audit_retry_count`, `health_alert`,
`notification_targets`, `is_active`); ProjectRead missing 9 (`agent_overrides`, `tools_config`,
`auto_decision_policy`, `tax_jurisdiction`, `legal_entity`, `fiscal_year_start`,
`currency_default`, `notification_targets`, `required_binaries`). FastAPI also emits
`process_status`/`priority`/`assigned_role`/`run_mode`/`task_kind` as bare integer/string.
**Fix lazily** — type a field when a component consumes it (blanket catch-up = YAGNI); no task opened.

## (D) Oversized production files — backlog (top 10; NOT refactored — #2435 AC4)

| # | File | LOC |
|---|---|---:|
| 1 | `api/src/routers/tasks.py` | 3,377 |
| 2 | `web/lib/api.ts` | 2,635 |
| 3 | `api/src/routers/tools_email.py` | 2,490 |
| 4 | `langgraph/worker.py` | 2,118 |
| 5 | `langgraph/nodes.py` | 1,836 |
| 6 | `api/src/schemas/task.py` | 1,670 |
| 7 | `langgraph/scenarios/capability_probe.py` | 1,430 |
| 8 | `api/src/schemas/project.py` | 1,365 |
| 9 | `web/components/TaskDetail.tsx` | 1,238 |
| 10 | `api/src/routers/projects.py` | 1,210 |
