# Architectural & process decisions — agent-teams (the Kanban app)

> **Lead is the only writer of this file.** Subagents propose updates in their final report — Lead reviews, may ask the user, then writes the entry.
>
> **Scope:** decisions about **agent-teams the Kanban app itself** — its data model, endpoints, UI, migrations, deps, schema choices. agent-teams is also the dogfood project for the dev-team orchestration system, but **methodology decisions** (Tier-1 / Tier-2 / lifecycle / zone architecture / agent prompts) live in `context/teams/dev/decisions.md` (the **Team-methodology zone**) — not here. When a project-specific incident produces a methodology lesson, the incident decision goes here and the methodology decision goes in the team file, cross-linked.
>
> Format: append-only log. Newest entry at the top. Each entry has a date, scope, and the locked decision + reasoning + downstream implications. Granular commit-narrative (per-agent lifecycle, pytest counts, file lists) belongs in `git log`, not here.

<!--
Template:

## YYYY-MM-DD — <short title>
**Scope:** frontend | backend | devops | qa | reviewer | shared
**Decision:** <what we decided>
**Reasoning:** <constraints, tradeoffs, alternatives considered>
**Implications:** <downstream coupling>
-->

## 2026-05-11 — Web container-internal port 3000 → 5431 — Kanban #763 closed (full symmetry with api 8456:8456)
**Scope:** devops / shared / standards
**Decision:** Close the asymmetry left by #762 (host-side only). Flip the container-internal Next.js listener from 3000 → 5431 so the compose mapping becomes **`5431:5431` symmetric** — mirrors the api precedent (host = container = 8456). `docker compose exec -T web wget http://localhost:5431` now works identically to host `curl localhost:5431`; no inside-vs-outside port gear-shift.

- **3-file devops diff:**
  - `web/Dockerfile` — `EXPOSE 3000` → `EXPOSE 5431`
  - `web/package.json` — scripts `next dev -p 3000` → `next dev -p 5431`; `next start -p 3000` → `next start -p 5431`
  - `docker-compose.yml` — port mapping `${WEB_PORT:-5431}:3000` → `${WEB_PORT:-5431}:5431`; healthcheck `wget http://localhost:3000` → `localhost:5431`; inline comment example port updated.
