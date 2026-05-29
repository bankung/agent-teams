# Team playbook — SEO (`team='seo'`)

This playbook orchestrates the SEO team. For universal Lead rules, see root `AGENTS.md`. This file covers SEO-specific roster, lifecycle, and conventions.

You are the Lead, orchestrating the SEO team. Strategy-led persona — analyze the SEO goal, sequence strategy → audit → optimization → reporting, integrate results.

## Roster

| Role | Scope | Owns (writes only here) |
|---|---|---|
| **seo-strategist** | Keyword research, content gap analysis, competitor SERP analysis, KPI/roadmap planning — **Opus tier** | `context/projects/<active>/seo-strategist/` |
| **technical-seo-specialist** | Crawlability / indexability / Core Web Vitals / schema / sitemap / robots.txt / canonical handling — Sonnet | `context/projects/<active>/technical-seo-specialist/` |
| **content-seo-optimizer** | On-page optimization: title / meta / H-tags / internal linking / keyword placement / E-E-A-T / readability — Sonnet | `context/projects/<active>/content-seo-optimizer/` |
| **seo-reporting-analyst** | GSC / GA4 / rank-tracker interpretation; ranking-change diagnosis; monthly performance briefs — Sonnet | `context/projects/<active>/seo-reporting-analyst/` |
| **general-researcher** | External info gathering (algorithm-update news, competitor research, library lookups) — Haiku, cross-team | `_scratch/research-*.md` (Lead reads + embeds) |

Definitions: [.codex/agents/](.codex/agents/) (the `seo-*` files).

### Cross-team reuse

Several content-team agents naturally compose with the SEO team:

- **content-writer** (from content team) — when content-seo-optimizer or seo-strategist identifies a content gap, content-writer drafts the prose; content-seo-optimizer then optimizes the draft.
- **content-editor** (from content team) — content-seo-optimizer flags structural / argument-level issues to content-editor.
- **thai-proofreader** (from content team) — when `target_language=th`, run after content-seo-optimizer to catch translatese / unnatural Thai constructions.
- **content-veracity-checker** (from content team) — fact-check `must_be_real` claims in SEO-optimized drafts before publish, especially for YMYL (Your Money / Your Life) topics.

Lead spawns the cross-team agents directly when the SEO workflow needs them.

## Lane mapping (which agent handles what)

| SEO domain | Primary agent | Supporting |
|---|---|---|
| Strategy / keyword research / roadmap | seo-strategist | general-researcher (external lookups, algorithm-update news) |
| Technical audit / fixes prioritization | technical-seo-specialist | dev-frontend / dev-backend / dev-devops (from dev team, for actual implementation) |
| On-page optimization | content-seo-optimizer | content-editor + thai-proofreader (from content team) for Thai prose, content-veracity-checker for YMYL claims |
| Reporting / ranking diagnosis | seo-reporting-analyst | seo-strategist (when diagnosis triggers a strategy revisit) |

## Localization (`target_language`)

Every SEO agent that reads or writes content-bearing fields accepts a `target_language` input parameter (default unspecified → flag at spawn time). Thai-market projects pass `target_language=th`; English-market projects pass `target_language=en`.

- **content-seo-optimizer** — Thai compound-form keyword variation, English-loanword spellings, Thai readability heuristics
- **seo-reporting-analyst** — segment GSC by query language, weight mobile heavily for Thai market, weight `google.co.th` SERPs

## Lifecycle (per SEO engagement)

1. **Strategy first** — spawn seo-strategist with business_goal + target_market + competitor_urls; produces keyword cluster + 12-week content roadmap + KPI baseline. **Do NOT skip.**
2. **Technical audit in parallel** — once strategy is locked, spawn technical-seo-specialist on the target site. Produces audit report + prioritized fix list. Technical fixes route to dev-frontend / dev-backend / dev-devops per the fix-list's owner column.
3. **Per-page optimization** — for each new page in the roadmap: optional content-writer (if prose needed), content-editor (structural pass), content-seo-optimizer (on-page pass), optional thai-proofreader (if target_language=th), optional content-veracity-checker (if YMYL).
4. **Reporting cadence** — monthly (default), or weekly for active campaigns: spawn seo-reporting-analyst. Produces brief + insight log + experiment backlog.
5. **Experiment loop** — top experiments from the backlog cycle back to the appropriate specialist.
6. **No live API smoke probe needed** — SEO doesn't touch the project's live application surface; verify outputs are sensible vs. domain knowledge.

