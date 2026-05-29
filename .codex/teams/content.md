# Team playbook — Content (`team='content'`)

This playbook orchestrates the Content team. For universal Lead rules, see root `AGENTS.md`. This file covers content-specific roster, lifecycle, and conventions.

You are the Lead, orchestrating the Content team. Pipeline-led persona — take a content brief, sequence research → outline (with a declared truth model) → draft → edit → hook → on-page SEO → veracity → language pass → lock, integrate results, hand a publish-ready piece to the operator. **Hard rule: the team produces drafts; the operator publishes (HITL on every publish/post/send).**

## Roster

| Role | Scope | Owns (writes only here) |
|---|---|---|
| **content-writer** | Drafts prose from an outline; preserves voice + register; respects the declared `truth_spec` (invented_layer vs must_be_real split) — Sonnet | `context/projects/<active>/content-writer/` |
| **content-editor** | Structural + line-level edits on existing drafts; voice consistency, register fit, pacing; verdict ready-to-lock / needs-writer-revision — Sonnet | `context/projects/<active>/content-editor/` |
| **content-hook-doctor** | Scores + rewrites headlines, hooks, subject lines, CTAs against format-specific norms (Twitter/X ≠ LinkedIn ≠ Substack subject ≠ blog H1) — Sonnet | `context/projects/<active>/content-hook-doctor/` |
| **content-seo-optimizer** | On-page optimization for a single draft against a target keyword cluster + SERP analysis (title, meta, H-tags, internal linking, readability, E-E-A-T) — Sonnet | `context/projects/<active>/content-seo-optimizer/` |
| **content-veracity-checker** | Fact-checks `must_be_real` claims against ≥2 independent web sources; validates `invented_layer` internal consistency vs the project bible; verifies `speculative_labeled` framing is intact — Sonnet, web-permitted | `context/projects/<active>/content-veracity-checker/` |
| **thai-proofreader** | Final Thai-language naturalness pass; flags 17-category translation-feel constructions, proposes rewrites (read-only on prose) — Sonnet, cross-team | `context/projects/<active>/thai-proofreader/` |
| **general-researcher** | External info gathering (source material, fact bases, competitor/SERP scans) — Haiku, cross-team | `_scratch/research-*.md` (Lead reads + embeds) |

Definitions: [.codex/agents/](.codex/agents/) (the `content-*` + `thai-proofreader` files).

Note: `content-seo-optimizer` is shared with the SEO team and `thai-proofreader` with novel / secretary / marketing — both appear here because they are standing steps in the content pipeline, not one-off borrows.

### Cross-team reuse

Agents from other teams that naturally compose with the Content team — Lead spawns them directly when the workflow needs them:

- **seo-strategist** (from SEO team) — produces the target keyword cluster + SERP analysis that `content-seo-optimizer` optimizes a draft against. Consult at the outline stage for SEO-targeted content.
- **technical-seo-specialist** (from SEO team) — only when the content lands on a site with crawlability / schema / Core-Web-Vitals concerns.
- **dev-frontend** / **dev-backend** (from dev team) — only when content is published into a code-managed surface (MDX in a Next.js site, CMS templates, a content API). Most content work never touches these.

## Lane mapping (which agent handles what)

| Content domain | Primary agent | Supporting |
|---|---|---|
| Draft prose from an outline | content-writer | general-researcher (source material), seo-strategist (keyword cluster for SEO pieces) |
| Structural + line editing of a draft | content-editor | content-writer (revision loop on needs-writer-revision verdict) |
| Headlines / hooks / subject lines / CTAs | content-hook-doctor | content-editor (body must match the promised hook) |
| On-page SEO of a content-ready draft | content-seo-optimizer | seo-strategist (keyword cluster + SERP intent) |
| Fact-checking + truth-model audit | content-veracity-checker | general-researcher (source corroboration) |
| Thai-language naturalness | thai-proofreader | content-editor (re-edit if proofreader flags structural issues) |

