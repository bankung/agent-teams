# Model-tier audit — .claude/agents/*.md (Kanban #1187)

Scope: 40 real agent definitions (Glob returned 41 files; `_dev-shared.md` excluded — it's a substrate doc `dev-*` agents read, no `name:`/`model:` frontmatter, not itself an agent). Baseline = post-ms55 (#2755/#2760) state; audited as-is, only CHANGES flagged. Principle: downshift where a wrong output is caught cheaply (tests / review gate / HITL / harness hook); keep opus where errors compound or voice-craft matters.

## (a) Full table (all 40, grouped by team)

### dev (11)
| Agent | Current | Rec. | Rationale |
|---|---|---|---|
| dev-analyst | sonnet | keep | Spec-structuring + conflict-flagging needs judgment; dev-spec-reviewer gate exists downstream but doesn't replace good scoping. 0 spawns in sample — see §d. |
| dev-spec-reviewer | sonnet | keep | File states "Sonnet (you) is required" for its own categories 4-7; catches multi-point-drop/conflict BEFORE spawn (error-compounding prevention). |
| dev-backend | sonnet | keep | Modify-existing-surface, established pattern. Highest-volume role (118/452 sampled spawns) — correctly tiered. |
| dev-frontend | sonnet | keep | Same shape as dev-backend, FE side (58 spawns). |
| dev-sr-backend | opus | keep | New-surface/schema/FK architecture judgment; Lead already gates entry to new-surface-only tasks. |
| dev-sr-frontend | opus | keep | New-surface UI architecture judgment; same gating. |
| dev-devops | sonnet | keep | Infra config within established compose/CI patterns; role file explicitly forbids new abstractions. |
| dev-reviewer | sonnet | keep | Checklist-driven (hypotheses + 9 reward-hacking patterns + AC audit); findings re-verified by dev-tester/operator. |
| dev-security-reviewer | sonnet | keep | Deep but pattern-bounded (OWASP/CVE/threat-model checklist); advisory-only, redundant catch points exist. Considered opus — see §d. |
| dev-documentor | haiku | keep | Explicitly cheap-model role; navigational summarization, Lead promotes/discards (review gate). High volume (28 spawns), correctly tiered. |
| dev-tester | sonnet | keep | Edge-case/regression reasoning + spurious-PASS self-audit — genuine reasoning work. |

### seo (4)
| Agent | Current | Rec. | Rationale |
|---|---|---|---|
| seo-strategist | opus | keep | START-of-engagement keyword/roadmap strategy — cascades to whole content team if wrong. |
| technical-seo-specialist | sonnet | keep | Evidence-cited audit across 5 structured dimensions, severity-checklist bounded. |
| content-seo-optimizer | sonnet | keep | On-page trade-offs (SEO vs voice) within a structured dimension checklist. |
| seo-reporting-analyst | sonnet | keep | Hedged causal diagnosis across data sources — ranked-hypothesis reasoning, not tally. |

### sem (4)
| Agent | Current | Rec. | Rationale |
|---|---|---|---|
| sem-campaign-lead | opus | keep | START-of-engagement budget/ROAS allocation — real ad-spend stakes, cascades to 3 specialists. |
| google-ads-specialist | sonnet | keep | Tactical blueprint build-out from sem-campaign-lead's brief — implement-within-pattern. |
| meta-ads-specialist | sonnet | keep | Same shape, Meta side. |
| platform-ads-coordinator | sonnet | keep | Same shape, multi-platform sub-methods but still pattern-driven per platform. |

### data-analytics (4)
| Agent | Current | Rec. | Rationale |
|---|---|---|---|
| bi-analyst | opus | keep | START-of-engagement metric/decision decomposition — wrong metric definition compounds through the whole downstream dashboard. |
| sql-optimizer | sonnet | keep | EXPLAIN-plan-driven rewrite reasoning, engine-aware — genuine technical reasoning. |
| dashboard-designer | sonnet | keep | Compositional design judgment (audience × platform capability × decision-question). |
| analytics-platform-integrator | sonnet | keep | Connection-strategy synthesis across sources w/ explicit heavy-ETL escalation valve. |

### content (5, cross-team)
| Agent | Current | Rec. | Rationale |
|---|---|---|---|
| content-writer | sonnet | keep | Voice-preserving prose drafting under truth_spec constraints. |
| content-editor | sonnet | keep | Hypotheses-first structural/voice edit pass. |
| content-hook-doctor | sonnet | keep | Rubric is mechanical but the rewrite itself is the deliverable — generative copywriting quality matters. |
| content-veracity-checker | sonnet | keep | Trust-boundary fact-check; judges source independence + claim conflicts — a bad "verified" ships as an uncaught factual error (no downstream gate). |
| thai-proofreader | sonnet | keep | 17-category native-ear linguistic judgment; pattern-bounded (worked examples per category) but genuinely hard — sonnet is the right floor. |

### secretary (4)
| Agent | Current | Rec. | Rationale |
|---|---|---|---|
| secretary | sonnet | keep | Cross-workflow orchestrator, PII handling, HITL gating — moderate-stakes established-pattern reasoning. |
| secretary-job-scout | haiku | keep | Fixed weighted-formula scoring + templated cover-letter snippets; HITL gate before any submit. Already has a documented cost rationale in-file. |
| secretary-linkedin-content | sonnet | keep | File explicitly justifies sonnet over haiku for drafting quality; propose-only + HITL gate. |
| secretary-email-triage | haiku | keep | Pure classify against a fixed taxonomy + fixed priority policy. |

### novel (2)
| Agent | Current | Rec. | Rationale |
|---|---|---|---|
| novel-writer | opus | keep | Voice-craft origination — the tier principle's own named exception. |
| novel-editor | sonnet | keep | Edits within an already-established voice, not originating it — one tier below the writer by design. |

### netops (3)
| Agent | Current | Rec. | Rationale |
|---|---|---|---|
| netops-l2l3 | sonnet | keep | Multi-signal diagnostic correlation (interface/route/ARP/log) into ranked hypotheses — real reasoning, propose-only output. |
| netops-monitoring-reader | sonnet | keep | Zabbix alert-storm correlation into a ranked root cause — same reasoning shape. |
| netops-triage | sonnet | **haiku** | Pure classify+route (OSI-layer symptom → fixed lane list); narrowest tool grant in the whole roster (Read/Grep/Glob only, no Bash); never diagnoses itself. |

### general / oversight (3)
| Agent | Current | Rec. | Rationale |
|---|---|---|---|
| general | sonnet | keep | Unpredictable-scope exploratory/scripting work with an escalation protocol — needs judgment to pick the right escalation target. |
| general-researcher | haiku | keep | Explicitly cheap-model role; fetch+summarize+cite, no synthesis allowed by design. |
| project-auditor | sonnet | **haiku** | Fully formulaic (explicit formulas + breach-threshold table in-prompt); harness-enforced read-only hook; Lead independently re-verifies before PATCH. |

## (b) Changes-only

**1. `.claude/agents/netops-triage.md`**
- Frontmatter: `model: haiku` (was `sonnet`)
- Top-of-body rationale comment: `> **Model tier (haiku, #1187):** pure classify+route — OSI-layer symptom classification into a fixed lane list, no device access, no command execution (Read/Grep/Glob only). A wrong routing call is caught cheaply when the chosen lane finds no evidence.`

**2. `.claude/agents/project-auditor.md`**
- Frontmatter: `model: haiku` (was `sonnet`)
- Top-of-body rationale comment: `> **Model tier (haiku, #1187):** metric computation is fully formulaic (explicit formulas + breach-threshold table below) behind a harness-enforced read-only hook; Lead independently re-verifies via curl before PATCH-ing the report, so a bad output costs a redo, not a live mutation.`

## (c) Cost-impact estimate

Pricing (from claude-api skill, current): Opus $5/$25 per Mtok in/out, Sonnet 5 $3/$15 ($2/$10 intro through 2026-08-31), Haiku 4.5 $1/$5. Sonnet→Haiku ≈ 67% cheaper (50% during intro pricing); Opus→Sonnet ≈ 40% cheaper; Opus→Haiku ≈ 80% cheaper.

Spawn frequency — live tally, `subagent_models.agent` across a 452-entry sample window (`GET /api/tasks?limit=500`, project 1; window is oldest-500-skewed per known API-list caveat, so treat as directional):

| Agent | Spawns | Share | Tier |
|---|---:|---:|---|
| dev-backend | 118 | 26% | sonnet (keep) |
| dev-frontend | 58 | 13% | sonnet (keep) |
| dev-sr-backend | 54 | 12% | opus (keep) |
| dev-devops | 41 | 9% | sonnet (keep) |
| dev-tester | 32 | 7% | sonnet (keep) |
| dev-reviewer | 31 | 7% | sonnet (keep) |
| **dev-documentor** | 28 | 6% | **haiku (already)** |
| dev-sr-frontend | 21 | 5% | opus (keep) |
| general | 20 | 4% | sonnet (keep) |
| dev-security-reviewer | 16 | 4% | sonnet (keep) |
| **general-researcher** | 15 | 3% | **haiku (already)** |
| secretary | 7 | 2% | sonnet (keep) |
| project-auditor, netops-triage, netops-l2l3 | 1 each | <1% | **2 of 3 recommended → haiku** |
| dev-analyst, dev-spec-reviewer | 0 | 0% | not observed in sample |

Reading: dev team dominates spawn volume (this repo IS the dev team's own dogfood project), and the two **already-haiku** high-volume roles (dev-documentor + general-researcher, 43/452 ≈ 9.5% of all sampled spawns) are the largest realized saving in the fleet today — not a new recommendation, but the strongest live evidence the tiering discipline works. The 2 NEW recommendations here (project-auditor, netops-triage) are directionally correct but low-frequency today (netops is a young team; project-auditor runs on-demand/scheduled, not per dev-task) — expect the $ delta to grow as netops usage matures and GOV3 (#1211) wires project-auditor onto a recurring schedule.

## (d) Genuinely uncertain

- **dev-security-reviewer** (currently sonnet) — considered for upgrade to opus: security errors compound (a missed SSRF/auth-bypass is a real, hard-to-detect vuln) and the reasoning (threat modeling, injection-path tracing) is genuinely hard. Landed on keep-sonnet because: it's explicitly checklist/pattern-driven (matches the principle's own Sonnet definition), it's advisory-only + read-only, and it sits behind 2 redundant catches (dev-reviewer's baseline OWASP pass + Tier-2 release security-mode). Flagging in case the operator weighs the compounding-risk side more heavily than I did.
- **dev-analyst** (currently sonnet) — considered for downshift to haiku: its output is always audited next by dev-spec-reviewer (a dedicated ambiguity/conflict/AC-completeness gate), which is about as cheap a catch-point as exists. Kept at sonnet because expanding a 1-line idea into a good scope/AC/lifecycle spec is synthesis+judgment, not classification — a weak first draft raises dev-spec-reviewer's WARN rate and adds a round-trip. 0 observed spawns in the sample, so low priority either way.
- **content-hook-doctor** — the scoring rubric (4 dimensions × 1-5) is mechanical, but the deliverable is 3-5 generated rewrites in distinct copywriting frames; a weak rewrite defeats the role's purpose. Kept sonnet, not flagged as a real candidate — noted only because it's the closest "mechanical-looking but actually generative" case in the roster.

**Summary for Lead:** 40 agents audited, 38 keep, 2 recommended downshifts (netops-triage, project-auditor — both sonnet→haiku). No opus→sonnet or sonnet→opus changes recommended; the 6 current opus roles map cleanly onto the 4 opus categories in the principle (dev-sr-backend/dev-sr-frontend = new-surface architecture, novel-writer = voice-craft, seo-strategist/sem-campaign-lead/bi-analyst = judgment-heavy engagement-start strategy).
