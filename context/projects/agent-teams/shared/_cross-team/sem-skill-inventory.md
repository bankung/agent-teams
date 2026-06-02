# SEM Skill Inventory (Kanban #1269 AC5)

**Source repos:**
- kostja94/marketing-skills — 160+ total skills, 11 SEM-relevant
- coreyhaines31/marketingskills — 43 total skills, 6 SEM-relevant

**Inventory date:** 2026-05-20

**Sibling inventories:** SEO at _scratch/research-seo-skill-inventory.md (#1266 AC2)

---

## Repo 1: kostja94/marketing-skills

**License:** MIT  
**Star count:** 484  
**Last commit:** 2026-05-05  
**Repository URL:** https://github.com/kostja94/marketing-skills

### Overview

Comprehensive 160+ skill library with dedicated `skills/paid-ads/` module containing **11 SEM-specific skills** organized into two tiers:

1. **Formats** (5 skills): ad unit types and delivery mechanisms (app installs, CTV, display, native, directory)
2. **Platforms** (6 skills): paid ad platforms (Google Ads, Meta, LinkedIn, YouTube, TikTok, Reddit)

All skills are pure Markdown SKILL.md files designed for Claude Code integration. The paid-ads module is peer to the SEO module (same structure, same integration pattern).

### SEM Skills Table

| Skill name | Category | 1-line summary | SEM-relevance | Native/External | Notes |
|---|---|---|---|---|---|
| **google-ads** | platforms | Set up, optimize, manage Google Ads (Search, Display, Performance Max); PMF testing vs conversion-driven | HIGH | NATIVE | Most mature platform guidance; 2 modes (PMF testing, conversion-driven); supports competitor bidding |
| **meta-ads** | platforms | Set up, optimize, manage Meta (Facebook/Instagram) Ads; audience targeting, creative iteration | HIGH | NATIVE | Includes audience segmentation, creative A/B testing, retargeting on Meta ecosystem |
| **linkedin-ads** | platforms | Set up, optimize, manage LinkedIn Ads; B2B targeting, account-based marketing | HIGH | NATIVE | B2B-focused; covers sponsored content, InMail, matched audiences |
| **youtube-ads** | platforms | Set up, optimize YouTube video ads (TrueView, Bumper, Performance Max); creative optimization | HIGH | NATIVE | Video-specific guidance; includes TrueView and Bumper campaign setup |
| **tiktok-ads** | platforms | Set up, optimize, manage TikTok Ads; creator partnerships, viral mechanics | HIGH | NATIVE | Emerging platform focus; covers creator incentives, organic-to-paid amplification |
| **reddit-ads** | platforms | Set up, optimize, manage Reddit Ads; community targeting, discussion-based campaigns | HIGH | NATIVE | Niche platform; community-focused ad targeting |
| **display-ads** | formats | Run display, banner, ad network campaigns; programmatic, retargeting | HIGH | NATIVE | Covers display networks, programmatic buying, retargeting mechanics |
| **app-ads** | formats | Run app install ads, user acquisition (UA), app store optimization via ads | HIGH | NATIVE | App-specific UA strategies; iOS/Android campaign setup |
| **native-ads** | formats | Run native ads on Taboola, Outbrain, similar platforms; content-style sponsored | HIGH | NATIVE | Third-party content recommendation platforms (non-platform-native) |
| **ctv-ads** | formats | Run CTV (Connected TV), OTT, streaming TV ads; over-the-top video | HIGH | NATIVE | Emerging format; covers Amazon Prime, Roku, Hulu, YouTube TV |
| **directory-listing-ads** | formats | Run paid ads within directories, marketplaces (Yellow Pages, Google Local); paid placement | MEDIUM | NATIVE | Local ads; marketplace paid listing strategy |

---

## Repo 2: coreyhaines31/marketingskills

**License:** MIT  
**Star count:** 29,579  
**Last commit:** 2026-05-19  
**Repository URL:** https://github.com/coreyhaines31/marketingskills

### Overview

Focused 43-skill library with **6 SEM-relevant skills** that span paid ads, creative, and performance optimization. Skills are Agent Skills spec-compliant (YAML frontmatter + markdown body), designed for Claude Code `.agents/skills/` installation. High engagement (29.5k stars), actively maintained.

Unlike repo 1's platform-specific skills, repo 2's SEM skills span the **funnel**: campaign setup (ads) → creative optimization (ad-creative) → experiment design (ab-testing) → performance measurement (analytics) → landing page conversion (cro) → pricing strategy (pricing).

### SEM Skills Table

| Skill name | 1-line summary | SEM-relevance | Native/External | Notes |
|---|---|---|---|---|
| **ads** | Help with paid advertising campaigns on Google Ads, Meta, LinkedIn, Twitter/X; setup, optimization, measurement | HIGH | NATIVE | Platform-agnostic paid ads guidance; supports Google, Meta, LinkedIn, Twitter; includes ROAS/CAC metrics |
| **ad-creative** | Generate, iterate, scale ad creative (headlines, descriptions, primary text, full ad variants); A/B testing | HIGH | NATIVE | Creative ideation + iteration; supports multi-variant generation; tightly integrated with ab-testing |
| **ab-testing** | Plan, design, implement A/B tests and growth experiments; statistical rigor, sample size, power | HIGH | NATIVE | Experimental methodology for ad creative, landing pages, offers; can power ad variant testing |
| **analytics** | Set up, improve, audit analytics tracking and measurement; conversion attribution, UTM setup | HIGH | NATIVE | Cross-funnel tracking; supports Google Analytics, platform-native tracking (GA4, GTM); conversion attribution |
| **cro** | Optimize, improve, increase conversions on marketing pages, forms, funnels | HIGH | NATIVE | Landing page optimization; form UX; directly supports ad campaign landing page ROI |
| **pricing** | Help with pricing decisions, packaging, monetization strategy; psychological pricing tactics | MEDIUM | NATIVE | Pricing strategy affects ad positioning + CPC/CAC trade-off; less direct SEM but affects ad funnel economics |

---

## Recommendations

### 1. Top SEM Skills to Integrate First

**Priority 1 (Immediate ROI for SEM team):**
- **ads** (coreyhaines31) — platform-agnostic SEM baseline; covers Google/Meta/LinkedIn/Twitter; drop-in compatible with Agent Skills spec
- **ad-creative** (coreyhaines31) — creative iteration + variant generation; tightly paired with ab-testing
- **ab-testing** (coreyhaines31) — A/B testing framework; essential for creative + landing page optimization
- **google-ads** (kostja94) — deepest platform-specific guidance for Google; PMF testing + conversion modes highly actionable
- **analytics** (coreyhaines31) — conversion tracking + attribution; closes the funnel-measurement loop

**Priority 2 (Foundation for scaling):**
- **meta-ads** (kostja94) — Meta ecosystem (Facebook/Instagram) optimization; second-largest ad spend for most orgs
- **cro** (coreyhaines31) — landing page optimization; directly impacts ad campaign ROAS; prerequisite for high-intent campaigns
- **youtube-ads** (kostja94) — video ads (TrueView, Bumper, Performance Max); emerging high-engagement format

**Priority 3 (Specialized platform skills):**
- **linkedin-ads** (kostja94) — B2B-specific; defer unless org targets B2B decision-makers
- **tiktok-ads** (kostja94) — emerging platform; high engagement but audience-dependent
- **pricing** (coreyhaines31) — optional; affects ad spend ROI but indirect to core SEM flows

**Skip for now:**
- **reddit-ads** (kostja94) — niche platform; low priority unless org has community
- **ctv-ads** (kostja94) — emerging format; budget constraints limit early adoption
- **native-ads** (kostja94) — third-party platforms; lower ROI for most B2B/B2C
- **app-ads** (kostja94) — app-specific; defer unless org has mobile UA as core channel
- **display-ads** (kostja94) — overlaps with Performance Max + Google Display; prioritize google-ads skill instead
- **directory-listing-ads** (kostja94) — local/marketplace focus; not core to agent-teams' broader SEM scope

### 2. Licensing Clearance

**Status: CLEAR**
- Both repos are MIT-licensed with no viral/copyleft clauses
- MIT allows proprietary use, modification, and internal integration without obligation to open-source
- Both have copyright holders (kostja94, Corey Haines) — attribute in imported skill files
- No GPL, AGPL, or restricted usage concerns

### 3. Skills NOT to Integrate (Overlap or Scope Mismatch)

**DO NOT integrate (duplicated or out-of-scope):**
- **display-ads** (kostja94) — overlaps with google-ads' Performance Max + Display Network guidance; redundant with google-ads skill
- **reddit-ads** (kostja94) — niche; low engagement for most SEM budgets; defer unless customer use case requires it
- **directory-listing-ads** (kostja94) — local/marketplace focus; misaligned with agent-teams' web-first scope
- **ctv-ads** (kostja94) — nascent format; budget constraints + limited agent-teams use case; revisit in Phase 2
- **pricing** (coreyhaines31) — MEDIUM relevance; indirect to SEM; defer to dedicated pricing-strategy skill if one exists

### 4. Integration Strategy by Repo

**Repo 1 (kostja94/marketing-skills):**
- **Modular structure** — each platform/format is a separate SKILL.md; requires mapping to individual agent-teams skills
- **Normalization needed** — YAML frontmatter already present; should be compatible with Agent Skills spec; verify `agent` / `activators` fields match agent-teams conventions
- **Dependency graph** — platforms are independent; formats are delivery mechanisms; suggest flattening both to skill tier (no sub-folders)
- **Example import flow:**
  ```
  skills/paid-ads/platforms/google-ads/SKILL.md → agent-teams/.agents/skills/seo-google-ads.md
  skills/paid-ads/platforms/meta-ads/SKILL.md   → agent-teams/.agents/skills/sem-meta-ads.md
  (rename to avoid collision with any existing `seo-` skills)
  ```
- **Maintenance burden** — 160+ total skills = high; recommend curated import (11 SEM + 6-8 adjacent skills max per refresh cycle)

**Repo 2 (coreyhaines31/marketingskills):**
- **Agent Skills spec-compliant** — YAML + markdown already aligned; drop-in integration possible with minimal normalization
- **Cross-functional** — ads, analytics, cro, ab-testing are designed to work together across SEM funnel; import as a cohesive group (don't cherry-pick)
- **Active upstream** — 29.5k stars + recent commits (2026-05-19) indicate community engagement; recommend bi-monthly sync for skill updates
- **Example import flow:**
  ```
  Direct copy: skills/{ads,ad-creative,ab-testing,analytics,cro}.md 
              → agent-teams/.agents/skills/sem-{ads,ad-creative,ab-testing,analytics,cro}.md
  Optional: skills/pricing.md → _scratch/review-pricing-skill.md (for manual review before import)
  ```

### 5. Team Composition Implications

**Suggested SEM team roster (based on imported skills):**
1. **sem-campaign-lead** — orchestrates paid ad strategy; references `ads`, `analytics`, `ab-testing` skills
2. **google-ads-specialist** — depth on Google (Search, Display, Performance Max); references `google-ads` skill + `ab-testing`, `ad-creative`
3. **meta-ads-specialist** — depth on Meta (Facebook/Instagram); references `meta-ads` skill + `ad-creative`, `analytics`
4. **cro-optimizer** — landing page optimization; references `cro`, `analytics`, `ab-testing` skills
5. **creative-lead** — ad variant generation + iteration; references `ad-creative`, `ab-testing`, `analytics` skills

(Optional agents if scope expands: **linkedin-ads-specialist**, **youtube-ads-specialist**, **pricing-strategist**)

### 6. Suggested Sequencing (Implementation Plan)

**Phase 1 (Week 1):**
- Import coreyhaines31 skills as-is: `ads`, `ad-creative`, `ab-testing`, `analytics`, `cro`
- Validate YAML + agent field compliance; test with Claude Code skill loader
- Create `.agents/skills/sem-*.md` files; document in team playbook

**Phase 2 (Weeks 2-3):**
- Import top 4 kostja94 platform skills: `google-ads`, `meta-ads`, `youtube-ads`, `linkedin-ads`
- Normalize YAML frontmatter to agent-teams conventions; add agent field + metadata
- Establish skill dependencies in team playbook (e.g., `sem-campaign-lead` → `sem-ads` → `sem-google-ads`)

**Phase 3 (Future):**
- Monitor coreyhaines31 upstream for new/updated skills; sync every 2 months
- Evaluate `tiktok-ads`, `pricing` based on customer demand
- Consider sunsetting `reddit-ads`, `display-ads` if not used in first quarter

---

## Cross-references

- Sibling inventory: _scratch/research-seo-skill-inventory.md (Kanban #1266 AC2)
- Referenced agent specs (to be drafted): sem-campaign-lead, google-ads-specialist, meta-ads-specialist, cro-optimizer, creative-lead
- Team playbook: .claude/teams/sem.md (to be created post-approval)

---

## Open Questions

1. **Does agent-teams have existing paid-ads or SEM agents?** If so, review skill overlap before importing to avoid duplication.
2. **Should skills be git-submoduled or copied wholesale?** Submodule keeps upstream updates live; copy provides stability + control.
3. **YAML normalization scope:** Do agent-teams' Agent Skills have specific `agent` field values or `activators` that differ from coreyhaines31's spec? Need validation before import.
4. **Maintenance ownership:** Who owns upstream sync (especially coreyhaines31's high churn rate)?
5. **Pricing skill integration:** Is pricing strategy a core SEM concern, or should it be deferred to product/revenue domain?
6. **Repo 1 modular structure:** Should `skills/paid-ads/formats/` and `skills/paid-ads/platforms/` be flattened, or kept as a hierarchical namespace in skill names (e.g., `paid-ads-format-display`)?

---

## Source URLs

- https://github.com/kostja94/marketing-skills — accessed 2026-05-20 — 160+ marketing skills with paid-ads module (11 SEM skills); MIT license; 484 stars; last commit 2026-05-05
- https://api.github.com/repos/kostja94/marketing-skills — accessed 2026-05-20 — repo metadata (License: MIT, Stars: 484, Last commit: 2026-05-05T19:11:33Z)
- https://raw.githubusercontent.com/kostja94/marketing-skills/main/skills/paid-ads/platforms/google-ads/SKILL.md — accessed 2026-05-20 — google-ads skill (Google Search/Display/Performance Max guidance)
- https://raw.githubusercontent.com/kostja94/marketing-skills/main/skills/paid-ads/platforms/meta-ads/SKILL.md — accessed 2026-05-20 — meta-ads skill (Facebook/Instagram Ads optimization)
- https://raw.githubusercontent.com/kostja94/marketing-skills/main/skills/paid-ads/platforms/linkedin-ads/SKILL.md — accessed 2026-05-20 — linkedin-ads skill (B2B ad targeting)
- https://raw.githubusercontent.com/kostja94/marketing-skills/main/skills/paid-ads/platforms/youtube-ads/SKILL.md — accessed 2026-05-20 — youtube-ads skill (Video ad formats)
- https://raw.githubusercontent.com/kostja94/marketing-skills/main/skills/paid-ads/platforms/tiktok-ads/SKILL.md — accessed 2026-05-20 — tiktok-ads skill (TikTok Ads setup + creator partnerships)
- https://raw.githubusercontent.com/kostja94/marketing-skills/main/skills/paid-ads/platforms/reddit-ads/SKILL.md — accessed 2026-05-20 — reddit-ads skill (Reddit Ads targeting)
- https://github.com/coreyhaines31/marketingskills — accessed 2026-05-20 — 43 focused marketing skills with 6 SEM-relevant skills; MIT license; 29,579 stars; last commit 2026-05-19; Agent Skills spec-compliant
- https://api.github.com/repos/coreyhaines31/marketingskills — accessed 2026-05-20 — repo metadata (License: MIT, Stars: 29,579, Last commit: 2026-05-19T05:30:15Z)
- https://raw.githubusercontent.com/coreyhaines31/marketingskills/main/skills/ads/SKILL.md — accessed 2026-05-20 — ads skill (platform-agnostic paid ad guidance)
- https://raw.githubusercontent.com/coreyhaines31/marketingskills/main/skills/ad-creative/SKILL.md — accessed 2026-05-20 — ad-creative skill (creative iteration + variant generation)
- https://raw.githubusercontent.com/coreyhaines31/marketingskills/main/skills/ab-testing/SKILL.md — accessed 2026-05-20 — ab-testing skill (A/B testing methodology + sample size)
- https://raw.githubusercontent.com/coreyhaines31/marketingskills/main/skills/analytics/SKILL.md — accessed 2026-05-20 — analytics skill (conversion tracking + attribution)
- https://raw.githubusercontent.com/coreyhaines31/marketingskills/main/skills/cro/SKILL.md — accessed 2026-05-20 — cro skill (landing page optimization)
- https://raw.githubusercontent.com/coreyhaines31/marketingskills/main/skills/pricing/SKILL.md — accessed 2026-05-20 — pricing skill (pricing strategy + monetization)
