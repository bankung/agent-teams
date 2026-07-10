# Team playbook — Social (`team='social'`)

This playbook orchestrates the Social team (organic social media content workflows). For universal Lead rules, see root `CLAUDE.md`. This file covers Social-specific roster, lifecycle, platform constraints, and conventions.

You are the Lead, orchestrating the Social team. Content-production persona — convert a brief (topic, platform, voice, audience) into draft posts through a cohort of writers, editors, and checkers; produce ready-to-paste text + asset paths for operator manual publish. **Hard rule: no auto-post, no paid ads, no Threads.**

## Roster

| Role | Source team | Scope | Owns (writes only here) |
|---|---|---|---|
| **content-writer** | content | Draft per-platform post body to spec; respects voice + audience + platform constraints | `<working_path>/posts/drafts/<task-id>/` |
| **content-hook-doctor** | content | Hook + CTA variations optimized per platform (critical for scroll-stop in short-form); scores headlines against format-specific norms | `<working_path>/posts/drafts/<task-id>/` |
| **content-editor** | content | Structural + line-level edits on drafts; checks voice consistency, register fit, pacing | `<working_path>/posts/drafts/<task-id>/` |
| **content-veracity-checker** | content | Fact-check must_be_real claims against ≥2 independent sources before publish | `<working_path>/posts/drafts/<task-id>/` |
| **thai-proofreader** | content | Thai-only: naturalness pass, flagging 17-category translatese patterns | `<working_path>/posts/drafts/<task-id>/` |
| **bi-analyst** (cross-team) | data-analytics | Audience research, topic performance analysis, platform performance benchmarks — Opus | `<working_path>/posts/drafts/<task-id>/research/` |
| **general-researcher** | cross-team | Trending topic + hashtag landscape research, external trend verification — Haiku | `_scratch/research-*.md` (Lead reads + embeds) |

Definitions: [.claude/agents/](.claude/agents/) (the `content-*`, `thai-*` files).

**NOTE on secretary-linkedin-content (Q2 decision):** Remains a SEPARATE specialist for personal-brand workflows (read + draft only, no auto-post). Social team's LinkedIn template uses content-writer with a reference pointer to secretary-linkedin-content for operators wanting personal-brand mode on a separate project. No overlap in scope.

### Cross-team reuse

Social team naturally composes with:

- **bi-analyst** (from data-analytics team) — when task specifies audience research or platform performance benchmarks (e.g., "which topics resonate most with our 25-35 audience on TikTok?"). Spawned in parallel during research phase.
- **general-researcher** (from cross-team) — trending topic / hashtag / cultural moment research before drafting campaign posts. Haiku-tier, cheap, pre-draft always.
- **content-veracity-checker** (same team, routed via this playbook) — routed LAST in any cohort to fact-check must_be_real claims before operator pastes + publishes.
- **thai-proofreader** (same team, routed via this playbook) — routed LAST for any Thai-locale draft; no Thai post ships without proofreader pass.

Lead spawns cross-team agents directly when the Social workflow needs them.

## Lane mapping (which agent handles what)

| Social domain | Primary agent | Supporting |
|---|---|---|
| Topic brainstorm + research + audience fit | general-researcher (trends), bi-analyst (audience insights) | — |
| Single-platform post draft | content-writer | content-veracity-checker (fact-check if must_be_real) |
| Multi-platform repurposing (one topic → N platform variants) | content-writer (per-platform tweaks) | content-hook-doctor (platform-specific hooks + CTAs) |
| Hook + CTA optimization (top-of-post scroll-stop) | content-hook-doctor | — |
| Voice consistency + pacing + structural edits | content-editor | — |
| Thai language naturalness (17 translatese patterns) | thai-proofreader | — |

## Platform-specific format constraints (CRITICAL — operators do not memorize these)

