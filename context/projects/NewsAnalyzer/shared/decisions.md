# Architectural & process decisions

> **Lead is the only writer of this file.** Subagents propose updates in their final report — Lead reviews, may ask the user, then writes the entry.
>
> Format: append-only log. Newest entry at the top. Each entry has a date, scope, who proposed it, and the reasoning.

<!--
Template for a new entry:

## YYYY-MM-DD — <short title>
**Scope:** frontend | backend | devops | qa | reviewer | shared
**Proposed by:** <role> (or `lead` / `user`)
**Decision:** <what we decided to do>
**Reasoning:** <why — constraints, tradeoffs, alternatives considered>
**Implications:** <what changes downstream>
-->

## 2026-05-15 (evening) — T13 #1027 closed — NewsAnalyzer cross-project pollution cleaned
**Scope:** repo hygiene
**Proposed by:** user (flagged dirty diff in NewsAnalyzer main 2026-05-15)
**Status:** T13 DONE 2026-05-15 (commit `93dae8e`).

**What landed (NewsAnalyzer side only):**
- `.gitignore` extended with 9 entries (`.claude/agents/`, `.claude/teams/`, `.claude/hooks/`, `.claude/docs/`, `.claude/settings.json`, `.claude/settings.local.json`, `context/`, `.codex/`, `hello-tier3.md`) — with comment block explaining the cross-project pollution + pointer to upstream fix.
- `git rm --cached .claude/settings.local.json` — was tracked; now untracked + ignored. File stays on disk for Claude Code Desktop session-local state.
- `CLAUDE.md` reverted to HEAD — restored NewsAnalyzer's Phase 1 scraper-logic instructions (was overwritten with agent-teams Lead orchestrator content during an earlier session).
- Files on disk PRESERVED — Claude Code Desktop can still read `.claude/agents/`, `.claude/teams/`, `context/`, etc., locally when user opens NewsAnalyzer. Git just hides them from tracking.

**Root cause:** auto-scaffold bug in agent-teams that wrote orchestration infrastructure into target-project working trees instead of staying contained in the agent-teams repo. Upstream fix is **Kanban #941 on agent-teams** — out of T13 scope. T13 only does NewsAnalyzer-side cleanup so the diff view stays clean while #941 work continues.

**Verification:** `git status` clean post-commit. `docker compose ps` confirms 10 containers still Up (no operational impact). Sample `CLAUDE.md` line 1 = `# NewsAnalyzer — Claude Instructions` (not `# Lead — Meta orchestrator`).

**Implications:**
- Future Claude Code Desktop sessions in NewsAnalyzer won't accidentally re-commit polluted files (gitignore covers them).
- Personal Claude Code allow-lists (`settings.local.json`) stay local to each user; no more tracking-noise from session prefs.
- **If the user re-opens NewsAnalyzer in Claude Code Desktop expecting Lead-style orchestration**, that content is no longer in `CLAUDE.md` — they'll need to either open the agent-teams worktree directly OR put a session-local note in `_scratch/`.

**Cross-references:**
- T13 commit: `93dae8e` on NewsAnalyzer main.
- Upstream platform fix: Kanban #941 on agent-teams (auto-scaffold bug — separate project).
- Memory: `feedback_cross_project_platform_edits` codifies the rule that triggered this cleanup.

---

## 2026-05-15 (late afternoon) — T10 #934 closed — fuzzy-match NewsDeduplicator landed; memory cost confirmed
**Scope:** shared (backend pipeline)
**Proposed by:** lead (closing report from dev-backend specialist)
**Status:** T10 DONE 2026-05-15 (commit `b83ee8b`).

**What landed:**
- `pipeline/app/services/deduplicator.py` rewritten with `rapidfuzz.fuzz.token_sort_ratio` + `pythainlp.tokenize.word_tokenize(engine='newmm')`. Threshold **0.75**, date window **±3 days**. Two-pass `group_articles` (match-against-existing then intra-batch); primary article = highest-priority source (high > normal > low).
- `embed_text()` signature change: now returns `str` (pythainlp-normalized title) rather than `list[float]` — documented in module docstring. Callers are inside-module only, so safe.
- Backend: `GET /events/recent?days=N` + `POST /events/with-articles` (atomic event-create + article-link). Existing `POST /articles/{id}/link-event` reused for add-to-existing path.
- BackendClient: `list_recent_events(days=7)` + `create_event_with_articles(...)` added. `set_article_event` spec name reused existing `link_article_to_event(article_id, event_id, is_primary)`.
- Pipeline `tasks.py`: dedup pass between source fan-out and `complete_ingest`. Cooperative-stop respected between groups. Worker log emits `Grouped N articles into M events (X new, Y added to existing)`.
- 13 unit tests in `pipeline/tests/test_deduplicator.py`, all passing. Tests cover: Thai tokenization, similarity range, similar-title grouping, unrelated-title separation, date-window rejection, English near-match grouping, find_existing_event match/reject/threshold, group_articles 3-case flow, primary-source selection.

