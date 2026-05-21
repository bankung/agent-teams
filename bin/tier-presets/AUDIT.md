# Agent Tier Audit — Kanban #1360
# Generated: 2026-05-21
# Covers: all .claude/agents/*.md (37 files, excluding _dev-shared.md)
#
# Tier MAX  = operator's full preset (no change from baseline)
# Tier L2   = Pro pilot preset (routine roles → Sonnet; sr-* + strategists → Opus)
# "implicit-Opus" = no model: line in frontmatter; harness defaults to Opus

| Agent | Current `model:` (frontmatter) | Tier MAX | Tier L2 | Risk notes if downgrade |
|---|---|---|---|---|
| dev-backend | sonnet | sonnet | sonnet | No change — already at L2. Sonnet handles routine FastAPI CRUD endpoints fine; design-heavy work routes to dev-sr-backend. |
| dev-frontend | sonnet | sonnet | sonnet | No change — already at L2. Routine Next.js component work fits Sonnet; new surfaces route to dev-sr-frontend. |
| dev-devops | sonnet | sonnet | sonnet | No change — already at L2. Docker / CI config is mechanical; no design judgment needed. |
| dev-tester | sonnet | sonnet | sonnet | No change — already at L2. Pytest + curl test generation fits Sonnet well. |
| dev-reviewer | sonnet | sonnet | sonnet | No change — already at L2. Code-review checklist work is pattern-matching; fits Sonnet. |
| dev-spec-reviewer | sonnet | sonnet | sonnet | No change — already at L2. Spec audit is structured analysis; Sonnet is adequate. |
| dev-security-reviewer | sonnet | sonnet | sonnet | No change — already at L2. Security checklist review fits Sonnet; deep architectural security → manual or sr-backend. |
| dev-analyst | sonnet | sonnet | sonnet | No change — already at L2. Spec expansion from a brief is structured reasoning; Sonnet handles it. |
| dev-sr-backend | opus | opus | opus | No change — stays Opus per tier rules; sr-* roles do new-surface design. |
| dev-sr-frontend | opus | opus | opus | No change — stays Opus per tier rules; sr-* roles do new-surface UI design. |
| dev-documentor | haiku | haiku | haiku | No change — cheap-model role; read+summarise only. |
| general-researcher | haiku | haiku | haiku | No change — cheap-model role; web fetch + summarise. |
| general | implicit-Opus | implicit-Opus | sonnet | Downgrade adds `model: sonnet`. Fallback tasks are mixed-scope by definition; Sonnet adequate. Upgrade path: Lead re-routes to sr-* if a spawned task turns out to need Opus quality. |
| project-auditor | sonnet | sonnet | sonnet | No change — already at L2. Read-only audit; structured output fits Sonnet. |
| content-writer | opus | opus | sonnet | Downgrade replaces `model: opus` with `model: sonnet`. Routine article/post drafting fits Sonnet; prose quality difference acceptable for most content. Operator can revert to MAX if output quality is a concern for high-stakes content. |
| content-editor | sonnet | sonnet | sonnet | No change — already at L2. Structural + line edits are pattern-matching; Sonnet is fine. |
| content-hook-doctor | sonnet | sonnet | sonnet | No change — already at L2. Hook scoring + rewrite is structured reasoning; fits Sonnet. |
| content-seo-optimizer | sonnet | sonnet | sonnet | No change — already at L2. On-page SEO work is formulaic; Sonnet adequate. |
| content-veracity-checker | sonnet | sonnet | sonnet | No change — already at L2. Fact-check is source-retrieval + comparison; Sonnet handles. |
| thai-proofreader | sonnet | sonnet | haiku | Downgrade replaces `model: sonnet` with `model: haiku`. Proofread pass is classification + rule-application against 17 known categories; no generative judgment needed. Risk: subtle register/idiom calls may degrade at Haiku tier — operator should spot-check first batch. |
| seo-strategist | opus | opus | opus | No change — stays Opus per tier rules; strategist role per Kanban description. |
| bi-analyst | opus | opus | opus | No change — stays Opus per tier rules; strategic insight-brief decomposition needs Opus. |
| sem-campaign-lead | opus | opus | opus | No change — stays Opus per tier rules; cross-platform budget strategy is a strategist role. |
| dashboard-designer | sonnet | sonnet | sonnet | No change — already at L2. Dashboard spec generation is structured; Sonnet adequate. |
| analytics-platform-integrator | sonnet | sonnet | sonnet | No change — already at L2. Data-source connection planning is mechanical; Sonnet fine. |
| sql-optimizer | sonnet | sonnet | sonnet | No change — already at L2. Query rewrite + index recommendation is analytical but structured; Sonnet fine. |
| google-ads-specialist | sonnet | sonnet | sonnet | No change — already at L2. Campaign blueprint generation is formulaic given a brief; Sonnet adequate. |
| meta-ads-specialist | sonnet | sonnet | sonnet | No change — already at L2. Meta campaign blueprint follows sem-campaign-lead brief; Sonnet adequate. |
| platform-ads-coordinator | sonnet | sonnet | sonnet | No change — already at L2. Multi-platform ad spec from brief is structured; Sonnet fine. |
| seo-reporting-analyst | sonnet | sonnet | sonnet | No change — already at L2. GSC/GA4 interpretation + report generation fits Sonnet. |
| technical-seo-specialist | sonnet | sonnet | sonnet | No change — already at L2. Technical audit checklist work is pattern-matching; Sonnet adequate. |
| secretary | implicit-Opus | implicit-Opus | sonnet | Downgrade adds `model: sonnet`. Secretary orchestrates browser workflows and returns summaries; Sonnet handles multi-step task sequencing well. High-volume triage quality risk is low — decisions surface to Lead for HITL. |
| secretary-email-triage | haiku | haiku | haiku | No change — already cheap-model. Classify-only triage. |
| secretary-job-scout | haiku | haiku | haiku | No change — already cheap-model. Score + summarize job listings. |
| secretary-linkedin-content | sonnet | sonnet | sonnet | No change — already at L2. Content drafting tier explicitly set to Sonnet in the agent file. |
| novel-writer | implicit-Opus | implicit-Opus | sonnet | Downgrade adds `model: sonnet`. Routine chapter drafting within an established outline and locked voice spec fits Sonnet. Significant quality risk for creative output: operator should pilot on one chapter before committing. |
| novel-editor | implicit-Opus | implicit-Opus | sonnet | Downgrade adds `model: sonnet`. Structural + line-edit pass follows explicit voice standards; Sonnet handles rule-application adequately. Similar creative quality caveat as novel-writer. |

---

## Harness-default agents (DO NOT MODIFY)

The following agents are Anthropic-internal / harness-managed. They are NOT listed above
because they must not have `model:` lines added by this tier system.

- `Explore` — Anthropic internal; harness-default model
- `Plan` — Anthropic internal; harness-default model
- `claude` (if present) — Anthropic internal
- `statusline-setup` — trivial config helper; harness-default sufficient; no model: line needed

Note: `_dev-shared.md` is a shared substrate file, not an agent; excluded from audit.

---

## Lead session note

Lead itself is NOT in `.claude/agents/` — it runs as the orchestrator session. The operator
controls its tier at the harness layer (Claude Code model selector / plan). On Pro plan the
session default is already Sonnet; on Max plan it is Opus. No agent file change needed.

---

## L2 diff count summary

Agents requiring a diff file: 5
- content-writer     (opus → sonnet)
- thai-proofreader   (sonnet → haiku)
- general            (implicit-Opus → sonnet, add model: line)
- secretary          (implicit-Opus → sonnet, add model: line)
- novel-writer       (implicit-Opus → sonnet, add model: line)
- novel-editor       (implicit-Opus → sonnet, add model: line)

Correction: 6 total diffs.
