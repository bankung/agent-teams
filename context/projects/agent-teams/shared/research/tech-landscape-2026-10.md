---
purpose: tech landscape Aug-Oct 2026 + Fable 5.1 evaluation of the agent-teams backlog and direction (#3473)
status: recommendations only — no task closed/created and no code changed by #3473; each action needs an operator go
owner: Lead
date: 2026-10-08
---

# Tech landscape (Aug–Oct 2026) and agent-teams direction — #3473

Inputs: 3 general-researcher reports + 1 Fable 5.1 evaluation (the raw drafts were in `_scratch/research-3473-*.md` and
`_scratch/eval-3473-fable.md`, local only). Lead re-checked every load-bearing claim against a primary source;
the verification column below says which.

## 1. Landscape

### Verified by Lead (primary source, accessed 2026-10-08)
| Item | Date | Fact | Source |
|---|---|---|---|
| Claude model lineup | Fable 5.1 2026-09-01 · Opus 5.5 2026-09-22 · Sonnet 5.5 2026-09-28 · Haiku 5.5 2026-10-07 | $/MTok in/out: Fable 10/50 · Opus 5.5 4/20 · Sonnet 5.5 2/10 · Haiku 5.5 from 0.10/0.50; all 1M context, 128K output | https://platform.claude.com/docs/en/about-claude/models/overview · https://www.anthropic.com/claude-haiku-5-5 |
| Claude Mythos 5.1 | 2026-09-01 | Trusted-Access-Program only — not usable by agent-teams | pricing footnote on the models overview; secondary: https://www.cometapi.com/ja/models/anthropic/claude-mythos-5-1/ |
| Claude Code releases | v2.1.293 on 2026-10-07 | **This machine runs 2.1.179 — 114 releases behind.** #3348 Phase 0c was measured on 2.1.179 | `gh api repos/anthropics/claude-code/releases` |
| "JEV" = Jev (TypeSafe AI) | 2026-09-15 | "System One" decision model: answers typed questions about text (pick an option, score, yes/no probability), 70–500 ms; $40M seed (DCVC); early-access waitlist | https://arxiv.org/abs/2609.30216 · https://digg.com/ai/skq6c9da |
| PALO IT Gen-e2 (public page) | method since 2024 | AI-first engineering methodology; public stack lists GitHub Copilot, OpenAI Codex, Claude, Mistral, MCP. **OpenRouter is not on the public page** — operator's understanding (Copilot as harness + OpenRouter) is unconfirmed; check internally after 2026-10-19 | https://www.palo-it.com/en/services/gen-e2 |

### Reported by researchers, NOT verified by Lead (treat as claims)
- Claude Code v2.1.284–293 feature list: "Claude Mods" framework (v2.1.287), plugin marketplace install (v2.1.292), agent/subagent permission changes (v2.1.290/293). Source: https://github.com/anthropics/claude-code/releases (Lead verified version numbers + dates only).
- Managed Agents: session budgets, scheduled deployments, environment vaults, permission auto-eval, Mid-Conversation Tools (beta, 2026-09-22). Source: https://platform.claude.com/docs/en/release-notes/overview. Different runtime from a CC-session harness.
- MCP spec 2026-07-28 (stateless core, extensions, auth hardening): https://blog.modelcontextprotocol.io/posts/2026-07-28/
- GitHub Copilot custom agents / MCP-native coding agent; AWS Kiro autonomous agents (Aug 2026); Cursor automations; OpenRouter 400+ models routing — all from secondary sources (e.g. https://www.constellationr.com/insights/news/aws-kiro-launches-autonomous-agents-individual-developers).
- Out of window, ignored: Jules GA (2025), Devin 2.2 (Feb 2026), ADK 1.0 (I/O 2026).

### Patterns (secondary sources — confirmation only, not a design basis)
Orchestrator-worker + worktrees for parallel agents; compaction / tool-result clearing; agent-as-judge evals;
MCP (agent-to-tool) vs A2A (agent-to-agent); cost-tiered model routing; spec/AC-first development
(https://developer.microsoft.com/blog/spec-driven-development-ai-native-engineering/); progressive autonomy /
earned trust (https://www.elastic.co/blog/the-future-of-governing-ai-agents). Rejected: the claim that agent-teams
lacks observability — hook cost capture, the activity rail and project-auditor exist (#1789 telemetry-to-DB is the open part).

## 2. Code facts found during the evaluation (Lead re-read the files)
- `api/src/schemas/task.py:333` already accepts `model: "fable"` (#3341) → **#3296 looks obsolete**; confirm with one live PATCH (#3473's own close carries a fable entry).
- `api/src/services/cost_tracker.py:23-39` and `api/src/pricing.py:57-59` price only the 4.x lineup (opus 5/25, sonnet 3/15, haiku 1/5; no fable row); `.env.example:101` default is `claude-sonnet-4-6`. Every cost figure (auditor burn, #2408 forecast, usage panels) is mis-priced for 5.x models — Haiku spend overstated up to ~10x if `haiku` resolves to Haiku 5.5.

## 3. Evaluation (Fable 5.1, Lead-reviewed)
Frame: from 2026-10-19 capacity is evenings/weekends (~4-6 h/week). Keep only what returns within 1-2 sessions,
fixes a correctness/safety defect, or is the measurement that unlocks a drop decision. Park (not delete) epics with
no active project behind them. Fable's totals over 109 open tasks: ~12 drop · ~45 park · ~14 re-scope · rest keep
→ working set ~25.

| Group | Verdict |
|---|---|
| no milestone | keep #2810 #2845 #2853 #3156 #3210 #3437 (finish first); re-scope #2814→fold into #3156, #2815 cadence "when dev has unreleased commits", #2871 verify residual then close; park #3350 (see D5), #2809, #2869; drop #3296. `[schedule:*]` rows #2742 #2748 #2750 #2751 #2753 #2806 are **fired instances** from Jun/Jul (Lead-checked `spawned_from`), not templates → close as noise |
| ms 17 aa-followups | keep #1297; park #1213 #1239; drop #1233 |
| ms 19 | drop #1388 (job search closed), #1439; re-run #1424 after the CC upgrade; park #1275 #1861 |
| ms 21 data · ms 30 social · ms 55 novel | park all |
| ms 23 methodology | re-scope #2528 (write a fresh Example 2); park #2549 |
| ms 24 operator-auth | keep #2060 (CSP img-src); park #1852 #1858 #2058; drop #2351 |
| ms 25 platform | re-scope #806 to a read-only MCP server and merge #1792 (D6); keep #2495; park #1316 #1911 #2717; drop #1791 |
| ms 26 mode-b-engine | keep #2555 (re-scoped: 10-task eval bed), #1845 (measure inside #2555), #2738; park #1801 #1843 #2739 #1846 (spike the browser toolset first); drop #2275 #2329 |
| ms 27 performance | finish #2505 (in REVIEW); park #2395 pending the upgrade |
| ms 28 schedule · ms 29 secretary | keep #1475, #1855; drop #1108 |
| ms 37 mode-a-cost | re-scope #2155 (pricing item → D1); keep #2408; fold #1265 into #2326 (park unless a funded key exists); re-test #2360 after upgrade; park #2325 #2362 |
| ms 39/41 skills + hooks | keep #2449+#2450 as one weekend (`/zb-test`), #2442; drop #2441 #2443 #2446 #2447 (job search closed); park #1965 #1789 #1790 #2402 |
| ms 40 tools | keep #1954 #2321; park #2101 #2103 #2105 |
| ms 42 · ms 47 scoring · ms 58 AG wizard · ms 59 netops | park all (#2557; #2464-2469 — no revenue projects to compare; #1022-1026 — market moved to Markdown-declared agents; #2523 #2524) |
| ms 54 | re-scope #1200 to a Haiku 5.5 re-measure; park #2782 |
| ms 56 | split #2766: keep the operator pytest run, park the rest |
| ms 57 v0.9.0 | keep, re-sequenced: decide #2767's 4 questions → #2813 (history query, no migration) → #2770 → #2772 → #2776; defer #2771 #2773 #2774 #2775 |

**Native overlap:** every Claude Code / Managed Agents overlap is UNVERIFIED until the upgrade (D5). Managed Agents
is a different runtime — "watch", not "migrate" (Fable disagrees with the researcher's migration suggestion; Lead agrees).

## 4. New directions
- **D1 Model-tier + pricing refresh** — new task: 5.x rows (fable/opus/sonnet/haiku 5.5) in both pricing tables with source URL + date; resolver maps `claude-fable-5-1`; tests + live-DB row count; `.env.example` default bumped. Check what `model: haiku` resolves to before touching agent files.
- **D2 Fast decision model (Jev-style)** — not now (waitlist only). Prepare a one-function decision seam and prototype walker auto-eligibility on Haiku 5.5 structured output against ≥20 historical Lead decisions; operator may join the Jev waitlist.
- **D3 Claude-only harness** — keep agent-teams Claude-only (Claude Code is the harness; Haiku 5.5 removes the cheap-tier argument for a gateway). Revisit OpenRouter only if Mode-B resumes and needs a non-Claude model. Optional: one decisions.md entry mapping Gen-e2's PUBLIC vocabulary onto agent-teams layers (no internal material).
- **D4 Eval + earned autonomy (v0.9.0)** — the lever that turns lost hours into throughput: an unattended walker that pings Telegram less, gated on rework-rate by class.
- **D5 Claude Code upgrade 2.1.179 → 2.1.293 + re-measure** — precondition for every overlap decision: hooks still fire, #1424 smoke, #2360 PreCompact on auto-compact, file-lease cells (Bash append, never-Read Write, /compact).
- **D6 Read-only MCP Kanban server** — #806 re-scoped + #1792 merged: list/get task, get AC, next actionable; project scope enforced server-side.

## 5. Top 5 next actions (value per operator-hour, Fable)
1. Finish #3437 (~30 min; blocks every researcher's firecrawl lane; owned by another session).
2. Kanban hygiene (~20 min): the drop/park list above.
3. D1 pricing refresh (1 evening).
4. D5 upgrade + re-measure (1 evening).
5. #2449 + #2450 `/zb-test` (1 weekend; removes the ~18-min operator pytest round-trip).

## 6. Open questions
What `model: haiku` resolves to per CC build · what `cost_tracker` returns for `claude-fable-5-1` (not executed) · what
#806 already has on disk · #2871's residual · whether the walker will run unattended during office hours · whether a
funded API key exists (#2326/#1265) · all unverified Claude Code / Managed Agents feature claims (resolved by D5).