**Memory cost — sizing assumption update (important for future decisions):**
- Pre-T10 pipeline container RSS: **82.95 MiB**
- Post-T10 pipeline container RSS: **316–323 MiB** (steady-state)
- Delta: **~234 MB** (pythainlp loads its Thai dict + corpus eagerly on `import`; rapidfuzz itself is ~2 MB)
- The T10 spec estimated +50 MB total; actual is ~5× that estimate. **However** Option A (sentence-transformers) was estimated at +5 GB, so Option B is still ~21× smaller. The Option B verdict holds; **234 MB is the new reference number for future sizing decisions**.
- Worker cold-start adds ~3 s for pythainlp's corpus load. Acceptable for a daemon; worth noting if worker is ever made short-lived.

**Smoke evidence (two consecutive fetch runs 2026-05-15 ~10:53 UTC):**
- Run 1 (task `92b13e1e`): 15 articles → 15 events (15 new, 0 added to existing).
- Run 2 (task `1870ffe4`): 15 articles → 15 events (15 new, 0 added to existing).
- DB post-smoke: 135 articles, 30 events, 30 article-event links. **Zero multi-article events** during smoke because live coverage was disjoint stories (max pairwise similarity computed < 0.50, far below the 0.75 threshold).
- Multi-article grouping verified via unit tests — `test_two_similar_thai_titles_same_date_group_together` proves the path works end-to-end. Production exposure depends on overlapping-coverage events landing in the ±3-day window (will surface naturally over time).

**Threshold tuning notes:**
- 0.75 left at locked value (max observed similarity < 0.50 in live smoke; well above noise floor; no false-positives).
- ±3 day window left at locked value (no edge cases surfaced).
- Recalibrate when Phase 1B widens coverage and multi-source same-story coverage actually appears.

**Open follow-ups (not yet filed; surface to user for decision):**
- **Orphan pre-T10 articles (~105):** T10 only operates on the new batch in each fetch run. Articles ingested before T10 have no event linkage. A backfill task could iterate orphan articles + run them through `group_articles` against same-window events. Optional; not blocking T11.
- **Memory deviation:** if strict +100 MB budget enforcement is required, file a follow-up task to swap pythainlp for a lighter Thai tokenizer (regex char splitter, ~10 MB) — at the cost of less-accurate Thai word segmentation. Lead's recommendation: accept the 234 MB deviation; Option B's verdict holds vs Option A regardless.

**Standards proposals (NOT auto-applied — for human MA):**
- `context/standards/python/external-clients.md` (from T9 + T10 both): document the get-or-create pattern + the "fetch existing context before mutating" pattern used in T10's dedup pass.
- `context/standards/python/dependency-sizing.md` (new): standardize that "lightweight" dependency claims must include a measured import-time RSS delta before locking. T10 spec said ~50 MB but actual was 234 MB. Pin this as a checklist item for future Option-A-vs-B decisions.

**API contract updates (proposal for `context/projects/NewsAnalyzer/shared/api-contracts.md`):**
- `GET /events/recent?days=N` — returns NewsEventOut[] for events created in last N days (1-30, default 7). Used by Pipeline dedup pass.
- `POST /events/with-articles?primary_article_id=<int>&article_ids=<int>...` — atomic event-create + article-link. Replaces older "POST /events then POST /articles/{id}/link-event" sequence on the new-event path.

**Cross-references:**
- T10 commit: `b83ee8b` on NewsAnalyzer main.
- 2026-05-15 entry (sequencing) above locked T10 = Option B and 0.75 threshold / ±3 day window.
- Next in queue: T11 #935 (un-stub `run_analysis_for_event` + wire AIPipeline per event). `blocked_by=934` resolves now that T10 is DONE.

---

## 2026-05-15 (mid-afternoon) — T9 #933 closed — RSS readers (7 feeds, not 8) wired into fetch path
**Scope:** shared (backend pipeline + frontend)
**Proposed by:** lead (closing report from dev-backend specialist)
**Status:** T9 DONE 2026-05-15.