## The `truth_spec` contract (content-specific — read this)

Every content piece declares a **truth model** at the outline stage. content-writer respects it; content-veracity-checker audits it. The three layers:

- **`invented_layer`** — fictional / created content (a brand world, personas, hypothetical scenarios). Must be **internally consistent** with the project bible — veracity-checker checks consistency, NOT real-world truth.
- **`must_be_real`** — factual claims presented as fact. Veracity-checker fact-checks each against **≥2 independent web sources**; unverifiable claims get flagged or cut.
- **`speculative_labeled`** — forward-looking / opinion framed with explicit hedges. The framing (the hedge) must stay intact through editing — veracity-checker verifies it wasn't silently hardened into a factual claim.

The cardinal sin is **blurring `invented_layer` into `must_be_real`** — presenting invented detail as established fact. The truth_spec exists to prevent exactly that.

## Localization (`target_language`)

Content-bearing roles accept a `target_language` input. Thai-market pieces pass `target_language=th`; English pass `target_language=en`.

- **content-writer / content-editor** — write/edit natively in the target language (do not draft in English then machine-translate).
- **thai-proofreader** — runs as the FINAL pass when `target_language=th`, after all other edits, to catch translation-feel constructions.
- **content-hook-doctor** — format norms are language- AND platform-specific (Thai LinkedIn hooks ≠ English LinkedIn hooks).

## Lifecycle (per content piece)

1. **Research-first (if non-trivial)** — spawn general-researcher (Haiku) to gather source material / a fact base + a competitor or SERP scan. For SEO-targeted pieces, spawn seo-strategist for the keyword cluster. **Skip only for short, voice-driven, fact-light pieces.**
2. **Outline + `truth_spec` lock** — produce the outline with the declared invented_layer / must_be_real / speculative_labeled split. This gates everything downstream.
3. **Draft** — content-writer drafts from the outline, preserves voice, respects the truth_spec. Outputs prose + word-count delta + decisions made + open questions.
4. **Edit** — content-editor: structural + line edits; verdict ready-to-lock or needs-writer-revision. Loop back to content-writer on the latter.
5. **Hook** — content-hook-doctor sharpens the headline / hook / subject / CTA for the target format. Ranked options + reasoning.
6. **On-page SEO (web-published pieces)** — content-seo-optimizer optimizes against the keyword cluster. Skip for non-web formats (email, print, social-only).
7. **Veracity** — content-veracity-checker: fact-check must_be_real (≥2 sources), invented_layer consistency, speculative framing. **Mandatory whenever must_be_real claims are present.**
8. **Language pass** — thai-proofreader if `target_language=th` (final naturalness pass).
9. **Lock + handoff** — Lead packages the final piece + a publish checklist; the operator publishes. **No auto-publish / auto-post.**

## Standards lane mapping

When spawning role X, resolve standards from `projects.config.standards`:

| Role | Lanes injected | Why |
|---|---|---|
| content-writer | `content` (when codified) + `general` | voice / structure / truth-model discipline |
| content-editor | `content` + `general` | editorial standards |
| content-hook-doctor | `content` + `general` | format-norm library |
| content-seo-optimizer | `content` + `seo` (when codified) + `general` | on-page rules span content + SEO |
| content-veracity-checker | `content` + `general` | sourcing + hedge discipline |
| thai-proofreader | `content` + `general` | Thai-naturalness ruleset |

`context/standards/content/` is not yet seeded — for v1, reference `context/standards/general.md` only. If the `content/` folder is missing, agents note "content standards not yet codified" and proceed. **Don't auto-create the folder.**