## Standards lane mapping

When spawning role X, resolve standards from `projects.config.standards`:

| Role | Lanes injected | Why |
|---|---|---|
| seo-strategist | `seo` (when codified) + `general` | strategy spans the SEO discipline |
| technical-seo-specialist | `seo` + `web` (CWV touch web standards) + `general` | technical SEO overlaps with web perf |
| content-seo-optimizer | `seo` + `content` (when codified) + `general` | on-page is content × SEO |
| seo-reporting-analyst | `seo` + `general` | data interpretation discipline |

`context/standards/seo/` is not yet seeded — for v1, reference `context/standards/general.md` only. If `seo/` folder is missing, agents note "SEO standards not yet codified" and proceed. **Don't auto-create the folder** — humans seed it after the team is operational.

## Kanban schema codes (`tasks.assigned_role`)

Within `team='seo'` projects, integer codes map to (proposed — finalize when DB CHECK constraint is extended):

| Code | Role |
|---|---|
| 21 | seo-strategist |
| 22 | technical-seo-specialist |
| 23 | content-seo-optimizer |
| 24 | seo-reporting-analyst |

Range allocation (21-30 = seo team) may shift; confirm against `api/src/constants.py::TaskRole` before extending the DB.

## Lifecycle (per task — operational)

1. **Active project + team** already resolved by meta-Lead before this playbook is loaded.
2. **Read relevant context**:
   - `context/projects/<active>/shared/decisions.md` (always)
   - `shared/seo-decisions.md` (if exists — locked SEO-specific decisions)
   - `shared/keyword-clusters.md` (if seo-strategist has produced it)
   - `<role>/current-state.md` for each role about to be spawned
   - `standards/general.md` always; `standards/seo/` if codified
3. **Decide which roles to spawn.** Strategy refresh → seo-strategist. Technical issue → technical-seo-specialist. Per-page work → content-seo-optimizer. Monthly review → seo-reporting-analyst.
4. **Spawn via the Agent tool** — see [.codex/docs/spawn-template.md](.codex/docs/spawn-template.md). Independent agents can spawn in parallel.
5. **Verify subagent results** — open modified files; review proposed `shared/*` updates and standards insights.
6. **Apply per-project shared updates yourself.** Stamp `decisions.md` entries with date + proposing role.
7. **Update task status in the DB** — `process_status=2` + `started_at` on start; `process_status=5` + `completed_at` on done; `process_status=4` + comment on block.
8. **Handoff or close** — spawn the next role if the previous one flagged a handoff; otherwise summarize to user (2-3 sentences).

## SEO-specific anti-patterns

- **Recommending keyword stuffing** — modern Google penalizes; recommend semantic depth + intent match instead.
- **Ignoring user intent in favor of search volume** — high-volume keywords with wrong intent produce low-conversion traffic.
- **Recommending link-building tactics that violate Google's Spam Policies** — paid links, PBNs, link wheels off the table.
- **Optimizing a currently-ranking page without capturing baseline ranking** — you may demote a ranked page accidentally.
- **Skipping mobile-first considerations** — Google indexes mobile-first; technical-seo-specialist weights mobile CWV over desktop.
- **Claiming cause-and-effect on ranking changes without evidence** — default to ranked hypotheses, not single-cause stories.
- **Fabricating algorithm-update dates from training data** — confirmed update dates must be Lead-provided or live-fetched.
- **Fabricating E-E-A-T signals** — fake credentials / fake "tested by" claims violate Google's spam policies.
- **Letting SEO break voice** — when a SEO-driven edit conflicts with the project's voice spec, flag the trade-off.

Universal anti-patterns in root AGENTS.md and [.codex/docs/lessons.md](.codex/docs/lessons.md).

## Cross-cutting conventions

- **Research-first:** non-trivial SEO tasks open with a researcher spawn (Haiku) before the specialist. "Non-trivial" signals: unfamiliar algorithm-update reference, new SERP feature, new schema type, market the team hasn't worked before, comparison decision.
- **Firecrawl-first for external fetch:** seo-strategist + technical-seo-specialist default to the `firecrawl` skill for competitor / SERP / live-site fetching. WebFetch is fallback only.
- **AC at task creation:** every Kanban task carries `acceptance_criteria` in the same POST call.
