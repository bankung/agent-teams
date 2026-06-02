# SEO Skill Inventory (Kanban #1266 AC2)

**Source repos:**
- kostja94/marketing-skills — 160+ total skills, ~25 SEO-relevant
- coreyhaines31/marketingskills — 43 total skills, 7 core SEO-relevant

**Inventory date:** 2026-05-20

---

## Repo 1: kostja94/marketing-skills

**License:** MIT  
**Star count:** 485  
**Last commit:** 2026-05-05  
**Repository URL:** https://github.com/kostja94/marketing-skills

### Overview
Comprehensive 160+ skill library organized into 9 categories. SEO skills are nested under `skills/seo/` with 8 subdirectory modules: **technical**, **on-page**, **content**, **off-page**, **local**, **entity-seo**, **programmatic-seo**, and **parasite-seo**. All skills are pure Markdown (no executable code) designed for integration with AI agents like Claude Code, Cursor, and others.

### SEO Skills Table

| Skill name | Category | 1-line summary | SEO-relevance | Native/External | Notes |
|---|---|---|---|---|---|
| **Technical SEO Module** | seo/technical | Foundational technical audit & crawlability setup | HIGH | NATIVE | Includes: Canonical tags, Core Web Vitals, Crawlability, Indexing, IndexNow, Mobile-Friendly, Rendering strategies, Robots.txt, XML Sitemaps |
| **On-Page SEO Module** | seo/on-page | On-page element optimization for SERP performance | HIGH | NATIVE | Includes: Title tags, Meta descriptions, Headings, Schema markup, Internal links, Image/Video optimization, Featured snippets, SERP features, URL structure, Open Graph, Twitter Cards |
| **Content SEO Module** | seo/content | Strategic content planning for search visibility | HIGH | NATIVE | Includes: Keyword research, Content optimization, Content strategy, EEAT signals, Competitor research |
| **Off-Page SEO Module** | seo/off-page | Authority & backlink development | HIGH | NATIVE | Includes: Link building, Backlink analysis |
| **Local SEO Module** | seo/local | Local search & geographic targeting | HIGH | NATIVE | Covers local business listings, local pack optimization |
| **Entity SEO Module** | seo/entity-seo | Entity-based optimization & knowledge graph signals | MEDIUM | NATIVE | Emerging discipline focusing on entity relationships |
| **Programmatic SEO Module** | seo/programmatic-seo | Automated large-scale SEO & template-driven pages | HIGH | NATIVE | For content at scale via templates and data |
| **Parasite SEO Module** | seo/parasite-seo | Leveraging external platforms for ranking | MEDIUM | NATIVE | Alternative ranking tactics using established domains |

---

## Repo 2: coreyhaines31/marketingskills

**License:** MIT  
**Star count:** 29,576  
**Last commit:** 2026-05-19  
**Repository URL:** https://github.com/coreyhaines31/marketingskills

### Overview
Focused 43-skill library with explicit SEO & Discovery category (7 skills). All skills follow the Agent Skills specification (Markdown + YAML frontmatter) designed for Claude Code, Cursor, Windsurf, and other AI agents. Each skill is a `SKILL.md` file installable to `.agents/skills/`. High engagement (29.5k stars), actively maintained.

### SEO Skills Table

| Skill name | File | 1-line summary | SEO-relevance | Native/External | Notes |
|---|---|---|---|---|---|
| **seo-audit** | skills/seo-audit.md | Audit, review, or diagnose SEO issues on-site | HIGH | NATIVE | Direct integration with Claude Code; supports SKILL.md standard |
| **ai-seo** | skills/ai-seo.md | Optimize content for AI search engines & AI-generated answers | HIGH | NATIVE | Emerging discipline; covers AI search behavior |
| **programmatic-seo** | skills/programmatic-seo.md | Create SEO-driven pages at scale using templates & data | HIGH | NATIVE | Supports template-based bulk content generation |
| **site-architecture** | skills/site-architecture.md | Plan website structure, hierarchy, navigation, URL patterns | HIGH | NATIVE | Foundational structure optimization |
| **schema** | skills/schema.md | Add, fix, or optimize schema markup & structured data | HIGH | NATIVE | Structured data & rich snippet optimization |
| **content-strategy** | skills/content-strategy.md | Plan content direction; identify topics to cover | MEDIUM | NATIVE | Adjacent to SEO; content planning foundation |
| **competitors** | skills/competitors.md | Create comparison pages for SEO & sales enablement | MEDIUM | NATIVE | Dual-purpose: SEO landing pages + sales assets |

---

## Recommendations

### 1. Top SEO Skills to Integrate First