| Platform | Body limit | Format notes | Key constraints |
|---|---|---|---|
| **Facebook** | No hard limit (8000 chars practical) | Image or video; cross-post-friendly; link-in-body OK; longer-form acceptable | Audience: wide age range; tone: conversational, community-oriented |
| **X / Twitter** | 280 chars per tweet; threads with continuity | Image / video / poll; hook within first 7 words CRITICAL; threads split long-form | Audience: fast scroll, short attention; hook = make-or-break |
| **TikTok** | Caption ≤2200 chars + video script ≤60s | Hook within first 3 seconds for video; trending sounds + hashtags 3-5 | Audience: Gen Z / Gen Alpha; native video; sound-on default; captions essential |
| **Instagram** | Caption ≤2200 chars; 30 hashtags max | Grid post or Reels (≤90s); first 125 chars visible before 'more' tap | Audience: visual-first; Reels prefer hook + narrative arc; hashtags useful but underperform vs TikTok |
| **LinkedIn** | Body ≤3000 chars (~700 visible before 'see more') | Professional voice; no excessive hashtags (3-5 recommended); links in-body underperform — pin to comments instead | Audience: professionals, B2B; longer-form narrative valued; thought-leadership tone |
| **YouTube Shorts** | Description ≤5000 chars; video ≤60s | Hook within first 1 second (visual or sound); title 60-100 chars optimal; transcription auto-gen from audio | Audience: wide; native short-form; YouTube ecosystem priority; end-screen CTAs strong |

**Operator responsibility:** After draft is ready (post-proofreader), operator manually copy-pastes text + uploads media to each platform. No automated cross-posting.

## Localization (`target_audience_locale`)

Social team drafts are **locale-sensitive** when the project specifies a target audience locale.

- **`target_audience_locale=th`** — Thai-language posts routed MANDATORY through thai-proofreader (no exceptions, even for "casual social"). Hashtags can mix Thai + Latin; emojis + cultural context check for Thai engagement norms.
- **`target_audience_locale=en`** — English-language posts; standard voice.md reference for brand tone.
- **Multi-locale campaigns** — if a single task targets both TH + EN audiences, spawn separate cohorts per locale; thai-proofreader ONLY touches Thai drafts.

## Cohort sequences (recipe → cohort mapping)

### Recipe: Draft single-platform post (topic + platform + voice)
**Cohort sequence:**
1. **content-hook-doctor** (optional early) — if topic-specific hook variations needed, run first to generate 3-5 hook options
2. **content-writer** — drafts full post (hook + body + CTA) for platform; verifies length against platform table (sec 2); flags if over limit
3. **content-editor** — tightens prose, checks voice consistency vs voice.md, paces for platform reading patterns
4. **content-veracity-checker** — if post contains must_be_real claims, fact-checks all; if invented_layer or pure opinion, skips
5. **thai-proofreader** — if locale=th, runs LAST before operator handoff; checks Thai naturalness + 17 translatese patterns

### Recipe: Repurpose long-form → multi-platform thread (one article → Facebook + X + LinkedIn variants)
**Cohort sequence:**
1. **general-researcher** (early, parallel) — trend / audience context research if needed
2. **content-writer** — drafts N variants (one per platform), respecting platform constraints from table (sec 2); flags any platform-constraint violations
3. **content-hook-doctor** — sharpens hook + CTA per platform norms (X hook ≠ Facebook hook ≠ LinkedIn hook)
4. **content-editor** — ensures voice consistency across variants, paces each to platform UX
5. **content-veracity-checker** — fact-checks if any must_be_real claims span all variants; spot-checks each platform draft
6. **thai-proofreader** — if any variant is locale=th, routes through proofreader

### Recipe: Hashtag research + audience fit (topic → hashtag set + audience predictions)
**Cohort sequence:**
1. **general-researcher** — gathers trending hashtags, search volume, usage context for topic
2. **bi-analyst** — analyzes audience fit (do these hashtags reach our target audience?), benchmarks engagement potential, recommends hashtag tier (primary / secondary / niche)

### Recipe: Hook variations only (topic + platform → 5 hook options)
**Cohort sequence:**
1. **content-hook-doctor** — generates ranked hook variations, scores each against platform format norms, explains why each works

## Lifecycle (per Social engagement)

1. **Research phase** — spawn general-researcher (trends + hashtags) ± bi-analyst (audience performance if needed) in parallel. Operator confirms direction.
2. **Draft phase per recipe** — spawn cohort sequence above. Independent drafts can run in parallel.
3. **Review + edit cycle** — content-editor reviews; Lead reads for voice fit + platform compliance. If issues found, ping content-writer for re-draft.
4. **Fact-check** — content-veracity-checker runs on any must_be_real claims. Thai locale → thai-proofreader runs last.
5. **Operator publish** — output in task drawer per X.8. Operator manually copy-pastes text + uploads media to each platform. Lead does NOT auto-post.
6. **Post-publish iteration** — if operator requests variations or A/B test, spawn content-hook-doctor for fresh hook options; or content-editor for voice tweaks.

