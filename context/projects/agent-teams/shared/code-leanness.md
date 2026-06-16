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
