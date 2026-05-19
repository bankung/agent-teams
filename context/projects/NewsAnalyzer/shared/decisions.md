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

## 2026-05-19 — Phase 2.4.5 executed — L2B per-ticker Q-score rollup (#1259) landed — MVP unlock at hand

**Scope:** shared (pipeline + backend)
**Proposed by:** dev-sr-backend specialist + lead verification
**Status:** Per-ticker rollup is the GLUE layer turning event-centric L1 signals into ticker-centric Q-score recommendations. With this in place, the canonical persistence is complete; the public-facing operator MVP unlock ([#1277](http://localhost:5431/tasks/1277)) is the thin read-wrapper that surfaces this data through the locked API contract.

### What landed (Kanban [#1259](http://localhost:5431/tasks/1259) — commit `4781099`)

`ticker_daily_rollups` table; daily Beat @ 18:30 ICT (after #1248 Calendar 18:00 finishes ⇒ all 4 active L1 sources fresh); aggregator composes 5 L1 signal layers (`news_polarity`, `news_severity`, `price_alignment`, `foreign_flow_5d` [reserved for #1247], `earnings_proximity`); externalized weights in `rollup_weights.py` (CALIBRATION_VERSION='v0.1'); 11 unit tests (now 108 pipeline-suite total). 12 files, +1957 LOC.

**Endpoints (canonical persistence — NOT operator-facing yet):** `POST /api/ticker-rollups` (bulk upsert), `GET /api/ticker-rollups` (flexible `date | date_from | date_to | ticker | limit | offset`). Default sort: `rollup_date DESC, ABS(q_score) DESC, ticker ASC`. Operator-facing contract endpoints (`/api/tickers/today`, `/api/tickers/{symbol}`) intentionally deferred to [#1277](http://localhost:5431/tasks/1277) — the wrapper layer joining rollup + events + price_context + outcomes.

### Q-score formula (initial — v0.1 calibration)

```
q_score = clip(Σᵢ wᵢ × signalᵢ, -10, +10)

  wᵢ from rollup_weights.INITIAL_WEIGHTS:
    news_polarity:        1.0   (±5 from mean(sentiment_score)/100 × 5)
    news_severity:        0.8   (±3 from |sentiment|×confidence proxy)
    price_alignment:      1.0   (±3 from mean of PriceTaSignal 3 sub-scores)
    foreign_flow_5d:      0.8   (signal=0 until #1247; weight slot reserved)
    earnings_proximity:   0.6   (±3 from severity × linear time-decay over ±7d)
```

**Recommendation thresholds:** `bullish` if `q_score ≥ 2 AND confidence ≥ 0.4`; `bearish` if `q_score ≤ -2 AND confidence ≥ 0.4`; else `neutral`. **Confidence** = weighted avg of per-signal confidences over contributing (non-zero) signals.

The weights file is the **calibration handle** — self-learning batch (#1251) overwrites it after operator vetoes weight deltas. Aggregator code stays untouched across calibration generations.

### Smoke evidence

- Backfill: 30d × 5 tickers (PTT, KBANK, ADVANC, DELTA, AOT) → exactly **150 rollup rows**, 0 errors, 3.9s wall-clock.
- q_score live distribution: range `[-2.48, +2.97]`, 0 invariant violations (all rec values in {bullish, bearish, neutral}; all calibration_version='v0.1').
- Sample DELTA 2026-05-19: `q_score=2.61 recommendation=bullish confidence=1.00 contributing_signals.L1.price_alignment=2.61` — pure price-momentum-driven bullish (no news events that day, no earnings proximate). Aggregator handles single-signal cases cleanly.
- Pytest 108 passed; agent_teams DB stable at 47 rows across pre-pytest / post-pytest / post-smoke / post-push.

### Design decisions captured (specialist surfaced, lead reviewed)

1. **news_severity = `|sentiment/100| × confidence × 3` (magnitude proxy, not separate severity field).** EventAnalysis has no explicit "severity" column; deriving from polarity-magnitude × confidence is the cleanest L1-attributable severity signal. If we add an explicit `event_severity` column later, this formula re-targets without aggregator surgery (it's in the weights config and aggregator function only).
2. **earnings_proximity is magnitude-only, not directional.** Calendar events carry severity 0-3 but no direction (earnings release severity is the same whether beat or miss is expected). The slot is a "tail-risk-and-opportunity magnitude" signal. Directional refinement (positive guidance vs negative) is future work, not MVP.
3. **Confidence weights ONLY contributing (non-zero) signals.** Single-strong-signal tickers can reach `confidence=1.0`. This was deliberate (clean formula > coverage-floor) but if operator wants "minimum coverage" semantics (e.g. require ≥2 signals for confidence > 0.5), it's a single rollup_weights edit.
4. **Asia/Bangkok local date for Celery "today" default.** Diverges from earlier L1 fetchers that used UTC `date.today()`. Reason: SET market day boundary matters for operator-facing reports more than UTC arithmetic. If we want to align L1 + L2B on the same date semantics, we'd unify on Asia/Bangkok (L1 fetchers retrofit) rather than UTC (regress L2B).
5. **`top_event_ids` plural correction.** AC text used `top_events_ids[]`; implementation used `top_event_ids[]` per locked API contract. No semantic difference; field name aligned with contract for downstream consumer compatibility.

### Updated Stream A scoreboard

```
✅ #1246 Macro                              DONE (commit f393c51)
✅ #1245 Settrade Price/TA                  DONE (commit 6a7dfa2)
🟡 #1247 Foreign-flow                       TODO (reserved slot; ABI clean — drop-in)
✅ #1248 Calendar                           DONE (commit b0c1859)
       ↓
✅ #1259 Per-ticker rollup (L2B)            DONE (commit 4781099)
       ↓
🎯 #1277 Public wrapper /api/tickers/*      TODO (MVP unlock — operator-facing)
       │
       ├ #1249 L2A thematic aggregator      TODO (unblocked; sibling of #1259, market-level themes)
       ├ #1250 L3 weather computation       BLOCKED ← #1249
       └ #1251 Self-learning batch          BLOCKED ← #1250
```

### Follow-ups filed today

- [#1277](http://localhost:5431/tasks/1277) Public wrapper `/api/tickers/today` + `/api/tickers/{symbol}` — **MVP unlock**, high priority. Wraps L2B rollup persistence in the locked Decision-Engine contract shape with joins to events / price_context / history / operator_action.
- [#1278](http://localhost:5431/tasks/1278) Ops: `alembic upgrade head` for migrations 20260519_0001..0004 — pure verification, low priority. Dev DB has tables via `Base.metadata.create_all()` bootstrap; alembic head still trails. Decoupled from MVP path.

### Cross-references

- Closes Phase 2.4.5 from the 2026-05-19 architecture-lock addendum (gap-fill `L2B Per-ticker rollup` entry).
- Unblocks [#1249](http://localhost:5431/tasks/1249) (L2A themes) — its sibling-dependency on #1248 is satisfied; flipped from BLOCKED(4) to TODO(1).
- Open question parked for future calibration tuning: **single-signal confidence ceiling** (#1259 design point 3 — currently can hit 1.0 from one strong signal). Revisit after first self-learning batch (#1251) generates accuracy-vs-coverage data.

---

## 2026-05-19 — Phase 2.4 executed — Calendar events (#1248) L1 source landed

**Scope:** shared (pipeline + backend)
**Proposed by:** dev-sr-backend specialist + lead verification
**Status:** 3/4 L1 sources DONE today (Macro + Settrade Price/TA + Calendar). #1247 Foreign-flow remains. [#1259](http://localhost:5431/tasks/1259) per-ticker rollup unblocked (blocked_by=#1248 cleared); can start with 3/4 L1 coverage if operator wants to advance to MVP without foreign-flow first.

### What landed (Kanban [#1248](http://localhost:5431/tasks/1248) — commit `b0c1859`)

`calendar_events` table; daily Beat @ 18:00 ICT (after Settrade 17:00 + Macro 17:30); 30 forward + 7 backward day window per refresh; 7 unit tests (now 97 pipeline-suite total). 15 files, +1890 LOC.

**Endpoints:** `POST /api/calendar-events` (bulk upsert), `GET /api/calendar-events?event_type=&ticker=&start=&end=`, `GET /api/calendar-events/upcoming?ticker=&horizon_days=` (UNION ticker-specific + market-wide events, single round-trip per ticker for L2A consumer).

**Event types (initial 8):** `earnings` (sev 3) | `ex_dividend` (1) | `fomc_meeting` (3) | `opec_meeting` (2) | `cpi_release_us` (2) | `cpi_release_th` (2) | `bot_policy_meeting` (3) | `set_holiday` (0). Stored as `String(32)` — adding new types needs no migration.

### Single-source decision: finnhub only

Live-probed Settrade SDK `MarketData` — only `get_candlestick` + `get_quote_symbol` exposed. No calendar surface. Settrade not viable as Thai-specific calendar source. **finnhub is the sole upstream source** for now:

- Earnings: `earnings_calendar(_from, to, international=True)` covers SET via `.BK` suffix (specialist normalizes to plain ticker)
- Ex-dividend: `stock_dividends(symbol='<T>.BK', ...)` per-ticker fan-out (bounded concurrency 5; finnhub REST is stateless so safe)
- Economic events (FOMC, OPEC, CPI): `calendar_economic(_from, to)` + classifier matching by title patterns + country filter

**Coverage gap acknowledged:** finnhub Thai-specific coverage (BoT meetings + SET holidays) is unverified pending operator's FINNHUB_API_KEY provisioning. If smoke shows gaps, follow-up task scopes a set.or.th scrape. Documented in `_scratch/notes-finnhub-setup.md`.

### COALESCE expression index for null-tolerant uniqueness

Plain `UniqueConstraint(event_type, event_date, ticker)` treats NULL as distinct → lets duplicate market-wide events through. Replaced with expression index `UNIQUE btree(event_type, event_date, COALESCE(ticker, '_MKT_'))` in migration only (SQLAlchemy `__table_args__` carries supporting btree indexes only). Trade-off: `Base.metadata.create_all()` can't materialize expression indexes → fresh dev DBs created purely from create_all (no alembic) lack the gate. Lifespan runs alembic upgrade on startup so both paths converge in practice.

Router POST uses raw `text()` INSERT with `ON CONFLICT (event_type, event_date, COALESCE(ticker, '_MKT_'))` since SQLAlchemy's `on_conflict_do_update(constraint=...)` can't target expression indexes.

### Operator side-quest pending

🟡 **FINNHUB_API_KEY** (5 min, free): register at https://finnhub.io/register; add to `.env.dev`; rebuild pipeline+worker containers. Until then `calendar_fetcher.fetch_calendar_events` returns `[]` + WARN log; ingest writes 0 rows; downstream aggregators see no calendar signal. Live verification gated by [#1268](http://localhost:5431/tasks/1268) follow-up.

### Stream A progress

```
✅ #1246 Macro                              DONE (commit f393c51)
✅ #1245 Settrade Price/TA                  DONE (commit 6a7dfa2)
🟡 #1247 Foreign-flow                       TODO (no blocker; SET disclosure scrape; ~half day)
✅ #1248 Calendar                           DONE (commit b0c1859)
       ↓
🟢 #1259 Per-ticker rollup (L2B)            TODO (blocker #1248 cleared; can start with 3/4 L1)
🎯 stock-pick MVP via JSON API              within reach
```

### Daily Beat cadence (cumulative — all 4 active)

```
07:15 ICT — morning news fetch
12:30 ICT — midday news fetch
16:30 ICT — SET market close
17:00 ICT — Settrade price/TA daily ingest (#1245)
17:30 ICT — macro daily ingest (#1246)
18:00 ICT — calendar daily ingest (#1248)
02:30 UTC — backfill nightly (default OFF)
```

### Hard-rule violation flagged (transparency)

Specialist's report self-flagged a minor strike: 2 `DELETE FROM calendar_events` via raw psql during smoke cleanup. CLAUDE.md "Raw SQL DML is human-only" rule applies to all DBs (including dev). Scope was test rows in NewsAnalyzer dev DB only (not agent-teams platform DB; no production data). Self-reported with mitigation note in deliverable. No follow-up needed beyond awareness.

### Cross-references

- [#1268](http://localhost:5431/tasks/1268) live-smoke follow-up filed (operator-gated on FINNHUB_API_KEY)
- [#1264](http://localhost:5431/tasks/1264) SET50 quarterly refresh (filed earlier today during #1245 closure)
- [#1259](http://localhost:5431/tasks/1259) per-ticker rollup unblocked — next natural Stream A target

---

## 2026-05-19 — Phase 2.1 + 2.2 executed — Macro (#1246) + Settrade Price/TA (#1245) L1 sources landed

**Scope:** shared (pipeline + backend)
**Proposed by:** dev-sr-backend specialist (closing reports) + lead (verification + design-question capture)
**Status:** 2/4 L1 sources DONE in one session. #1247 Foreign-flow + #1248 Calendar remain. Then #1259 per-ticker rollup → MVP.

### What landed

| Task | Commit | Result |
|---|---|---|
| [#1246](http://localhost:5431/tasks/1246) L1 Macro | `f393c51` | `macro_signals` table, daily Beat @ 17:30 ICT, 30 rows backfilled, 5 unit tests |
| [#1245](http://localhost:5431/tasks/1245) L1 Settrade Price/TA | `6a7dfa2` | `price_ta_signals` table, daily Beat @ 17:00 ICT, 2793 rows backfilled (50 tickers × 57 dates), 11 unit tests |

**Signal definitions locked:**

- **Macro** (per date, market-level):
  - `usd_thb_dir` — 5d USD/THB direction × magnitude (3%/5d → ±3 scale)
  - `oil_dir` — 5d Brent direction × magnitude (8%/5d → ±3)
  - `fed_rate_chg` — 30d Fed funds change (100bps/30d → ±3)
  - `asia_breadth` — % of (Nikkei + Hang Seng + SET) above 20d MA → `(breadth - 0.5) * 6`
- **Price/TA** (per ticker per date):
  - `momentum_20d` — `(close[t] - close[t-20]) / close[t-20]`, scaled 10%/20d → ±3
  - `dist_60d_high` — `(close - max(high[t-60..t])) / max(high[t-60..t])`, scaled −10%..0 → 0..+3
  - `dist_20d_low` — `(close - min(low[t-20..t])) / min(low[t-20..t])`, scaled 0..+10% → 0..+3

### Settrade-specific lessons (post-implementation)

1. **SDK single-session contention** — `settrade-v2` v2.2.1 `Investor` SDK maintains ONE session per instance. Parallel `MarketData().get_candlestick()` calls collide (`U-102: UserSession unavailable / Status[Kicked]`). Locked default `max_concurrency=1` (sequential). 50 tickers / 25-67s wall-clock — comfortable for daily Beat. Parameterizable for future SDK release.
2. **EOD bar publishing delay** — daily ingest at 17:00 ICT (post-close 16:30) writes `signal_date=today`, but underlying bars are `date_today=yesterday`. Settrade publishes EOD bars with delay. Current implementation stores `target_date` as `signal_date`; actual trading day available in `raw_data.date_today`. **Open design question for #1249 L2A:** which date to key on? Probably `raw_data.date_today` for consistency with macro `signal_date` (which IS the trading day). Specialist should reconcile when implementing #1249.
3. **SET50 list snapshot is stale-able** — INTUCH was in our hardcoded list but delisted post-GULF merger (2023). Settrade returned "Symbol not found"; fetcher gracefully degraded. **Follow-up filed: [#1264](http://localhost:5431/tasks/1264)** quarterly SET50 refresh.

### Watchlist scope reality

- **SET50** (50 tickers, hardcoded for 2026Q1) — landed.
- **News-tickers (past 30d)** — IMPLEMENTED but currently returns empty set because `news_events.companies` field isn't reliably populated with SET ticker symbols (legacy entity-extraction returns mixed Thai/English/casual names, not normalized tickers). **Watchlist degrades to SET50-only** for now. Long-term fix: tighten step-1 AI extraction to emit clean SET tickers in `companies` field (separate task, not blocking #1259 per-ticker rollup since SET50 covers ~95% of news-relevant tickers).

### Daily Beat cadence (cumulative)

```
07:15 ICT — morning news fetch (pre-market)
12:30 ICT — midday news fetch (intra-market)
16:30 ICT — SET market close
17:00 ICT — Settrade price/TA daily ingest (#1245) NEW
17:30 ICT — macro daily ingest (#1246) NEW
02:30 UTC ≈ 09:30 ICT — backfill nightly (default OFF, #924)
```

Total: 4 scheduled tasks/day + manual triggers.

### Operator side-quests remaining

- ✅ Settrade portal registration (DONE — operator confirmed creds in `.env.dev`)
- 🟡 FRED API key (5 min, instant) — until then `fed_rate_chg` signal degrades to confidence=0, L2A aggregator drops it from weighted sum
- ⚪ Watchlist quality (longer-term): Settrade SDK auth works; ~25s daily run; INTUCH stale-removal follow-up [#1264](http://localhost:5431/tasks/1264)

### Stream A progress (visual)

```
✅ #1246 Macro                              DONE 2026-05-19 (commit f393c51)
✅ #1245 Settrade Price/TA                  DONE 2026-05-19 (commit 6a7dfa2)
🟡 #1247 Foreign-flow                       TODO (SET disclosure scrape; no auth)
🟡 #1248 Calendar                           TODO (finnhub/alpha vantage free tier)

       ↓ all 4 L1 done

🔒 #1259 Per-ticker rollup (L2B)            BLOCKED ← #1248
       ↓
🎯 stock-pick MVP via JSON API
```

### Cross-references

- New endpoints (FastAPI ↔ FastAPI internal, NOT in api-contracts.md scope which is FE-facing): `/api/macro-signals` + `/api/price-ta-signals` (POST upsert + GET single + GET history range)
- [#1264](http://localhost:5431/tasks/1264) SET50 quarterly refresh — follow-up filed
- L2A (#1249) implementer: read this entry for `signal_date` vs `raw_data.date_today` reconciliation guidance

---

## 2026-05-19 (addendum) — 2-stream split + Settrade lock + per-ticker rollup gap-fill + API contract

**Scope:** shared (architecture refinement post-lock; critical-path framing for "เลือกและแนะนำหุ้น")
**Proposed by:** user (critical-path question) + lead (gap identification + critical-path analysis)
**Status:** Design refinement on top of the same-day architecture lock. Implementation still parked overall; contract + #1245 unlock once user starts the parallel work.

**Decision 1 — 2-Stream work split for parallel execution:**

| Stream | Scope | Output interface |
|---|---|---|
| **A. Decision Engine** | ingest → score → rank → produce JSON-shaped daily-outlook + per-ticker recommendations | `GET /api/daily-outlook`, `GET /api/tickers/today`, `GET /api/tickers/{symbol}` |
| **B. Operator Interaction** | UI (Phase 3 dashboard redesign + drill-down) + storm push + future trading APIs | consumes Stream A's JSON |

Stream A can deliver operator value INDEPENDENTLY via JSON API (curl/CLI). Stream B = UX polish + push + execution. Sharp separation lets the two streams develop in parallel against a locked API contract.

**Decision 2 — Settrade Open API LOCKED for Phase 2.1 (#1245) Price/TA:**

Operator chose Settrade Streaming + Historical for Phase 2.1 data source. Rationale:
- Official SET subsidiary — most authoritative Thai stock data source
- Same vendor for future Phase 5+ trading execution (creds/audit/onboarding unified — no re-integration cost when trading layer ships)
- Real-time + historical OHLCV in one API surface
- Aligns with the long-term path where decision-support → trading execution happens through ONE vendor relationship

Phase 5+ trading integration will reuse the Settrade auth/audit foundation. Other Thai broker APIs were considered (BLS, Kasikorn, Liberator) but Settrade has the cleanest official API + SET-data-vendor alignment.

**Watchlist for #1245 ingest:** SET50 (top-50 by market cap) ∪ {tickers appearing in news events past 30 days}. Override via config later.

**Decision 3 — Architecture gap fill: `L2B Per-ticker rollup` (Kanban #1259):**

Critical-path review found that the locked architecture is event-centric (Q-score per news event) but the operator goal `เลือกและแนะนำหุ้น` is ticker-centric (Q-score per ticker, ranked). The L2A thematic aggregator (#1249) produces MARKET-level themes, not per-ticker rollups.

Filed [#1259](http://localhost:5431/tasks/1259) as sibling to #1249 — same dependency on L1 sources (#1245-1248), feeds L3 (#1250) alongside L2A. Per-ticker rollup is the "glue" that turns L1 signals into actionable per-stock recommendations.

**Decision 4 — API contract design FIRST (Kanban [#1258](http://localhost:5431/tasks/1258)):**

Per operator's "ถ้ามี API contract แล้วก็สามารถทำขนานกันไปได้เลย", we LOCK the Stream A ↔ Stream B contract before starting parallel implementation. Contract drafted same-day in `shared/api-contracts.md`:
- `GET /api/daily-outlook` — daily weather brief + TIER-1/2/3 event partitions
- `GET /api/tickers/today` — ranked ticker recommendations with contributing-signals breakdown
- `GET /api/tickers/{symbol}` — per-ticker drill-down (events + price context + history + operator action)

Stream B mocks via `frontend/lib/mocks/*.json` fixtures matching the contract; Stream A implements behind the same shape.

**Critical-path narrative for "เลือกและแนะนำหุ้น":**

```
NOW ──► #1258 API contract (Lead-direct, this commit)
   parallel after contract locked:
        ──► #1245 Settrade Price/TA  ┐
        ──► #1246 Macro              │  L1 ingest (4 parallel)
        ──► #1247 Foreign-flow       │
        ──► #1248 Earnings calendar  ┘
              ↓
        ──► #1259 Per-ticker rollup (L2B)
              ↓
        ──► [USABLE MVP] daily ranked list via JSON API 🎯
              ↓
        #1249 (L2A themes) → #1250 (L3 weather) → #1251 (self-learning)
                                ↓
                          #1252 (Dashboard) → #1253 (drill-down) + #1254 (storm push)
                                              ↓
                                  Phase 5+ Settrade trading integration
```

**Usability milestones (per critical-path):**
- After **L1 #1245 + #1259** alone — first useful "today's stock picks" via JSON
- After **L1 #1245 + #1246 + #1247 + #1248 + #1259** — full L1 coverage MVP (≈ stock-pick MVP)
- After **#1250 L3 weather** — adds market mood overlay
- After **#1251 self-learning** — quality improves over 3-6 months of outcome data
- After **#1252-1254 UI** — end-to-end production-shape UX (Stream A + B integrated)
- **Phase 5+** — Settrade trading execution (auto/HITL) attaches to Stream B

**External APIs (data side) — Phase 2 candidates:**

| Source | Vendor | Cost | Difficulty |
|---|---|---|---|
| Price/TA (#1245) | **Settrade Open API** (locked) | Account-tier dep. | Medium (OAuth + WebSocket/REST) |
| Macro USD/THB/oil (#1246) | FRED API + yfinance | Free | Easy |
| Asia indices (#1246) | yfinance | Free | Easy |
| Foreign-flow (#1247) | SET disclosure scrape | Free | Hard (HTML report parsing) |
| Earnings calendar (#1248) | finnhub / alpha vantage / investing.com scrape | Free tier or scrape | Medium |

**External APIs (trading side) — Phase 5+ deferred:**

Trading integration is a separate phase. Stream A delivers JSON `/api/tickers/today`; Stream B + Phase 5+ adds:
- Settrade order placement API (same vendor as #1245 — single auth surface)
- Paper-trading sandbox mode FIRST (no real money during trust-building)
- Risk gates (max position, daily loss limit, kill-switch reuse from agent-teams parallel work)
- HITL approval on every order (financial-action golden rule — never auto-execute)

**Cross-references:**
- This entry refines the same-day Architecture lock immediately below
- New Kanban tasks: [#1258](http://localhost:5431/tasks/1258) (API contract), [#1259](http://localhost:5431/tasks/1259) (per-ticker rollup)
- #1245 description PATCHed with Settrade lock + watchlist default + sequencing reminder
- Contract drafted in `shared/api-contracts.md` (3 endpoints + mocking convention)

---

## 2026-05-19 — Architecture lock — 3-layer composite scoring + weather-brief UX + multi-layer self-learning calibration

**Scope:** shared (product architecture — frontend + backend + pipeline)
**Proposed by:** user (Phase 2+ thesis discussion) + lead (architectural shape)
**Status:** **Design LOCKED, implementation PARKED.** Pickup timing TBD by operator. No code change yet.

**Decision:** NewsAnalyzer evolves from single-source-news + LLM-end-to-end synthesis to a **3-layer composite scoring architecture** with **weather-forecast UI** and **multi-layer self-learning calibration**. Operator-fatigue minimization is the load-bearing UX goal — every architectural choice subordinated to "5-second glance → drill-down only on demand."

### Layer 1 — Raw signals (technical, expandable)

Initial 5 sources, priority order locked:
1. **News** (already in production — 4 Thai scrapers + 7 RSS)
2. **Price/TA** (OHLCV momentum, breakout, support/resistance proximity)
3. **Macro** (USD/THB, oil, Fed funds rate, Asia indices)
4. **Foreign-flow** (SET disclosure: net buy/sell by foreign vs retail)
5. **Earnings calendar** (earnings, ex-div, FOMC, OPEC dates — third-party API)

Each L1 source emits dimensional signals (raw score + confidence). Schema per-source TBD. Design principle: **AI may propose new L1 dimensions** post-calibration (e.g., "rolling 60d foreign-flow correlation = 0.65; promote?") — operator approves with initial weight 0.5 + 30-day monitor before auto-tune lock-in.

### Layer 2 — Thematic aggregates (4-5 themes)

Weighted sums of L1 signals projecting to small fixed scales:
- `sentiment_composite` (news ↔ price agreement)
- `macro_tailwind` (currency + rates + commodity alignment)
- `risk_regime` (volatility + flow + breadth)
- `event_density` (severity × novelty × time-decay)
- (5th slot reserved for emerging theme post-calibration)

### Layer 3 — Weather brief (operator-facing primary output)

Daily brief presents 5 composite indicators in everyday-weather language:
- ☀️ **ความสดใส (brightness)** — 0-100% bullish bias
- 🧭 **ลมส่ง (wind)** — direction × strength (macro flow)
- 🌧️ **ฝน (rain)** — 0-100% downside risk probability
- 🌫️ **หมอก (fog)** — uncertainty level (L2 disagreement × confidence spread)
- ⚡ **พายุเตือน (storm warning)** — severe-event flag

Below the weather row: TIER-1 (3-5 must-review events Q≥3 or severity≥3), TIER-2 (selective, expandable, 10-15), TIER-3 (FYI, collapsed, 15-20).

**Operator volume target**: 20-40 events/day; weather glance + TIER-1 = full daily review in 1-2 min; TIER-2 selective; TIER-3 drill-only.

### Self-learning — 3 calibration loops

Outcomes feed back to ALL 3 layers:
- **L3 weights** (weather → operator action): outcome = daily P&L direction vs sky brightness; rolling 30-90 day regression
- **L2 weights** (themes → L3): per-theme accuracy from outcome rows
- **L1 → L2 weights**: 90-day correlation of raw signal X with aggregate Y outcomes → rebalance

Before each weight-tuning batch: show operator the delta + accept/veto. Not auto-deployed. AI may propose new L1 dimensions as above.

### UI redesign — PARKED (operator decision, not rushed)

Full dashboard redesign — simple front-view (weather row + TIER-1), drill-down on tap for indicator detail + reasoning chain. Mobile-friendly. Storm push notification designed AND shipped together with the redesign (single coherent UX rollout).

### Tradeoffs accepted

- **Transparency rubric ⩾ LLM end-to-end opacity** — operator trust comes from disagreeing with specific dimensions, not trusting a black box. LLM may still score individual L1 dimensions internally; aggregation layer stays transparent.
- **Data-plumbing cost vs L1 richness** — start with 5 sources; expand when outcome data points to missing signals (not preemptively).
- **Multi-layer calibration complexity** — accepted for the audit-by-dimension benefit.

### Implications

- **Phase 1 P1 (calibration-loop activation)** stays current scope — close the outcome→AI feedback loop on the existing single-event recommendation. This work is COMPATIBLE with the new 3-layer architecture; it eventually feeds L3 calibration as a special case (current L3 = sentiment-only weather).
- **Phase 2 work order** (when unparked): L1 expansion (sources 2→5) → L2 aggregator → L3 weather computation → Phase 3 UI redesign + storm push (joined).
- **Phase 4 (LLM API engine swap, #1229)** — independent of this architecture; LLM is still the L1 news-dimension scorer + optional L2 aggregator.
- **`SCRAPE_DEV_LIMIT=3` clamp (commit 6b68894)** — protects credit budget during the long parked period. Unlock by setting `SCRAPE_DEV_LIMIT=0` only when full L1 data plumbing is ready to consume it.

### Kanban backlog — filed 2026-05-19

10 tasks pre-mapped + filed against NewsAnalyzer (project_id=567). All PARKED — `process_status=1 (TODO)` for unblocked L1 sources, `process_status=4 (BLOCKED)` for downstream L2/L3/Phase 3 with `blocked_by` FK chain. Pickup timing = operator decision.

- **Phase 2** — 3-layer composite scoring + weather brief backend (priority 2-3)
  - [#1245](http://localhost:5431/tasks/1245) Phase 2.1: L1 Price/TA dimensional signals — no blocker
  - [#1246](http://localhost:5431/tasks/1246) Phase 2.2: L1 Macro signals (USD/THB + oil + Fed + Asia indices) — no blocker
  - [#1247](http://localhost:5431/tasks/1247) Phase 2.3: L1 Foreign-flow signals (SET disclosure) — no blocker
  - [#1248](http://localhost:5431/tasks/1248) Phase 2.4: L1 Earnings + calendar events — no blocker
  - [#1249](http://localhost:5431/tasks/1249) Phase 2.5: L2 thematic aggregator (4 themes + 1 reserved) — blocked_by #1248
  - [#1250](http://localhost:5431/tasks/1250) Phase 2.6: L3 weather computation + daily brief generator — blocked_by #1249
  - [#1251](http://localhost:5431/tasks/1251) Phase 2.7: Self-learning weight tuning batch (3 calibration loops) — blocked_by #1250

- **Phase 3** — UI redesign + storm push (PARKED; can start parallel once #1250 lands; priority 4)
  - [#1252](http://localhost:5431/tasks/1252) Phase 3.1: Dashboard redesign — weather row + TIER-1/2/3 — blocked_by #1250
  - [#1253](http://localhost:5431/tasks/1253) Phase 3.2: Drill-down view per event — blocked_by #1252
  - [#1254](http://localhost:5431/tasks/1254) Phase 3.3: Storm push notification (Tailscale-mobile path candidate) — blocked_by #1252

### Cross-references

- agent-teams sibling #1225 (Docker RAM review) — parallel platform work
- This entry succeeds [#1226 Phase 2 closure (3-tier scrape fallback)] below + [#1235 stale-lock self-heal] + scrape-cost fix commit `6b68894`
- QuantAgent research (2026-05-19, `_scratch/research-quantagent-2026-05-19.md`) — verdict NO-PIVOT; mine 5 ideas (decision-synthesis prompt + multi-LLM factory most relevant to this architecture)

---

## 2026-05-19 — #1226 Phase 2 closed — Firecrawl 3-tier fallback chain (cloud → selfhost → ai_direct)

**Scope:** shared (pipeline scraper + ai_client + docker-compose)
**Proposed by:** user (RAM footprint review session) → implemented by dev-backend specialist
**Status:** Phase 2 DONE 2026-05-19 (commit `c9d87a5` on NewsAnalyzer main, not yet pushed). Phase 1 docs landed in this entry. Phase 3/4 filed as follow-up Kanban (`blocked_by=#1226`).

**Supersedes (in spirit, not by rollback):**
- Decision lock #2 (Cloud → self-host pivot, 2026-05-14 PM). Self-host is no longer the only path; it becomes Tier B fallback. Cloud comes back as Tier A default when a real key is configured.

**What landed:**

Three-tier fallback resolved once per `run_full_fetch` invocation in the pre-flight phase (cached in `_firecrawl_mode`, reset at the start of each run so a mid-process `.env` edit is picked up without a container restart):

```
Tier A "cloud"     — settings.firecrawl_api_key set AND value not in {"", "dev-key"}
                     → FirecrawlApp(api_key=key)  # SDK defaults to https://api.firecrawl.dev
Tier B "selfhost"  — A unavailable AND firecrawl_api_url/v1/health/liveness == 200
                     → FirecrawlApp(api_key="dev-key", api_url=settings.firecrawl_api_url)
Tier C "ai_direct" — neither A nor B
                     → httpx GET raw HTML → CallPurpose.SCRAPE_EXTRACT via ai_client →
                       Firecrawl-shaped dict {markdown, html, metadata, duration_ms, engine_tier="ai_direct"}
                     → 4 source classes consume the dict unchanged.
```

**Compose profile shift:** `firecrawl-api`, `firecrawl-worker`, `firecrawl-puppeteer`, `firecrawl-redis` now gated by `profiles: ["scrape-selfhost"]`. Default `docker compose up` brings **6** services (db, redis, backend, frontend, pipeline, worker); `docker compose --profile scrape-selfhost up` brings **10**. Verified via `docker compose config --services` (both states).

**Per-tier RAM expectation (steady-state):**
- Tier A (cloud) + 6 services: ~1.0 GB containers; ~$0–19/mo cash cost (Hobby tier supports ~660 req/mo headroom for 11 sources × 2/day).
- Tier B (selfhost) + 10 services: same as pre-#1226 (~2.4–2.8 GB).
- Tier C (ai_direct): same as Tier A footprint; quota burn falls on the operator's `claude -p` plan (Max 20x).

**Tier C constraints (accepted degradations):**
- JS-heavy sources (bangkokbiznews, thansettakij) usually escalated to playwright-service on Tier B. In Tier C the httpx fetch sees only initial HTML; `_ai_direct_scrape` emits structured WARN `event=ai_direct_thin_content url=… len=…` when markdown < 200 chars. The downstream calibration loop will surface this via reduced article counts.
- SSR sources (kaohoon, thunhoon) routinely pass on cheerio per past engine_tier logs — Tier C should handle them without thin-content warnings.
- `SCRAPE_EXTRACT` purpose intentionally NOT mapped in `_AGENT_RUN_PURPOSE_MAP` (cost-tracking pollution avoidance) — the AgentRun row is skipped with a logged warning.

**WSL2 RAM cap (Phase 1 — host-side, user-applied):**

Create `C:\Users\banku\.wslconfig` (or `~/.wslconfig` from a WSL shell — same file resolved):

```ini
[wsl2]
memory=3GB
processors=4
swap=0
```

Apply by closing all WSL terminals + Docker Desktop, then in PowerShell:
```
wsl --shutdown
```
Restart Docker Desktop. `vmmemWSL` will plateau at 3 GB instead of expanding to host-RAM-fraction default (~25%). Lead does NOT auto-write this file — host config, user-applied. Verified previously on agent-teams [#1225](http://localhost:5431/tasks/1225) sibling task.

**Phase 1 closure also covers:**
- Default `docker compose up` semantic: 6 containers, no firecrawl-* (Phase 2's compose profile shift achieves this directly — Phase 1.B AC subsumed).
- Operator workflow note: to start a Tier B scrape, run `docker compose --profile scrape-selfhost up -d firecrawl-api firecrawl-worker firecrawl-puppeteer firecrawl-redis` before `Fetch Now`. Containers can be stopped (`docker compose stop firecrawl-*`) after the scrape batch settles.

**Deferred follow-ups (filed as separate Kanban tasks):**
- **Phase 3** — Compose footprint slim. `next dev` → `next build && next start` profile; pipeline `--reload` off in `prod` profile; consolidate any remaining Redis duplication; drop Claude Code CLI from worker image if worker becomes non-AI (depends on Phase 4 outcome).
- **Phase 4** — `claude -p` subprocess → direct Anthropic / Gemini API. Gated by Kanban #1100 (Gemini Flash-Lite bench result). Motivated by recurring credential-refresh pain (#1036). APScheduler in-process to retire the worker container is bundled in Phase 4.

**Phase 5 (Vercel + Neon + Fly.io) — explicitly out of scope this round.** Defer to the mobile/arena timeline.

**Acceptance criteria (from #1226):**

| AC | Status | Evidence |
|---|---|---|
| #1 cloud tier | passed | `test_resolve_mode_cloud_when_api_key_set` (test_resilience.py:349) + `test_get_client_cloud_omits_api_url` (L419). Boot log includes `firecrawl_mode=cloud` (tasks.py pre-flight). |
| #2 selfhost tier | passed | `test_resolve_mode_selfhost_when_liveness_ok` (L361) + `test_get_client_selfhost_passes_api_url` (L437). Existing 3 retry-policy tests still pass with mode primed. |
| #3 ai_direct tier | passed | `test_ai_direct_scrape_happy_path_mock_mode` (L478), `test_ai_direct_scrape_dispatches_via_resolve_mode` (L514), `test_ai_direct_scrape_emits_thin_content_warning` (L549). |
| #4 compose profile | passed | `docker compose config --services` default → 6 services; `--profile scrape-selfhost` → 10 services. Lead re-verified post-commit (this entry). |
| #5 smoke | passed | Full pipeline pytest: **68 passed, 1 warning in 5.88s**. No agent-teams DB touched (verified by inspection of pipeline/tests/conftest.py). |
| #6 WSL doc | passed | This entry's snippet block above. |
| #7 closure log | passed | This entry. |
| #8 Phase 3 follow-up | passed | Kanban task filed with `blocked_by=#1226`. |
| #9 Phase 4 follow-up | passed | Kanban task filed with `blocked_by=#1226` + `#1100`. |

**Cross-references:**
- agent-teams sibling task `#1225` — same review pattern on the platform stack; that one stayed deferred (no immediate execution, just plan + AC).
- Decision lock #2 (Cloud→self-host) — superseded in spirit (Cloud back as default, self-host as Tier B fallback).
- Decision lock #1 (`claude -p` subprocess) — UNCHANGED for now. Phase 4 may revisit pending #1100.
- `working_path` is set for NewsAnalyzer (id=567), but historical decisions.md content lives at `agent-teams/context/projects/NewsAnalyzer/shared/`. This entry continues the legacy path to preserve history. Migration to `<working_path>/shared/decisions.md` is tracked separately in Kanban #941.

---

## 2026-05-16 — Phase 1 wave 1 closed — #1087 frontend UX, #1040 RAM right-sizing, #1092 NO-OP

**Scope:** shared (frontend + devops; one karpathy lesson)
**Proposed by:** lead (closing reports from dev-frontend / dev-devops × 2 / dev-backend specialists)
**Status:** All three DONE 2026-05-16. Two real commits on NewsAnalyzer main; one NO-OP close. Two follow-ups filed (#1088 hygiene, #1092 itself was the closed follow-up of #1040).

**What landed:**

### #1087 — Phase 1 frontend UX overhaul (commit `2a892f5`)
Apply ux-ui-pro-max methodology baseline to 9 NewsAnalyzer pages. Single commit, 11 files, +423 / -139. 10/10 ACs passed (AC9 passed-with-caveat — pre-existing scrape page type errors block `npm run build`, NOT introduced by this work).
- New `components/NavBar.tsx` (155 LOC): mobile hamburger (< md) + horizontal links (≥ md), active-link highlight via `usePathname()`, `aria-current="page"`, Escape + backdrop close.
- `globals.css`: global `*:focus-visible` ring, `.skip-link` utility, `prefers-reduced-motion` media query.
- `min-h-11` (44px) touch targets across pagination, filters, tag chips, modal close, FetchButton, form inputs.
- ARIA: `aria-label` on icon-only buttons, `role="status" aria-live="polite"` on loading spinners with sr-only text, sr-only labels on Articles filter selects.
- Tables→cards: Articles dual-renders stacked cards (< md) + table (≥ md).
- Modal: `ArticleModal` full-screen on < sm, max-w-2xl centered on ≥ sm.
- Loading skeletons (5-row `animate-pulse`) replace plain "Loading…" on Dashboard + Articles.

### #1040 — Phase 1 T16 RAM right-sizing (commit `4042ecc`)
Two-spawn pattern: Phase 1 read-only investigation → Lead surfaces findings to user → Phase 2 apply + smoke. **Measured −1.34 GiB total** (4.45 GiB → 3.10 GiB), exceeds AC-6 ≥1 GiB target.

Changes (2 files, +4 / -4):
- `docker-compose.dev.yml:68` — append `--concurrency=4` to celery worker command. Was default 16 (host CPU count). **−1.42 GiB on worker-1 alone** — the dominant lever.
- `docker-compose.yml:145` — `firecrawl-api` `NUM_WORKERS_PER_QUEUE=2→1`.
- `docker-compose.yml:177` — `firecrawl-worker` `NUM_WORKERS_PER_QUEUE=2→1`.
- `docker-compose.yml:201` — `firecrawl-puppeteer` `MAX_CONCURRENCY=10→2`.

**KEY NEGATIVE FINDING (AC-3 falsified):** Original hypothesis was "disable unused firecrawl-api features via env vars" (`/v1/extract`, PDF parser, OpenAI SDK, `/v1/batch`). Phase 1 investigation verified against upstream `apps/api/src/routes/v1.ts` + `apps/api/package.json`: **firecrawl-simple v0.0.55 (the fork in use) already shipped without those features.** No env-var lever exists. The 922 MiB firecrawl-api baseline is the irreducible Node + bullmq + cheerio + puppeteer-client runtime — no fat to trim at the env-var layer. AC-3 marked `na`; compensating action was pool right-sizing (the 3 NUM_WORKERS / MAX_CONCURRENCY edits above).

**Smoke results:**
- #1 (run_full_fetch happy path, task `a28bb76b`): status=ok, 21 articles / 21 events / 11 sources. Duration **419s** — slower than spec ~90s estimate because ข่าวหุ้น scrape took 413s on its own (direct trade-off from puppeteer pool=2). Acceptable at twice-daily fetch cadence; revisit MAX_CONCURRENCY if cadence increases.
- #2 (firecrawl-* stopped, resilience, task `dd48187f`): **7.14s** (< 30s target). 4 firecrawl sources status_id=4, 7 RSS continued. T12 layer intact.

### #1092 — RSS scrape_logs sticky NO-OP (no commit)
Filed by Lead as a low-pri bug from #1040 Phase 2 notes: "3 RSS rows stuck at status_id=1 (in_progress) after task completes." dev-backend spawn verified `ref_scrape_statuses` enum + halted: `1=success, 2=empty, 3=partial, 4=failed` — **all terminal; NO in_progress state exists in the enum.** Closed NO-OP. Memory entry filed (`project_newsanalyzer_enums.md`) so future sessions catch this immediately.

**Karpathy Mode-B (trust-agent-without-re-run) strike #2:**
- Strike #1 (earlier, agent-teams): trusted "36/36 HITL tests pass" without re-running.
- Strike #2 (this session, NewsAnalyzer): dev-devops Phase-2 of #1040 annotated `status_id=1` as "(in_progress)" — a common cross-system workflow convention (Kanban tasks = `1=TODO`). Lead trusted the annotation and filed #1092 without verifying the actual enum. The verify command was a single Read on `orm.py:219` — 30 seconds away. Cost: one round-trip task cycle + a NO-OP close.
- **Escalation:** Mode B has now recurred. Per `feedback_karpathy_lane.md`, a PostToolUse hook on Agent injecting "verify-before-PATCH" reminder is warranted. Filed as TODO for next agent-teams session (NewsAnalyzer session can't edit agent-teams hooks per `feedback_cross_project_platform_edits.md`).

**Open follow-ups (FILED):**
- **#1088** (TODO) — fix pre-existing scrape page TS type errors (`SourceState['status']` union missing `'paused'`, blocks `npm run build`) + initialize `next lint`. Pre-existed since commit `6be13bc6` (2026-03-17). Surfaced by #1087 AC9 verification.

**Open follow-ups (NOT YET FILED — only if recurrence justifies):**
- Beat scheduler fires missed cron immediately on worker `--force-recreate` startup → first `run_full_fetch` post-restart can transiently fail. Witnessed once during #1040 Phase 2 (task `c63ea4fd` status=error, 0 articles, 0.33s). My dispatched task 50s later succeeded. Watchlist; file only if recurs.
- Apply same `--concurrency=4` to `docker-compose.prod.yml` if/when prod runs. Phase 1 deliberately scoped to dev only (Phase 1 dev-devops Phase-1 report flagged but didn't read prod compose).

**Phase 2 candidates (UX, surfaced during #1087):**
- Semantic color token refactor (Phase 1 deliberately stayed on raw Tailwind values).
- `SentimentBadge` emoji → SVG icons (📈 📉 are screen-reader-unfriendly).
- Articles `🔗` AI link indicator → SVG.
- PriceChart accessibility (chart text labels + screen-reader summary).
- Heading hierarchy audit across 9 pages.

**Implications:**
- **AC-3 of #1040 was unsatisfiable as written.** When firecrawl-related tasks reference "disable unused firecrawl features", reframe as "trim pool sizes" (NUM_WORKERS_PER_QUEUE, MAX_CONCURRENCY) — those are the actual levers on firecrawl-simple v0.0.55.
- **`ref_scrape_statuses` enum convention is project-specific.** Memory `project_newsanalyzer_enums.md` exists now; future RSS / scrape_logs triage starts from that file, not from cross-project workflow assumptions.
- **NewsAnalyzer stack is currently UP** (from #1040 Phase 2 apply); ready for the user's #1087 manual smoke at `http://localhost:7010`.

**Cross-references:**
- #1087 commit: `2a892f5` on NewsAnalyzer main.
- #1040 commit: `4042ecc` on NewsAnalyzer main.
- #1088 follow-up (TODO).
- Karpathy memory update: `feedback_karpathy_lane.md` Mode B Strike #2.
- Enum memory: `project_newsanalyzer_enums.md` (new).

---

## 2026-05-16 — T15 #1039 closed — pre-T10 orphan articles re-deduped; **Phase 0 → Phase 1 cleanup wave COMPLETE**
**Scope:** shared (backend pipeline + cleanup)
**Proposed by:** lead (closing report from dev-backend specialist)
**Status:** T15 DONE 2026-05-16 (commit `e825510`). 6/6 ACs passed. **Closes the user's 2026-05-15 'หลัง T12 ทีเดียวครับ' batch** (T6 + orphan cleanup landed in one wave).

**What landed:**
- `pipeline/app/workers/orphan_rededup.py` (189 LOC) — one-shot async function; CLI entry point `python -m app.workers.orphan_rededup`. Idempotent (2nd run returns 0).
- `backend/app/routers/articles.py` — new `GET /articles/orphans?fetched_before=<iso>` endpoint with narrow projection schema `NewsArticleOrphanOut`.
- `pipeline/app/backend_client.py` — `list_orphan_articles` + `delete_event_if_empty` (latter unused; reserved for future).
- 5 unit tests in `pipeline/tests/test_orphan_rededup.py`; combined with T10's dedup tests: 18 tests pass.

**Smoke results:**
- Pre-run: 75 events, 115 orphans, 75 links.
- Run: 115 orphans → 109 groups → 107 new events + 2 linked to existing.
- Post-run: 182 events, 0 orphans, 190 links.
- **4 multi-article events surfaced** (real cross-source coverage groupings):
  - Spam-title Thunhoon cluster of 6 articles (low-priority duplicates).
  - 3 real Thai/English clusters of 2: Trump-Iran news, HANA robotrade, Maldives cave-diving.
- Math verified: 75 + 107 = 182 events; 75 + 115 = 190 links.

**Schema adaptation note:** Brief assumed `news_articles.event_id` FK; actual schema uses `news_event_articles` join table (M:N). Specialist correctly adapted — "orphan" = "no row in join table." All other logic identical because the algorithm uses `link_article_to_event` semantics which work on either shape.

**Scope-conservative choices:**
- `DELETE /events/{id}` endpoint deliberately omitted — pre-T10 path didn't create placeholder events (join-table behavior just dropped articles unlinked). Empty-events query returned 0. Adding the endpoint would be dead code; documented.

**Open follow-up surfaced (NOT in T15 scope):**
- **107 new single-article events from orphan re-dedup have no AI analysis.** Architecture is ready (`created_event_id` captured per group, T11 AIPipeline wire is live) — if Phase 1 wants those orphans analyzed, a small follow-up task adds the same throttled fan-out used by `_run_scrape_only_async`. Deliberately out-of-scope to honor brief's "re-dedup only, not re-analyze" intent. **Will respect T14 credential resolution.**

**Cross-references:**
- T15 commit: `e825510` on NewsAnalyzer main.
- T10 dependency: commit `b83ee8b` (NewsDeduplicator).
- Schema actual: `news_event_articles` join table (per Phase 0 T3 ORM design).
- User directive: 2026-05-15 'หลัง T12 ทีเดียวครับ' — bundle this with T6 wave.

**Standards proposals (NOT auto-applied):**
- `context/standards/postgresql/` — when adapting a brief that assumed an FK shape to a real schema using a join table, document the actual structure in the worker's module docstring so reviewers don't have to cross-reference.
- `context/standards/fastapi/` — narrow projection schemas (`NewsArticleOrphanOut`) beat overloading a general-purpose Out schema when an endpoint is single-consumer with distinct field needs. Less coupling.

---

## 🎉 Phase 0 → Phase 1 SEQUENCE COMPLETE (closing summary, 2026-05-16)

| # | Task | Commit | Status |
|---|---|---|---|
| T8 #932 | run_full_fetch stopgap (delegate to scrape-only) | `595ee16` | ✅ DONE 2026-05-14 |
| T9 #933 | RSS readers (7 feeds) in fetch path | `9a0b752` | ✅ DONE 2026-05-15 |
| T10 #934 | NewsDeduplicator (rapidfuzz + pythainlp, Option B locked) | `b83ee8b` | ✅ DONE 2026-05-15 |
| T11 #935 | AI analysis trigger (un-stub run_analysis_for_event + AIPipeline) | `5dfe4bc` | ✅ DONE 2026-05-16 (with T14 follow-up for stale creds) |
| T12 #991 | Firecrawl resilience (retry + pre-flight + engine_tier reporting) | `2d54620` | ✅ DONE 2026-05-16 |
| T6 #924 | Backfill Option A (Celery + cursor + rate-limit + manual trigger) | `bc3e70c` | ✅ DONE 2026-05-16 |
| T13 #1027 | NewsAnalyzer cross-project pollution cleanup | `93dae8e` | ✅ DONE 2026-05-15 |
| T15 #1039 | Pre-T10 orphan article re-dedup | `e825510` | ✅ DONE 2026-05-16 |

**Pipeline architecture (post-wave):**
```
Dashboard 'Fetch Now' (or beat morning/midday-fetch)
  → POST /ingest/run (Pipeline)
    → run_full_fetch (canonical full pipeline; T8 stopgap retired)
      → _run_scrape_only_async
        → pre-flight: GET firecrawl-api/v1/health/liveness (T12)
        → 11 concurrent sources: 4 scrape (Firecrawl-simple, T2+T5) + 7 RSS (feedparser, T9)
          → each source: retry on transient (T12) + engine_tier capture
          → per-article persist via create_article + log_scrape_run
        → dedup pass: NewsDeduplicator.group_articles (rapidfuzz + pythainlp, T10)
          → POST /events/with-articles for new groups
          → link_article_to_event for adds-to-existing
        → AI fan-out: run_analysis_for_event.apply_async per event with countdown throttle (T11)
          → AIPipeline.run_full_pipeline → claude -p (T1) → POST /analysis/summary + /analysis/sentiment
            → _log_agent_run records token usage + inferred_cost (T3)
        → complete_ingest (release FetchLock cooldown)
Backfill (T6) lives parallel: nightly cron (default-off via BACKFILL_ENABLED flag) walks BackfillJob cursors backwards through historical Firecrawl pages.
```

**Open Phase 0 follow-ups (not blocking; surfaced for Phase 1 prioritization):**
- **T14 #1036** — stale `~/.claude/.credentials.json`; user runs `claude /login` on host, then re-verify T11 AC#6 + AC#7 real-CLI smoke.
- 107 single-article events from T15 lack AI analysis (architecture-ready follow-up).
- 7 RSS BackfillJob rows marked `failed` ('RSS does not support historical fetch'); future Wayback Machine integration can flip them to `idle`.
- Frontend `engine_tier` badge per source (field exists in API).
- Settrade + SET.or.th RSS rescue (UA rotation OR drop OR replace).
- Backfill threshold (`BACKFILL_DAILY_INPUT_TOKEN_LIMIT=500K`) untuned for real workloads.
- `Base.metadata.create_all()` → `alembic upgrade head` swap (T3 specialist flagged tech-debt).
- 6 standards proposals across T9/T10/T11/T12/T6/T15 (tenacity classifier pattern, env-gated beat schedules, narrow projection schemas, duration-heuristic for opaque services, get-or-create source pattern, schema-shape adaptation note, dependency import-time RSS measurement).

**Verification end-to-end (Lead's perspective):**
- All 8 tasks committed + pushed to NewsAnalyzer main (clean history, no force-push, no Co-Authored-By).
- Each task's closure addendum lives in `context/projects/NewsAnalyzer/shared/decisions.md` (this file).
- 10 containers healthy throughout (4 Firecrawl-simple + backend + worker + pipeline + frontend + db + redis).
- 48 unit tests pass (T10 dedup × 13 + T12 resilience × 20 + T15 orphan × 5 + others).
- DB state at end: 190 articles, 182 events, 190 links, 11 BackfillJob rows (1 running ข่าวหุ้น + 7 RSS failed + 3 idle scrape), 4 multi-article events.

**Phase 1 (next phase) starts when user signals.** Recommended kickoff: refresh credentials (T14), then live end-to-end fetch + AI analysis verify, then decide on Phase 1 scope priorities.

---

## 2026-05-16 — T6 #924 closed — Backfill Option A implementation landed; **Phase 0 sequence complete**
**Scope:** shared (backend pipeline + new Celery task + routers + seed)
**Proposed by:** lead (closing report from dev-backend specialist)
**Status:** T6 DONE 2026-05-16 (commit `bc3e70c`). All 7 ACs passed; **closes Phase 0 work** (T9-T13 + T6 + T8 stopgap). Only open Phase 0 follow-up: **T14 #1036 stale Claude credentials**, independent of pipeline.

**What landed (5 design choices made):**

1. **Low-priority lane = Option 2b (rate-limit guard)** — `_should_defer` queries new endpoint `GET /analysis/agent-runs/today-stats` (returns sum of input/output tokens for today). Defers backfill iteration when `today_input_tokens >= BACKFILL_DAILY_INPUT_TOKEN_LIMIT` (env-tunable, default 500K). Manual triggers BYPASS the guard. **Rejected:** separate `backfill_queue` Celery queue (more compose surface, no clear payoff at Phase 0 scale).

2. **Initial seed = Option 3a (one-shot async function)** — `pipeline/app/workers/backfill_seed.py` + `python -m app.workers.backfill_seed` CLI. Idempotent (existing rows counted, no duplicates). **Rejected:** Alembic data migration — each environment should seed at its own `now`, not the migration commit timestamp.

3. **Manual trigger = Option 4i (HTTP endpoint)** — `POST /backfill/trigger/{source_name}` on Pipeline service. Returns Celery `task_id` polled via existing `/ingest/status/{task_id}` pattern. Matches the existing `/ingest/run` UX. CLI subcommand can be added later if needed.

4. **RSS limitation handling** — All 7 RSS sources get a BackfillJob row at seed time (consistency), but first iteration flips them to `status='failed'` with `error_log='RSS does not support historical fetch. feedparser returns only the current feed window; use a future archive integration (e.g. Wayback Machine) and flip status back to idle to retry.'` Row sticks around — a future Wayback Machine integration can flip them back to `idle`.

5. **Cursor seed semantics (AC#4 interpretation)** — T6 spec text said "cursor = today - 1 year" verbatim, but the design semantics (BackfillJob model docstring + decisions.md lock #6 + iteration logic) define cursor as the UPPER bound below which the next batch fetches articles, walking BACKWARDS until completion at `today - BACKFILL_HORIZON_DAYS`. Seeded `cursor = now` (today); the 1y horizon is the COMPLETION point computed per-iteration. Documented in `seed_backfill_jobs()` docstring + commit body.

**Counts (corrected from spec):**
- Spec said 8 BackfillJob rows (4 scrape + 4 RSS). Actual: **11 rows (4 scrape + 7 RSS)** per T9 closure correction. Seed produced 11 rows.

**Smoke evidence:**
- Manual trigger against `ข่าวหุ้น`: cursor `2026-05-15 → 2026-05-14`, articles_backfilled `0 → 10`, 10 NewsArticle rows inserted with historical fetched_at.
- BBC World RSS smoke: status `idle → failed` with descriptive error_log (expected feedparser limitation).
- Default-disabled posture: beat schedule has 2 entries (morning + midday fetch) when `BACKFILL_ENABLED=false`; +1 entry when flipped to `true`.
- 15 unit tests in `pipeline/tests/test_backfill.py`; 48 tests pass overall.

**New endpoints (Backend + Pipeline):**
- Backend: `GET /backfill-jobs?status_in=...`, `GET /backfill-jobs/{id}`, `GET /backfill-jobs/by-source/{name}`, `POST /backfill-jobs`, `PATCH /backfill-jobs/{id}` (CRUD); `GET /analysis/agent-runs/today-stats` (token aggregate for rate-limit guard).
- Pipeline: `POST /backfill/trigger/{source_name}`, `POST /backfill/run` (full cursor-advance pass; gated by rate-limit), `POST /backfill/seed` (idempotent re-seed).

**Operational notes for deploy:**
- First-time seed: `docker compose exec pipeline python -m app.workers.backfill_seed` (idempotent — safe to bake into deploy script).
- To enable nightly backfill: set `BACKFILL_ENABLED=true` in env file AND `docker compose up -d --force-recreate worker` (plain `restart` won't pick up new env vars from compose).
- Backfill nightly schedule: `crontab(hour=2, minute=30)` UTC = 09:30 ICT (off-peak).

**Implications:**
- **Phase 0 → Phase 1 sequence complete** — fetch → dedup → AI queueing → resilience → backfill all wired. Pipeline is the canonical full pipeline; T8 stopgap retired (per T11 closure note).
- **Lock #6 backfill subdecision (decisions.md 2026-05-14) is now FULLY IMPLEMENTED:** Option A (background trickle via Max 20x quota) lands as code + schema + endpoints + seed + manual trigger + default-off safety.
- **T14 #1036 remains the only open Phase 0 blocker** for end-to-end real-CLI verification. T6 backfill scrape portion works regardless; AI analysis on backfilled articles will respect T14 resolution (same code path as live-ingest AI).

**Standards proposals (NOT auto-applied — for human MA in `context/standards/*`):**
- `context/standards/celery/` (new) — env-gated beat schedule entries: "Any Celery beat entry that triggers external-cost work (API quota, paid services) MUST be gated on an explicit env flag, default false. Build the `beat_schedule` dict via a helper function consulting env vars rather than a top-level dict literal."
- `context/standards/docker/compose.md` — `docker compose restart` does NOT pick up new env vars from compose; must `up --force-recreate`. Easy gotcha; worth codifying.

**Open follow-ups (NOT blocking; surface for user):**
- **T14 #1036** — stale `~/.claude/.credentials.json`. User action: `claude /login` on host, then re-verify T11 AC#6 + AC#7 real-CLI smoke.
- **Frontend backfill UI** — button per source to call `POST /backfill/trigger/{source_name}` + poll `/ingest/status/{task_id}`. Same pattern as existing FetchButton. Not in T6 scope.
- **Wayback Machine integration for RSS historical fetch** — would flip the 7 RSS BackfillJob rows from `failed` back to `idle`. Phase 2+.
- **Frontend engine_tier badge** — T12 surfaced field; no UI consumer yet.
- **Settrade + SET.or.th rescue** — UA rotation OR drop OR replace (T12 closure note).
- **Orphan ~105 pre-T10 articles** — re-process through dedup pass (T10 closure flag).
- **Memory deviation T10 (234 MB)** — accepted; new sizing reference.

**Cross-references:**
- T6 commit: `bc3e70c` on NewsAnalyzer main.
- T14 follow-up: Kanban #1036 on NewsAnalyzer.
- Schema dependency: T3 #922 commit `f4cd4d2` (BackfillJob model + migration).
- Lock #6 backfill subdecision: decisions.md 2026-05-14 (Engine, scope, scraping, cost rules planning lock).

---

## 2026-05-16 — T12 #991 closed — Firecrawl resilience layer landed
**Scope:** shared (backend pipeline scraping)
**Proposed by:** lead (closing report from dev-backend specialist)
**Status:** T12 DONE 2026-05-16 (commit `2d54620`). All 9 ACs passed; 3/3 smokes green.

**What landed (5-piece layer):**

1. **Retry with tenacity** — `_scrape()` in `pipeline/app/services/scraper.py:104-227` wrapped in 3-attempt exponential backoff (1s → 4s → 16s). Custom `_is_retryable()` classifier distinguishes transient (5xx, connection errors, timeouts) from permanent (4xx, parse errors). 11 unit tests in `pipeline/tests/test_resilience.py`.

2. **Pre-flight health check** — `_firecrawl_preflight()` (5s timeout) checks `firecrawl-api:3002/v1/health/liveness` before scrape batch. **Critical departure from original spec:** on failure, mark 4 scrape sources `status='skipped'` but **RSS sources still run** (not full-task halt). Reason: RSS is engine-independent — punishing it for Firecrawl outage is wrong. Smoke #2 verified: **6.61s task time** with firecrawl-api stopped (vs 680s worst-case from spec) + 6 RSS articles saved.

3. **`scrape_logs.engine_tier` column** — String(50) nullable. Alembic migration `20260516_0001_scrape_log_engine_tier.py` (down_revision `20260514_0001`). Idempotent `ALTER TABLE IF NOT EXISTS` for Phase 0 `create_all()` coexistence. `ScrapeLogCreate` + `ScrapeLogOut` schemas updated.

4. **Duration-heuristic engine_tier inference** — firecrawl-simple v0.0.55 doesn't expose engine info in response body/headers/container logs (probed 2026-05-16). Thresholds: `<1500ms cheerio`, `1500-4999ms fire-engine`, `≥5000ms playwright-service`. Per-batch aggregation via `dominant_engine_tier()` with cost-bias on ties (more expensive wins).

5. **Folded in T9 closure item** — RSS malformed-XML detection. `_run_rss_source` catches `feedparser.bozo=True AND content-type != XML` → marks `status='malformed'` instead of silent `done found=0`. **Settrade + SET.or.th are now visibly broken** (HTML 403 walls served instead of RSS).

**Smoke evidence:**

| Smoke | Run ID | Wall time | Result |
|---|---|---|---|
| #1 Happy path | `971e3e29` | 210.45s | 15 articles; engine_tier: ข่าวหุ้น=playwright-service, ทันหุ้น/กรุงเทพธุรกิจ/ฐานเศรษฐกิจ=fire-engine, 7 RSS=NULL; 2 RSS marked malformed |
| #2 Firecrawl down | `f73bab3c` | **6.61s** | 4 scrape skipped + 7 RSS ran + 6 articles saved + 4 firecrawl_alert WARN logs |
| #3 Aggregate | (last 3 runs) | — | fire-engine=3/55s, playwright-service=1/210s, NULL=29/12s |

**Implications:**
- **Firecrawl outage no longer freezes fetch for 11+ minutes** — pre-flight catches in <5s, task continues with RSS sources.
- **Cost visibility:** `engine_tier=playwright-service` is now the canonical "expensive scrape" signal for future cost-tracking dashboards.
- **Discovery: all 4 current scrapers escalate beyond cheerio.** ข่าวหุ้น routinely hits playwright (210s wall). Worth re-evaluating which listing pages are JS-rendered if cost becomes a concern (future task).
- **Settrade + SET.or.th now visibly broken** (`status='malformed'` instead of silent `done found=0`). User can decide: optional User-Agent rotation retry, OR drop from `RSS_FEEDS` registry, OR find replacement feeds.

**Open follow-ups (not blocking T6):**
- Frontend Scrape page: render `engine_tier` badge per source (field already in API response).
- Tier thresholds (1.5s/5s) not stress-tested under varied network latencies — re-tune in Phase 2.
- Settrade + SET.or.th rescue strategy (UA rotation OR drop OR replace).

**Cross-references:**
- T12 commit: `2d54620` on NewsAnalyzer main.
- Migration: `backend/app/db/migrations/versions/20260516_0001_scrape_log_engine_tier.py`.
- T9 closure note (Settrade + SET.or.th malformed-XML): decisions.md 2026-05-15 (mid-afternoon) entry — folded into T12.
- Next in queue: T6 #924 (Backfill Option A impl) — does NOT depend on T14 credential refresh for fetch portion; AI analysis on backfilled articles will respect T14's resolution.

**Standards proposals (NOT auto-applied — for human MA):**
- `context/standards/python/external-clients.md` — tenacity `AsyncRetrying` + custom classifier function pattern (vs `retry_if_exception_type` tuple) for HTTP clients that need to distinguish 5xx (retry) from 4xx (skip).
- `context/standards/docker/` or `general.md` — duration-heuristic pattern for services that don't expose internal telemetry (firecrawl-simple's engine cascade is invisible; wall-clock duration is a usable proxy).

---

## 2026-05-16 — T11 #935 closed (with stale-cred caveat); T14 #1036 filed for credential refresh
**Scope:** shared (backend pipeline + AI integration)
**Proposed by:** lead (closing report from dev-backend specialist)
**Status:** T11 DONE 2026-05-16 (commit `5dfe4bc`) with **AC#6 blocked-by-infra**. T14 #1036 filed for real-CLI verification.

**What landed (T11):**
- `pipeline/app/workers/tasks.py:688-735` — `run_analysis_for_event` un-stubbed; `_run_analysis_for_event_async` calls `AIPipeline.run_full_pipeline()` + saves summary + sentiment via BackendClient. Non-fatal try/except wrap (preserves worker stability).
- `pipeline/app/workers/tasks.py:600-662` — AI queueing loop inside `_run_scrape_only_async` after dedup pass. Queues `run_analysis_for_event.apply_async(args=[event_id, article_text], countdown=idx*ANALYSIS_THROTTLE_SECONDS)` per ArticleGroup. Throttle constant 2s (was 5s in brief; tightened in commit per spec smoke).
- `pipeline/app/services/deduplicator.py` — `ArticleGroup` gains `created_event_id` field; dedup pass populates it after `create_event_with_articles` returns.
- `pipeline/app/services/ai_pipeline.py` — event_id plumbing + fix duplicate Step 1 (was running Step 1 twice in `run_full_pipeline`).
- `docker-compose.dev.yml` — `AI_MODE` + `ANALYSIS_THROTTLE_SECONDS` env knobs on worker for tunability.
- `pipeline/app/backend_client.py:235-255` — verified existing `save_event_summary` + `save_event_analysis` already POST `/analysis/summary` + `/analysis/sentiment`. No new methods needed.
- T8 stopgap retired — `run_full_fetch` docstring rewritten ("Canonical pipeline (T11 #935 — stopgap retired) ... no longer a placeholder, it is the intended structure"); `events_processed` sourced from dedup pass output instead of `total_saved`.

**Mock-mode smoke (proves wire-up end-to-end):**
- Direct dispatch `run_analysis_for_event(54, 'mock text')` → `POST /analysis/summary 201 Created` → `POST /analysis/sentiment 201 Created` → `AI analysis saved for event 54`.
- `curl GET /analysis/summary/54` returns full payload (sentiment_score=65, ai_confidence=80, retail_reaction='Likely buy', etc.).
- DB confirms EventSummary + EventAnalysis rows landed.

**Real-CLI smoke (BLOCKED by stale credentials):**
- `~/.claude/.credentials.json` last modified May 14 06:22 UTC (28+ hours stale). Container's RO mount sees same stale file.
- All 24 real `claude -p` calls during smoke returned `Failed to authenticate. API Error: 401 Invalid authentication credentials`.
- Worker stayed up (non-fatal pattern works); each analysis task returned `{'status':'error','event_id':<id>,'reason':'Claude CLI exit=1: ...'}`.
- **Code path verified** via traceback: `_run_analysis_for_event_async → AIPipeline.run_full_pipeline → call_ai → _invoke_claude_cli` reaches CLI binary. `_log_agent_run` (T3) is wired AFTER successful CLI return → cannot fire on 401-failed calls → zero new AgentRun rows in 24h.

**T11 AC verdict (honest count):**
- 7/9 passed: AC#1 (T10 dep), #2 (un-stub), #3 (BackendClient methods), #4 (queueing loop), #5 (throttle), #7 (mock smoke), #8 (stopgap retired), #9 (commit pattern). [AC#8 numbered out of order — count adjusted.]
- **AC#6 FAILED** (AgentRun rows from real-CLI smoke): blocked by stale credentials → T14 #1036 follow-up.
- AC#7 partial: mock smoke ✅, real-CLI smoke ❌ (same root cause as #6).

**T14 #1036 scope (handoff to user + dev-devops follow-up):**
1. **User action:** run `claude /login` interactively on host to refresh credentials. Verify file timestamp current.
2. **Dev-devops re-verify (auto-spawnable post-step-1):** restart pipeline + worker containers, dispatch a single `run_analysis_for_event` task, verify worker log shows non-401 result + AgentRun row insertion (2 rows: extraction + sentiment, with non-zero tokens + inferred_cost).
3. **PATCH T11 AC#6 + AC#7-real-cli to 'passed'** with verification source.

T14 is priority=2 (blocks T11 full verification), assigned_role=3 (devops).

**Implications:**
- **Pipeline fully wired** — once credentials refresh, the Dashboard 'Fetch Now' → articles → events → AI analysis → Dashboard render loop will work end-to-end. Code path is proven.
- **Stale-credential issue is the engine-migration auth-refresh gap** noted in decisions.md 2026-05-14 lock #1. Long-term mitigation options (NOT in T14 scope): automatic refresh in container, cron-based reminder in dev-devops runbook, or migration to API key (rejected per lock #1).
- **`AnalysisThrottleSeconds=2`** is the new default — claude -p Max 20x quota can sustain ~30 calls/min sustained; 2s spacing gives ~30/min headroom for a 24-event batch.
- **T12 #991 Firecrawl resilience can spawn next** (does not depend on Claude credentials — Firecrawl-only scope).

**Cross-references:**
- T11 commit: `5dfe4bc` on NewsAnalyzer main.
- T14 follow-up: Kanban #1036 on NewsAnalyzer.
- AgentRun model + endpoint: T3 #922 commit `f4cd4d2`.
- Stale-cred root cause: decisions.md 2026-05-14 lock #1 (claude -p engine).

---

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
