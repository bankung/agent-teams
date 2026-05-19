# API contracts (FastAPI ↔ Next.js)

> **Lead is the only writer of this file.** Backend proposes new/changed contracts; frontend consumes them. Lead reviews and writes.
>
> This is the source of truth for HTTP contracts shared between the Next.js client and the FastAPI server. If code disagrees with this file, fix the code (or fix this file via a proposal — not both at once).

## Conventions

- **Base URL:** `<set per environment in .env — e.g., http://localhost:8456>`
- **Auth:** JWT in httpOnly `access_token` cookie. Set by `POST /auth/login`; cleared by `POST /auth/logout`. All non-auth endpoints require valid cookie.
- **Error envelope:** FastAPI default — `{"detail": "<message>"}` with appropriate HTTP status
- **Pagination:** `offset` + `limit` query params. `offset=0` default; `limit` defaults vary per endpoint. Response carries `total` for client-side pagination math.
- **Datetime:** ISO 8601 with timezone (`2026-05-04T12:34:56+07:00`)
- **IDs:** BigInteger (autoincrement) unless specified

## Endpoints

<!--
Template for a new endpoint:

### <METHOD> /path/{param}
**Purpose:** <one line>
**Auth:** <required role / public>

**Request:**
```json
{ "field": "type" }
```

**Response 200:**
```json
{ "field": "type" }
```

**Errors:**
- `400` — `{ "detail": "<message>" }` when <condition>
- `401` — when <condition>
- `404` — when <condition>
-->

---

## Decision-Engine endpoints (Stream A → Stream B contract, locked 2026-05-19)

These three endpoints are the integration surface between **Stream A (Decision Engine)** and **Stream B (Operator Interaction)** per the architecture lock entry in `decisions.md` 2026-05-19. They surface the 3-layer composite scoring output to the frontend / API clients.