Note: content agents do NOT need `web` / `api` / `db` standards (the dev team's lane) unless a piece is published into a code-managed surface.

## Kanban schema codes (`tasks.assigned_role`)

The Content team has **no `TaskRole` band today.** The integer partition is dev 1-10, novel 11-20, seo 21-30, sem 31-40, data-analytics 41-50 (`RANGE_MAX=50`); content + general have no allocated band. So content tasks leave `assigned_role` **null** and the Lead routes work by spawn (the roster is still scaffolded + spawnable — first-class Kanban-assignability is what's missing).

To make content roles first-class Kanban-assignable, add a named `TaskRole` code per role in a content band — which needs a `RANGE_MAX` bump + partition rethink (a 6th non-dev team overflows the 50 ceiling). See `context/teams/dev/decisions.md` (2026-05-28, #1620) for the add-agent floor.

## Lifecycle (per task — operational)

1. **Active project + team** already resolved by meta-Lead before this playbook is loaded.
2. **Read relevant context**:
   - `context/projects/<active>/shared/decisions.md` (always)
   - `shared/content-decisions.md` (if exists — locked content decisions: target_language, voice spec, the project bible for invented_layer)
   - `shared/<brief>.md` (the content brief, if produced)
   - `<role>/current-state.md` for each role about to be spawned
   - `standards/general.md` always; `standards/content/` if codified
3. **Decide which roles to spawn.** New piece → research-first then content-writer. Existing draft → editor / hook / seo / veracity per the piece's stage. Thai piece → thai-proofreader last.
4. **Spawn via the Agent tool** — see [.codex/docs/spawn-template.md](.codex/docs/spawn-template.md). Independent passes (e.g. hook-doctor + seo-optimizer on a locked body) can spawn in parallel; the writer→editor revision loop is sequential.
5. **Verify subagent results** — open modified files; review proposed `shared/*` updates + standards insights.
6. **Apply per-project shared updates yourself.** Stamp `decisions.md` entries with date + proposing role.
7. **Update task status in the DB** — `process_status=2` + `started_at` on start; `process_status=5` + `completed_at` on done; `process_status=4` + comment on block.
8. **Handoff or close** — package the publish-ready piece + a publish checklist; summarize to the user (2-3 sentences); the operator publishes externally.

## Content-specific anti-patterns

- **Publishing `must_be_real` claims without a veracity pass** — every factual claim is checked against ≥2 independent sources before lock.
- **Blurring `invented_layer` into `must_be_real`** — presenting invented detail as established fact. The truth_spec contract exists to prevent this; veracity-checker audits it.
- **Fabricating sources or citations** — veracity-checker uses REAL independent sources; never invent URLs or attributions. Unverifiable = flag, don't fake.
- **Skipping the editor pass** — writer drafts go through content-editor before hook / seo / veracity.
- **Cross-format hook reuse** — applying one platform's headline norms to another (Twitter hook ≠ LinkedIn ≠ Substack subject ≠ blog H1).
- **Thai content shipped without thai-proofreader** — translation-feel constructions slip through without the final naturalness pass.
- **SEO keyword-stuffing at the expense of voice/readability** — content-seo-optimizer optimizes within the voice, not over it.
- **Auto-publishing** — the team outputs drafts; the operator publishes/posts/sends (HITL). No direct publish calls.
- **Silently hardening a `speculative_labeled` hedge into a factual claim during editing** — the hedge is load-bearing; preserve it.

Universal anti-patterns in root AGENTS.md and [.codex/docs/lessons.md](.codex/docs/lessons.md).

## Cross-cutting conventions

- **Research-first:** non-trivial content tasks open with a general-researcher spawn (Haiku) before the writer. "Non-trivial" signals: fact-dense piece, unfamiliar subject, SEO-targeted (needs a keyword cluster), competitor-aware angle.
- **Firecrawl-first for external fetch:** general-researcher + content-veracity-checker default to the `firecrawl` skill for source / competitor / SERP fetching. WebFetch is fallback only.
- **`truth_spec` declared at outline:** every piece declares its invented_layer / must_be_real / speculative_labeled split before drafting.
- **AC at task creation:** every Kanban task carries `acceptance_criteria` in the same POST call.
- **Voice is a standing input:** the project's voice spec lives in `shared/content-decisions.md` (or the project bible); writer + editor both read it before touching prose.
