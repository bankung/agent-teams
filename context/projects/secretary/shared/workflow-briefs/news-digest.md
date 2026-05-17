# Workflow brief — News digest

> Spawn template for `secretary` agent. Lead reads this when operator says "news digest" / "what's happening in my themes" / "scan today's news" / similar.
>
> Mode A (Chrome MCP for some sources) OR Mode B-read (when langgraph browser tools land). For now Mode A.

## Pre-flight (Lead checks)

- [ ] Lead extracted `operator_context` — recommended: `operator_themes` (3-5 themes from linkedin-strategy), `news_sources` (RSS feeds + URLs operator follows), `news_horizon_hours` (default 48), `news_volume_cap` (default 30 items)
- [ ] If `operator_themes` missing → secretary uses GENERIC tech / engineering themes from `linkedin-strategy.md` generic sources
- [ ] Today's date directory exists

If pre-flight fails → halt + report.

## Secretary's expected workflow

1. **Read frameworks + operator_context**:
   - `shared/linkedin-strategy.md` — generic source list (HN, TLDR AI, Pragmatic Engineer, etc.) + format conventions
   - `operator_context` from spawn brief — `operator_themes` + `news_sources` (overlay) + `anti_themes` (skip filter)
2. **Fetch sources** (Mode A: Chrome MCP via WebFetch for RSS; firecrawl-search for keyword-based discovery):
   - Generic sources from `linkedin-strategy.md` "Content discovery sources" (RSS feeds — operator-agnostic)
   - Operator-specific feeds from `operator_context.news_sources` (overlay)
   - Time horizon: last `news_horizon_hours` (default 48h to catch weekend backlog)
3. **Filter** items to operator's themes:
   - Match item title / abstract against `operator_themes` keywords + tags
   - Reject items matching `anti_themes` (politics, hype, listicles per voice.md)
   - Score each item 0-100: theme match × source signal × freshness
4. **Cap at `news_volume_cap` items** (default 30 → operator's mental budget)
5. **Group by theme** (3-5 buckets per `operator_themes`)
6. **Per item**: extract 1-sentence why-it-matters (the operator's specific angle, not the source's framing)
7. **Stash digest** in `general/{YYYY-MM-DD}/news-digest.md`:
   ```markdown
   # News digest — {YYYY-MM-DD}
   
   ## Theme: <theme 1> (8 items)
   - [Title](url) — source · 2h ago — 1-sentence operator angle
   - [Title](url) — source · 5h ago — angle
   
   ## Theme: <theme 2> (4 items)
   - ...
   
   ## Cross-theme threads
   - <observation if 2+ items connect across themes>
   ```
8. **Return to Lead** with: counts per theme + 3-5 highest-signal items surfaced + topic candidates for potential LinkedIn post

## Auto-execute (no HITL)

- All reading / fetching / scoring
- Stash digest locally

## Never HITL (this workflow has NO external effect)

News digest is read-only. No HITL needed unless operator explicitly chains into "draft a LinkedIn post on item N" — that's a separate `linkedin-post.md` spawn.

## Source-fetch strategy

### RSS feeds (operator-agnostic generic + operator-specific)
- Use `WebFetch` directly (no Chrome needed — RSS is public)
- Parse standard RSS / Atom format
- Extract: title, link, summary, published_at, source_name
- Volume per feed: cap at 10 items per source (avoid one chatty source dominating)

### Newsletter web archives (TLDR AI, Pragmatic Engineer)
- `firecrawl-scrape` on the archive page → markdown
- Parse top 5-10 items
- Linked deeper article = optional drill-down if score warrants

### HN top / new
- `firecrawl-search` with theme keywords filtered to news.ycombinator.com
- Or `WebFetch` on https://news.ycombinator.com/rss

### Specific blog feeds (Simon Willison, etc.)
- `WebFetch` on the RSS URL
- Pull last 3-5 posts

### What NOT to source
- Twitter / X (format mismatch + no clean RSS post-2023)
- Reddit (low signal-to-noise without subreddit-specific deep cuts)
- LinkedIn posts as news (don't be a remix account)

## Scoring algorithm

```
score = (theme_match × 50) + (source_signal × 30) + (freshness × 20)

theme_match (0.0-1.0):
  exact tag match → 1.0
  title keyword match → 0.7
  abstract keyword match → 0.4
  no match → 0.0 (skip)

source_signal (0.0-1.0):
  operator-curated source → 1.0
  HN top quartile / Pragmatic Engineer / Simon Willison → 0.8
  HN new / lower-curation source → 0.5
  unknown source → 0.3

freshness (0.0-1.0):
  <2h old → 1.0
  2-12h → 0.8
  12-24h → 0.6
  24-48h → 0.4
  >48h → 0.0 (skip unless evergreen flag)
```

Threshold for inclusion: score >= 40. Cap at `news_volume_cap`.

## Failure modes

- RSS feed 404 / timeout → log + continue with other sources; report "{source} unreachable"
- Captcha on web archive page → halt for that source; continue with rest
- 0 items above threshold → report "no news matched themes in {horizon}h — consider expanding sources or relaxing themes"
- Token cost approaching cap → cap items aggressively + report (don't burn $0.30 on a news digest)

## Per-run output

`general/{YYYY-MM-DD}/news-digest-summary.md`:
```markdown
# News digest — {YYYY-MM-DD HH:MM}

- Sources scanned: N
- Items above threshold: M
- Items capped at: 30
- Themes: <list>
- Top item: <title> — <why>
- Topic candidates for LinkedIn post (if operator-themes match): N

Full digest at general/{date}/news-digest.md
```

## Operator-facing summary (Lead renders)

```
📰 News digest — 27 items across 4 themes

🧠 AI agent engineering (12 items)
  - Anthropic releases sub-agents support → tooling parity with Claude SDK
  - LangGraph 1.3 ships interrupts on streaming → solves multi-turn HITL latency
  - <highest-signal item title — operator's specific angle>

⚙️ Backend craft (8 items)
  - Postgres 17.2 ships incremental backups → relevant to your #959 work
  - <item>

🚀 Indie SaaS (4 items)
  - <item>

🤖 Browser automation (3 items)
  - <item>

🎯 Topic candidates for LinkedIn post:
  1. "How auditor pattern compares to retry-on-timeout"
  2. "Postgres incremental backups + per-project DR"
  3. "MCP adoption pace in 2026 Q2"

Full digest: general/2026-05-18/news-digest.md
```

## Tuning hooks

- **Themes**: operator inline `themes: [<list>]` overrides linkedin-strategy.md defaults
- **Sources**: operator inline `sources: [<URLs>]` or update `operator-context.md` `defaults_for_linkedin.operator_rss_feeds`
- **Horizon**: operator inline `horizon: 24h` (default 48h)
- **Volume**: operator inline `cap: 50` (default 30)
- **Score threshold**: operator inline `threshold: 60` (default 40) — stricter filter
- **Chain to LinkedIn draft**: operator says `news digest then draft post on top candidate` → secretary returns digest + Lead spawns `linkedin-post.md` workflow with picked topic
