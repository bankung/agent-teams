# LinkedIn Strategy: Content framework + generic source list

**Purpose:** Define theme pillars, post-shape templates, and cadence guidelines for LinkedIn content drafting. Operator-specific themes, audience, and RSS feeds arrive via `operator_context` in spawn brief.

**Source:** Secretary agent definition, Pattern 3, lines 160–168.

## Content framework: Theme pillars

Generic pillars suitable for most engineering / professional backgrounds. Operator's personal themes override these at spawn time via `operator_context.operator_themes`.

### Tier 1 — Core pillars (always in rotation)

1. **Technical learning / problem-solving**
   - Deep dives into a tool, pattern, or language you learned recently.
   - "I spent 2 weeks learning Kubernetes and made these 5 mistakes..."
   - How-to posts: "3 patterns for testing async code in Python"
   - Avoid: generic "why X is important" (everyone says it); focus on concrete lessons.

2. **Career insight / growth**
   - Lessons from role transitions, job hunts, negotiation.
   - "Negotiating my last offer taught me these 3 things about asking for what you want..."
   - Mistakes and recoveries.
   - Avoid: career advice that sounds like LinkedIn clichés ("always be learning").

3. **Project shipped / shipped wins**
   - What you shipped, what you learned, metrics if public.
   - "We shipped a real-time collab feature and halved latency in the process; here's how."
   - Milestone posts (1 year at company, project launched, milestone hit).
   - Avoid: humble-bragging; own your wins.

4. **Industry commentary / trends**
   - Reaction to news, framework releases, industry shifts.
   - "The move to serverless is real; here's what changed for backend teams."
   - Contrarian takes (supported by evidence).
   - Avoid: editorializing without substance; back claims up.

5. **Personal essay / reflection**
   - Longer-form thought piece about work life, priorities, or philosophy.
   - "Why I left a big tech company for a startup—and what I learned."
   - Lessons on remote work, burnout, career trajectory.
   - Avoid: pure venting; reframe as insight for others.

### Tier 2 — Optional pillars (as suited to operator's background)

- **Hiring / team building** (if operator leads or interviews)
- **Open source / community** (if operator contributes or organizes)
- **Startup / founder** (if operator is building)
- **Mentorship** (if operator actively mentors)

## Post-shape templates

### Template 1: Hook + list (best for practical posts)

```
[HOOK: bold claim or open question]

Here are N keys [to achieving X / I learned about Y]:

1. [Point 1 in ≤10 words] — [one sentence of context/proof]
2. [Point 2 in ≤10 words] — [one sentence of context/proof]
3. [Point N in ≤10 words] — [one sentence of context/proof]

[TAKEAWAY: one memorable sentence]

[CTA question]
```

**Example:**
```
I spent 2 months rebuilding our payment system and learned these 3 things:

1. Feature flags beat big-bang deploys — we split traffic 80/20 and hit parity in a week
2. Monitoring > guessing — set up alerts before go-live, not after incidents
3. Communicate async — 15 status updates beat 10 sync meetings

This is why iterative > all-or-nothing.

How does your team approach large system rebuilds? Curious about your strategy.
```

### Template 2: Narrative (best for lessons learned / personal essays)

```
[ANECDOTE / STORY: 2–3 sentences setting context]

[MID-STORY TURN: where things went wrong, or the lesson hit]

[LESSON EXTRACTED: broad takeaway in 1–2 sentences]

[REFLECTION: how operator applies this now / what changed as a result]

[CTA: question or invitation to share similar experience]
```

**Example:**
```
I joined a startup as the 5th engineer and spent the first month writing code no one read.

We shipped features nobody asked for. We'd build for 2 weeks then discard the work. 
The problem: we had no metric for "success." We just... built.

The turning point was when the CEO stopped us mid-sprint and said, "What are we trying 
to learn this week?" That one question rewired how we worked.

We defined a metric per sprint. We measured. We cut features ruthlessly. Velocity 
tripled; burnout fell. We shipped a product.

If you're in a team that feels like it's spinning, ask: "What are we trying to learn?" 
It changes everything.

Have you had a moment that flipped how your team thinks about shipping?
```

### Template 3: Contrarian take (best for industry commentary)

```
[PROVOCATIVE CLAIM]

The common wisdom is [X]. But here's why [X] is incomplete / wrong:

[Reason 1 — evidence or example]
[Reason 2 — evidence or example]
[Reason 3 — evidence or example]

[NUANCE / CAVEAT: when the common wisdom IS right]

[Call to action: What's your take?]
```

**Example:**
```
Microservices aren't worth it for most startups.

Everyone says "scale with microservices." But here's what I've seen:

1. You're not Twitter-scale. The operational complexity of 20 services + Kubernetes 
   slows shipping, not speeds it.
2. Monoliths are underrated. Stripe, GitHub, Figma all started monolith. They scaled 
   one service out AFTER they hit real scale, not before.
3. Premature distribution is premature optimization. Split your system when your 
   monolith is the actual bottleneck, not when you're "expecting" growth.

(Caveat: if you're actually at 10M requests/sec, this doesn't apply. But most of us 
aren't thinking in terms of 10M.)

When did you split your architecture? Was it pressure-driven or speculative?
```

