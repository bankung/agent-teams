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
