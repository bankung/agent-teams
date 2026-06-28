# Audit #2062 — un-reviewed 0.6.0 surfaces (pre-release)

**Date:** 2026-06-08 · **Task:** #2062 (milestone v0.6.0) · **Mode:** read-only audit, no fixes applied this round.
**Reviewers:** dev-reviewer (quality, sonnet) + dev-security-reviewer (security, sonnet).
**Scope:** surfaces in `origin/main..dev` NOT covered by #2045 (email/calendar/resources/langgraph) or #1005 (comments):
CLI installer (`cli/index.js` + `cli/lib/*`), zb-* skills (`.claude/skills/zb-*`), `audit_archive.py`/`audit.py`,
adjust UI (`AdjustFlagForm.tsx`/`TerminateFlagModal.tsx`), Playwright e2e (`web/e2e/*`), `bin/email-audit-rotate.ps1`.

> Severity column = **Lead-reconciled** (the two reviewers were internally inconsistent on F-07; Lead set it to Med — a
> skill doc-drift with low blast radius). All findings preserved; nothing dropped (multi-point rule).

## Follow-up task map (all in milestone v0.6.0)

| Task | Surface | Owner | Findings | Priority |
|---|---|---|---|---|
| **#2063** | CLI installer — SECURITY | dev-devops | FIND-01, FIND-02 (High); FIND-05, FIND-06, FIND-07, FIND-08 (Low) | HIGH |
| **#2064** | CLI installer — correctness | dev-devops | F-01 (High); F-02 (Med); F-03, F-04, F-05, F-06 (Low) | HIGH |
| **#2065** | audit_archive.py + audit.py | dev-backend | F-12, F-14 (High); F-13, FIND-03, FIND-04 (Med); F-15 (Low) | HIGH |
| **#2066** | AdjustFlagForm + TerminateFlagModal | dev-frontend | F-16 (High); F-17, F-18 (Med); F-19 (Low) | HIGH |
| **#2067** | Playwright e2e specs | dev-tester | F-20 (High, latent); F-21, F-22, F-23, F-24 (Med); F-25, F-26 (Low) | NORMAL |
| **#2068** | zb-* skills doc-drift | dev-devops | F-07, F-08, F-09 (Med); F-10, F-11 (Low) | NORMAL |

## SECURITY findings (dev-security-reviewer)