**Decision:** RSS readers now run alongside the 4 Firecrawl scrapers in `_run_scrape_only_async`. 11 concurrent sources (4 SCRAPE + 7 RSS, not 12 as initial brief claimed — `RSS_FEEDS` registry has 7 entries; Lead's brief was off by one; specialist adapted and noted). Commit `9a0b752` on NewsAnalyzer main.

**Implementation summary:**
- New `_run_rss_source` nested function in `pipeline/app/workers/tasks.py` modeled exactly on `_run_source` (cooperative pause/stop, per-source progress meta, error isolation, scrape-log persistence).
- New `fetch_single_rss_feed(config)` helper in `pipeline/app/services/rss_reader.py` (13 lines; wraps `parse_feed` in `asyncio.to_thread`). Keeps per-feed-progress UI granular instead of one-shot batch fetch.
- Frontend `frontend/app/scrape/page.tsx` SOURCES const extended from 4 → 11 entries, single-grid layout, `(RSS)` suffix on the 7 RSS labels. Names match `RSS_FEEDS[].source_name` exactly so per-source icons render.
- NewsSource rows for RSS sources created organically via existing `POST /articles` get-or-create flow (same pattern as 4 scrapers). No explicit seed file change needed.
- Total: 3 files modified, 172 insertions, 1 deletion.

**Smoke result (task `ca510bbf`, 2026-05-15 10:10 UTC):** 120.25s end-to-end, 18 articles_found, sources_state shows all 11 keys with `status='done'`. Real RSS articles persisted (ids 98-102): BBC World × 2 (Adani fraud settlement, Supreme Court abortion pill), Bangkok Post World (Jerusalem Day), Thailand Business News (Chinese EV demand), Bangkok Post Business (Thailand cybersecurity spending). Single-feed-failure smoke (BBC URL → invalid.example.com): isolation confirmed, other 10 sources completed normally.

**Implications:**
- Article volume per fetch now ~2-3× pre-T9 (RSS feeds have more items per source than the dev-capped 3 articles/scraper). DB at ~105 articles after 2-3 fetch runs (vs ~33 after T8 alone).
- **Settrade + SET.or.th feeds return malformed XML** — HTML 403 walls served instead of RSS (auth or geofencing). feedparser sees `bozo=True` + 0 entries → currently `status='done', found=0` (graceful no-op). **Fold into T12 scope:** detect content-type mismatch (HTML when expecting application/xml) and retry with different User-Agent, OR mark a distinct scrape_status code. Existing behavior is non-blocking but invisible — not surfaced to user.
- **Standards proposal (NOT auto-applied):** `context/standards/python/external-clients.md` — note the get-or-create pattern (POST /articles auto-creates NewsSource on first article) so future RSS feeds don't require a separate seed step. User decides.

**Cross-references:**
- T9 commit: `9a0b752` on NewsAnalyzer main.
- T12 (#991) Firecrawl resilience scope EXPANDED in spirit: should also catch malformed-XML RSS feeds (HTML returned in place of application/xml). Specialist will pick this up when T12 spawns.
- DB verify (2026-05-15 17:24 UTC, post-Lead-side smoke): 9 NewsSource rows (5 RSS: Al Jazeera, BBC World, Bangkok Post Business, Bangkok Post World, Thailand Business News + 4 SCRAPE). Settrade & SET.or.th absent from sources table because organic creation only fires on successful article save.

---

## 2026-05-15 — Phase 1 sequencing + T10 dedup choice + T12 Firecrawl resilience filed
**Scope:** shared (planning lock)
**Proposed by:** user (answered 4 sequencing questions 2026-05-15)
**Status:** LOCKED 2026-05-15.

**Decisions:**

1. **T10 NewsDeduplicator embedding choice = Option B** (fuzzy match + 3-day date window via `rapidfuzz` + `pythainlp`). ~50 MB RAM, deterministic, no model download. Option A (sentence-transformers `paraphrase-multilingual-MiniLM-L12-v2`, ~5 GB RAM) **noted as future upgrade path** when Phase 1B scale demands paraphrase-tolerant matching beyond what fuzzy ratio captures. Option C (Anthropic embeddings API) **rejected** — conflicts with lock #1 (no `ANTHROPIC_API_KEY` in pipeline). Locked because user explicitly chose lighter Phase 1 footprint over richer semantic matching.

2. **T12 Firecrawl resilience layer FILED (#991)** — user clarification: firecrawl-simple HAS internal fallback engines (cheerio → fire-engine → playwright-service cascade); it just costs more per crawl when it escalates. T12 wraps `_scrape()` with retry+backoff, adds pre-flight health check (avoids 680s dead time when firecrawl-api is down), records per-scrape `engine_tier` to `scrape_logs` table (new column via Alembic migration), and emits structured WARN log on expensive escalations OR retry exhaustion OR pre-flight fail. **No engine-restoration** (Playwright/BS4 stays removed per lock #2); resilience is about Firecrawl's own cascade visibility + recovery, not adding a parallel engine.

3. **Sequencing of remaining work = sequential** (verify each step before next; ไม่เร่ง เน้นถูกต้อง):
   1. Merge cleanup ✅ DONE (commit `b976a86` on agent-teams main; branch `claude/stoic-archimedes-66ea3b` merged via prior turn).
   2. T9 #933 — Add RSS readers (8 feeds) to fetch path.
   3. T10 #934 — NewsDeduplicator real impl (Option B locked above).
   4. T11 #935 — Wire AI analysis pipeline (`blocked_by=934`). Closes original Option B scope; retires T8 stopgap delegation.
   5. T12 #991 — Firecrawl resilience (retry + pre-flight + engine-tier reporting).
   6. T6 #924 — Backfill Option A implementation (was unblocked when T3 landed; user chose to defer until after T12).

4. **No parallel spawning in this wave** — Step 0 visual-verify after each task lands before spawning next. This is the explicit "correctness over speed" mode per user 2026-05-15.

**Reasoning:**
- User's chosen sequence + Option B reflect Phase 1 sizing priority: prove the fetch + dedup + AI loop with a small-footprint stack first, then upgrade individual components (embeddings, resilience) when scale or quality demands it.
- T12 lands after T11 (not before) because resilience on a 0-feature pipeline is low-leverage; resilience on a fully-wired pipeline catches real production failure modes.
- T6 backfill last because it's the only piece that consumes meaningful Max 20x quota — best landed when the live pipeline is fully proven and observable.

**Implications:**
- Specialist for T10 will use `rapidfuzz.fuzz.token_sort_ratio` + `pythainlp.tokenize.word_tokenize` (mode='newmm') for Thai title normalization. Calibrate similarity threshold ~0.75 (rapidfuzz is more forgiving than embedding cosine).
- Specialist for T12 has 3 engine-tier extraction options (response field if firecrawl-simple exposes / duration heuristic / container log parse) — choice documented in commit.
- Each task closure includes a visual smoke verify (click 'Fetch Now' in browser, watch articles appear) before spawning the next specialist.

**Cross-references:**
- T10 task PATCH: Kanban #934 description prepended with LOCKED Option B note (2026-05-15).
- T12 task filed: Kanban #991 (this addendum).
- 2026-05-14 (late evening) entry below covers T8 closure + B breakdown context.

---

## 2026-05-14 (late evening) — Bug fix: Dashboard 'Fetch Now' stub (T8 stopgap closed; T9/T10/T11 filed as B-breakdown)
**Scope:** shared (backend pipeline)
**Proposed by:** lead (user-reported bug; diagnosed during session)
**Status:** T8 LOCKED + DONE 2026-05-14; T9/T10/T11 filed for next session.

**Decision:** Phase 0 Dashboard 'Fetch Now' button was firing a stubbed Celery task (`run_full_fetch` returned `articles_found=0` in 0.156s with TODO-stubbed scrapers/RSS-readers). Diagnosed root cause: **NOT caused by recent T1-T7 cleanup** — the stubs in `_run_full_fetch_async` have been present since the initial scaffold commit `ccceb9d`. T1/T2/T3/T5 never touched the stub block; T2's commit only dropped the Playwright async_api import.

**Fix (T8 #932 — stopgap, Option C):** Replace `_run_full_fetch_async` body with delegation to `_run_scrape_only_async` (which is fully implemented since `6be13bc`: 4 scrapers concurrent via Firecrawl-simple, cooperative pause/stop, per-source progress, per-article persistence via BackendClient, lock acquisition+release). Single commit `595ee16` on NewsAnalyzer main, +23/-78 in `pipeline/app/workers/tasks.py`. Smoke verified: 12 articles persisted (3 per source × 4 sources, SCRAPE_DEV_LIMIT=3 cap), 170.92s end-to-end, cooldown 429 still works.

**B breakdown — remaining 3 follow-ups filed:**
- **T9 #933** — Add RSS readers (8 feeds) to fetch path. `rss_reader.py` is fully implemented; just needs wiring into the Celery task path alongside the 4 scrapers.
- **T10 #934** — Implement `NewsDeduplicator` (currently 11-TODO stub; each article becomes its own event in placeholder state). Recommended embedding choice: sentence-transformers `paraphrase-multilingual-MiniLM-L12-v2` (local, free, Thai-capable, ~5GB RAM cost).
- **T11 #935** — Un-stub `run_analysis_for_event` + trigger `AIPipeline.run_full_pipeline()` per new event after fetch+dedup. `blocked_by=934`. AIPipeline class itself is already implemented (Sonnet prompts + ID-mapping for categorical fields).

After T9+T10+T11 land, the stopgap T8 delegation retires — `run_full_fetch` becomes the canonical full pipeline (fetch → dedup → AI) rather than a thin delegator.

**Reasoning:** Splitting B (full implementation) into T8+T9+T10+T11 keeps PRs reviewable in isolation: T8 fixes the user-visible bug (~30 min specialist), T9 is mechanical RSS-wiring, T10 is the only design-heavy task (embedding choice), T11 closes the AI loop. The user requested the breakdown explicitly so the stopgap could land first without waiting for T10's design decisions.

**Implications:**
- **Dashboard FetchButton now works** — clicking it produces real articles. The button hits the lock-controlled `/ingest/run` path (Pipeline → run_full_fetch → delegate to scrape-only). The `/scrape` page button hits the same scrape-only path directly with its own UI (per-source progress display, pause/stop controls).
- **Articles persist 1:1 with events until T10 lands** — `events_processed=articles_found` until real dedup groups multi-source coverage of the same story.
- **No AI analysis triggers yet** — T11 covers that. Until then, the dashboard shows raw articles without AI summary/sentiment/recommendation.
- **Dead imports** in `pipeline/app/workers/tasks.py:31-34` (AIPipeline, NewsDeduplicator, fetch_all_rss_feeds, run_all_scrapers) kept intentionally — T9/T10/T11 will re-use them. Removing now means T11 adds them back. Not worth the churn.

**Cross-references:**
- T8 commit: `595ee16` on NewsAnalyzer main.
- Pre-existing working code studied for delegation: `_run_scrape_only_async` (`pipeline/app/workers/tasks.py:142-406`).
- Scaffold-era stub origin: initial commit `ccceb9d` (`feat: initial project scaffold and documentation`).

---

## 2026-05-14 (evening) — Phase 0 Wave 1 closure (T3/T4/T5/T7) + T5 implementation deltas
**Scope:** shared (backend + devops + docs)
**Proposed by:** lead (closing report from 4 parallel specialists)
**Status:** LOCKED 2026-05-14 — addendum to lock #2 / lock #5 / lock #6 implementations.

**Decision:** Phase 0 Wave 1 closed. 4 tasks landed on NewsAnalyzer main:
- **Kanban #913 (T5)** — firecrawl-simple compose integration, commit `1498747`
- **Kanban #922 (T3)** — AIRecommendation + AgentRun + BackfillJob + DecisionTag FK + Alembic migration, commit `f4cd4d2`
- **Kanban #923 (T4)** — PRD + ARCHITECTURE sync to 7 decision locks, commit `55004bc`
- **Kanban #925 (T7)** — gitignore pipeline/celerybeat-schedule, commit `93310d3`

Plus **Kanban #910 (T2)** — flipped to DONE; AC-7 subsumed by T5 live-scrape smoke (kaohoon + thunhoon both returned real Article rows via firecrawl-simple).

**T5 implementation deltas (vs brief expectations — supersedes specific assumptions in the 2026-05-14 PM pivot entry):**
1. **Image source:** brief assumed `ghcr.io/devflowinc/firecrawl-simple-*`; reality is no such ghcr image is published. Upstream `firecrawl-simple/docker-compose.yaml` itself ships **`trieve/firecrawl:v0.0.55`** + **`trieve/puppeteer-service-ts:v0.0.13`** (both on Docker Hub, verified via hub.docker.com/v2/repositories/trieve/firecrawl/tags). NewsAnalyzer's compose uses those prebuilt tags directly. Total image pull ~8.4 GB.
2. **Health endpoint:** firecrawl-simple does NOT expose `/health` bare (404). Real endpoints: `/v1/health/liveness`, `/v1/health/readiness`, `/serverHealthCheck` — all return 200. Healthcheck uses `/v1/health/liveness`. The trieve/firecrawl image is `node:20-slim`-derived and lacks `wget`/`curl` — healthcheck shell uses `node -e "fetch(...)"` and worker uses `pgrep -f node`.
3. **Env vars:** `ALLOW_LOCAL_WEBHOOKS=false` and `BLOCK_MEDIA=false` are upstream-Firecrawl-only — firecrawl-simple silently ignores them. Setting them is harmless future-proofing. `USE_DB_AUTHENTICATION=false` is the load-bearing one (lets any non-empty key pass).
4. **Scraper SDK bug fixes** (carried forward from T2 #910's incomplete state — required to make T5 AC-6 smoke pass):
   - `scrape_url(url, formats=[...])` → `scrape_url(url, params={"formats": [...]})` (firecrawl-py 1.6.3 signature mismatch — was raising TypeError).
   - `"html"` → `"rawHtml"` (firecrawl-simple's `/v1/scrape` enum value; `"html"` returned 400 Bad Request).
   - SDK pinned at `firecrawl-py==1.6.3`. Future SDK bumps to 2.x may change the params shape — surface during bump.
5. **scraper.py architecture:** T2 #910 had already consolidated 4 source classes through a single lazy `_get_client()` singleton (not 4 separate `FirecrawlApp` init sites as the T5 brief assumed). One init covers all 4 classes (KaohoonScraper / ThunhoonScraper / BangkokBizNewsScraper / ThansettakijScraper).

**T3 design decisions (record for future archaeology):**
1. **Single Alembic migration `20260514_0001_schema_additions_phase0.py`** — co-exists with the existing `Base.metadata.create_all()` in `backend/app/main.py` lifespan. `create_all` outraces alembic on backend startup, leaving alembic in an awkward "tables exist, alembic doesn't know" state. **Follow-up tech-debt:** replace `create_all` with `alembic upgrade head` in lifespan to make migration the single source of truth (separate task — out of T3 scope; main.py wasn't in T3 file list).
2. **PG enums use lowercase VALUES, not Python enum NAMES** — required `Enum(MyEnum, values_callable=lambda x: [e.value for e in x], name="...")` on the ORM column because Pydantic Literal types use lowercase strings (`"extraction"`) for API stability. Default SQLAlchemy stores uppercase NAMES (`"EXTRACTION"`) which mismatched the PG enum definition. Opposite of the existing SourceType/SourcePriority pattern — chose API-stability over codebase-consistency.
3. **Pydantic Out schemas use `Literal[...]` + `_enum_to_str` `field_validator(mode="before")`** to bridge ORM enum instances → Literal types cleanly. Stable against future enum renames.
4. **AgentRun.event_id is nullable** because some calls (dedup, language_detect) aren't event-scoped.
5. **`call_ai` gains optional `event_id` kwarg** (default None) — backward-compat with existing Step1/Step2 callers.
6. **AgentRun logging is non-fatal** — wrapped in try/except logging at WARNING. Lock #6 telemetry MUST NOT break the AI call (backend hiccup ≠ pipeline death). Mock mode skips logging.
7. **Inferred cost rates** baked at: claude-sonnet-4-6 = $3/$15 per MTok, claude-haiku-4-5 = $0.80/$4 per MTok, fallback to Sonnet rates for unknown models (over-estimate beats silent-zero). Revisit when Anthropic publishes new prices.
8. **BackfillJob.source_name is plain String UNIQUE column, NOT FK to news_sources.id** — keeps backfill tracking decoupled from the seed-table id stability.

**Status changes recorded:**
- T2 #910: HALTED → DONE (AC-7 verified-via-T5)
- T3 #922: TODO → DONE (8/8 ACs passed)
- T4 #923: TODO → DONE (7/7 ACs passed)
- T5 #913: TODO → DONE (7/7 ACs passed)
- T7 #925: TODO → DONE (4/4 ACs passed)
- T6 #924: blocked_by=922 → now actionable (T3 schema landed; T6 backfill impl can spawn in next session)

**Coordination note:** T4 #923 commit `55004bc` had mild scope-bleed — accidentally removed `pipeline/celerybeat-schedule` from index during docs sync (T7's territory). T7 specialist confirmed net effect is consistent (file off-index, on-disk, ignored); no rework needed. Flagged as anti-pattern reminder: `git add -A` on a scoped task violates the file-ownership rule.

**Standards insights proposed for human review (NOT auto-applied):**
- `context/standards/docker/compose.md` add: "Node-base service images (e.g. `node:20-slim`-derived `trieve/firecrawl`) often lack `wget`/`curl` — healthchecks must use `node -e 'fetch(...)'` or `pgrep -f <process>` rather than `wget`/`curl` in CMD-SHELL (silent unhealthy state otherwise)."
- `context/standards/sqlalchemy/` add: "SQLAlchemy `Enum()` column needs `values_callable=lambda x: [e.value for e in x]` when the API contract uses lowercase enum values (matches Pydantic Literal pattern). Default stores uppercase NAMES, which mismatches PG enum definitions when the Pydantic Literal type uses lowercase."
- `context/standards/pydantic/` add: "Bridge ORM enum instances → Pydantic Literal types with `field_validator(mode='before')` calling a `_enum_to_str` helper. Stable against ORM enum renames."

**Open follow-ups (NOT yet filed as Kanban tasks):**
- `Base.metadata.create_all()` → `alembic upgrade head` migration source-of-truth swap (T3 specialist flagged).
- ThunhoonScraper article-acceptance regex tunes — currently matches `/about` and `/contact` (T5 smoke surfaced; not a stack failure).
- 2 external-site rendering timeouts on kaohoon.com `/news` and `/news/local` listing pages (slow Hero/Ulixee render) — neither a stack issue.

**Implications:**
- T6 (#924) can spawn next session — backfill Option A implementation (Celery beat + cursor + low-priority lane) now that BackfillJob model is on main.
- T5 smoke proved end-to-end scrape path: pipeline container → firecrawl-api:3002 (compose-internal) → kaohoon.com/thunhoon.com → Article rows. The Phase 0 scraping stack is operational.
- Phase 0 schema (4 new tables/columns + Pydantic schemas + Alembic migration) is on main and `alembic upgrade head` applies cleanly. Phase 1 work (AI pipeline integration) can start using AIRecommendation + AgentRun seams.

**Cross-references:**
- T5 commit: `1498747` on main (NewsAnalyzer)
- T3 commit: `f4cd4d2` on main (NewsAnalyzer)
- T4 commit: `55004bc` on main (NewsAnalyzer)
- T7 commit: `93310d3` on main (NewsAnalyzer)
- T6 (next session): Kanban #924

---

## 2026-05-14 (PM) — Pivot scraping: Cloud → firecrawl-simple self-host (supersedes lock #2)
**Scope:** shared (backend pipeline + devops)
**Proposed by:** user (pivot during T2 #910 work — preferred local/self-host over external SaaS dependency)
**Status:** LOCKED 2026-05-14 — replaces "Firecrawl Cloud free tier" from earlier 2026-05-14 entry (lock #2).

**Decision:** Add **firecrawl-simple** (devflowinc fork — MIT-friendly stripped variant of upstream Firecrawl) as 4 services in NewsAnalyzer's existing `docker-compose.yml`. Pipeline talks to it via firecrawl-py SDK with `api_url=http://firecrawl-api:3002` (compose-internal hostname).

**Service composition (firecrawl-simple):**
1. `firecrawl-api` — main scrape endpoint, host-side port **7030** (internal 3002)
2. `firecrawl-worker` — background scrape job consumer
3. `firecrawl-puppeteer` — headless browser service
4. `firecrawl-redis` — message queue + cache (separate from NewsAnalyzer's main redis on 7079 to keep concerns isolated)

**Why firecrawl-simple over full Firecrawl:**
- NewsAnalyzer load is tiny (4 sources × 2/day = 240 scrapes/mo ≈ 0.005/min) — full Firecrawl's RabbitMQ + Postgres + extract layer is unused overhead.
- 4 services vs 5; ~3 GB RAM vs 8 GB; faster first-build; fewer setup pain points (no RabbitMQ-race-condition footgun, no `pg_cron` init).
- MIT-friendly fork sidesteps the AGPL-3.0 license question entirely.
- We do all LLM analysis with `claude -p` (lock #1) — Firecrawl's `/v1/extract` is not needed.

**Why over Cloud (the original lock #2):**
- Zero external SaaS dependency — single source of failure removed.
- No `FIRECRAWL_API_KEY` provisioning friction (self-host accepts any non-empty key when `USE_DB_AUTHENTICATION=false`).
- Local network = no rate-limit concern (free tier was 500/mo; trivial headroom but mental tax of monitoring).

**Why over standalone Firecrawl (not in compose):**
- One `docker compose up` brings the whole stack — no second compose stack to manage.
- Reuses NewsAnalyzer's existing `newsanalyzer` network; compose-internal DNS handles service discovery.

**Reasoning (broader):**
- Self-host is the more durable choice for a project meant to run unattended for months — eliminates surprise pricing/policy/quota changes from the SaaS vendor.
- The cost of adding 4 containers is one-time setup pain (~30 min specialist task) vs ongoing recurring tax of SaaS-key management. Amortizes fast.

**Implications:**
- **Supersedes** the "Firecrawl Cloud free tier (500/mo)" portion of lock #2 (2026-05-14 entry above). The rest of lock #2 (RSS sources stay on feedparser; 4 scrape sources covered by Firecrawl) is unchanged.
- T2 (Kanban #910) HALTED on AC-7 — it had assumed Cloud + `FIRECRAWL_API_KEY` env var. The scraper.py code from T2 is mostly reusable (SDK calls unchanged — just `api_url` added). Resolution: file T5 (this pivot's implementation); T5's smoke AC subsumes T2 AC-7; T2 closes once T5 lands with a note "AC-7 verified via T5 smoke".
- New env vars in `pipeline/app/config.py`: `firecrawl_api_url: str = "http://firecrawl-api:3002"` (default compose-internal); `firecrawl_api_key: str = "dev-key"` (any non-empty; auth disabled in firecrawl-simple).
- Resource baseline now ~3 GB RAM dedicated to Firecrawl services. Surfaces as a host-requirement note for the project's README.
- Future option (out of scope for now): if `/v1/extract` LLM-integration becomes useful, switch to full upstream Firecrawl. firecrawl-py SDK call sites stay the same; only compose services change. Defer until a real use-case demands it.

**Cross-references:**
- Original lock #2: this same date entry below, point 2. Marked superseded inline.
- Research source: `_scratch/research-firecrawl-selfhost-2026-05-14.md`.
- Implementation: Kanban T5 (to be filed; will subsume T2 AC-7 smoke).

## 2026-05-14 — Engine, scope, scraping, cost rules (planning lock — backfill = Option A)
**Scope:** shared (all roles)
**Proposed by:** lead (with user)
**Status:** LOCKED 2026-05-14 — all 6 sub-decisions confirmed. Backfill resolved to Option A (background trickle via Max 20x quota) in continuation session.

**Decisions:**

1. **LLM engine: Claude Code CLI in headless mode** (`claude -p` subprocess from pipeline service, optionally Agent SDK later). Uses user's Claude Max 20x subscription quota — **no `ANTHROPIC_API_KEY` in pipeline**. Interactive CLI reserved for dev/research/manual investigation by the user only.
   - Pipeline Dockerfile must install `@anthropic-ai/claude-code` and mount user's `~/.claude` for session auth.
   - Prompt definitions + JSON-parsing layer kept **separate** from invocation layer so we can swap to API key later without rewriting prompts.

2. **News scraping: Firecrawl Cloud free tier** (500 scrapes/month). Covers the 4 scrape sources (ทันหุ้น, ข่าวหุ้น, กรุงเทพธุรกิจ, ฐานเศรษฐกิจ) at 2 fetches/day. RSS sources (Settrade, SET.or.th, Bangkok Post, Thailand Business News) continue using `feedparser` directly — no Firecrawl call needed for RSS.
   - Replaces the PRD's Playwright + BeautifulSoup plan (~500MB+ Docker savings).
   - License note: Firecrawl self-hosted is AGPL-3.0; single-user personal use does not trigger AGPL obligations. Cloud free tier is SaaS — no license concern at all.
   - Migration plan: if scraping volume exceeds free tier, evaluate self-host vs Hobby tier ($19/mo, 3K).

3. **Phase 1 scope: Thai stocks only, 2 LangGraph nodes** (News Agent + Synthesizer). No Technical/Pattern/Macro agents in Phase 1. Gold/crypto/international defer to Phase 4+.
   - Drops the multi-agent QuantAgent-style decomposition from initial scope to keep MVP tight.
   - Vision-based Pattern Agent dropped entirely from current plan (vision LLM ~5-10x cost).
   - LangGraph orchestration also **deferred** — Phase 1 uses Lead-as-orchestrator pattern (pipeline service calls `claude -p` sequentially); revisit LangGraph if/when 3+ agents land.

4. **Model usage rules in pipeline:**
   - **Sonnet only** for extraction, sentiment, and synthesizer calls (current PRD default — confirmed).
   - **Haiku** for low-stakes tasks: dedup embedding compare, title classification, language detection.
   - **Opus** never in pipeline. Reserved for manual user investigation via interactive Claude Code CLI.
   - **Prompt caching enabled** when batching multiple articles in one Sonnet system-prompt window (5-min TTL).
   - **AgentRun table tracks token usage and inferred cost per call** even though Max 20x is flat-fee — needed for "when do we migrate to API key" decision later.

5. **Human is the sole decision-maker.** AI emits `AIRecommendation` (Bullish/Bearish/Neutral + reasoning + confidence + per-agent breakdown). User reviews in Frontend, then submits `UserDecision` (extends PRD's `DecisionTag` with `ai_recommendation_id` FK). No auto-trade, no execution integration ever — out of scope.

6. **Backfill 1 year — LOCKED: Option A (background trickle via Max 20x quota).**
   - Free in $ terms — uses the same Max 20x quota the live pipeline runs on.
   - Tradeoff accepted: ~2-4 weeks elapsed wall-time to fill 1y history (rate-limit bounded). Runs in parallel with live ingestion from Phase 1 launch.
   - Implementation hint: separate `BackfillJob` row tracks per-source progress (last-fetched-date cursor); pipeline runs backfill calls in low-priority lane behind live calls so daily quota doesn't starve fresh data.
   - Rejected: Option B (API key + Batch API ~$10-30, 1-2 days) — keeps $0-MVP story intact + avoids `ANTHROPIC_API_KEY` introduction at this phase. Revisit only if a future analysis use-case demands faster history (e.g., backtesting before Phase 1B closes).
   - Rejected: Option C (defer) — chose to lock now so backfill design seams (BackfillJob model, low-priority lane) land with Phase 0 scaffold instead of retrofitting later.

7. **Port plan: NewsAnalyzer = `7xxx` range, no port shared with agent-teams.**
   - Mnemonic: agent-teams uses 5xxx/8xxx (web=5431, api=8456, langgraph=8465, db=5432); NewsAnalyzer claims 7xxx so both can run in parallel.
   - Allocations: `frontend=7010` (Next.js), `backend=7020` (FastAPI), `pipeline=7021` (FastAPI controller), `db=7042` (Postgres — avoids 5432 collision), `redis=7079`.
   - Rewrite scope: `docker-compose.dev.yml` + `docker-compose.prod.yml` publish-port mappings; `frontend/package.json` "dev" script (`next dev --port 7010`); backend + pipeline uvicorn CMD (in Dockerfiles or compose `command:`); `.env.dev` / `.env.prod` URL env vars (BACKEND_URL, NEXT_PUBLIC_BACKEND_URL, NEXT_PUBLIC_PIPELINE_URL, DATABASE_URL, REDIS_URL); any hardcoded `localhost:8000` / `localhost:3010` in code/docs.
   - Locked because: previous default 3010/8000/8001/5432/6379 either looks generic (5432 collision risk) or sits near common defaults (3010 too close to 3000). Distinct 7xxx range is harder to accidentally collide with other local stacks the user runs.
   - Rationale for putting in lock entry: port rewrite is bundled with T1 (engine migration — same Dockerfile + compose touches) so doing both in one task minimizes compose-file churn.

**Reasoning:**
- User has Max 20x → marginal LLM cost = 0 during MVP. Picking CLI over API key avoids burning real $ until product proves out.
- Firecrawl Cloud free tier sized to the actual daily fetch volume (8 sources × 2/day × 30 ≈ 480/mo).
- Scope cut to Thai stocks + 2 agents because system is greenfield (only `api/health.py` exists today) — start minimal, prove the loop, then expand.
- Cost rules locked at decision time so we don't accidentally call Opus from pipeline 6 months later when no one remembers.
- Backfill is the only thing that breaks the "free" story (volume × token cost is real even on Max 20x via rate limits) — explicit choice required.

**Implications:**
- Pipeline `requirements.txt` adds `firecrawl-py`, drops `playwright` + `beautifulsoup4` (RSS-only feedparser stays).
- Pipeline Dockerfile adds Node.js + `@anthropic-ai/claude-code` install + `~/.claude` mount.
- Backend schema adds: `Asset`, `PriceBar` (Phase 2 onward), `AgentRun`, `AIRecommendation`, `UserDecision` (FK to `ai_recommendation_id`). PRD's `DecisionTag` either renamed to `UserDecision` or extended.
- PRD addendum needed — capture engine choice, Firecrawl integration, and revised phase plan (dev-documentor task after Phase 0 scaffold lands).
- Next Kanban tasks queue (Phase 0 scaffold): backend + frontend + pipeline scaffold, Docker compose verify, alembic baseline, seed default `NewsSource` rows, seed `TagClass` predefined rows.

<!-- No decisions yet. First entry will be appended above this line. -->