- **Image rebuild required** (Dockerfile EXPOSE is metadata, but the `next dev -p 5431` script change requires the image to ship the updated package.json — `docker compose build web` + `up -d web`). Devops confirmed via `docker compose ps web` showing `0.0.0.0:5431->5431/tcp` + `Health:healthy` post-rebuild.
- **Tier-1 verdict GREEN 5/5** with **matched-pair listener-flip proof:** Probe B (`docker exec wget localhost:5431` inside container) PASS + Probe C (`docker exec wget localhost:3000`) refused with `Connection refused EXIT=1`. Together these are causal proof that the listener moved (not just port-forwarded). #407 V3 surface still serves 6/6 markers on the symmetric port.
- **Reviewer NIT from #762 closed in this slice:** the project-scoped-port rule in `context/standards/docker/compose.md` got promoted + the symmetric-port-mapping rule explicitly added (host:container must match unless deliberate-asymmetry justification). Worked examples: api 8456:8456 (from project genesis) + web 5431:5431 (post-#763).
- **Files DELIBERATELY untouched:** `web/lib/api.ts`, `web/components/**`, `web/app/**`, `INTERNAL_API_URL` (api URL, unchanged), `NEXT_PUBLIC_API_URL` (api URL, unchanged), `.env.example` (already correct from #762), `.claude/settings.json` allowlist (already correct from #762), `.claude/hooks/tester-curl-allow.ps1` (regex port-agnostic), `README.md` (already correct from #762). Historical entries preserved.

**Reasoning:** Host:container port asymmetry was a hidden footgun. `docker compose exec` debugging probes silently produced an inside-vs-outside surface (developer types `localhost:5431` outside, must remember `localhost:3000` inside). The api side never had this issue (8456:8456 from genesis); web inherited it from Next.js's default 3000. Symmetry restores the "agent-teams web = 5431, period" mental model. Cost is minimal — 3 files, one image rebuild. The symmetry rule is now codified in `standards/docker/compose.md` so future services / scaffolds get it right at provisioning.

**Implications:**
- **Standards rule landed:** `standards/docker/compose.md` now explicitly says container-side port MUST match host-side port (with the inside-vs-outside gear-shift anti-pattern as the worked example). Future Lead spawn prompts should reference this when scaffolding new services.
- **Healthcheck contract reinforced:** healthcheck command port literal MUST match the container-internal listener. The symmetric mapping makes this automatic; asymmetric mappings silently break healthchecks if devs forget to update both the port mapping AND the healthcheck command.
- **`.env`-override risk unchanged from #762** — local `WEB_PORT=<value>` still overrides the compose default; same caveat as #762 close-out.
- **Slice scope clean** — devops, Lead, tester, reviewer cycles all converged GREEN. No follow-up filed.

**Superseded:** N/A — additive (closes the #762 residual). #762's "container-internal port stays 3000" note in this file's entry is correct historical context for that slice's scope; #763 is the deliberate follow-through.

---

## 2026-05-11 — Web host port 3000 → 5431 — Kanban #762 closed
**Scope:** devops / shared / standards
**Decision:** Bind agent-teams web to host port **5431** (custom, project-scoped). Container-internal port stays **3000** (unchanged: `web/Dockerfile` EXPOSE, `next dev -p 3000`, in-container healthcheck `wget http://localhost:3000`, `INTERNAL_API_URL`). Only the host-side mapping changes via `WEB_PORT` env-var; `docker-compose.yml` default `${WEB_PORT:-3000}:3000` → `${WEB_PORT:-5431}:3000`. Mirrors the **api project-scoped port pattern** (8456 for agent-teams api) — each project picks its own host port at scaffolding time instead of using the framework default.

- **User rationale (2026-05-11):** future scaffolded projects will use Next.js default 3000; collision-free side-by-side dev requires project-scoped host ports. Picking 5431 here reserves the slot for agent-teams web; analogous to api=8456 reserving for agent-teams api.
- **Container-internal port DELIBERATELY unchanged.** Container-side stays framework-native (3000 for next dev) — only host mapping is project-scoped. This minimizes blast radius: `web/Dockerfile`, `web/package.json` scripts, `INTERNAL_API_URL`, in-container healthcheck all untouched.
- **Files edited (7):** `.env.example` (WEB_PORT default + comment), `docker-compose.yml` (default substitution), `.claude/settings.json` (3 allowlist lines for `localhost:5431`), `.claude/hooks/tester-curl-allow.ps1` (2 comment lines; hook regex `(localhost|127\.0\.0\.1):\d+` is port-agnostic — no logic change), `README.md` (host-facing port refs in quickstart + services table + Kanban UI section), `shared/smoke-matrix.md` (Web URL line + #762 cross-link), `standards/docker/compose.md` (example port + project-scoped-port rationale note).
- **Files DELIBERATELY untouched:** `web/Dockerfile`, `web/package.json`, `web/lib/api.ts`, `web/components/**`, `web/app/**`, in-container healthcheck command, `INTERNAL_API_URL`. All container-internal or app-code; port abstraction stops at the compose mapping. Historical entries in `current-state.md` files and prior `decisions.md` entries also UNTOUCHED — audit trail; values were correct at write-time.

**Reasoning:** Default-port collision is a real cost on multi-project workstations (Next.js 3000, Vite 5173, Postgres 5432, FastAPI 8000 are all common defaults). Project-scoped port allocation at scaffolding time avoids retro-incidents later (#762 itself is the retrofit — would have been cheaper to allocate at Phase 3 V1). Pattern now codified in `standards/docker/compose.md` for future project scaffolds.

**Implications:**
- **Tier-1 dev-tester GREEN 5/5** on the new port. All #407 V3 markers (data-task-id ≥ 50, data-project-switcher = 1, data-board="dnd" = 1, data-consent-grant-trigger = 1) intact on `localhost:5431/p/agent-teams`. Old port 3000 returns connection-refused (`STATUS:000 EXIT:7`). Container healthcheck still GREEN (internal port 3000 unchanged).
- **Hook regex port-agnostic** — `tester-curl-allow.ps1` auto-allow continues to work for `localhost:5431` without any port-anchored allowlist gap. Tester observed zero permission prompts across 5 curls on 3 distinct ports.
- **Local `.env` consideration:** if a developer machine has `.env` with `WEB_PORT=3000`, that overrides the compose default. The repo's `.env.example` is updated; individual developers must update their local `.env` (gitignored). The current Lead workstation `.env` does NOT contain `WEB_PORT` — compose-default fall-through; no action needed here.
- **Standards insight surfaced:** project-scoped host ports for dev containers (added to `standards/docker/compose.md`). Mirrors the implicit pattern api=8456 + new web=5431; documenting it closes the loop and shifts the convention from "implicit per project" to "explicit at scaffolding time."

**Superseded:** N/A — additive config + doc. Phase 3 V1 scaffold's choice of port 3000 (originally documented in the 2026-05-08 #406 entry, kept historical) is the prior state.

---

## 2026-05-11 — `BACKEND_FAILURE_INJECT` env-knob — Kanban #761 closed (env-knob slice; Playwright residual deferred)
**Scope:** frontend / shared / team-methodology
**Decision:** Add a test-only env-knob `BACKEND_FAILURE_INJECT` consumed by `web/lib/api.ts` `jsonFetch`. When set to `"true"` AND `NODE_ENV != "production"`, `jsonFetch` throws `new HttpError(500, ...)` BEFORE hitting the real backend. Used by dev-tester to verify the WARN-1 fix from #760 (Server Component catch routes non-404 errors to `app/error.tsx`, NOT `notFound()`). This is the runtime verification path that was deferred from #760.

- **Double-guarded against production:** (a) `process.env.NODE_ENV !== "production"` check inside the code path, AND (b) non-`NEXT_PUBLIC_*` naming so the var is inaccessible to the client bundle (Next.js inlines non-public vars as `undefined` on the client). Single-failure prod-enablement is structurally impossible.
- **Detail / message source-text-locked:** `"BACKEND_FAILURE_INJECT=true (synthetic 500 from web/lib/api.ts)"`. dev-tester asserts the substring + verbatim stack trace chain `jsonFetch → getProjectByName → ProjectBoardPage`.
- **Boolean-only V1 (no per-path scoping).** Original #761 description mentioned `BACKEND_FAILURE_INJECT_PATHS` for surgical injection; deferred — simple boolean is enough for the WARN-1 probe and any future generic SSR-failure smoke. File follow-up if surgical scoping is ever needed.
- **Tier-1 methodology probe C1-live landed in `context/teams/dev/smoke-methodology.md`** — wraps the full enable / probe / restore cycle (docker-compose edit → restart → curl → restore → git diff = empty assertion). Optional probe — run only when task touches Server-Component error handling.

**Reasoning:** Static-code review confirmed the WARN-1 discriminator logic post-#760, but a live runtime assertion was missing. The env-knob is the cheapest mechanism that produces a real non-404 throw from the same `jsonFetch` code path the real backend uses — no mock layer, no test framework. The synthetic `HttpError(500)` traverses the same `app/p/[name]/page.tsx` catch + the same `if (e instanceof HttpError && e.status === 404) notFound(); throw e;` discriminator that a real DB outage would. Tester captured verbatim stack trace + RSC `data-dgst` sentinel + `app/error.tsx` chunk-registration evidence proving the error-boundary path fires.

**Implications:**
- **Tier-1 verdict GREEN 5/5** with `git diff docker-compose.yml` empty post-restore (production-grade restoration gate intact).
- **Methodology gotcha captured:** Next.js dev-mode SSR with a `"use client"` `app/error.tsx` renders the Suspense loading skeleton in the initial HTML, NOT the error UI text — the error.tsx hydrates client-side. The distinguishing wire-level signal is the `<template data-dgst="..." data-msg="..." data-stck="...">` sentinel + RSC graph's error.tsx chunk registration. Captured in the methodology probe so future testers don't waste cycles asserting against the visible-text marker.
- **Playwright residual deferred to a new Kanban ticket** (alpine/musl libc vs glibc blocker — Playwright wants glibc, web image is alpine). Options for the deferred slice: (a) switch `web/Dockerfile` to `node:20-slim` (Debian), (b) add separate `web-e2e` service on `mcr.microsoft.com/playwright` base, (c) other. User-decision when the slice opens.
- **Standards insight (CONFIRM, proposed for `context/standards/nextjs/` or `general.md`):** Test-only env knobs in SSR code MUST be double-guarded — (i) `NODE_ENV !== "production"` runtime check, AND (ii) non-`NEXT_PUBLIC_*` naming. The double-guard is the difference between "dev-only by convention" and "structurally impossible to enable in prod or in-browser." Worked example: `BACKEND_FAILURE_INJECT` in `web/lib/api.ts`.

**Superseded:** N/A — additive infrastructure. Original #761 scope split: env-knob shipped here; Playwright harness + D1-headless UX walk deferred.

---

## 2026-05-11 — Typed `HttpError` + ProjectSwitcher loadError reset — Kanban #760 closed (V3 WARNs)
**Scope:** frontend / shared
**Decision:** Three operational-quality WARNs from #407 V3 reviewer closed in one FE slice. No backend changes. No new dependencies. `tsc --noEmit` clean; #407 V3 Tier-1 baseline re-verified (57 task rows, switcher + grant trigger intact, 404 path renders not-found marker).

- **`web/lib/api.ts`** — exported `class HttpError extends Error { readonly status: number; readonly detail: unknown }`. `jsonFetch` throws `HttpError` instead of bare `Error` on non-2xx. `.message` semantics preserved (formatted detail OR status-line fallback), so all existing `err instanceof Error ? err.message : "..."` catches in Board.tsx + ConsentGrantModal.tsx + ProjectSwitcher.tsx work unchanged. **Discrimination at the throw layer, not the catch layer** — each caller picks its behavior (404 → `notFound()`, others → bubble to `error.tsx`).
- **`extractDetail` removed; new sync `formatDetail(detail: unknown): string | null`** handles BOTH string `detail` (400 / 404 source-text-locked) AND **array `detail`** (Pydantic 422 from `extra='forbid'` + future field validators). Array path joins each error's `msg` field with `"; "`; `JSON.stringify` per-element fallback for unknown shapes. Pre-#760 the modal rendered bare `"422 Unprocessable Entity"` on extra-field smuggle; now renders the actual `"Extra inputs are not permitted"` message.
- **`web/app/p/[name]/page.tsx`** — `catch (e) { if (e instanceof HttpError && e.status === 404) notFound(); throw e; }`. Non-404 errors (500, connection-refused, future 422) bubble to `app/error.tsx` — symmetric with the unguarded `listTasks` below. Closes the WARN-1 footgun where backend outage looked like "wrong project name" to the user.
- **`web/components/ProjectSwitcher.tsx`** — new `onToggle` handler calls `setLoadError(null)` before `setOpen((v) => !v)`. Trigger button wired to `onToggle`. The lazy-fetch effect's `projects.length > 0` short-circuit preserves happy-path no-refetch. Pre-#760, a single failed fetch permanently latched the error state until full-page reload; now every (re)open retries — correct UX for a dropdown.

**Reasoning:** Typed HTTP errors at the throw layer is the canonical TS pattern for letting each caller discriminate without parsing `error.message` strings. The `HttpError extends Error` shape keeps backward-compatibility with every existing `instanceof Error` catch — zero refactor required across the rest of the codebase. The `Server / Client bundle duplication` concern (would `instanceof HttpError` fail across boundaries?) is dismissed by evidence: the Server-Component catch in `page.tsx` lives in the same Node SSR process as the throw site in `lib/api.ts` (same module instance → same class identity); the Client catches (Board / Modal / Switcher) use `err instanceof Error` via the prototype chain, where class identity is irrelevant. If a future Client surface needs `instanceof HttpError`, fall back to duck-typing (`'status' in e && e.status === 404`).

**Implications:**
- **Tier-1 verdict GREEN 5/5.** A (#407 baseline re-confirm), B (404 path via discriminator), C (non-404 → error.tsx — static code review per spawn-brief authorisation since live 500 simulation requires container restart), D (loadError reset — static code review since no headless browser in `web` image), N1 (tsc clean).
- **Live runtime defense-in-depth deferred** (Probes C-live + D-headless) — both fixes have FE + reviewer + static-code triangulation; tester explicitly noted defers are safe. Future tooling slice candidate: add a Playwright harness to the web container + a `BACKEND_FAILURE_INJECT=true` SSR-side knob for deterministic 500 injection.
- **`api-contracts.md` did NOT need an edit** — Pydantic 422 array shape was already documented at the contract level; `formatDetail` matches the documented shape verbatim.
- **Tester surfaced a marker-grep drift in `standards/nextjs/notfound-dev-vs-prod.md` (which Lead wrote 2026-05-11):** the literal `>This page could not be found<` text-node pattern does NOT match in `next dev` SSR streams (markers live inside `__next_f` JSON chunks). The correct substring fingerprint is `could not be found` (without the angle-brackets). Two Tier-1 probes (#407 V3 + #760) hit this trap — surface to user for standards correction.
- **Two new standards candidates surfaced by reviewer** (propose-only — human MA pending): (a) `nextjs/typed-error-catch.md` (Server Component catch must discriminate via typed error — bare `catch { notFound() }` is the anti-pattern); (b) `typescript/typed-errors.md` (class-shape rule for HTTP-error classes — `extends Error`, `readonly status`, optional `readonly detail`, super(message) for legacy compat).

**Superseded:** N/A — additive refactor; existing #407 V3 surface preserved exactly.

---

## 2026-05-11 — `GET /api/projects/{id}` route added — Kanban #691 closed
**Scope:** backend / shared
**Decision:** Wire the direct id-based lookup route. Was 405 Method Not Allowed pre-#691 (only PATCH + DELETE registered on `/{project_id}`). New `GET /{project_id}` mirrors `/by-name/{name}` parity: `get_or_404` with `status=RecordStatus.ACTIVE` — soft-deleted rows 404 (parity). Detail string `f"Project id={project_id} not found"` source-text-locked, **byte-equal with PATCH / DELETE / grant-consent** on the same path (single shared format).

**Reasoning:** FE V3 project switcher (and future external integrations) want id-based GETs; today they must use `/by-name/{name}` or `?...` filters as a workaround. Active-only filter parity with `/by-name/{name}` is the right contract: soft-deleted rows should not be visible via id either; restore is a future admin path.

**Implications:**
- pytest 302 → 305 GREEN (+3 tests: positive on seeded id=1, 404 on missing, 404 on soft-deleted).
- Tier-1 live smoke 5/5 GREEN including route-ordering defense-in-depth check (`/active` still 410, `/by-name/agent-teams` still 200 — new dynamic route did NOT shadow the static segments).
- Route ordering safe by two independent defenses: (a) declaration order at lines 60 (`/active`) + 92 (`/by-name/{name}`) + 109 (`/{project_id}`); (b) `project_id: int` makes Starlette's int-converter reject non-digit segments like `"active"` and `"by-name"` outright (reviewer-confirmed).
- Reviewer 1 cosmetic NIT only (compress 4-line comment block to 2 lines — optional; deferred).

---

## 2026-05-11 — Phase 3 V3 landed — Kanban #407 closed (project switcher + consent grant)
**Scope:** frontend / shared
**Decision:** First mutation surface on the Kanban board (V2 was read-only; T4 #709 added drag-drop; this slice adds project navigation + the consent grant mutation). Route structure split:
- `/` → Server `redirect()` to `/p/${NEXT_PUBLIC_PROJECT_NAME ?? "agent-teams"}` (3-line page).
- `/p/[name]` → dynamic Server Component that `getProjectByName(params.name)` + renders `<Board>`; `notFound()` on the 404 throw.
URL is the project-selection source-of-truth — **NO localStorage** (scope-lock). URL bookmarks are how users share/save project context.

- **`<ProjectSwitcher>`** (Client) lives in the Board header (left of project-name h1). Lazy-fetches `listProjects({status:1})` on first open; client-side `router.push` on selection; outside-click + Escape close; hairline Linear-style dropdown with team chip per row. Stale list is acceptable for V3 (no project create/edit UI yet).
- **`<ProjectConsentGrantModal>`** (Client) embedded in the zinc-banner branch of `<ProjectConsentBanner>` (Server). **Composition pattern** — Server parent imports Client child as sibling, banner stays SSR; only the action is shipped to the browser. Typed-acknowledgment flow per #483: text input must match `project.name` exactly (case-sensitive). Backend 400 detail `"confirm_name must match project name exactly"` renders verbatim in an inline red alert. **NO optimistic update** — deliberate-action mutation class (auditable / consent-binding); wait for 200 then `router.refresh()` re-runs the Server banner so it flips zinc → emerald. Idempotent re-grant returns 200 unchanged on the wire (server side); UI surface for re-grant is structurally unreachable once consented (modal trigger removed from the emerald-branch DOM). No revoke UI — backend endpoint not yet shipped.
- **Two new API helpers** in `web/lib/api.ts`: `listProjects(opts?)` and `grantConsent(projectId, confirmName)`. Both **omit `X-Project-Id`** (project endpoints — project IS the resource).

**Reasoning:** Server/Client composition pattern is the canonical Next.js 14 App Router shape and the textbook anti-pattern is making the parent Client just to embed an interactive child (ships read-only state to the browser unnecessarily). Deliberate-action mutations (consent grant, account delete, payment confirm) MUST NOT use optimistic updates — auditable / legally-binding / hard-to-reverse → wait for server confirmation. V3 #407 grant flow is the worked example; the V2 drag-drop optimistic-update pattern (#709, locked) is the contrast (low-stakes mutation where optimistic IS correct). Both rules surfaced as candidate `context/standards/web/` insights (human MA pending).

**Implications:**
- **Tier-1 dev-tester verdict GREEN 11/11.** Probe pairs causally bound (A vs J: same web server, only diff is consented state → zinc-trigger-present vs emerald-trigger-absent; G + H: same project, idempotence locked via "non-null on first + byte-equal on re-grant"). `?status=1` silently ignored by backend surfaced as YELLOW — code is correct, gap is in the backend (no `status: int | None = Query(None)` plumbing) and api-contracts.md (now documents the silent-ignore explicitly).
- **Dev-mode quirks for testers:** `next dev` renders `notFound()` as HTTP 200 + 404-page body (not wire 404); `next dev` emits `redirect()` as a meta-refresh sentinel + `NEXT_REDIRECT;...;307` template hint (not wire 307). Production `next build && next start` is the only path that emits wire-level 404 / 307. Smoke matrices on V3 routes must assert against rendered markers (e.g., `>This page could not be found<`) OR run a prod build. Captured for `context/standards/web/nextjs/` insight (human MA pending).
- **Three WARNs filed by dev-reviewer for follow-up (do NOT block #407 close):** (a) `app/p/[name]/page.tsx` bare `catch { notFound() }` swallows non-404 backend errors as 404 — fix via `jsonFetch` typed-error refactor (`HttpError extends Error { status: number }`); (b) `ProjectSwitcher.loadError` never reset → permanent failure latch on first-fetch failure, reset on (re)open; (c) `extractDetail` only handles `typeof detail === "string"` — Pydantic 422 array form falls back to `"422 Unprocessable Entity"`, defense-in-depth fix in the same helper. WARN-1 + WARN-3 share the same fix surface and were bundled into one follow-up Kanban ticket; WARN-2 filed separately or bundled together.

**Superseded:** none. Builds on V2 polish (#406+) and inherits T3/T4/#750 selectors unchanged.

---

## 2026-05-11 — `tasks.is_pending` schema slice — Kanban #750 closed (supersedes #748 pending=TODO design error)
**Scope:** backend / frontend / devops / shared
**Decision:** "pending" is a first-class schema flag — `tasks.is_pending BOOLEAN NOT NULL DEFAULT FALSE` — orthogonal to `process_status`. Migration 0011 additive (PG 16 metadata-only via `server_default=false`; 94 rows backfilled). Cross-state rule enforced APP-LAYER at `src/services/is_pending.py`: `is_pending=true` REQUIRES `process_status=2` (in_progress). Backwards process_status transitions do NOT silently mutate is_pending — validator catches invalid pairs at write time.
- **Source-text-locked detail:** `"is_pending=true requires process_status=2 (in_progress)"`. Pinned at 4 rejection sites (POST default-ps, POST explicit-ps=3, PATCH asymmetric drift, PATCH drag-proxy).
- **Resolved-final PATCH pattern** (4th worked example after task_kind/run_mode/scheduled_at): `resolved_is_pending = updates.is_pending if 'is_pending' in updates else task.is_pending`; same for `resolved_process_status`. Validator runs against the pair. Bundled clear `{is_pending:false, process_status:3}` is the documented escape hatch.
- **FE predicate:** `task.is_pending && task.process_status === TaskStatus.IN_PROGRESS` keys yellow card bg + `<PendingBadge>` + `data-card-pending`. Yellow is structurally locked to in_progress (backend rejects impossible pair; FE `=== IN_PROGRESS` is second gate).
- **Pending cards NOT draggable.** `draggable = !isAi && !isPending` on `TaskCard`. Backend cross-state validator already rejects implied PATCH from drag (400 + optimistic rollback + toast); FE surfaces as `cursor-not-allowed` + `data-draggable="false"`. To move a pending card user must first PATCH `is_pending=false`.
- **Known minor a11y NIT (deferred):** `aria-disabled={isAi}` on TaskCard still keys on AI-ness only, not full `!draggable`. One-char fix; not a usability blocker.
- **No DB CHECK constraint this slice** — V1 app-layer enforcement, lockstep with `task_kind`/`run_mode`/`scheduled_at` validators.

**Reasoning:** Corrects the #748 design error where pending was keyed on `process_status === TODO` (mistakenly meaning "not yet picked up"). User clarification 2026-05-11: pending = "in-flight work that hit a problem and is stuck", a sub-state of in_progress alongside the BLOCKED column. Schema column gives FE a real source of truth (vs. stale-by-N-days auto-derivation or reusing BLOCKED). Cross-state validator is load-bearing: without it, the FE marker is a visual lie; with it, semantics are locked at the wire layer for every future client.

**Implications:** `is_pending` is now part of the universal `TaskRead` contract. V3 #407 inherits this slice's mutation primitives (patchTask + Toast + optimistic + rollback from #709) plus the resolved-final validator pattern. Future bundled-PATCH UX (clear pending + advance ps in one gesture) is V3+ scope. **Lesson — semantic-frame-misread:** when a Thai user word is ambiguous (column vs. sub-state), clarify the semantic frame BEFORE spawning. Distinct from dropped-point class; new sibling to `feedback_multi_point_requirements`.

**Superseded:** the prior #748 "V2.1 UX evolution" entry (4-column merge with `process_status === TODO` predicate) is wrong on semantics — kept here for reference only. The 4→5 column restoration landed in #709 (1:1 column↔ps mapping); the yellow/PendingBadge artifacts were stripped pre-#709-close, then recreated under #750 with the corrected predicate.

---

## 2026-05-11 — Kanban #709 closed: T4 drag-drop process_status (human-only, @dnd-kit)
**Scope:** frontend
**Decision:** First FE mutation surface on the Kanban board. Drag a `task_kind='human'` card across any of 5 columns (New / In progress / Review / Blocked / Done) → `PATCH /api/tasks/{id}` with new `process_status`. AI cards are doubly-disabled (`useSortable({disabled})` + `onDragEnd` kind-check guard).
- **Library:** `@dnd-kit/core ^6.3.1` + `@dnd-kit/sortable ^10.0.0` (first new deps since Phase 3 V1 scaffold).
- **Drop-target → ps mapping (LOCKED): 1:1.** `COLUMN_PS["1"]→1 .. "5"→5`. `COLUMN_PS` is **derived** from `COLUMNS` via `Object.fromEntries(...)` — single source of truth, no lockstep drift.
- **`over.id` resolution (LOCKED): typeof discriminator.** `@dnd-kit/core`'s `UniqueIdentifier = string | number` preserves type. Column droppables register with `id: columnId` (string `"1".."5"`); sortable cards with `id: task.id` (number). Resolution: `typeof over.id === "string"` → column key (`COLUMN_PS[over.id]`); otherwise → numeric card id, resolve `newPs` from THAT card's current `process_status`. **Anti-pattern:** `String(over.id)` lookup collides because `String(1) === "1"` — for task.id ∈ {1..5}, drop-on-card silently mis-targets (B1 bug caught by reviewer, missed by curl smoke).
- **Optimistic update + rollback pattern (LOCKED — V1 mutation primitive):** capture `original` per-drag → optimistic `setTasks` → fire `patchTask` → reconcile on success; on failure rollback `setTasks` + push toast with API `detail` string. Canonical FE mutation pattern; V3 #407 + future slices inherit.
- **`patchTask(projectId, id, body)`** added to `web/lib/api.ts`. Body shape: `Partial<Pick<TaskRead, 'process_status'|'priority'|'title'>>` (extensible). `jsonFetch` generalized to accept `method` + `body`.
- **Client-Component boundary at `<Board>`.** `page.tsx` stays Server Component (data fetch); `<Board>` is the single Client orchestrator that owns DnD state + tasks state + toast state. `BoardColumn` + `TaskCard` are Client (use `useDroppable` / `useSortable`).
- **`Toast.tsx`:** `role="status"` + `aria-live="polite"` + 4s auto-dismiss + cleanup on unmount. Style: `fixed bottom-4 right-4 z-50 shadow-sm` (the ONE allowed shadow surface — toast is floating chrome).
- **New smoke selectors:** `data-draggable={!isAi}` and `data-board="dnd"` on `<Board>` root.
- **AI gesture suppression — defense in depth:** `useSortable({disabled})` + `onDragEnd` early-exit + `aria-disabled="true"` + `cursor-not-allowed`.
- **API has NO `task_kind` PATCH restriction.** Only FE drag-handle is gated. API-level enforcement would land as separate slice.

**Reasoning:** T4 sequenced BEFORE V3 #407 (user direction 2026-05-11) so T4 builds the mutation primitives V3 inherits. `started_at`/`completed_at` server-stamping is documented API behavior — `started_at` set on first ps=2 entry NOT cleared on backwards transitions; same for `completed_at` on ps=5.

**Implications:** V3 #407 builds on the same patchTask + Toast + optimistic+rollback primitives. The `data-draggable` + `data-board` selectors are now part of the project's smoke vocabulary. **dnd-kit lesson codified:** `UniqueIdentifier` preserves string/number; future drag-drop slices must use typeof-discriminator, not `String()` coercion. **Limitation:** curl-based smoke does NOT exercise the @dnd-kit JS gesture (mouse/keyboard drag) — only wire layer + markup. Future Playwright suite recommended for keyboard drag, mouse drag, AI gesture rejection, and PATCH-failure-injection rollback. Reviewer's source-level scrutiny was the gate that caught B1.

---

## 2026-05-11 — Kanban #708 closed: T3 task_kind + recurrence badges (read-only) + fetch widening
**Scope:** frontend / shared
**Decision:** Surfaced T1 (#706) + #723 schema fields on the V2 Linear-baseline board as quiet read-only chrome.
- **Tailwind-only, no icon dep.** Codebase convention is text-only badges (`RunModeBadge`, priority/role chips).
- **Violet accent for AI rows.** `text-violet-700 bg-violet-50` — the only new color this slice. Distinct from blue/indigo (roles), orange/red (priority), amber (auto_headless).
- **Quiet null self-suppression for `RecurrenceIndicator`.** Returns `null` (not empty `<span>`) on dominant case (`!is_template && spawned_from_task_id === null`) — preserves V2 polish ~68px card height. Cross-field order deterministic: `is_template` checked before `spawned_from_task_id`.
- **New smoke selectors:** `data-task-kind` (`"ai"|"human"`) and `data-is-template` (`"true"|"false"`) on `<article>` root.
- **`scheduled_at` (#723) added to `TaskRead`** for type completeness but not rendered this slice (T2 one-shot UI is V3+).
- **Sub-fix: `web/app/page.tsx` fetch widening to `{ limit: 500 }`.** Pre-fix, `listTasks(project.id)` defaulted to `limit=50, ORDER BY id ASC` → with 53 active tasks, id-tail rows were structurally invisible. 500 is the API's server-side hard cap (verified 422 on 1000). Pagination UI is a separate UX ticket.

**Reasoning:** T3 is the read-only "wire-up display" slice between T1 schema and T4 mutation. Quiet visual treatment preserves V2 polish density; violet AI chip is the one accent reserved for the discriminator that matters in T4 (AI cards not drag-draggable).

**Implications:** T4 #709's drag-disabled predicate uses `data-task-kind`. V3 #407 can land without T3 reflow risk. **Next.js 14 SSR fingerprint:** SOME DOM strings appear twice in served HTML (className strings; JSX text adjacent to `{interpolated}` like `from #<!-- -->ID`). PLAIN text children (`<span>manual</span>`) and quoted `data-*` attribute values appear ONCE per row. **Use `data-*` attributes for unique-per-row smoke assertions.** **MIN deferred:** `RecurrenceIndicator.tsx` uses `new Date().toLocaleString()` in RSC context, so timestamp renders in container locale (UTC); `title` discloses IANA TZ. Locale-aware client-island formatting deferred until 2nd RSC datetime ships.

---

## 2026-05-10 — Context-management subsystem closed (CTX-1..CTX-4 + 2 audit follow-ups)
**Scope:** schema / api / devops / shared
**Kanban:** #716 (CTX-1 schema) → #717 (CTX-2 store) → #718 (CTX-3 token/cost) → #719 (CTX-4 Haiku compact) + audit follow-ups #722 (sessions ceilings extension) + #723 (tasks.scheduled_at one-shot path)
**Decision:** Session-based context model with hybrid DB+filesystem layout.

**Scope-lock (user-decided 4 directions):**
| # | Question | Locked |
|---|---|---|
| 1 | Session storage backend | **Hybrid** — DB (`sessions`/`session_runs`/`session_compacts`) for metadata + queryability; filesystem (`_sessions/<id>/{session.md, archive/, cards/}`) for markdown content |
| 2 | Session boundary | **Per project × per Claude Code instance** — 1 session = 1 project × 1 process. Multiple active sessions per project allowed |
| 3 | Token budget enforcement | **Soft** — measure + warn + log; never block. Surface `compact_recommended=true` in API response |
| 4 | Compact runner | **Claude Haiku 4.5** (`claude-haiku-4-5-20251001`) — ~10x cheaper than Sonnet/Opus. Reads `ANTHROPIC_API_KEY` from env. Provider abstraction deferred |

**CTX-1 #716 — migration 0008 + ORM + 8 endpoints:**
- 3 new tables: `sessions`, `session_runs`, `session_compacts`. Multi-instance partial index `ix_sessions_project_id_active` is **intentional accelerator, NOT uniqueness gate** (mirrors post-#694 freedom on `projects.is_active`).
- **Lockstep tuple in `src/constants.py`, not inline** (drift tests need monkeypatch-survivable target; module-level state gets re-set on `importlib.reload`). Mirrors `TaskRunMode`/`TaskKind` pattern.
- **post-INSERT `session_root_path` update inside single COMMIT** via `flush()` (row never observable with placeholder path).
- **Filesystem skeleton write AFTER `commit()`** — favors audit-row durability; CTX-2 writer creates dir on first append (defensive).
- **MAJ-1 rule:** every new ORM module MUST be imported in BOTH `src/models/__init__.py` AND `alembic/env.py` in the same commit — else `Base.metadata` doesn't see the new tables and next autogenerate produces spurious DROP TABLE diffs.
- **Two `APIRouter`s** from sessions module: `router` → `/api/sessions`, `runs_router` → `/api/session_runs` (run id is global, not nested).
- CTX-1 accepts `total_cost_usd` on PATCH session_run with no validation (CTX-3 replaces with server-authoritative compute).

**CTX-2 #717 — session_store.py + 3 endpoints:**
- `services/session_store.py` is canonical; `session_files.py` is a 20-line back-compat shim.
- **File locking: `filelock` (cross-platform), per-session at `_sessions/<sid>/.lock`.** Picked over POSIX `fcntl` for Windows-test portability. Single-process FastAPI is V1; multi-process (gunicorn workers) deferred to V2+.
- **Reader symmetry:** `read_session_for_prompt` and `get_section_text` ALSO acquire per-session lock (FileLock is exclusive-only; reads serialize behind writes — acceptable V1 tradeoff).
- **`total_bytes` (not `bytes_written`) on heartbeat response** — total file size, NOT bytes appended this call. Honest field name.
- **Section markers exact-match contract:** `## Compacted History` and `## Recent Activity`. `_split_sections` does byte-equal find with newline-boundary check.
- **Markdown round-trip:** append writes `content + "\n"`; replace writes `content` verbatim with no trailing newline.
- 5 source-text-locked detail strings introduced (404/400 closed-session/runless-run patterns).

**CTX-3 #718 — token counter + soft-warn + server-authoritative cost:**
- **Lead-lock: chars/4 LOCAL HEURISTIC, NO real tokenizer.** User-picked 2026-05-10 from 3 options (chars/4 vs tiktoken vs Anthropic SDK). Rationale: api container has no `ANTHROPIC_API_KEY`; soft-warn tolerates ~10-20% inaccuracy on English. Module docstring + locked snapshot test (`count_tokens("hello world") == 2`) defend against silent drift.
- **Server-authoritative cost: client value silently overwritten, NOT 422.** `extra="ignore"` retained on `SessionRunUpdate`.
- **`provider` + `model` NOT persisted** — pure inputs to `compute_cost`. Per-run provenance deferred to future column-add slice.
- **Pricing table (USD per 1M tokens):** opus-4-7 15.0/75.0; sonnet-4-6 3.0/15.0; haiku-4-5 0.8/4.0. Unknown pair → cost SKIPPED, WARNING logged, PATCH still 200.
- **Soft-warn budget:** log + flip `budget_warning` column, never block. Gated on `total_input_tokens` presence (status-only PATCHes don't re-fire).
- **Activity endpoint advisory (additive):** `POST /sessions/{id}/activity` response gains `compact_recommended`, `current_recent_tokens`, `recent_ceiling_tokens`.

**CTX-4 #719 — Haiku 4.5 compact runner + POST /compact:**
- **Run-count trigger DEFERRED to V2.** V1 = manual + size triggers via `POST /api/sessions/{id}/compact` with `trigger_kind`.
- **Compacted History strategy: REPLACE, NOT concat.** Archive captures prior Compacted History VERBATIM as 3rd section (alongside Recent Activity + LLM summary) — immutable forensic record; replay fully reconstructible. Section order: header → prior Compacted → original Recent → LLM summary.
- **Atomic status lock via single-UPDATE:** `UPDATE sessions SET status='compacting' WHERE id=:sid AND status='active' RETURNING id`. Empty RETURNING → 409. Lock release via `try/finally` returns status to `'active'` on every failure path.
- **Anthropic SDK lazy-import** inside function body (NOT module-top) — lets test envs without SDK still import compact_runner; `MissingApiKey` surfaces first as typed app exception.
- **respx HTTPX-transport-layer stub** for tests (one fixture covers sync + async paths).
- **Cost from SDK-reported `usage`, not chars/4** (input_tokens/output_tokens are authoritative including system + cache effects).
- **Provider exception wrapping at boundary:** `_call_anthropic` catches every exception, logs `exc_info=True`, raises typed `AnthropicCallFailed`. Router translates to 502 with locked detail; underlying provider error NOT leaked to client (could contain API key fragments).
- **Trigger-kind literal: single source of truth.** `SessionCompactTriggerLiteral` in `schemas/session.py`; runtime defensive check uses `SessionCompactTrigger.ALL` from `constants.py`.
- **Archive ordinal: max(existing)+1, NOT len(existing)+1** (handles gaps from hand-deletion).
- **`ANTHROPIC_API_KEY` not provisioned today** — 503 path is realistic live state. Provisioning is separate slice (Mode B / Step 2 prep).
- 5 new source-text-locked detail strings (404/400 closed/409 already-compacting/503 no-key/502 API-failed).

**Audit follow-ups (after CTX-1 close, reconciling doc spec):**
- **#722 — migration 0009 + 4 ceilings extended on sessions.** Doc spec'd 4-bucket budget (system prompt ~2k + session.md ~28k + card_detail ~6k + output_budget ~4k = ~40k); CTX-1 modeled only 2. #722 added `card_detail_ceiling_tokens` (default 6000) + `output_budget_tokens` (default 4000) + lifted all 4 to optional Create/Update fields with `le=1_000_000` operator-typo guard. Server_default backfills 3 pre-existing rows. Router pattern: dict-comp over non-None overrides + `**`-splat (NOT explicit `kwarg=None` — would override `server_default` to NULL).
- **#723 — migration 0010 + tasks.scheduled_at one-shot path.** T1 #706 covered cron-recurring only; user Req 1 also asked "ระบุวัน+เวลาที่จะทำ task นี้ได้" for non-recurring. **One-shot is a NEW column on regular task row, NOT a degenerate template.** `tasks.scheduled_at TIMESTAMPTZ NULL` with `is_template=false`. Templates spawn child rows; one-shots transition the existing row's `process_status` 1→2 in place. **3-layer defense-in-depth XOR** (Pydantic + router resolved-final + DB CHECK `ck_tasks_scheduled_xor_template`) — all share source-text-locked `"scheduled_at is incompatible with is_template=true (use recurrence_rule for templates)"`. **Router resolved-final placement: AFTER `assert_run_mode_for_kind`, BEFORE `assert_consent_for_run_mode`** (pure-function checks fire before DB-hitting checks). Partial index `ix_tasks_scheduled_at_pending ON tasks(scheduled_at) WHERE scheduled_at IS NOT NULL AND process_status = 1 AND status = 1` — predicate byte-identical between migration + ORM `__table_args__`.

**Reasoning:** Hybrid storage over filesystem-only — queryability matters once `sessions` rows exceed ~50. Per project × process boundary — matches Lead bootstrap unit-of-work. Soft budget — hard enforcement cascades on compact failure; aligns with "never block on observability". Haiku 4.5 — compact is summarization, not reasoning; provider abstraction deferred until OpenAI/others actually need to plug in (premature-abstraction risk).

**Cross-cutting integration:**
- Sessions are a NEW persistence layer — orthogonal to the existing 5 zones (DB / Standards / Team methodology / Project shared / Role state). Sessions live in their OWN zone (DB+filesystem hybrid, per-project-process scope).
- `_sessions/` at repo root, `.gitignore`-ed, dev-only V1. Production migration to a named Docker volume deferred until Mode B headless ships.
- Audit: `session_runs` complements `tasks_history` — `tasks_history` captures per-row OLD snapshots; `session_runs` captures per-run cost + token + status.
- T2 #707 apscheduler will eventually fire recurring tasks via Mode B headless — that's where session.md becomes load-bearing. CTX-* shipped independently to keep slices small.

**Deferred gaps (acknowledged):**
- **Selective context fetch** (file tree + relevant files + git diff) deferred until Mode B / master-agent runtime ships.
- **Session terminus mismatch with doc** — doc says session ends on clear/compact/isolate; our design: `closed` is the only terminator; compact archives + rebuilds + session continues. Deliberate doc deviation (terminate-on-compact would force fresh bootstrap per compact).

**Implications:**
- Phase 2 Backend layer COMPLETE.
- `ANTHROPIC_API_KEY` still NOT configured — POST /compact returns 503 until provisioned.
- `session.md` Compacted History is REPLACE-only post-compact; prior history preserved ONLY in `_sessions/<sid>/archive/compact_NNN.md`.
- Visibility gap: uvicorn swallows non-uvicorn INFO/WARN logs (no app-level log for 503 path; only wire access log). Follow-up: `logging.basicConfig(level=INFO)` in `src/main.py` OR `--log-config` to uvicorn.

---

## 2026-05-10 → 2026-05-11 — V3+ recurrence + task_kind + drag-drop subsystem (scope-lock + T1/T2)
**Scope:** schema / api / shared
**Kanban:** scope-lock + #706 (T1) + #707 (T2). T3 (#708) + T4 (#709) closed entries above. #710 (T5 theme) DEFERRED.
**Decision:** Lock 4 features in one round (cron-recurring tasks + task_kind + drag-drop + theme).

| # | Feature | Locked |
|---|---|---|
| 1 | Recurring tasks | **Cron string** in `recurrence_rule TEXT` + `recurrence_timezone VARCHAR(64)` (IANA TZ; cron is TZ-sensitive) + `next_fire_at TIMESTAMPTZ`. Templates flagged `is_template=true`. Children carry `spawned_from_task_id` pointing back. Fire creates NEW row, never modifies template |
| 2 | task_kind | `task_kind VARCHAR(8) NOT NULL DEFAULT 'human' CHECK (task_kind IN ('ai','human'))` |
| 3 | Drag-drop | Restricted to `task_kind=human`. AI cards' lifecycle is runner-driven; user must not override |
| 4 | Theme (light/dark/system) | **Deferred** (#710) until T1-T4 + #407 GREEN |

**Cross-cutting locks:**
- **task_kind ↔ run_mode constraint:** app-layer cross-table validator at `services/task_kind.py`: `task_kind == 'human' AND run_mode != 'manual'` → 400 with source-text-locked detail `"task_kind 'human' is incompatible with run_mode '<r>'"`. Fires on POST + PATCH against RESOLVED final values (mirrors `services/run_mode.py` consent pattern). Implication: human-kind cards guaranteed `run_mode=manual`; drag-drop's `task_kind === 'human'` is sufficient — no need to also check `run_mode`.
- **Scheduler runtime:** FastAPI background task + apscheduler `AsyncIOScheduler` in lifespan. NOT separate worker; NOT pg_cron. Single instance per api container; horizontal scale needs future Redis/pg-advisory lock. 60s default tick (`APP_SCHEDULER_TICK_SECONDS`). Same scheduler will host #481 Mode B auto-headless.

**T1 #706 — migration 0007 + ORM/Pydantic/router/service:**
- **Validator-firing order pinned:** POST + PATCH call `assert_run_mode_for_kind` (pure function) BEFORE `assert_consent_for_run_mode` (DB read). Cheaper check first.
- **PATCH resolved-final cross-validator** mirrors `services/run_mode.py` consent pattern: `payload.field if 'field' in updates else task.field`. Asymmetric drift (PATCH only `task_kind='human'` on existing `auto_pickup` row) → 400. Bundled downgrade `{task_kind:'human', run_mode:'manual'}` → 200.
- **Two-key PATCH rejection pattern:** `parent_task_id` (#238) and `spawned_from_task_id` (#706) both use `model_fields_set` membership — explicit-null treated identically to non-null. V1 forbids re-parenting any lineage column.
- **`spawned_from_task_id` settable on POST, rejected on PATCH** (T2 scheduler calls POST to spawn children; FK ON DELETE SET NULL).
- **Adjacency-list pattern hardening:** with 2nd self-FK (`spawned_from_task_id`), ORM relationships now require `foreign_keys=lambda: [Task.parent_task_id]` (lambda required, not bare class ref — class not fully defined at relationship-declaration time).
- **`croniter>=2.0,<7.0`** added; image rebuild required before applying T1 migration.
- **Datetime serialization:** Pydantic v2 normalizes `+00:00` → `Z` on serialize. FE round-trip comparisons must use `Date.parse()`, not string `===`.

**T2 #707 — apscheduler 2-path scheduler:**
- **Scope extended for #723: 2-path tick.** Each `tick_once` runs BOTH in two independent sessions:
  - **Path A (templates):** `is_template=true AND next_fire_at <= now()` → spawn child + advance `next_fire_at` from `now()` (single-fire-on-resume catch-up — overdue daily template spawns ONE child + advances to next future slot, NOT N children).
  - **Path B (one-shots):** `scheduled_at <= now() AND process_status=1 AND is_template=false` → transition in place (ps 1→2, stamp `started_at`, clear `scheduled_at` to NULL per #723).
  - Path A failure does NOT roll back Path B (separate sessions). Per-row try/except + `logger.exception` + `db.rollback()`.
- **Lifespan integration:** `@asynccontextmanager` (NOT deprecated `@app.on_event`). `AsyncIOScheduler(timezone="UTC")` with `max_instances=1, coalesce=True`. Job id `"recurrence_tick"`. `APP_SCHEDULER_DISABLE=true` env knob for pytest.
- **Audit trail through ORM commits.** Both paths write via attribute assignment + `commit()` — fires same `tasks_audit_trg AFTER UPDATE OR DELETE`. Child INSERT in Path A does NOT generate `tasks_history` row (trigger is UPDATE/DELETE only); template's `next_fire_at` UPDATE IS audited. Path B's row transition IS audited.
- **PATCH recompute** — changing `recurrence_rule` (with or without `recurrence_timezone`) re-computes `next_fire_at` from now. Changing only `recurrence_timezone` ALSO recomputes. Honors explicit `next_fire_at` in same payload (does NOT override).
- **`POST /api/tasks/{id}/fire-now`** — manual trigger, bypasses `next_fire_at <= now()`. Locked 400 detail `"Task id=<n> is not a template; fire-now only applies to is_template=true"`. X-Project-Id header gate (#695).
- **Server-side default for missing `next_fire_at` on POST: REJECTED.** Keep T1's strict 422 (`_check_template_completeness`). Auto-fill would silently weaken contract.
- **Visibility gap (known):** uvicorn swallows non-uvicorn INFO logs. Scheduler liveness IS provable via DB query-pair tick observation, but ops-level visibility broken. Fix via `logging.basicConfig(level=INFO)` or `--log-config`.
- **apscheduler 3.11.2** baked into image (pyproject pin `>=3.10,<4.0`).

**Reasoning:** Cron string over RRULE/simple-enum — best balance of expressiveness vs Pydantic-validatable string + library availability. FastAPI bg task over separate worker — ops simplicity; agent-teams is single-process. Constrained kind/run_mode over independent — keeps existing run_mode wire contract stable; drag-drop's enable predicate becomes simple `task_kind === 'human'` check.

**Implications:**
- pytest 124 → 280 across T1+T2 + #722 + #723.
- Scheduler LIVE on `docker compose up` with default 60s tick.
- `tasks` now has 16 user-facing columns + lifecycle/audit. `scheduled_at` joins 5 recurrence template fields.
- T1 dev-reviewer MINs filed as #714 (TaskUpdate template-completeness validator; Literal type narrowing on services; explicit-null on `recurrence_timezone`). None blocking.
- **Operational note:** scratch DB `agent_teams_scratch` left on dev PG after dev-devops round-trip (block-raw-sql-dml.ps1 hook correctly denied `DROP DATABASE`). Cleanup is human-only — #715 filed for manual step. Scratch-DB lifecycle (CREATE + DROP) is propose-only for subagents.

---

## 2026-05-10 — Phase 3 V2 read-only Kanban board landed (#406) + V2 visual baseline locked
**Scope:** frontend / shared
**Decision:** First UI surface on top of Phase 3 scaffold. Conventions locked at project layer:

**#406 Read-only board (Server Component):**
- **API base URL split** in `web/lib/api.ts`: `BROWSER_API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8456'`; `SERVER_API_URL = process.env.INTERNAL_API_URL ?? BROWSER_API_URL`. Runtime selection via `typeof window === 'undefined'`. dev-devops sets `INTERNAL_API_URL=http://api:8456` on `web` service so SSR fetches stay on compose network (Linux compose can't DNS-resolve `localhost:8456` from inside a container).
- **`cache: 'no-store'` on every V2 fetch.** Revalidation strategy deferred to V3+.
- **Within-column ordering:** priority desc, then id asc. No `display_order` column.
- **Project name source:** `process.env.NEXT_PUBLIC_PROJECT_NAME` (default `'agent-teams'`). Single-project board for V2; project switcher is V3 (#407).
- **`<RunModeBadge>` and `<ProjectConsentBanner>`** reused unchanged from #484/#481-C.
- **Defensive detail-string extraction** in `jsonFetch`: `await response.json().catch(() => ({}))` tolerates non-JSON error bodies.
- **NIT fixed in close-out:** magic-string `"auto_headless"` → `TaskRunMode.AUTO_HEADLESS` (lockstep guard at `schemas/task.py` only catches Python ↔ wire drift, not TS literal drift).

**V2 visual baseline (Linear-style minimalism — user picked over bento-dark / IBM Plex editorial):**
- **Style:** hairline 1px borders, NO card shadows, NO gradients. Color reserved for state badges; chrome stays achromatic (`bg-white`/`bg-zinc-50/60`/`border-zinc-200`). Light mode only.
- **Typography:** Inter via `next/font/google` at `app/layout.tsx`. `inter.className` on `<html>`; `antialiased` on `<body>`. Self-hosted at build time; NO new deps.
- **Density tokens:** page `px-6 py-5`; column `p-2.5 rounded-md bg-zinc-50/60`; card `p-2.5 rounded-md border border-zinc-200 bg-white` with `hover:bg-zinc-50 hover:border-zinc-300`; intra-column gap `gap-1.5`; grid gap `gap-3`. Target: 5+ cards per column at 1280px without scroll.
- **Inline header pattern (no chips):** `name · team: <name> · N tasks` with `·` middle-dot `aria-hidden`.
- **Column header:** small-caps `uppercase tracking-wide text-zinc-500` + `·` + count in `tabular-nums`. Hairline `border-b border-zinc-200` divider. NO count pill.
- **Role-badge palette:** frontend = `text-blue-700 bg-blue-50`; backend/devops/qa/reviewer = `text-indigo-700 bg-indigo-50`.
- **`tabular-nums` on every numeric chrome.**
- **Empty-state convention:** em-dash `—` in `text-zinc-400 text-xs text-center py-4`.

**Reasoning:** V2 must surface #481 UI seams (run_mode badge, consent banner) from day 1 so V3 doesn't reflow. API base URL split avoids Linux-compose foot-gun (containers can't DNS-resolve `localhost`; Docker Desktop for Windows happens to route it). `cache: 'no-store'` keeps V2 stateless. Solo-developer dogfood audience values info density + scannability + calm focus — Linear/Height/Vercel chrome.

**Implications:**
- **dev-frontend agent** picked up `ui-ux-pro-max` opt-in skill section (commit `63ce0ec`).
- **Tier-1 smoke methodology:** React SSR splits adjacent static-text + interpolated-variable with HTML comment markers (`>team: <!-- -->dev<` not `>team: dev<`). Smoke probes that grep across that boundary must accept the comment-marker form or split the assertion.
- **dev-devops follow-up #704 DONE:** `INTERNAL_API_URL=http://api:8456` wired on `web` service env (with `${INTERNAL_API_URL:-http://api:8456}` fallback) + mirrored in `.env.example`.
- **Tester hook follow-up #705 DONE:** `.claude/hooks/tester-curl-allow.ps1` regex widened from literal `:8456` to `://(localhost|127\.0\.0\.1):\d+` (any port).
- **Operational note for future agents:** host VS Code TS server emits `Cannot find module 'next'` after every `Edit` because `node_modules` lives inside the `web` container, not on the Windows host. Authoritative check: `docker compose exec -T web sh -c "cd /app && npx tsc --noEmit"`.

---

## 2026-05-10 — API tidy-up: drop single-active invariant (#694 Phase 2) + `?pending=true` shortcut (#697)
**Scope:** api / db / tests / shared

**#694 Phase 2 — drop `ux_projects_active_one` + remove PATCH atomic-clear + `GET /api/projects/active` → 410:**
- **Migration `0006_drop_active_one`** drops partial unique index `ux_projects_active_one ON projects(is_active) WHERE is_active IS TRUE AND status = 1`. Session-scoped active-project model (#694 Phase 1 / #695 Phase 3) makes multi-active-row legitimate — each terminal binds to its own project. ORM `Index(...)` decl removed in same commit. Downgrade restores byte-identical predicate.
- **PATCH `/api/projects/{id}` atomic-clear removed.** `_clear_other_active` helper + both call sites (POST + PATCH) gone. N7 no-op-skip / `updated_at` bump / IntegrityError-409-translation on `ux_projects_name_active` paths untouched.
- **GET `/api/projects/active` → 410 Gone** with source-text-locked detail `"Endpoint deprecated. Use /api/projects/by-name/{name} or /api/projects?status=1 instead."` Route decorator declares `responses={410: {"description": ...}}` so deprecation is publicly discoverable in `/openapi.json` — **runtime `raise HTTPException(...)` does NOT auto-document; explicit decorator kwarg is mandatory.**
- **DELETE `/api/projects/{id}` side effect retained:** `is_active=true → false` clear inside `delete_project` survives. Rationale refreshed — not because of any unique constraint (gone), but because a soft-deleted row should not advertise itself as active in any list/by-name query.

**#697 `?pending=true` on `GET /api/tasks`:**
- `pending: bool = Query(default=False)`. When `pending=true`, filters `WHERE process_status != TaskStatus.DONE` (IN (1,2,3,4)).
- **When BOTH `pending=true` AND `process_status=N` provided: explicit `process_status` wins** via control-flow `elif` (NOT boolean arithmetic). Future drift to `if pending:` would silently re-enable false-positive; precedence test (`test_list_tasks_pending_and_process_status_explicit_wins`) seeds BOTH ps=5 AND ps≠5 rows and asserts `?pending=true&process_status=5` → exactly 1 ps=5 row.
- Uses named constant `Task.process_status != TaskStatus.DONE` (NOT bare literal `5`).
- **Out of scope:** multi-value `process_status` (kept as int), name-based filter (todo/in_progress/...), any change to `process_status` semantics.

**Reasoning:** Single-active invariant was load-bearing on the pre-session bootstrap model; keeping it would silently fail PATCH `is_active=true` on a second project. Keeping atomic-clear would silently STOMP the first session's active flag. 410 (over redirect or 404) keeps deprecation visible — silent fallback would mask migration of every existing client. `?pending=true` shortcut eliminates Lead's "list pending tasks" Python-fallback (~3 prompts/session saved).

**Implications:** Multiple rows may carry `is_active=true` simultaneously. Frontend consumes `/api/projects?status=1` for live-projects list; bootstrap clients use `/api/projects/by-name/{name}`. Lead bootstrap uses `curl -H "X-Project-Id: <id>" "/api/tasks?pending=true"`. **Convention propagated:** future convenience-shortcut bool params on list endpoints MUST cede precedence to the more-specific explicit param via control-flow `elif`, not boolean arithmetic.

---

## 2026-05-09 — `tasks.run_mode` + grant-consent endpoint + cross-table validator (#481-B / #483 closed)
**Scope:** api / tests / shared
**Decision:** Wired migration 0005's `run_mode` + `auto_run_consent_at` through the stack. Cross-team-applicable methodology framing lives in `context/teams/dev/decisions.md` 2026-05-09 'Kanban-driven AI: 2-mode model + per-project consent gate'.
- **Constraint name** mirrored: `ck_tasks_run_mode_valid` in migration AND ORM `CheckConstraint` (lockstep pattern from `_PROJECT_TEAM_ALL` / `ck_projects_team_valid`).
- **`POST /api/projects/{id}/grant-consent`** — body `{"confirm_name": "<name>"}` with Pydantic `extra="forbid"` (NOT default `extra="ignore"`). A typed-acknowledgment endpoint MUST fail loud on smuggled fields. 400 on mismatch with source-text-locked detail `"confirm_name must match project name exactly"`. 404 on missing OR soft-deleted project. 422 on extra fields.
- **Idempotent re-grant:** read `project.auto_run_consent_at` and short-circuit BEFORE assigning `func.now()` if non-null. First consent is the auditable timestamp; re-grant must not bump `auto_run_consent_at` OR `updated_at`.
- **Cross-table validator location:** `src/services/run_mode.py::assert_consent_for_run_mode(db, project_id, run_mode)` — service-layer helper, NOT a DB CHECK (spans tables). Reads only `Project.auto_run_consent_at` with `Project.status == ACTIVE`.
- **PATCH resolved-final-mode rule:** validator fires on RESOLVED final `run_mode` — `payload.run_mode if "run_mode" in updates else task.run_mode`. Downgrade `auto_headless → manual` always succeeds. PATCH on `auto_headless` row when consent gone fails (forces operator to downgrade first OR re-grant).
- **Lockstep guard:** `TaskRunModeLiteral` ↔ `TaskRunMode.ALL` import-time guard at bottom of `schemas/task.py`. Uses `RuntimeError` (not `assert` — survives `python -O`). Drift test in `tests/test_run_mode_consent.py` monkeypatches `TaskRunMode.ALL` → reloads schemas → asserts RuntimeError.
- **Source-text-locks:** 2 new lock tests pin (a) `"confirm_name must match project name exactly"`, (b) consent-required template `"project {project_id} has not granted auto-headless consent"`.
- **MINOR-1 follow-up (filed):** when POST `/api/tasks` carries `run_mode='auto_headless'` AND `project_id` references missing/soft-deleted project, consent error masks FK error. Wire-contract drift, not a bug — acceptable to ship as-is.

**Reasoning:** Idempotent-re-grant rule was specced in team-methodology but implementation needed short-circuit before `func.now()`. PATCH resolved-mode rule prevents PATCH-other-fields-on-headless-task from slipping past. 404-on-soft-deleted consistent with `get_or_404 status=ACTIVE` pattern.

**Implications:** Frontend (#484) types: `run_mode: "manual"|"auto_pickup"|"auto_headless"` (Literal, default `"manual"`), `auto_run_consent_at: string | null`. Grant-consent body `{confirm_name: string}` with `extra="forbid"`. **Advisory pre-existing items observed during Tier-1:** (a) `GET /api/projects/{id}` direct-by-id route returns 405 (clients must use `/api/projects/by-name/{name}` or `?...`). (b) POST `/api/projects` body uses nested `paths:{web,api,db}` + nested `stack:{...}`; PATCH uses flat `paths_web`/`paths_api`/`paths_db` (asymmetry vs PATCH — by design).

---

## 2026-05-09 — Test-database isolation (`agent_teams_test`) — Issue 2 of raw-SQL-DML incident response
**Scope:** api / tests / dev tooling
**Decision:** Tests run against per-pytest-session ephemeral database named `agent_teams_test`. Lifecycle: (1) `tests/conftest.py` sets `DATABASE_URL` at module top — BEFORE any `from src import …` (because `src.db.engine` is built from `get_settings().database_url` at import time). (2) Session-scoped `autouse` fixture `_setup_test_database` connects to maintenance `postgres` DB, runs defensive `pg_terminate_backend`, drops + creates `agent_teams_test`, runs `alembic upgrade head` (subprocess so sync alembic API stays out of async event loop), runs `scripts.seed._seed()`, disposes engine, yields. (3) Teardown drops the test DB. (4) Tests MAY leave data within the test DB during the session (no transaction-rollback wrapper). All 4 pre-existing fixtures preserved. Two contract tests at `tests/test_db_isolation.py` pin the invariant — `engine.url` must contain `agent_teams_test`, AND round-trip via `SessionLocal` must report `current_database() = 'agent_teams_test'`.

**Reasoning:** 2026-05-09 audit found live `agent_teams` DB had grown to **636 tasks (32 active + 604 soft-deleted) + 510 projects (39 active + 471 soft-deleted)** — pytest had been writing every run for ~2 days. End-to-end real-system verification (audit triggers fire, soft-delete partial-unique exercised, FK cascade covered) outweighs intra-session test data leftover (user explicitly accepted: "มี test data ได้เลยไม่ติดปัญหา จะได้รู้ว่ามันทำงานเข้าระบบได้ถูกต้องจริงๆ ด้วย"). Per-test transaction rollback rejected — audit triggers fire-then-roll-back would silently break tests asserting on `tasks_history` row counts. Truncate-per-session rejected — doesn't isolate parallel pytest invocations.

**Implications:** pytest no longer touches live DB. Issue 3 (cleanup of 604 + 471 soft-deleted live rows from prior runs) is one-time human-only work per raw-SQL-DML hard rule. Hook `.claude/hooks/block-raw-sql-dml.ps1` does NOT fire on fixture's CREATE/DROP DATABASE — those go through async SQLAlchemy `text()`, not Bash `psql -c` (different tool boundary).

---

## 2026-05-09 — Rename `projects.lead` → `projects.team` (Phase 2.5b1)
**Scope:** db / backend / frontend / shared
**Decision:** Rename DB column `projects.lead` → `projects.team`, Python class `ProjectLead` → `ProjectTeam`, Pydantic Literal `LeadCode` → `TeamCode`, scaffold constant `LEAD_ROSTERS` → `TEAM_ROSTERS`. Member values `'dev'`/`'novel'` unchanged. Migration `0004_rename_lead_to_team`: pure DDL — drop `ck_projects_lead_valid` → ALTER COLUMN RENAME → create `ck_projects_team_valid`. Web mirror `web/lib/constants.ts` renamed in lockstep. POST with old `lead` key 422 (no alias mapping — Pydantic `extra='ignore'` silently drops `lead` then required-`team` triggers). The orchestrator persona "Lead" (capital-L = meta-orchestrator) and role-tag persona `'lead'` in templates remain unchanged — they are NOT the column.

**Reasoning:** "lead" was overloaded — same word for column value AND orchestrator persona. After Bucket-4 split, the column actually selects **which team of agents** the project gets — `project.team == 'dev'` reads cleanly. Repo name `agent-teams` aligns. User explicitly weighted "accumulate effort during operations > upfront effort".

**Implications:** API contract change: POST request key + `ProjectRead` field key both renamed. Phase 2.5b2 will rename `.claude/leads/` → `.claude/teams/` + `context/leads/` → `context/teams/`. Dogfood-pollution lesson now reinforced 3x (smoke-checklist Phase 2, decisions.md Phase 2.5a, this rename Phase 2.5b1).

---

## 2026-05-08 — Subtask hierarchy on `tasks` (parent_task_id + API support) — Kanban #238 closed
**Historical context (added 2026-05-08 after archaeology):** Requirement was given by user on 2026-05-04 21:19 in a 5-point design message ("Schema: task ให้มี parent ด้วยเพื่อทำ work break down เป็น sub task ได้"). Initial migration `0001_initial_schema` shipped WITHOUT `parent_task_id` — requirement vanished from durable artifacts (no decisions.md entry, no Kanban task, no schema column). Re-surfaced 4 days + 11 commits later when Phase 3 needed subtask split of #3. Caught + fixed here. Lesson codified at `context/standards/general.md` "Multi-point user requirements MUST be propagated point-by-point" + memory entry `feedback_multi_point_requirements.md`.

**Scope:** api / db / shared
**Decision:** Adds `tasks.parent_task_id BIGINT NULL` self-referential FK with full app-layer validation. Migration `0003`: `add_column` + `ON DELETE CASCADE` FK `fk_tasks_parent_task_id` + CHECK `ck_tasks_parent_task_id_not_self` (`parent_task_id IS NULL OR parent_task_id <> id`) + index. ORM uses canonical adjacency-list pattern with string `remote_side="Task.id"` (survives circular-ref import order). Pydantic: `TaskCreate.parent_task_id: int | None = Field(default=None, ge=1)`; `TaskRead.parent_task_id` exposed; **`TaskUpdate.parent_task_id` REJECTED** via `@model_validator(mode='after')` checking `if "parent_task_id" in self.model_fields_set` — explicit-null and explicit-int both 422. Router: POST validates parent existence + `parent.status=ACTIVE` + `parent.project_id == payload.project_id` (locked 400 details `parent_task_id <n> does not exist or is deleted` and `parent_task_id <n> belongs to a different project`). DELETE blocks 409 with locked detail `Cannot delete task — <n> active subtask(s) reference this task` AFTER the idempotent re-DELETE early-return. GET adds `?parent_task_id=N` + `?top_level_only=true` — when both provided, `top_level_only` wins, `parent_task_id` silently ignored.

**Reasoning:**
- **Soft-delete parent with active children → 409 (block, not cascade-soft-delete).** Cascade on 50-child umbrella is too easy to invoke by accident.
- **Same-project enforced at app layer** — composite FK across (project_id, id) would be DB-cleaner but adds two-column FK complexity for a 3-line Python check at the only entry point.
- **Re-parenting NOT allowed in V1** — introduces ordering/cycle questions not worth solving until Phase 3 UI demands. `model_validator` REJECT-BY-PRESENCE pattern (vs `extra='ignore'` silent-drop) required so silent client bugs surface as 422.
- **FK `ON DELETE CASCADE`** — app never hard-deletes (only soft-delete via `status=0`), so CASCADE never fires from app path. Defense-in-depth backstop for raw-SQL drift.
- **No status rollup** — UX may compute derived "umbrella status" on display; baking into DB couples write paths to TBD UX policy.

**Implications:** Phase 3 UI can now create true parent/child task relationships. **Standards-propagation:** (a) codify the Pydantic `model_validator` REJECT-BY-PRESENCE pattern at `pydantic/v2-conventions.md` Settings/Update section — `extra='ignore'` silent-drops + `if x is not None` misses explicit-null + only `model_fields_set` correctly differentiates "not provided" from "provided as anything"; (b) codify SQLAlchemy adjacency-list with string `remote_side` at `sqlalchemy/orm.md`; (c) extend `general.md` Testing — Update-schema-REJECT pattern tests MUST cover BOTH `{field: value}` AND `{field: null}` cases (single-case is the Kanban #76 vacuous-assertion class). **N4 deferred:** `_check_role` validator hardcodes `TaskRole.ALL` (dev roster 1..5) without `lead='novel'` awareness — Phase 3 follow-up.

---

## 2026-05-08 — Phase 3 web/ scaffold landed (scaffold-only) — Kanban #3
**Scope:** frontend / devops / shared
**Decision:** `web/` directory at repo root with minimal Next.js 14 (App Router) + TypeScript (strict) + Tailwind v3 bones. 13 files. Key choices:
- App Router over Pages Router (Next 14 default + matches `projects.stack_web`).
- Tailwind v3 (stable) over v4 (alpha).
- Path alias `@/* → ./*`.
- `lib/constants.ts` mirrors `api/src/constants.py` (`RecordStatus`/`TaskStatus`/`TaskPriority`/`TaskRole`/`ProjectLead`) — `as const` + literal types. `TaskHistoryOperation` deferred (internal audit-trigger payload, no browser-facing use).
- `Dockerfile` single-stage dev on `node:20-alpine`; `next dev -p 3000`.
- `docker-compose.yml` `web` service: `depends_on: api: condition: service_healthy`, `NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL:-http://localhost:8456}` (host-resolvable default — browser runs on host, NOT compose network). Bind-mount `./web:/app` + anonymous `/app/node_modules` (prevents host-shadowed deps). `wget` healthcheck (curl not in node:20-alpine; wget is).
- **Out of scope:** API client, board UI, project switcher, forms, prod multi-stage Dockerfile, integration tests, FE→BE cross-container probes — all V2.

**Reasoning:** Decoupling FE scaffold from first UI feature keeps each slice small. `NEXT_PUBLIC_API_URL` defaults to `http://localhost:8456` in BOTH compose and `.env.example` (earlier `http://api:8456` fallback was browser-unreachable). Bind-mount + anonymous `node_modules` is the canonical Next dev pattern (avoids Linux/Windows binary mismatch).

**Implications:** Phase 3 V2 unblocked. dev-tester Tier-1 smoke extended to web surface (POSITIVE: `curl http://localhost:3000` 200 + body marker; NEGATIVE: unknown route → 404; container `Health=healthy`). #7 (per-project agent roster) remains gated on V2 UI lifecycle.

---

## 2026-05-08 — Backfill #81 + 4 follow-ups closed (#120 + #121 + #122 + #123) + #76 inception
**Scope:** api / db / tests / shared / dev tooling
**Decision:** Discipline-rollout chain (#78 → #79 → #80 → #81) validation. First Tier-2 release-wrap-up DRY-RUN against branch HEAD caught two BLOCKER-class bugs in `routers/tasks.py` that escaped original Kanban #76 fix bundle, plus 2 SECURITY-WARN + SECURITY-NIT bundle.

**#76 (inception, parity bug from cleanup-pass deploy verify):** `routers/projects.py update_project` and `delete_project` (real-write branch only) now explicitly set `project.updated_at = func.now()` — `server_default=func.now()` fires only on INSERT. PATCH adopts N7-style no-op-write skip (`isinstance(value, ClauseElement) or getattr(project, field) != value` guard with `changed` flag) so identical-body PATCHes do NOT bump `updated_at`. DELETE idempotent early-return path untouched. **#79 discipline inception:** every BLOCKER/MAJOR fix demonstrably fails on pre-fix code; M9 was vacuous (asserted `updated_at_after == updated_at_before` after first DELETE — equality held trivially because no DELETE bumped `updated_at`).

**#120 — tasks router updated_at parity (sibling of #76):** Mirror of `routers/projects.py:174-183` and `:225-226` in `routers/tasks.py update_task` (lines 121-130) + `delete_task` (lines 168-171). **#81 caught it:** original #76 fix only patched projects.py; matching defect in tasks.py survived 4 commits because no test asserted the bug-class. Closes the post-#76 propagation gap. **Standards-propagation:** "near-clone audit" review heuristic — when fixing a bug in one of two near-clone modules, search for symmetric pattern in the sibling and either fix in same commit or file explicit follow-up.

**#121 — projects.name path-traversal hardening (SECURITY-WARN W1):** Two-layer defense against path traversal via user-controlled `name` field that flows into `Path(repo_root) / "context" / "projects" / project_name`.
- **Layer 1 (boundary):** `ProjectCreate.name` + `ProjectUpdate.name` gain anchored `pattern=r"^[a-zA-Z0-9_-]{1,64}$"` → 422 `string_pattern_mismatch`.
- **Layer 2 (defense-in-depth):** `scaffold_project_folder` adds forbidden-token short-circuit (`/`, `\`, `..`, `\x00`) BEFORE `Path()` construction + `base.resolve().is_relative_to(projects_root.resolve())` check BEFORE `mkdir`. Both layer-2 guards `return False` (never raise) per existing scaffold contract.
- **Anchored regex (`^...$`) non-negotiable** — unanchored would accept `"../evil_anything_long_enough_to_match"` somewhere in the string.

**#122 — POST /api/tasks 400 detail-string hygiene (SECURITY-WARN W2):** `create_task` now wraps `await session.commit()` in `try/except IntegrityError` with 5-branch constraint-name → stable-detail ladder: `tasks_project_id_fkey` → `f"project_id {payload.project_id} does not exist"`; 3 CHECKs → `"<col> violates <constraint>"`; fallback → `"Task creation violates a database constraint"`. Mirror of M5 pattern from `update_task` modulo extra FK branch. **The leaky `detail=str(exc.orig)` shape is fully gone** — grep confirms no raw asyncpg text reaches `HTTPException(detail=...)` anywhere in `routers/tasks.py`. CHECK branches (3 of 5) are unreachable via HTTP today (`TaskCreate` rejects at 422 first) — defense-in-depth for raw-SQL bypass / future schema drift. FK branch IS reachable (Pydantic accepts any positive int as `project_id`); wire-level test mandatory.

**#123 — SECURITY-NIT bundle (4 items + 1 sub-fix):** (1) **APP_DEBUG fail-CLOSED:** default `True → False` in `settings.py`; `.env.example` keeps `APP_DEBUG=true` for dev convenience. (2) **REPO_ROOT required:** `_DEFAULT_REPO_ROOT` constant removed; `Field(alias="REPO_ROOT")` raises `ValidationError` at startup if unset; `docker-compose.yml` already sets `REPO_ROOT: /repo`. (3) **CVE pytest CVE-2025-71176:** bumped `pytest>=9.0.3,<10.0`. (4) **pip-audit declared dev dep:** `pip-audit>=2.7,<3.0`. (5) **Sub-fix:** `pytest-asyncio>=0.24,<2.0` (0.23.x calls `collector.obj` on `Package` collectors which pytest 9 removed).

**Reasoning:** Discipline rollout was theoretical until tested. #81 was validation: did new workflow actually catch bugs conventional review would have missed? **Yes.** Original #76 only patched projects.py; matching bug in tasks.py survived because no step in old workflow probed the live tasks API the way Tier-1 demands. Same for W1 (would have shipped to Phase 4 unflagged under correctness-only review).

**Implications:** Phase 3 (Kanban UI scaffold, #3) UNBLOCKED end-to-end after #120-#123. **Pattern matured (3 sites: `update_project` 409, `update_task` 400 M5, `create_task` 400 M122) — lift to standard:** IntegrityError-translation 5-step at `fastapi/error-handling.md` (rollback first → capture orig_text → translate well-known constraint names → fallback → never `detail=str(exc.orig)`). **Standards-propagation queue:** (a) paired-tuple source-text-lock idiom at `general.md` Testing — assert constraint name AND detail string per branch (single-case is the #76 vacuous-assertion class); (b) two-layer path-traversal defense at `python/path-handling.md`; (c) uuid-suffix-per-case + pre-clean idiom for FS-mutating regression tests; (d) fail-CLOSED defaults for security-adjacent booleans; (e) Pydantic-required for I/O paths; (f) security audit tooling as declared dev dep. Future Tier-2 wrap-ups: re-run pip-audit on non-transient install, fire `/security-review`, run matrix on release tag.

---

## 2026-05-08 — Cleanup pass on post-rename / post-soft-delete debt (no schema changes)
**Scope:** api / shared / root
**Decision:** Pure debt-cleanup pass. Eleven files touched, zero schema or contract changes. (a) Root + meta playbook: `.claude/leads/dev.md` step-7 PATCH example + `README.md` Example-2 use `process_status=N` (lifecycle) instead of pre-rename `status=N`; Lead-step-7 line gained explicit "`status` is the soft-delete flag — do not PATCH it for lifecycle" reminder. (b) `api/src/constants.py` module docstring renamed `tasks.status` → `tasks.process_status` (class name `TaskStatus` preserved per prior decision). (c) `api/scripts/seed.py` `paths_db` corrected from non-existent `api/migrations/` to `api/alembic/versions/`. (d) Scaffold templates `db-schema.md` + `api-contracts.md` realigned to current locked decisions: `id BIGINT GENERATED BY DEFAULT AS IDENTITY` + `status SMALLINT … 1=active/0=deleted`. (e) Migration `2026_05_08_0300_soft_delete_and_lead.py` rename `_TASK_STATUS_ALL → _TASK_PROCESS_STATUS_ALL`. (f) `api/src/routers/projects.py` aliased `from fastapi import status as http_status` to mirror `tasks.py` (avoid shadowing `RecordStatus`). (g) `api/src/routers/tasks.py` got 2 terse comments — one above M5 400-detail-string chain pointing at lock test; one sharpening `isinstance(value, ClauseElement)` guard. (h) `api/tests/test_in_clause.py` literal column-name `"status"` → `"process_status"`. Plus harness: `Agent(*)` added to `.claude/settings.json` allowlist.

**Reasoning:** 3 large requirement changes (lifecycle column rename, soft-delete adoption, multi-domain lead bundle) shipped over 2 days and left scattered cosmetic / docstring / variable-name drift. Scaffold-templates fix (item d) is the only one with downstream-user impact: every NEW project starts with shared docs that match locked decisions.

**Implications:** Future Lead PATCH on `/api/tasks/{id}` for lifecycle MUST use `process_status` — Pydantic `extra='ignore'` would silently drop stray `status`, and soft-delete `tasks.status` column rejects values outside {0,1}. Standards-file drift remains (humans-only writers): multiple files still reference old column name — surfaced to user as standards-propagation queue.

---

## 2026-05-08 — Multi-domain `lead` column + soft-delete migration bundled (`0002_soft_delete_and_lead`)
**Scope:** db / backend / shared
**Decision:** Single Alembic migration `0002` lands three coupled changes atomically:
- **(a) Soft-delete (decided 2026-05-05):** rename `tasks.status → tasks.process_status`; add `status SMALLINT NOT NULL DEFAULT 1 CHECK (status IN (0,1))` to `projects` + `tasks`; partial unique on `projects.name` gated on `status=1`; tighten `ux_projects_active_one` to `WHERE is_active IS TRUE AND status=1`.
- **(b) Lead column:** `projects.lead TEXT NOT NULL DEFAULT 'dev' CHECK (lead IN ('dev','novel'))`. *(Note: renamed to `team` in Phase 2.5b1; see 2026-05-09 entry.)*
- **(c) Dropped `ck_tasks_assigned_role_valid`** — app-layer validates per active project's lead roster (dynamic; can't express as single static CHECK across all leads).
- Two leads seed multi-domain pattern: dev (1..5 roles), novel (11..12 roles). Scaffold service dispatches on `project.lead` to pick role-folder names.

**Reasoning:** All three changes touch same migration touchpoints; app rename has to flip on same deploy as column rename — splitting invites schema-mismatch window. Per-lead roster validation is dynamic.

**Implications:** `DELETE /api/projects/{id}` and `DELETE /api/tasks/{id}` are now public verbs (204; flip `status=0` internally; project DELETE also clears `is_active` if true). List endpoints default-filter `WHERE status=1` with opt-in `?include_deleted=true` (debug; intentionally NOT in api-contracts.md). Detail endpoints return rows regardless of soft-delete status. PATCH does NOT accept soft-delete `status` flag — `TaskUpdate`/`ProjectUpdate` omit field; unknown fields silently ignored (`extra='ignore'` made explicit via `model_config`); locked by `test_patch_task_silently_ignores_soft_delete_status_field`. Lifecycle query param renamed `?status=1..5 → ?process_status=1..5`. POST `/api/projects` requires `lead` (422 if missing/unknown). Seeded `agent-teams` row inherits `lead='dev'` via DEFAULT backfill. **M5 — PATCH `/api/tasks/{id}` 400 detail strings translate well-known CHECK constraint names to stable wire text;** HTTP path is gated by Pydantic 422 first, so 400 branches reachable today only via raw-SQL bypass / future schema drift. **M9 — re-DELETE on already-soft-deleted row is no-op write (skipped) so `tasks_history` doesn't grow on idempotent DELETEs.**

---

## 2026-05-05 — Soft delete via uniform `status` flag (no hard DELETE in app code)
**Scope:** db / shared
**Decision:** Every business table carries `status SMALLINT NOT NULL DEFAULT 1 CHECK (status IN (0, 1))` (1=active, 0=deleted). Application code never issues SQL DELETE — "delete" endpoints flip the flag. To keep column name uniform across tables, existing 1-5 lifecycle column on `tasks` renamed `tasks.status → tasks.process_status` (codes unchanged); new `tasks.status` carries 0/1 like every other table. `tasks_history` exempt (audit append-only).

**Reasoning:** User policy — never lose business data. Audit trigger snapshots flag flip as `'U'`, so soft deletes remain traceable. Renaming lifecycle column rather than picking different soft-delete name avoids "different soft-delete column per table" sprawl. **Reverses the earlier "Soft delete: no" line in db-schema.md Conventions.**

**Implications:** Every list endpoint defaults `WHERE status=1`; opt-in `?include_deleted=true`. DELETE endpoints become PATCH `{"status": 0}`. **Hard DELETE reserved for manual psql cleanup** — human-only per raw-SQL-DML hard rule (see incident 2026-05-09); subagents propose, user executes.

---

## 2026-05-04 — Foundational backend decisions (initial schema + patterns)
**Scope:** backend / db / shared

- **Auto-scaffold folder on POST /api/projects:** commits DB row first, then runs `scaffold_project_folder()` which creates `context/projects/<name>/{shared,frontend,backend,devops,qa,reviewer}/`, copies 3 shared templates from `api/src/templates/project_shared/`, drops `.gitkeep` in role folders. Idempotent. Scaffold failure logged but does NOT roll back DB row (DB is source of truth — folder gaps repairable manually; row stuck "created but rolled back" is worse).

- **Integer codes (not enums) for status / priority / assigned_role:** `tasks.status`, `tasks.priority`, `tasks.assigned_role` are INTEGER columns with CHECK constraints; canonical names in `src/constants.py` (Python) + `web/lib/constants.ts` (TypeScript). No PG enum types (painful to extend — require schema migration to ADD VALUE; cannot remove). Integer + CHECK is trivially extensible. Adding a new code requires updating `general.md` + migration + constants files in both languages in lockstep.

- **Async SQLAlchemy + asyncpg:** SQLAlchemy 2.0 async ORM with `asyncpg`; FastAPI handlers `async def`. Pairing FastAPI with sync DB I/O would block event loop. Alembic env.py uses `async_engine_from_config` + `run_sync(do_run_migrations)`. Tests use `pytest-asyncio` + `httpx.AsyncClient(transport=ASGITransport)`.

- **BigInteger autoincrement, not UUID, for primary keys:** `id BIGINT GENERATED BY DEFAULT AS IDENTITY` (SQLAlchemy `BigInteger autoincrement=True`). Single-tenant dogfood — no client-generated/sharded keys. Smaller indexes, sequential cache locality, URL-friendly ("task #42" beats "task 6f3a..."). Overrides placeholder in `db-schema.md` Conventions. All Pydantic IDs typed `int`; UI route params integers. Future distributed write → add separate `external_id uuid` column rather than swap PKs.

- **Audit trail via PG trigger (not application code):** `tasks_history` populated by AFTER UPDATE OR DELETE trigger that snapshots `to_jsonb(OLD)`. Application code never INSERTs into `tasks_history`. Captures every mutation including out-of-band edits (psql, future admin scripts). Avoids "forgot to call audit helper" bug class. `tasks_history.task_id` is intentionally NOT a FK (history rows must outlive the row they describe). Application reads of history will go through future `GET /api/tasks/{id}/history`.

- **Initial schema migration:** Single Alembic migration `2026_05_04_2130_initial_schema.py` creates `projects`, `tasks`, `tasks_history`, and PG `tasks_audit_fn()` + `tasks_audit_trg`. Bundling v1 schema in one migration keeps bootstrap atomic — easier for new clones + drop/recreate during early dev. Trigger co-located so audit invariant cannot be applied without it.

<!-- No decisions yet. First entry will be appended above this line. -->