- **FIND-01 — High — `cli/lib/env.js:65,24`** — `.env` written with default umask (0644, world-readable on Linux) holding `CREDENTIALS_MASTER_KEY` (Fernet vault), API keys, `POSTGRES_PASSWORD`, `OPERATOR_ACTION_KEY`. Fix: `fs.chmodSync(envFile, 0o600)` / `{mode:0o600}`. *(No-op on Windows NTFS but real on shared Linux / the `--images` deploy path → fix before npm publish.)*
- **FIND-02 — High — `cli/index.js:288-353` (cmdUp)** — `warnIfInsecure()` (checks weak `SECRET_KEY`, `APP_ENV=development`, `APP_DEBUG=true`) runs ONLY on the `--images` path, NOT the documented source-build `up`. `.env.example` ships dev defaults + no `SECRET_KEY` → forgeable HMAC opt-out tokens. Fix: hoist `warnIfInsecure()` to a shared fn, call from both paths; add `SECRET_KEY` placeholder to `.env.example`.
- **FIND-03 — Med — `audit_archive.py:240-243`** — `AUDIT_ARCHIVE_CRON` env → `CronTrigger.from_crontab()` unvalidated; `* * * * *` accepted → sweep every minute → `tasks_history` write amplification. Fix: 5-field regex validate / next-fire sanity ≥1h before scheduling.
- **FIND-04 — Med — `api/src/routers/audit.py:34-181`** — `GET /api/audit/daily-rollup`: no auth, no rate-limit, no window cap; cross-project aggregation; `from=1970&to=2099` → full-table scan. Single-operator/local-first posture (decisions.md #2047) downgrades to WARN, but blocker before multi-user. Fix: `@limiter.limit("60/minute")` + 365-day window cap; track auth under #1275.
- **FIND-05 — Med — `cli/index.js:291-294`** — `targetDir` positional → `path.resolve` → git clone cwd, unvalidated (path traversal; `shell:false` blocks injection). Fix: guard resolved path not in system dirs; warn on pre-existing non-repo dir.
- **FIND-06/07/08 — Low** — `open-url.js` SAFE_URL_RE missing `%` (defence-in-depth, currently compiler-controlled URLs only); `email-audit-rotate.ps1:120` operator-supplied `$RuntimeDir` not asserted absolute; `env.js:88` `new RegExp(\`^${varName}=\`)` (varName currently literal). All forward-looking / not currently exploitable.

**Installer risk verdict (reviewer):** meaningful HIGH risk on the secret-handling path (FIND-01/02) the moment the stack is on a shared host / network (the `--images` target scenario). Fix FIND-01 + FIND-02 before any public `npm publish`.

**CLI dependency audit:** zero runtime deps (Node built-ins only) → minimal supply-chain risk.

## QUALITY findings (dev-reviewer)

**CLI installer:** F-01 (High) `cli/index.js:603` cmdReset calls `cmdUp([])` discarding flags (e.g. `--images`) → reset always source-builds. · F-02 (Med) `:633` cmdReset invoked with `"reset"` injected into argv. · F-03 (Low) `health.js:43` elapsed counter steps by interval not wall-clock. · F-04 (Low) `docker.js:45` `docker info` no timeout → can hang. · F-05 (Low) `confirm.js:15` no EOF/close handler → hangs on closed stdin. · F-06 (Low) `index.js:225` readline `terminal:false` mismatch.

**zb-* skills:** F-07 (Med, *reviewer-inconsistent*) `zb-task-create:54` example `priority:3` contradicts "default normal" prose → copy creates HIGH tasks. · F-08 (Med) `zb-task-create:74` lists `200` as success but POST returns 201 only. · F-09 (Med) `zb-audit` "no X-Project-Id" + project filtering note vs cross-project endpoint reality. · F-10 (Low) `zb-tasks-next` `limit=500` vs server cap. · F-11 (Low) `zb-milestone-done` 0/0 progress when all tasks cancelled → blocks release forever.

**audit module:** F-12 (High) `audit_archive.py:133-148` `total_archived` uses pre-flight count not `UPDATE.rowcount` (TOCTOU). · F-14 (High) `audit.py:77` `date.today()` (local TZ) vs UTC `timestamptz` → day-boundary mismatch; use `datetime.now(timezone.utc).date()`. · F-13 (Med) `audit_archive.py:183` self-commit vs docstring "caller commits". · F-15 (Low) magic `"verdict"` JSONB key strings.

**adjust UI:** F-16 (High) `AdjustFlagForm.tsx:83-85` budget inputs pre-filled from existing values → "empty = leave unchanged" UX broken; an operator changing only `total` re-submits all three. Fix: init `""`, show existing as placeholder. · F-17 (Med) `:185` threshold merge keeps only numeric/null keys → string/bool keys silently dropped on edit. · F-18 (Med) `TerminateFlagModal.tsx:176` `as unknown as` ref cast (input ref into textarea slot). · F-19 (Low) `0` budget vs "unlimited" hint clarity.

**Playwright e2e:** F-20 (High, latent) `review-flag-resolution.spec.ts:98` `createFlagTask` omits `task_type` → defaults `feature`, real flags are `chore`; archive-sweep filters `audit` so coverage gap + breaks if `listAuditFlags` ever filters task_type. · F-21 (Med) `:59` phantom `project_id:1` in project-create body (ignored). · F-22 (Med) `:481` `?limit=50` on audit rollup (param doesn't exist) → vacuous. · F-23 (Med) `:501` "Cleanup" test asserts only name format, no real cleanup. · F-24 (Low) `:239` contradictory budget-PATCH comment. · F-25 (Low) `smoke.spec.ts:3` stale BLOCKED note. · F-26 (Low) `playwright.config.ts:9` 30s test timeout vs 15s waits → CI flake headroom.

## Known gaps (already tracked — do NOT re-flag)
- Operator-proof gate DORMANT when `OPERATOR_ACTION_KEY` unset — documented single-operator 0.6.0 gap (decisions.md #2047).
- `author_kind` caller-asserted on task comments — deferred #2058.
- External image src in task comments (tracking-pixel leak) — deferred #2060.

## Tally
Critical 0 · High 7 (FIND-01, FIND-02, F-01, F-12, F-14, F-16, F-20) · Med ~10 · Low ~11.