## Cadence + volume guidelines

**Target:** 1–2 posts per week (sustainable over months; avoid burnout or spam perception).

**Pacing:**
- Publish 2–3 days apart (not daily; not once per month).
- Mix pillar types: don't do 4 "technical learning" posts in a row. Vary.
- Avoid Sundays / early mornings (engagement lower; post early week, daytime).

**Seasonal:**
- Avoid long silences (>3 weeks) — algorithm penalizes low-activity accounts.
- Ramp up around job change, company milestone, or personal event.
- Post-drafting: if batch-drafting 5 posts, schedule over 2–3 weeks, not day 1.

## Generic source list for topic research

**Note:** Operator's personal `operator_rss_feeds` (if provided) take priority. These are fallback generic sources for topic discovery when operator doesn't specify.

### Tier 1: News + trending

- **Hacker News** (`news.ycombinator.com/newest`) — tech news + discussion; filter by category (AI, databases, cloud, etc.)
- **Lobsters** (`lobste.rs`) — curated tech community; high signal-to-noise ratio
- **dev.to** (`dev.to`) — developer blog platform; search by tag
- **IndieHackers** (`indiehackers.com`) — startup + indie builder perspective

### Tier 2: Domain-specific blogs + publications

- **Engineering blogs:** grab company engineering blog URLs from companies on operator's target list or companies they admire. Examples: Stripe, Figma, Slack, Airbnb, Twilio engineering blogs. RSS feeds available on most.
- **Language/framework news:** Python Weekly, JavaScript Weekly, Golang Blog, Rust Blog (search "X weekly newsletter").
- **Cloud + infrastructure:** AWS What's New, Azure Blog, GCP Blog, Kubernetes Blog.

### Tier 3: Newsletters (operator-curated)

- Operator provides via `operator_rss_feeds` or spawn brief.
- Common examples (not required, just reference): Pointer.io, Sidebar.io, tldr.tech, Morning Brew (for business context).

### Tier 4: Social listening (real-time, no RSS)

- LinkedIn feed: scroll top-20 posts in operator's network; note engagement + topic.
- Twitter/X: search operator's interests (e.g., "#Kubernetes", "#CareerGrowth"); find threads with engagement.

## How to pick topics for LinkedIn post (secretary workflow)

1. **Operator provides topic** (spawn brief, e.g., "post about API design") → skip to step 3.
2. **Secretary proposes 3 topics** from recent reading:
   - Scan sources above (Hacker News, blogs, operator RSS feeds).
   - Pick 1 trending topic (technical / industry-relevant).
   - Pick 1 evergreen topic (from operator's core pillar).
   - Pick 1 contrarian / hot-take topic.
   - Return 3-line pitches to Lead. Operator chooses, or says "none of these, do X instead."
3. **Research:** WebSearch + firecrawl-search for 2–3 references (articles, docs, discussions). Secretary skims, not reads-in-full.
4. **Outline:** 3–5 points mapped to one of the post-shape templates above.
5. **Draft:** per `voice.md` tone (professional, concrete, no jargon, active voice).
6. **Return to Lead** with draft in `general/linkedin-draft-<date>-<slug>.md`. Lead surfaces to operator for approval.

## Post-publish: follow-up engagement (not secretary job; operator manual)

After posting:
- Monitor comments for 24 hours.
- Respond to substantive comments (builds algorithm engagement + community).
- Pin the post if it gets high engagement (LinkedIn feature; operator does this).
- Repost to Twitter/other if operator wants cross-posting (not secretary automation for now).

## Operator-specific overrides at spawn time

Spawn brief may include:

```json
{
  "operator_themes": ["backend systems", "career pivots", "bootstrapping"],
  "audience": "engineers transitioning to management",
  "audience_NOT_for": ["pure sales/recruiting", "cryptocurrency"],
  "operator_rss_feeds": ["https://example.com/feed.xml"],
  "stance_for_this_post": "technical + witty, emoji OK, contrarian welcome",
  "skip_topics": ["blockchain", "AI hype"]
}
```

Use overrides to:
- Filter topic proposals (e.g., "no AI hype" → don't suggest AI topics).
- Adjust tone (e.g., "witty" → add light humor to template).
- Weight sources (operator RSS feeds > generic sources).
- Tailor audience (e.g., "for managers" → focus on leadership / delegation angles).

## Metrics to track (optional; inform future posts)

After each post publishes:
- Engagement rate (likes + comments / impressions).
- Top comment / most common reaction.
- Highest-engagement time-of-day (optimize next post).

Secretary can track in `general/linkedin-metrics-<month>.md` if operator wants trend data.
