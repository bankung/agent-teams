# LinkedIn content strategy

> **Lead is the only writer of this file.** Operator dictates; Lead writes.
>
> Used by secretary for: topic discovery, post drafting, comment drafting, content cadence. All posts pause for HITL approval before going live.

## Audience [TODO — operator fills]

Who is operator writing FOR on LinkedIn?

- **Primary audience**: [TODO e.g. "Backend engineers + tech leads in Thailand/SEA who care about pragmatic AI integration"]
- **Secondary audience**: [TODO e.g. "Founder/CTO at small SaaS, possible future clients/employers"]
- **Audience NOT for**: [TODO e.g. "AI hypers, hustle-bro influencers — operator doesn't want this orbit"]

## Goals [TODO]

What does operator want OUT of LinkedIn?

- [TODO e.g. "Build credibility for senior backend / AI agent work — secondary goal: warm pipeline for next role / consulting"]
- [TODO e.g. "NOT for sales / not for course launches / not for political opinions"]

## Content themes [TODO]

3-5 themes operator writes about. Specific enough that AI-drafted topics are recognizable.

1. **[Theme name]**: [TODO e.g. "Pragmatic AI agent engineering — concrete patterns from real production code, NOT speculative future-of-AI takes"]
   - Sub-topics: [TODO e.g. "auditor pattern, HITL design, context budget, multi-agent orchestration"]
   - Frequency: [TODO e.g. "1-2 posts/week"]

2. **[Theme name]**: [TODO e.g. "Backend craft — Python idioms, FastAPI/SQLAlchemy patterns, postgres tricks"]
   - Sub-topics: [TODO]
   - Frequency: [TODO]

3. **[Theme name]**: [TODO e.g. "Solo SaaS / indie hacker mindset — building agent-teams in public, lessons from operator's projects"]
   - Sub-topics: [TODO]
   - Frequency: [TODO]

4. **[Theme name]**: [TODO if any — e.g. "Career notes — interviews, hiring, what good looks like"]
   - Sub-topics: [TODO]
   - Frequency: [TODO]

## Anti-themes

Topics secretary NEVER drafts on operator's behalf (even if trending):

- Politics / religion / nationalism
- Salary specifics / job-hunting drama (handle in DMs, not public)
- Hot takes on people / companies (legal + reputation risk)
- Generic motivational content / hustle-bro
- Listicles that are content-thin ("10 things every X must know" without substance)
- Anything operator hasn't directly experienced (no "I've heard...")

## Post format preferences [TODO]

- **Default length**: [TODO e.g. "150-300 words for technical posts; 400-600 for longer essays"]
- **Format mix**: [TODO e.g. "70% narrative paragraphs, 20% short-list with context, 10% screenshot+commentary"]
- **Hook style**: [TODO e.g. "open with a specific observation or contrarian frame, NEVER with a question"]
- **CTA preference**: [TODO e.g. "soft CTAs only — 'curious how others handle this' beats 'comment your thoughts!'"]
- **Hashtag count**: [TODO e.g. "2-4 max, lowercase, specific over generic"]

## Cadence

- **Posting frequency target**: [TODO e.g. "2 posts/week — Tue + Fri, before 10am ICT for SEA timezone reach"]
- **Engagement floor**: if a post gets <N impressions in 24h, don't post another for 48h (don't compound a bad reach window)
- **Burst policy**: max 1 post / day even if multiple drafts ready

## Content discovery sources [TODO operator fills]

Where secretary looks for topic ideas:

### RSS feeds (curated, operator-approved)
- [TODO e.g. "Hacker News top: https://news.ycombinator.com/rss"]
- [TODO e.g. "Simon Willison's blog: https://simonwillison.net/atom/everything/"]
- [TODO add operator's trusted technical sources]

### Newsletters (operator-subscribed)
- [TODO e.g. "TLDR AI, The Pragmatic Engineer"]

### Specific blogs / authors
- [TODO]

### Recent topics operator has been thinking about (volatile, operator updates weekly)
- [TODO e.g. "Anthropic's Computer Use, langgraph 1.x, MCP protocol adoption"]

### What NOT to source from
- Twitter (too noisy + format mismatch)
- Reddit (sourcing it explicitly is fine; drafting "as seen on Reddit" feels lazy)
- Other LinkedIn posts (don't be a remix account)

## Drafting protocol

When operator asks "draft a linkedin post on [topic]" or "find 3 topic candidates":

### Find 3 topic candidates
1. Scan content discovery sources for last 48h
2. Filter to operator's themes
3. Pick 3 with strongest "operator's specific angle" hook
4. Surface to operator: title + 1-sentence angle + 1-sentence reason it fits

### Draft 1 post
1. Read `voice.md` thoroughly
2. Outline 3-5 points
3. Draft in `general/linkedin-draft-<date>-<slug>.md`
4. Self-check against voice.md ANTI-PATTERNS — if any present, redraft
5. HITL pause: "approve draft as-is, request edits, or skip?"

### Reply to comment / DM (operator-initiated)

- Read the original conversation
- Draft a reply per voice.md (casual context unless DM is from a recruiter)
- HITL pause always

## Engagement automation rules

Secretary CAN (no HITL):
- Read comments on operator's posts (so operator can scan summary)
- Read DMs from known senders (summarize for operator)

Secretary CANNOT (HITL or operator-only):
- Reply to comments (HITL pause)
- Reply to DMs (HITL pause)
- Like / react to other posts (operator-only — don't manage operator's social graph)
- Connect / accept connection requests (operator-only)
- Follow / unfollow (operator-only)

## Tracking

`general/linkedin-log-<YYYY-MM>.md`:
```
- 2026-05-17 — POSTED — "Auditor pattern in LangGraph" — 1200 chars — link: https://...
- 2026-05-17 — DRAFT — "MCP vs OpenAI function calling" — operator marked "save for later"
- 2026-05-18 — SKIPPED — "10 AI agent frameworks ranked" — voice.md anti-pattern (listicle)
```

## Operator fill checklist

- [ ] Audience (primary + secondary + NOT-for)
- [ ] Goals (and explicit non-goals)
- [ ] 3-5 themes with sub-topics + frequency
- [ ] Anti-themes (don't-touch list)
- [ ] Post format preferences (length / format mix / hook / CTA / hashtags)
- [ ] Cadence target + bad-reach policy
- [ ] RSS feeds (5-10 trusted sources)
- [ ] Newsletter subscriptions list
- [ ] Specific authors operator follows
- [ ] Recent topics file (weekly-updated)
- [ ] Drafting protocol confirmed
- [ ] Engagement automation rules confirmed

**Time estimate**: 20-30 min — themes + audience are the slow part; sources can grow over time.
