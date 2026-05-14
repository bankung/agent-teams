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