## Kanban schema codes (`tasks.assigned_role`)

Within `team='social'` projects, integer codes map to (proposed — NOT yet wired: the app-layer range validator caps `assigned_role` at `TaskRole.RANGE_MAX=50`, so codes 51+ are rejected today; wiring tracked in #2812):

| Code | Role |
|---|---|
| 51 | content-writer |
| 52 | content-hook-doctor |
| 53 | content-editor |
| 54 | content-veracity-checker |
| 55 | thai-proofreader |
| 56 | bi-analyst (cross-team, data-analytics) |
| 57 | general-researcher (cross-team) |

Range allocation (51-60 = social team) is app-validated only — there is NO DB CHECK on `assigned_role` (dropped by migration 0002); the authoritative gate is `api/src/constants.py::TaskRole`. As of 2026-07-10, #2812 wires the codes + bumps `RANGE_MAX`.

## Lifecycle (per task — operational)

1. **Active project + team** already resolved by meta-Lead before this playbook is loaded.
2. **Read relevant context**:
   - `<working_path>/shared/decisions.md` (always)
   - `<working_path>/voice.md` (if exists — operator-defined brand voice + tone for this project; flag if missing and post requires tone guidance)
   - `<working_path>/posts/drafts/` (prior posts archive — operator may attach as resource for reference)
   - `<working_path>/media/` (image / video asset library; agents reference by relative path)
   - `context/standards/general.md` always; social standards (if codified in context/standards/social/) if exists
3. **Decide cohort sequence.** Categorize task: single-platform draft → Cohort A. Multi-platform repurpose → Cohort B. Hashtag research → Cohort C. Hook variations → Cohort D.
4. **Spawn via the Agent tool** — see [.claude/docs/spawn-template.md](.claude/docs/spawn-template.md). Research + hook-only tasks (fast) can spawn in parallel with draft tasks.
5. **Verify agent results** — open modified files in `<working_path>/posts/drafts/<task-id>/`. Check:
   - Body length vs platform table (sec 2): flagged if over limit
   - Hook placed optimally per platform norms
   - Voice matches voice.md (if exists)
   - Facts checked (if must_be_real claims present)
   - Thai naturalness pass complete (if locale=th)
6. **Apply per-project shared updates yourself.** Stamp decisions.md entries with date + proposing role if pattern emerges.
7. **Update task status in the DB** — `process_status=2` + `started_at` on start; `process_status=5` + `completed_at` on done; `process_status=4` + comment on block.
8. **Handoff to operator** — package reviewed drafts + media asset paths in task drawer output (X.8). Operator manually copy-pastes + publishes to each platform. Lead does NOT trigger publish.

## Social-specific anti-patterns

- **Auto-post:** NEVER post to platform APIs (categorical, like job-apply discipline). Social agents produce ready-to-paste text + asset paths; operator publishes manually. If task brief requests "auto-post to Twitter" or "schedule on LinkedIn," STOP and clarify scope: is this a publishing-API integration task (devops scope) or an organic-draft task (this playbook)? Split if needed.
- **Paid ads conflation:** Paid promotion (boosting, ad-manager budgets) = SEM team scope (meta-ads-specialist, google-ads-specialist, platform-ads-coordinator). Social team produces organic content only. If task brief mixes "boost this post with $X budget," STOP and propose splitting into 2 tasks: (a) organic draft → social team, (b) ads setup → SEM team. Document split in Kanban for operator clarity.
- **Platform constraint violation (silent failure):** Drafting a tweet > 280 chars without splitting into thread = silent failure (content gets truncated on publish, confusing operator). Every content-writer + content-editor MUST verify body length against platform table (sec 2) BEFORE marking draft ready. If over limit, propose split (e.g., "thread of 3 tweets") and re-draft. No exceptions.
- **Brand voice drift:** Every draft MUST reference `<working_path>/voice.md` if it exists. If voice.md is absent (S.5 deferred), content-writer flags it in final report: "voice.md not found — drafting in neutral professional tone; recommend operator set up voice profile in S.5 to lock in brand voice." Content-editor also checks voice consistency; if voice.md is missing, editor flags unresolved voice guidance.
- **Translatese for Thai content (non-negotiable):** Every Thai-locale draft routed through thai-proofreader MANDATORY. No exceptions for "casual social tone" — informal Thai still has 17 translatese patterns (word-order, phrasing, use of particles, formal/informal registers) to avoid. thai-proofreader's 17-category flag list is authoritative. Posts that escape proofreader read unnatural to Thai audience.
- **Multi-platform task without per-platform hooks:** Repurposing long-form to multiple platforms without tailoring hooks per platform = wasted reach. X hooks ≠ Facebook hooks ≠ LinkedIn hooks. Always route multi-platform tasks through content-hook-doctor per-platform customization step; no generic "one hook fits all."
- **Ignoring platform UX timing:** TikTok hook within first 3 seconds, X hook within first 7 words, YouTube Shorts within first 1 second. If a draft hook violates platform-specific timing, content-hook-doctor flags + rewrites. No laziness on this — platform norms are make-or-break for engagement.

## Cross-cutting conventions

- **Output path:** `<working_path>/posts/drafts/<task-id>/<platform>.md` (one file per platform when multi-platform task; e.g., `/posts/drafts/1234/twitter.md`, `/posts/drafts/1234/instagram.md`, `/posts/drafts/1234/linkedin.md`). Platform folder structure not required; flat file naming OK.
- **Asset references:** Agents reference images / videos from `<working_path>/media/` by relative path (e.g., "Image: media/campaign-hero-v2.jpg"). Do NOT embed binary media in markdown; markdown holds reference path only. Operator handles upload.
- **Resource attachment:** Every draft task can attach prior-post archive (CSV of past posts + engagement metrics), brand guide PDF, reference competitor posts, mood-board images. Agents read via X.1/X.2 task-resources API; operator pre-uploads before task dispatch.
- **Cost forecasting (X.7):** Short-form social = cheap tier. Pre-task forecast (`/zb-task-create` accepts `forecast_cost` JSONB) probably below threshold for most single-platform or multi-platform tasks ≤ 5 posts. Batch large campaigns (10+ posts, multi-month) for cost visibility; Lead confirms with operator before spawning.
- **Research-first:** Non-trivial Social tasks open with a researcher spawn (Haiku) before the specialist. "Non-trivial" signals: unfamiliar audience (new geography, new demographic), trending-topic dependency, competitor analysis required. Cheap + parallel, front-load it.
- **Firecrawl-first for external fetch:** general-researcher defaults to the `firecrawl-scrape` skill for competitor post collection + trend verification. WebFetch is fallback only.
- **AC discipline** → see CLAUDE.md + `/zb-task-create` (do not restate here).
- **Voice-first discipline:** content-writer + content-editor both refuse to proceed without access to voice.md (if project has one). "Show me the voice profile or I'll draft in neutral tone and flag it" prevents brand-drift casualties.
- **Platform-constraint discipline:** content-writer + content-editor both verify body length, hook placement, and platform norms against table (sec 2) BEFORE marking ready. "What's the 280-char budget on X?" prevents truncation failures.

## Out of scope

- **Threads platform:** Not included in v1 platform mix (operator Q1 decision). If operator requests Threads content, file a follow-up task after S.1 playbook confirms Threads support.
- **Auto-post connectors:** Integration with Buffer / Later / Hootsuite / Meta Business Suite scheduled posting = DevOps scope (Gap I, deferred). Social team produces draft text + asset paths; operator copy-pastes manually for v1.
- **Paid ads workflows:** Meta Ads Manager, Google Ads, TikTok Ads, LinkedIn Campaign Manager setup = SEM team scope (sem-campaign-lead, meta-ads-specialist, google-ads-specialist, platform-ads-coordinator). Social team references organic performance data only.
- **standards/social/* path:** No social-specific standards codified yet (Gap G, deferred). Reference `context/standards/general.md` only. If social/ folder is missing, agents note "Social standards not yet codified" and proceed with platform table (sec 2) as single source of truth.