Schemas reference these concepts from the architecture lock:
- **Q-score**: per-(ticker, date) signed weighted score, range −10..+10, computed by `L2B Per-ticker rollup` (Kanban #1259)
- **Weather**: per-date market-mood indicators (brightness/wind/rain/fog/storm) computed by `L3 weather` (Kanban #1250)
- **TIER-1/2/3**: per-event partitioning by Q-score magnitude × severity, computed alongside L3
- **Signal layers**: `L1.news_polarity`, `L1.news_severity`, `L1.price_alignment`, `L1.foreign_flow_5d`, `L1.earnings_proximity` (initial 5 — extensible); `L2.sentiment_composite`, `L2.macro_tailwind`, `L2.risk_regime`, `L2.event_density`

All three endpoints require auth (JWT cookie). Datetime fields are ISO-8601 with Asia/Bangkok offset (`+07:00`).

---

### GET /api/daily-outlook

**Purpose:** Return the daily market weather brief + tier-partitioned event IDs. Primary endpoint for the Dashboard's at-a-glance row (Phase 3.1).
**Auth:** required.

**Query params:**
- `date` (optional, default `today` in Asia/Bangkok) — `YYYY-MM-DD`

**Response 200:**
```json
{
  "date": "2026-05-19",
  "weather": {
    "brightness": 72,
    "wind": { "direction": "bull", "speed": 25 },
    "rain": 25,
    "fog": 15,
    "storm_warning": {
      "active": false,
      "reason": null,
      "event_id": null
    }
  },
  "tier_1_event_ids": [101, 102, 103],
  "tier_2_event_ids": [104, 105, 106, 107, 108],
  "tier_3_event_ids": [200, 201, 202],
  "generated_at": "2026-05-19T08:00:00+07:00",
  "calibration_version": "v0.1"
}
```

Field semantics:
- `weather.brightness` — `0..100` bullish bias percentage (composite of L2.sentiment_composite + L2.macro_tailwind)
- `weather.wind.direction` — `"bull"` | `"bear"` | `"flat"`. Frontend icon mapping: ⬆️ bull, ⬇️ bear, ➡️ flat
- `weather.wind.speed` — `0..100` integer magnitude shown as numeric annotation next to the direction icon (e.g., `⬆️ 25`). Higher = stronger macro flow conviction
- `weather.rain` — `0..100` downside-risk probability (composite of L2.risk_regime + L2.event_density)
- `weather.fog` — `0..100` uncertainty (L2 disagreement × confidence-spread). All four primary indicators (brightness, rain, wind.speed, fog) share the same 0..100 scale for operator-glance consistency
- `weather.storm_warning.active` — `true` when L2.event_density spikes >3σ above rolling baseline OR L2.risk_regime shifts past threshold
- `weather.storm_warning.reason` — human-readable rationale (e.g., `"BoT emergency rate cut"`) when `active=true`
- `weather.storm_warning.event_id` — primary driver event when `active=true`
- `tier_1_event_ids` — must-review (Q-score ≥ 3 OR severity ≥ 3); typical 3–5/day
- `tier_2_event_ids` — selective (borderline conviction); typical 10–15/day
- `tier_3_event_ids` — FYI (low Q + low severity); typical 15–20/day
- `calibration_version` — bumps on every accepted self-learning weight delta (operator-veto gated)

**Errors:**
- `401` — no/expired JWT cookie
- `404` — no daily-outlook row exists for that date (job hasn't run yet, or date in the future)

---

### GET /api/tickers/today

**Purpose:** Return today's ranked ticker recommendations. Primary endpoint for "stock-pick MVP" usage (callable via curl / CLI / frontend list view).
**Auth:** required.

**Query params:**
- `date` (optional, default `today` in Asia/Bangkok) — `YYYY-MM-DD`
- `limit` (optional, default `20`, max `100`) — page size
- `offset` (optional, default `0`)
- `recommendation` (optional) — `bullish` | `bearish` | `neutral` filter

**Response 200:**
```json
{
  "date": "2026-05-19",
  "total": 47,
  "limit": 20,
  "offset": 0,
  "tickers": [
    {
      "symbol": "PTT",
      "q_score": 4.2,
      "recommendation": "bullish",
      "confidence": 0.8,
      "summary": "บอร์ด PTT อนุมัติงบลงทุน ฿150B; oil + foreign-flow ส่งผลทางบวก.",
      "signals": {
        "L1": {
          "news_polarity": 2.5,
          "news_severity": 3.0,
          "price_alignment": 1.5,
          "foreign_flow_5d": 0.2,
          "earnings_proximity": 0.1
        },
        "L2": {
          "sentiment_composite": 1.8,
          "macro_tailwind": 0.1
        }
      },
      "top_event_ids": [101, 102],
      "calibration_version": "v0.1"
    }
  ]
}
```

Field semantics:
- `tickers[].q_score` — signed weighted composite from L2B Per-ticker rollup (#1259), range −10..+10
- `tickers[].recommendation` — derived from `q_score` × `confidence` threshold; tunable in calibration config
- `tickers[].confidence` — `0..1`; lower when signal coverage is sparse (e.g., ticker with only news, no foreign-flow data)
- `tickers[].signals.L1` — raw-signal contributions ATTACHED to this ticker (per-ticker subset; market-level L1 signals like `macro` are NOT in this dict because they're not ticker-attributed)
- `tickers[].signals.L2` — thematic-aggregate contributions when applicable
- `tickers[].top_event_ids` — up to 3 events driving the score (frontend can fetch details from `/api/events/{id}`)
- `tickers[].summary` — 1–2 sentence operator-facing rationale, AI-generated

Default sort: `q_score` absolute-magnitude DESC (most-conviction first), tie-break on `confidence` DESC.

**Errors:**
- `401` — auth
- `404` — no ticker rollups for that date

---

### GET /api/tickers/{symbol}

**Purpose:** Per-ticker drill-down view (Phase 3.2 detail page consumes this).
**Auth:** required.

**Path params:** `symbol` — e.g., `PTT`, `KBANK`, `ADVANC` (Thai SET ticker, case-insensitive normalized to upper)

**Query params:**
- `date` (optional, default `today` in Asia/Bangkok)
- `history_days` (optional, default `30`, max `365`) — include prior daily Q-score series for sparkline

**Response 200:**
```json
{
  "symbol": "PTT",
  "date": "2026-05-19",
  "current": {
    "q_score": 4.2,
    "recommendation": "bullish",
    "confidence": 0.8,
    "signals": {
      "L1": { "news_polarity": 2.5, "news_severity": 3.0, "price_alignment": 1.5, "foreign_flow_5d": 0.2, "earnings_proximity": 0.1 },
      "L2": { "sentiment_composite": 1.8, "macro_tailwind": 0.1 }
    }
  },
  "events": [
    {
      "event_id": 101,
      "title": "บอร์ด PTT อนุมัติงบลงทุน ฿150B",
      "polarity": 2.5,
      "severity": 3.0,
      "published_at": "2026-05-19T06:30:00+07:00",
      "primary_article_id": 901,
      "ai_reasoning_excerpt": "Large capex signals confidence in 2027-2030 expansion roadmap..."
    }
  ],
  "price_context": {
    "last_close": 41.50,
    "change_1d_pct": 1.2,
    "change_20d_pct": 5.8,
    "distance_to_60d_high_pct": -3.5,
    "distance_to_20d_low_pct": 8.2
  },
  "history": [
    {
      "date": "2026-04-19",
      "q_score": 0.4,
      "recommendation": "neutral",
      "confidence": 0.55,
      "event_count": 2,
      "top_event_id": 87,
      "close_price": 40.25,
      "close_change_pct": 0.8,
      "outcome": {
        "direction_5d": "up",
        "pct_5d": 2.1,
        "direction_30d": "up",
        "pct_30d": 5.8,
        "direction_90d": "up",
        "pct_90d": 12.3
      },
      "operator_action": {
        "action": "act",
        "note": null
      }
    }
  ],
  "operator_action": {
    "last_action": "act",
    "last_action_at": "2026-05-19T09:15:00+07:00",
    "note": "เห็นด้วย; เข้าซื้อ 200 หุ้น"
  }
}
```

`operator_action` is `null` if operator has not acted on this ticker for the date. `events[]` is empty if no news events attached but ticker still has Q-score from price/macro/flow signals alone.

**History rows — field semantics:**

Each row in `history[]` covers one (ticker, date). Three logical groups per row:

- **Signal trend** (what AI thought that day): `q_score`, `recommendation` (`bullish`/`bearish`/`neutral`), `confidence` (0..1), `event_count` (news events for this ticker that day), `top_event_id` (primary driver event id; `null` if no news that day)
- **Outcome validation** (was AI right): `close_price`, `close_change_pct` (daily return %), `outcome` object with 3 horizons (5d / 30d / 90d). Each horizon = `direction_<N>d` (`up`/`down`/`flat`) + `pct_<N>d` (actual % move). Per-horizon fields are `null` until the date has been reached + 1 trading day buffer (so a row created today has all 3 horizons `null`; a row 6 days old has `direction_5d` populated but `30d`/`90d` still `null`)
- **Operator action** (what user did that day): `action` (`act`/`pass`/`note`), `note` (free-text, nullable). The whole `operator_action` object is `null` if operator never reviewed this ticker on this date

History rows ordered by date ascending (oldest first) so frontend sparkline plots left-to-right naturally. Default `history_days=30`; max `365`.

**Outcome horizons** (5d / 30d / 90d) chosen 2026-05-19 to span swing → position-trade analysis windows. Same horizons feed the L2B/L3 self-learning calibration batch (#1251) — operator-action accuracy and AI Q-score accuracy are evaluated against these same checkpoints.

**Errors:**
- `401` — auth
- `404` — symbol not in watchlist OR no data for date

---

## Mocking convention (Stream B before Stream A is live)

Frontend may consume static JSON fixtures matching the above schemas. Fixture location: `frontend/lib/mocks/daily-outlook.json`, `frontend/lib/mocks/tickers-today.json`, `frontend/lib/mocks/ticker-detail-PTT.json`. Switch via env: `NEXT_PUBLIC_USE_MOCK_OUTLOOK=true`. Lead bundles initial fixtures when Phase 3.1 task (#1252) spawns.

<!-- New endpoints go above this line. -->
