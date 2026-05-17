# LinkedIn content strategy — framework + generic sources

> **Audience specifics, operator's personal LinkedIn URL, content themes operator wants to be known for ARE PII** (reveal positioning + identity). Operator injects per-session. This file holds the GENERIC framework + a starter list of operator-agnostic content sources.

## Goal framework (operator selects per-session)

Operator declares per-workflow what they're trying to get out of LinkedIn:

```yaml
session_goal: one_of:
  - "build credibility for senior IC role"
  - "build credibility for executive role (CTO / VP Eng)"
  - "warm pipeline for next role / consulting"
  - "thought-leadership on a specific topic"
  - "share project work / build-in-public"
  - "comment on industry trend"

audience: list (operator-provided per session)
  - e.g. "backend engineers + tech leads in SEA who care about pragmatic AI integration"
  - e.g. "founder/CTO at small SaaS"
  - e.g. "operator's existing network at <industry>"

audience_NOT_for: list (anti-audience)
  - e.g. "AI hypers / hustle-bros / 'manifestation' crowd"

operator_themes: list (3-5 themes operator wants to be associated with)
  - operator-provided per session

anti_themes: list (NEVER draft on these regardless of operator's request)
  - politics / nationalism (any direction)
  - religion / spirituality
  - hot takes on specific people / companies
  - salary / financial / health / medical advice
  - "I've heard..." / "people say..." content (no direct experience)
```

## Content format framework (operator-agnostic)

### Default format mix (override per session)

- 70% narrative paragraphs (personal experience / specific observation)
- 20% short list with context (NOT raw listicle)
- 10% screenshot + commentary (project shots, code, real artifacts)

### Length budgets (per `voice.md`)

- Standard post: 150-400 words
- Long essay: 400-600 words (use sparingly)
- Pure announcement: 30-80 words

### Hook conventions (banned + preferred)

**Banned hooks:**
- Question opener ("Have you ever wondered...?")
- Generic motivational ("In today's fast-paced world...")
- Statistic-only ("90% of devs don't...")
- "I have a confession to make..."

**Preferred hooks (per voice.md):**
- Specific observation ("Most agent frameworks treat failure as a timeout — auditor flips it.")
- Contrarian frame ("Everyone says X. Here's why it's wrong.")
- Personal anecdote opening ("Last week I spent 4 hours debugging...")
- Concrete artifact reference ("This 3-line change saved 40% of our cost.")

### CTA conventions

- Soft CTAs only: "curious how others handle this" / "interested to hear if you've seen this"
- BANNED: "comment your thoughts!" / "agree?" / "tag a friend" / "DM me for details"

### Hashtags

- 2-4 max
- Lowercase
- Specific over generic (#langgraph beats #ai)

## Cadence framework

- Posting frequency target: operator-defined per session OR session-default "2 posts/week"
- Engagement floor: if <100 impressions in first 24h → don't compound with another post within 48h
- Burst policy: max 1 post / day even if multiple drafts ready (looks spammy)

## Content discovery sources (operator-agnostic starter set)

Generic high-signal sources that work regardless of operator's niche. Operator overlays domain-specific sources per session.

### RSS / news (engineering / AI)
- Hacker News top: https://news.ycombinator.com/rss
- Hacker News new: https://news.ycombinator.com/rss (filtered for niche)
- TLDR AI archive: https://tldr.tech/api/latest/ai/rss
- The Pragmatic Engineer: https://newsletter.pragmaticengineer.com/feed
- Simon Willison: https://simonwillison.net/atom/everything/
- arxiv-sanity recent ML: http://www.arxiv-sanity.com/top
- LangChain blog: https://blog.langchain.com/rss/
- Anthropic blog: https://www.anthropic.com/news (no RSS — secretary WebFetches periodically)

### Engineering blogs (RSS-enabled)
- High Scalability: http://highscalability.com/rss.xml
- Martin Fowler: https://martinfowler.com/feed.atom
- Postgres weekly: https://postgresweekly.com/rss

### Public job-market signal (for content tied to hiring trends)
- HN "Who's hiring": secretary fetches monthly thread

### Sources to AVOID (low-signal for content)
- Twitter / X (format mismatch + noise)
- Reddit (sourcing it explicitly is fine; drafting "as seen on Reddit" feels lazy)
- Other LinkedIn posts (don't be a remix account)
- Generic "AI news roundup" newsletters (too shallow)

### Operator-specific sources (session-time injection)

```yaml
operator_rss_feeds:
  - <feed URLs operator subscribes to>
operator_newsletter_subscriptions:
  - <names>
operator_recent_topics_thinking:
  - <volatile, operator refreshes weekly>
```

OR operator stores in `general/operator-context.md` for persistence.

## Drafting protocol

### Mode A — Operator provides topic

1. Read `voice.md` + session-time injected `operator_themes` + `audience`
2. Research topic (≤15 min Chrome MCP + WebFetch):
   - Operator-specific sources first
   - Generic sources second
   - 2-3 references with 1 specific insight each
3. Outline 3-5 points in `_scratch/linkedin-outline-{slug}.md`
4. Draft in `general/linkedin-drafts-{date}/{slug}.md`
5. Self-check:
   - [ ] Voice.md anti-patterns clean
   - [ ] No banned hooks
   - [ ] CTA is soft (or absent)
   - [ ] Em-dash budget within voice.md per-200w
   - [ ] Length within session-target
   - [ ] No anti-themes content
   - [ ] No conclusion paragraph that restates body
6. Return to Lead with draft path + 1-paragraph summary + self-check pass/fail
7. Lead surfaces to operator: "approve, edit, save_for_later, skip?"

### Mode B — Operator asks "find 3 topic candidates"

1. Read `voice.md` + session-time `operator_themes` + `anti_themes`
2. Scan content discovery sources (last 48h)
3. Filter to operator's themes (NOT anti-themes)
4. Score 3 candidates with operator's specific angle:
   - Title (10-15 words)
   - Angle (1 sentence — operator's specific take)
   - Theme it fits
   - Source(s) for backup
5. Return to Lead (no draft yet) — operator picks 1
6. Re-enter Mode A with picked topic

## Engagement automation (Mode A defaults)

Secretary CAN (no HITL):
- Read comments on operator's posts (summary for operator)
- Read DMs from known senders (summary for operator)

Secretary CANNOT (always HITL or operator-only):
- Reply to comments / DMs
- Like / react to other posts (operator-only — don't manage operator's social graph)
- Connect / accept connection requests (operator-only)
- Follow / unfollow (operator-only)

## Per-session output

`general/linkedin-log-{YYYY-MM}.md`:
```
- 2026-05-18 — POSTED — "Auditor pattern in LangGraph" — 320 words — link: https://...
- 2026-05-18 — DRAFT — "MCP vs OpenAI function calling" — operator marked "save for later"
- 2026-05-18 — SKIPPED — "10 AI frameworks ranked" — voice.md anti-pattern (listicle)
```

## Tuning hooks

- **Generic sources**: edit this file (additions/removals propagate to all sessions)
- **Operator's themes / audience**: session-time inject or `general/operator-context.md`
- **Voice / format**: edit `voice.md`
