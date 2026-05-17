# Niche research — Tier-2 arena POC candidates (2026-05-17)

> Synthesis of 5 parallel researcher spawns (Haiku tier). Source reports in `_scratch/niche-research-*.md`. Per session-review-2026-05-17.md §5 W5 — unblock niche selection before committing dev time to POC-specific tools.

## Goal

Pick 2-3 candidate business niches for the SECOND project on agent-teams (after `secretary` validates the architecture). Niche must generate revenue with <10h/week ongoing operator time once stable. Honest signal > marketing fluff.

## Summary scoring (0-10 per criterion)

| Criterion | A: AI consulting | B: Productized for solo founders | C: Vertical SaaS | D: B2B research | E: Newsletter |
|---|---|---|---|---|---|
| Pricing power | 7 | 8 | 6 | 7 | 4 |
| Quality measurability | 8 | 7 | 7 | 8 | 6 |
| Operator domain fit | 9 | 6 | 5 | 6 | 9 |
| Concrete buyer profile | 6 | 8 | 5 | 6 | 6 |
| Solo-feasibility | 7 | 8 | 8 | 6 | 9 |
| Ramp to $1K MRR | 4 | 8 | 3 | 5 | 2 |
| AI 10x leverage | 4 | 8 | 6 | 9 | 7 |
| Substrate reuse | 6 | 9 | 5 | 8 | 8 |
| Reputation risk (10=low) | 5 | 5 | 7 | 6 | 8 |
| Abandon cost (10=cheap) | 8 | 7 | 5 | 8 | 9 |
| **TOTAL (max 100)** | **64** | **74** | **57** | **69** | **68** |

Ranking: **B (74) > D (69) > E (68) > A (64) > C (57)**.

Scoring is noisy — triangulate with the per-niche notes below.

---

## A — AI agent implementation consulting (score 64)

**Pitch:** Sell LangGraph/MCP/agent-teams setup to SaaS startups + SMBs.

**Numbers:**
- TAM: $8.5B → $45B by 2030 (53% CAGR); 70% of SaaS deploying AI
- Project pricing: $25K-$150K typical; hourly $100-$300; SaaS startups budget $50-$80K for first agent
- Real competitors: Intuz ($100-$250/hr), Space-O ($25K min RAG), Uvik, Thoughtworks ($300-$600/hr enterprise), Upwork freelance (race-to-bottom)
- **Gap nobody owns yet:** "LangGraph + MCP specialist for Series A SaaS CTO, 8-week launch"
- Ramp: 5-7 months to $1K MRR via LinkedIn cold + case studies; faster (3-4mo) with partner white-label

**Why score isn't higher:** 5-7 month ramp + race-to-bottom on Upwork + AI 10x is only 20-25% time savings (not transformational). Consulting depends on operator's time directly — doesn't scale.

**Why score isn't lower:** operator domain fit is near-perfect; pricing power exists at the "LangGraph specialist" positioning.

