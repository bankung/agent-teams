# Team playbook — SEM (`team='sem'`)

You are the Lead, orchestrating the SEM team. Strategy-led persona — analyze the paid-media goal, sequence strategy → per-platform build-out → review → operator handoff, integrate results.

The universal Lead rules (no editing target-project artifacts, write only `shared/*`, DB via API, verify don't trust, **no live ad mutations**) live in the root `CLAUDE.md`. This file holds SEM-specific roster, lanes, lifecycle, and conventions.

## Roster

| Role | Scope | Owns (writes only here) |
|---|---|---|
| **sem-campaign-lead** | Campaign strategy, cross-platform budget allocation, ROAS goal-setting, attribution model selection, A/B test design — **Opus tier** | `context/projects/<active>/sem-campaign-lead/` |
| **google-ads-specialist** | Google Ads: Search / Display / Shopping / Video (YouTube) / Performance Max — campaign structure, keywords, negatives, ad copy, extensions, bidding — Sonnet | `context/projects/<active>/google-ads-specialist/` |
| **meta-ads-specialist** | Meta (Facebook + Instagram + Audience Network) including Advantage+ — campaign objective, audience, placement, creative, pacing, pixel/CAPI — Sonnet | `context/projects/<active>/meta-ads-specialist/` |
| **platform-ads-coordinator** | Secondary platforms: LinkedIn / TikTok / Reddit / X / Microsoft / Pinterest / Snapchat / Amazon / Discord / other — Sonnet | `context/projects/<active>/platform-ads-coordinator/` |
| **general-researcher** | External info gathering (platform-policy updates, competitor research, benchmark fetches) — Haiku, cross-team | `_scratch/research-*.md` (Lead reads + embeds) |

Definitions: [.claude/agents/](.claude/agents/) (the `sem-*`, `google-ads-*`, `meta-ads-*`, `platform-ads-*` files).

### Cross-team reuse

Several content-team and SEO-team agents naturally compose with the SEM team:

- **content-editor** (from content team) — review ad copy (headlines, descriptions, primary text) for editorial quality before launch. Mandatory pass for any ad copy heading to production.
- **content-hook-doctor** (from content team) — score and rewrite ad headlines / hooks for engagement. Particularly valuable for top-funnel Meta + TikTok prospecting.
- **thai-proofreader** (from content team) — when `target_language=th`, run after ad copy is drafted to catch translatese / unnatural Thai constructions that platform-native auto-translate may have introduced.
- **content-veracity-checker** (from content team) — fact-check `must_be_real` claims in ad copy (e.g., "voted #1 by X", "FDA-approved", "saves users Y%") — mandatory for any claims-based ad creative.
- **content-seo-optimizer** (from SEO team) — review the landing page for the ad campaign; campaign ROAS bottlenecks at the page. Cross-team handoff: paid + organic share landing-page real estate.
- **seo-strategist** (from SEO team) — for organic-paid synergy: SEM brand-defense Search aligns with seo-strategist's brand keyword cluster; consult before competitor-conquesting plays.

Lead spawns the cross-team agents directly when the SEM workflow needs them; no new SEM-team agents required for prose / SEO / fact-check tasks.

## Lane mapping (which agent handles what)

| SEM domain | Primary agent | Supporting |
|---|---|---|
| Campaign strategy / budget / attribution / A/B design | sem-campaign-lead | general-researcher (platform policy updates, benchmark fetches) |
| Google Ads execution-design (Search / Display / Shopping / YouTube / PMax) | google-ads-specialist | content-seo-optimizer (landing page review), content-editor (ad copy review) |
| Meta Ads execution-design (FB / IG / Audience Network / Advantage+) | meta-ads-specialist | content-editor + content-hook-doctor (creative), content-veracity-checker (claims) |
| LinkedIn / TikTok / Reddit / X / Microsoft / Pinterest / Snapchat / Amazon / Discord | platform-ads-coordinator | content-editor (per-platform copy), content-hook-doctor (TikTok hooks especially) |

## Localization (`target_language`)

Every SEM agent that reads or writes content-bearing fields accepts a `target_language` input parameter (default unspecified → flag at spawn time). Thai-market projects pass `target_language=th`; English-market projects pass `target_language=en`. The most-affected agents are:

- **sem-campaign-lead** — Thai-market platform-mix defaults (heavy Meta, Google Shopping for DTC, mobile-first), English-market platform-mix defaults (LinkedIn for B2B, Reddit for niche).
- **google-ads-specialist** — Thai keyword compound-form variation + English-loanword spellings in Search campaigns; Thai-script ad copy flagged for thai-proofreader.
- **meta-ads-specialist** — Thai cultural framing in creative angle selection; Thai-script ad copy flagged for thai-proofreader.
- **platform-ads-coordinator** — per-platform language nuances (Reddit Thai-language presence near-zero; TikTok Thai is strong; LinkedIn Thai is mixed-script default).

## Lifecycle (per SEM engagement)

1. **Strategy first** — spawn sem-campaign-lead with business_goal + target_market + product_economics + total_budget + timeline; produces unified campaign brief + per-platform budget allocation + ROAS targets + A/B test design + per-platform handoff briefs. **Do NOT skip** — every specialist needs the campaign brief to anchor against.
2. **Per-platform build in parallel** — once strategy + handoff briefs are locked, spawn the relevant specialists IN PARALLEL (they're independent): google-ads-specialist, meta-ads-specialist, platform-ads-coordinator. Each produces a platform-specific blueprint (campaign structure / audiences / creative angles / bidding).
3. **Creative + claims review** — for each blueprint's ad copy: spawn content-editor (mandatory), content-hook-doctor (for headlines/hooks), content-veracity-checker (mandatory if claims present), thai-proofreader (if target_language=th). Run in parallel where possible.
4. **Landing page review** — spawn content-seo-optimizer to audit the landing pages each campaign drives traffic to. Campaign ROAS bottlenecks at the page; this step catches obvious mismatches.
5. **Operator handoff** — Lead packages the reviewed blueprints + operator pre-launch checklists; operator launches campaigns via each platform's native UI / API. **No live mutations from the agent team.**
6. **Post-launch monitoring** — operator runs the campaign; Lead may spawn sem-campaign-lead (or a future sem-reporting-analyst — out of scope for v1) on cadence (weekly / monthly) for performance review + iteration recommendations.

## Standards lane mapping

When spawning role X, resolve standards from `projects.config.standards`:

| Role | Lanes injected | Why |
|---|---|---|
| sem-campaign-lead | `sem` (when codified) + `general` | strategy spans the SEM discipline |
| google-ads-specialist | `sem` + `general` | platform-specific execution |
| meta-ads-specialist | `sem` + `general` | platform-specific execution |
| platform-ads-coordinator | `sem` + `general` | multi-platform execution |

`context/standards/sem/` is not yet seeded — for v1 of the team, reference `context/standards/general.md` only. If `sem/` folder is missing, agents note "SEM standards not yet codified" in their spawn prompt and proceed. **Don't auto-create the folder** — humans seed it after the team is operational and patterns emerge.

Note: SEM agents do NOT need `web` / `api` / `db` standards (the dev team's lane). Ad-ops touches platform UIs and configs, not the project's web/api/db surfaces.

## Kanban schema codes (`tasks.assigned_role`)

Within `team='sem'` projects, integer codes map to (proposed — finalize when DB CHECK constraint is extended):

| Code | Role |
|---|---|
| 31 | sem-campaign-lead |
| 32 | google-ads-specialist |
| 33 | meta-ads-specialist |
| 34 | platform-ads-coordinator |

The range partition (31-40 = sem team) follows the existing pattern (1..10 = dev, 11..20 = novel, 21..30 = seo, 31..40 = sem). Range allocation may shift; confirm against `api/src/constants.py::TaskRole` before extending the DB.

## Lifecycle (per task — operational)

1. **Active project + team** already resolved by meta-Lead before this playbook is loaded.
2. **Read relevant context**:
   - `context/projects/<active>/shared/decisions.md` (always)
   - `shared/sem-decisions.md` (if exists — locked SEM-specific decisions: target_language, platform mix defaults, ROAS targets, attribution model)
   - `shared/campaign-brief.md` (if sem-campaign-lead has produced it)
   - `<role>/current-state.md` for each role about to be spawned
   - `standards/general.md` always; `standards/sem/` if codified
3. **Decide which roles to spawn.** New campaign → sem-campaign-lead first. Per-platform build-out → google-ads / meta-ads / platform-ads in parallel. Creative review → content-team agents. Landing-page review → content-seo-optimizer.
4. **Spawn via the Agent tool** — see [.claude/docs/spawn-template.md](.claude/docs/spawn-template.md). Independent platform specialists can be spawned in parallel.
5. **Verify subagent results** — open modified files; review proposed `shared/*` updates and standards insights.
6. **Apply per-project shared updates yourself.** Stamp `decisions.md` entries with date + proposing role.
7. **Update task status in the DB** — same protocol as dev playbook: `process_status=2` + `started_at` on start; `process_status=5` + `completed_at` on done; `process_status=4` + comment on block.
8. **Handoff or close** — package the reviewed blueprints + operator checklists; summarize to user (2-3 sentences); user launches campaigns externally.

## SEM-specific anti-patterns

- **Recommending campaign budgets without ROAS targets attached** — every budget allocation MUST cite break-even ROAS (= 1/margin) and target ROAS; "spend $10K on Meta" without ROAS math is not a plan.
- **Single-platform strategy without attribution / multi-touch consideration** — a Meta-only ROAS view ignores cross-channel influence (Search + Meta + Display work together); pick an attribution model upfront and document it.
- **Ignoring landing page conversion in campaign design** — campaign ROAS bottlenecks at the page; even a perfect campaign with a broken landing page burns budget. Always include landing-page review in the lifecycle.
- **Skipping platform Terms of Service / policy review** — Meta's medical/financial/health/political ad policies vary by jurisdiction; Google's trademark policy varies; Reddit's brand-safety per-subreddit varies; flag policy-sensitive categories at strategy stage, not at rejection time.
- **Hard rule: never execute live ad mutations.** Echoes universal rule. SEM agents output recommendations; the operator launches campaigns via platform UI / API. NO direct Ads API write calls against live accounts.
- **Allocating budget across too many platforms** — $5K spread across 10 platforms underfunds every test; concentrate budget on 2-3 platforms with the strongest funnel-stage fit.
- **Recommending Smart Bidding before conversion volume justifies it** — Smart Bidding needs 30+ conversions / 30 days on Google, 50+ on Meta; recommending it on a cold account wastes budget on learning-phase exploration.
- **Fabricating platform-rate data** — CPM / CPC benchmarks vary by quarter; if not verified from a current source, label estimates "training-data heuristic" and recommend operator pull current benchmarks.
- **Skipping creative review before launch** — every ad copy variant goes through content-editor (+ thai-proofreader if target_language=th, + content-veracity-checker if claims present) BEFORE the operator launches. Bad copy = bad ROAS, regardless of bidding sophistication.
- **Mid-test budget / audience / creative changes** — Meta's 24-hour learning phase + Google's tCPA / tROAS learning periods are real; avoid mid-test changes unless the ad set has clearly failed (3-5× target CPL with no signal of improvement).

Universal anti-patterns in root CLAUDE.md and [.claude/docs/lessons.md](.claude/docs/lessons.md).

## Cross-cutting conventions

- **Research-first:** non-trivial SEM tasks open with a researcher spawn (Haiku) before the specialist. "Non-trivial" signals for SEM: new platform the team hasn't worked before, platform-policy update reference (e.g., iOS 14+ AEM rollout, GDPR-tracking change), new ad format (e.g., Advantage+ Shopping Campaign launch), comparison decision (bidding strategy A vs. B, attribution model selection). Escape valves: continuation of a researched campaign, trivial single-edit follow-up (e.g., add 3 keywords), monthly reporting cadence on a stable campaign.
- **Firecrawl-first for external fetch:** sem-campaign-lead + all three platform specialists default to the `firecrawl` skill for ad-library / competitor / landing-page / SERP fetching. WebFetch is fallback only. Documented in each agent's "Web search tool preference" section.
- **AC at task creation:** every Kanban task carries `acceptance_criteria` in the same POST call. Per-AC verdict gates DONE-flip per universal Lead rules.
- **Modular scaling:** if a single platform under platform-ads-coordinator grows large (e.g., LinkedIn consumes 60%+ of an account's budget across many campaigns), recommend splitting out to a dedicated agent (e.g., linkedin-ads-specialist) in standards insights. The coordinator pattern is intentionally a scaling stepping-stone, not a permanent home for every secondary platform.