**Priority 1 (Highest ROI):**
- **seo-audit** (coreyhaines31) — narrow, actionable, directly operationalizable for diagnostic workflows
- **programmatic-seo** (both repos have this) — aligns with agent-teams' scale-oriented automation principle
- **content-strategy** (coreyhaines31) — foundational; needed before on-page or technical sprints

**Priority 2 (Architectural):**
- **site-architecture** (coreyhaines31) — prerequisite for any large-site work
- **Technical SEO Module** (kostja94) — modular; supports dependency chains (crawlability → indexing → rendering)
- **On-Page SEO Module** (kostja94) — highest density of sub-skills; enables multi-agent coordination

### 2. Licensing Clearance

**Status: CLEAR**
- Both repos are MIT-licensed with no viral clauses
- MIT allows proprietary use, modification, and internal integration without obligation to open-source
- No GPL, AGPL, or copyleft concerns
- Attribution required in copied files; agent-teams can keep license headers in skill frontmatter

### 3. Skills NOT to Integrate (Overlap or Scope Mismatch)

**DO NOT integrate:**
- **parasite-seo** (kostja94) — ethically questionable; leveraging third-party domains for unauthorized ranking signals; likely to conflict with agent-teams' integrity standards
- **entity-seo** (kostja94) — too specialized/nascent; defer until explicit KG-targeting use case emerges
- **local-seo** (kostja94) — low relevance for agent-teams' current scope (assumes web-first, non-geographic focus)
- **competitors** (coreyhaines31) — overlaps with existing content-verification + competitive-intel workflows; defer pending review of existing intelligence agents

### 4. Integration Notes

**Repo 1 (kostja94/marketing-skills):**
- Modular structure (`seo/technical/`, `seo/on-page/`, etc.) requires mapping subdirectories to individual Claude Code skills
- Each subdirectory SKILL.md needs extraction and normalization to cross-agent Agent Skills spec
- Dependencies explicit (seo-strategy → technical → on-page → content → off-page); can be documented in skill YAML
- 160+ skills imply high maintenance burden; recommend curated import (25-30 core skills only)

**Repo 2 (coreyhaines31/marketingskills):**
- Already compliant with Agent Skills spec; drop-in integration possible
- `product-marketing` listed as foundational context for all other skills; load first in skill hierarchy
- 29.5k stars + recent commits (2026-05-19) indicate active community; good candidate for following upstream updates
- Smaller scope (43 skills) = lower maintenance, higher leverage for focused SEO team

### 5. Suggested Sequencing

1. **Phase 1:** Import coreyhaines31/marketingskills SEO & Discovery tier as-is (7 skills; 1-2 days)
   - seo-audit, ai-seo, programmatic-seo, site-architecture, schema, content-strategy (skip competitors)
   - Validate YAML frontmatter compliance; test with Claude Code skill loader
2. **Phase 2:** Curate & extract top 15-20 skills from kostja94/marketing-skills (technical, on-page, content, off-page modules; skip parasite, local, entity-seo)
   - Normalize to Agent Skills spec; establish dependency graph
   - Map to agent-teams team structure (dev-seo-specialist role + content-veracity-checker)
3. **Phase 3 (future):** Monitor coreyhaines31 upstream; consider bi-monthly sync for new skills

---

## Open Questions

1. **Does agent-teams have existing SEO-specific agents?** If so, review skill overlap to avoid duplication before importing.
2. **Product-marketing foundation skill (coreyhaines31):** Is this needed as a prerequisite, or can SEO skills stand alone?
3. **Integration method:** Should skills be git-submoduled, copied wholesale, or selectively imported per-skill?
4. **Maintenance ownership:** Who owns upstream updates and skill deprecation decisions?

---

## Source URLs

- https://github.com/kostja94/marketing-skills — accessed 2026-05-20 — 160+ marketing skills with 8-module SEO structure; MIT license
- https://api.github.com/repos/kostja94/marketing-skills — accessed 2026-05-20 — repo metadata (485 stars, last commit 2026-05-05)
- https://github.com/coreyhaines31/marketingskills — accessed 2026-05-20 — 43 focused marketing skills including 7 core SEO skills; MIT license; Agent Skills spec-compliant
- https://api.github.com/repos/coreyhaines31/marketingskills — accessed 2026-05-20 — repo metadata (29,576 stars, last commit 2026-05-19)
- https://raw.githubusercontent.com/kostja94/marketing-skills/main/LICENSE — accessed 2026-05-20 — MIT License text
- https://raw.githubusercontent.com/coreyhaines31/marketingskills/main/LICENSE — accessed 2026-05-20 — MIT License text (Copyright © 2025 Corey Haines)