**First concrete step (if chosen):** Build 2-3 case studies (use #1102 secretary itself + a contrived demo) on personal LinkedIn → outbound to 30 Series A SaaS CTOs.

---

## B — Productized AI service for solo founders (score 74) ⭐ TOP PICK

**Pitch:** Outcome-priced workflow automation. Examples: investor outreach for YC founders, candidate sourcing for niche hiring, LinkedIn content engine for fractional execs.

**Numbers:**
- TAM: 29.8M solopreneurs (US alone), $1.7T economy
- Pricing: $500-$2K/mo hybrid (base + per-outcome success fee). Labor-replacement framing wins ("save 100hrs @ $400/hr = $40K, charge $5K/yr")
- Competition: Clay / Apollo / Lemlist tool-priced ($50-$150/mo); **Stormy AI emerging in outcome-priced** but space is wide open for "investor outreach for founders" or "LinkedIn brand for execs"
- Ramp: 5-8 weeks to $1K MRR (50 customers) via X cold DM + creator partnerships + paid search; real benchmarks 22-35% DM reply, 10-15% conversation→paid

**Why score is high:** Fastest ramp + USES every piece of agent-teams substrate (HITL approval, knowledge base, approval policies, Mode A Chrome MCP, future Mode B-read). The arena vision and this niche are the SAME shape.

**Risks (real):**
1. Commoditization in 12-18 months when Clay-class players ship outcome-priced
2. **High churn 20-48%/yr** if not locked to annual contracts (binary outcomes = volatile)
3. **Brand reputation** — spam-tier outreach damages founder's brand → operator's brand by proxy

**First concrete step (if chosen):** Pick ONE outcome (e.g., "20 investor meetings for early-stage SaaS founders in 30 days"), 5 customer-discovery calls in 2 weeks. Decision gate: 3+ "yes I'd pay $X today".

---

## C — Vertical SaaS micro-tool (score 57)

**Pitch:** Niche AI tool for specific profession. Examples: contract review for freelance designers, resume tailoring for FAANG-track engineers, listing optimization for Shopee/Lazada SEA sellers.

**Numbers:**
- Indie Hackers Stripe cohort: median **8-9 months** to $1K MRR; 70% never reach $1K
- Teal-class pricing: $29-79/mo (annual), $13/wk (weekly), freemium conversion 5-10%
- Viable IF: operator knows the vertical personally + 20+ beta testers in 2 weeks + 6-8mo runway commit

**Why score is lower:** Operator doesn't have a vertical they know deeply (vs. consulting which IS their expertise). Cold-start CAC is high. Median 8-9 months is too slow for cheap-experiments doctrine.

**Defer unless:** operator identifies a specific vertical (Shopee/Lazada SEA seller? freelance designer subset?) AND has community access.

---

## D — B2B research-as-a-service (score 69) ⭐ SECOND PICK

**Pitch:** Competitive intel / market research / due diligence via AI + human-polish. Buyers: VC associates, mid-market PMs, founders pre-fundraise.

**Numbers:**
- Market: $150B global (90% enterprise; SAM = boutique segment)
- Buyer budgets: VC associates $12-30K/yr; PMs 1-3% of dev spend ($2-10K/quarter); founders $1-2K pre-Series A
- Pricing: $1-$10K/project; $2-10K/mo retainer; freelance $25-70/hr ($38 median)
- Existing options buyers use: GLG/Tegus ($25K+/yr — too expensive), expert calls ($1-2K each — slow), Upwork freelance ($25-70/hr — flaky)
- Ramp: 3 clients in 4-5 months via LinkedIn outreach to $100-500M AUM VCs + accelerator partnerships

**Why score is strong:** AI 10x is GENUINE (parallel browsing of competitors/papers/filings + cited synthesis + structured extraction). Substrate fit excellent (Mode B-read browser tools needed regardless).

**Why not #1:** Capacity ceiling at 5 clients without hiring. "AI-generated research" stigma in conservative segments needs careful framing ("analyst-augmented AI"). Warm-intro dependency for first sale.

**First concrete step (if chosen):** Build 1 free sample report on a high-profile vertical (e.g., "SEA fintech landscape 2026"). Use as warm-outreach asset to 20 VCs via LinkedIn. Decision gate: 1 paid project in 8 weeks at $1.5K-3K.

---

## E — Specialized newsletter / paid community (score 68)

**Pitch:** AI agent engineering content for SEA tech leads. B2C $15-30/mo, B2B $100+/mo.

**Numbers:**
- TAM: 500-2,000 SEA tech leads @ $15-30 WTP + 30-50 enterprise architects @ $100+
- Benchmarks: Pragmatic Engineer 1.1M subs ($175K MRR implied); Latent Space ~200K-500K; TLDR AI ad-funded
- Realistic to $1K MRR: **12-18 months** (not 6 months)
- Hybrid math: 50 paid @ $12 ($600) + 1 sponsor @ $2K = $2.6K MRR by month 8-9
- Pure subscription path needs 2,000-3,000 free subs first

**Why score is high on fit, low on speed:** Operator-domain perfect (already does LinkedIn content via secretary workflow). Reputation-aligned (content quality = operator's brand). Cheap to walk (content stays as portfolio).

**Why score is low on ramp:** 12-18 months is WAY outside cheap-experiments doctrine. Newsletter business builds slowly.

**Strategic positioning:** Treat newsletter NOT as primary revenue niche but as **distribution channel** for whichever niche operator picks. Free content → LinkedIn audience → warm pipeline for consulting/productized-service sales. Newsletter monetization becomes meaningful at year 2-3.

---

## Recommendation: pursue B (primary) + D (parallel)

### Why B + D, not B alone

Customer-discovery is the gate. Doing it for 2 niches × 5 calls each = 10 calls in 2 weeks ≈ 8 operator hours. If only B is pursued and signal is weak, operator wastes 4 weeks before discovering D's strong signal. Parallel discovery costs marginal time + halves "wrong-niche" risk.

### Sequencing (post-secretary-validation)

```
Week 1-2  (now): Secretary Mode A first test (#1106) → validate substrate works on real workload
Week 3-4: Customer-discovery × 5 calls each for B (productized) + D (research-as-service)
                — Lead spawns secretary to draft outreach + book calls per existing workflow
                — Operator runs calls (60 min each), captures notes
Week 5:   Synthesis — which niche has 3+ "yes I'd pay $X today" signals?
Week 6-8: Build proof-of-concept for chosen niche, deliver to 2-3 paying pilots
Week 9+:  Iterate or pivot based on pilot outcomes
```

### Decision gates (pre-commit to dev)

- 3+ explicit "yes I'd pay $X for that today" responses per niche → green-light POC build
- <3 → either pivot (try one of A / C / E) or stop arena Tier-2 work and stay on Tier-1 polish

### Cost estimate

- Week 1-2: ~$5/day for secretary Mode A test = ~$50 total
- Week 3-4: ~$10/day for outreach + research = ~$140
- Week 5: ~$20 synthesis tokens
- Week 6-8: build cost varies; budget ~$300
- Total to "yes/no" decision: ~$510 + ~30 operator hours

Per cheap-experiments doctrine, this is on-budget.

---

## Deferred / rejected (with reason)

- **A (AI consulting)**: Operator domain fit perfect but 5-7 month ramp + Upwork race-to-bottom pricing pressure + AI 10x only 20-25%. **Revisit if** operator builds 2-3 strong public case studies via secretary work → repositions as "specialist for Series A SaaS CTOs".
- **C (Vertical SaaS)**: Median 8-9 months to $1K MRR per Stripe data + operator lacks deep vertical knowledge. **Revisit if** operator picks a specific vertical they know AND has community access for 20+ beta testers.
- **E (Newsletter primary)**: 12-18 month ramp too slow for cheap-experiments. **Repositioned as DISTRIBUTION CHANNEL** for whichever niche operator picks — secretary's LinkedIn workflow already produces content that builds the audience needed for niche acquisition. Monetization becomes meaningful at year 2-3.

---

## What this synthesis does NOT cover (operator must validate)

1. **Operator's actual LinkedIn footprint** — researcher E's numbers assume audience-building from 0; if operator has 5K+ followers already, ramp is faster
2. **Specific outcome pick for B** — "investor outreach" vs "candidate sourcing" vs "LinkedIn brand engine" are 3 different POCs; customer-discovery answers which
3. **Operator's network for D first-3-clients** — warm intros to VC associates exist? If yes, ramp is faster; if cold-only, slower
4. **Bandwidth — secretary substrate is week 1-2 ONLY**; can operator commit 30 hours over 8 weeks for the POC arc?

These are NOT research-resolvable — only operator can answer.

---

## Next concrete action

After secretary first test completes (#1106 closes), Lead opens a follow-up task to draft the 5+5 customer-discovery call list (target VCs for D + target solo-founders/fractional execs for B) using secretary's job-apply-style outreach pattern. Decision gate at Week 5 picks one niche → POC build.

If operator wants to revisit before that, just say "let's re-rank niches" or "drop X / add Y" and Lead re-runs the relevant researcher spawn.

---

## Source reports

- `_scratch/niche-research-ai-agent-consulting.md` (A — 350 lines)
- `_scratch/niche-research-productized-solo-founder.md` (B)
- `_scratch/research-niche-vertical-saas.md` (C — note researcher used variant filename)
- `_scratch/niche-research-b2b-research-service.md` (D)
- `_scratch/niche-research-newsletter-community.md` (E)
- `_scratch/niche-research-framework.md` (Lead's evaluation framework + spawn brief template)

All researcher reports include source URLs + per-niche risks + concrete buyer personas — refer to them for depth.

---

## Decision log

This doc summarizes the 5-niche survey for the Tier-2 arena POC question. No decision committed yet — operator's call.

**Cross-references:**
- `context/projects/agent-teams/shared/session-review-2026-05-17.md` §5 W5 (the gap this research fills)
- `context/projects/agent-teams/shared/portfolio-experiment-process.md` (the cheap-experiments + abandon-rules framework this niche pick lives within)
- `context/projects/agent-teams/shared/approval-policy-design.md` (capacity-scaling depends on approval policies — relevant for all 5 niches)
