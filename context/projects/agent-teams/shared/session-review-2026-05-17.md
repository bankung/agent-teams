# Session review — 2026-05-16/17

End-of-session synthesis: what landed, what was discussed, what's actually weak in our reasoning, and what to do next. Written for the operator + next-session Lead to bootstrap from.

## TL;DR (read this first)

- **Landed this session**: Karpathy Tier-2 discipline layer, #952 auditor engine (ACs 1-4), #7 Section B dev-security-reviewer role, #1079 Kanban closure, design docs promoted.
- **Discussed but not built**: DeepSeek integration plan (#1086 filed), browser-tool insight via Chrome MCP, 3-tier architecture (Operator / Project Lead / Secretary / Specialists), "เลขา" project as template-builder for arena pattern, persistent autonomous Project Lead as new architectural piece.
- **Biggest honest weakness**: persistent autonomous Project Lead is harder than the 2-3 week estimate (industry-wide unsolved; possibly 2-3 months R&D). Most other claims are unmeasured.
- **Next session opener**: see "Concrete next-action plan" section. Start with #1086 DeepSeek + #1085 flake fix + #1084 security-reviewer smoke.

---

## 1. State of the stack (committed + pushed today)

### Code on origin/main

```
1b268ef  feat: add dev-security-reviewer role (code 6, dev range) — Kanban #7 Section B
2c98361  docs(claude): add Karpathy lane golden rule (Tier-2 discipline layer)
604d5a2  docs(infra): clarify VPN-agnostic architecture in remote-access readme
8acce66  feat(langgraph): in-graph auditor node + retry-loop + ESCALATE via HITL — Kanban #952
2140f6c  Merge branch 'claude/stoic-archimedes-66ea3b' — T15 orphan re-dedup closed (#1039)
ff3429c  feat(api): strict answer validation gate on PATCH /api/tasks/{id} — Kanban #987
43013ee  feat(api): per-project HITL timeout policy + on-demand halt — Kanban #989
f07df36  fix(web): same-origin /api proxy via Next.js rewrites — Kanban #1079
73811e2  fix(langgraph): detect __interrupt__ in ainvoke result + demo HITL branch — Kanban #1073
59126e1  docs(shared): promote HITL resume design doc — Kanban #990 AC4 / #950 closeout
a20442c  feat(web): HITL TaskCard badge + 44px tap targets — Kanban #988
b29d8bb  docs(infra): Tailscale remote-access guide + helper scripts — Kanban #956
2c65956  test(api): empty-DB smoke gate + fresh-DB invariant — Kanban #994
```

### Kanban state (verify on next-session bootstrap)

- **DONE** (today + yesterday): #994, #956, #1073, #1037, #1079, #986, #987, #988, #989, #990, #950, #1078, #1080, #1081
- **IN_PROGRESS**: #7 (Section B done; Section A deferred), #952 (engine done; #952b + #952c siblings TODO)
- **TODO follow-ups filed today**:
  - #1038 — #986 review-cleanup minors (6 cosmetic findings)
  - #1082 (alias #952b) — Auditor cross-project daily aggregation API
  - #1083 (alias #952c) — #952 live smokes (AC6 + AC7 — recoverable + escalate)
  - #1084 — Smoke: spawn dev-security-reviewer on commit 73811e2 (NEW SESSION REQUIRED — agent registry loads at start)
  - #1085 — test_empty_db_smoke SERIAL reset flake (P1, 5 cross-test failures)
  - #1086 — DeepSeek LLM provider integration (P1)

### Discipline layers installed

- **Karpathy Tier-2** — CLAUDE.md "Karpathy lane" bullet (commit `2c98361`) + memory `feedback_karpathy_lane.md` (user-private). 4 drift modes catalogued (A jump-to-install-without-env-check, B trust-agent-without-re-run, C over-batch-parallel-spawns, D commit-without-re-reading-diff). Escalation path: hard hook on next recurrence.

### Live workflows proven (real tasks)

- HITL pause/answer/resume via FE button (task #1080): BLOCKED → answer via same-origin proxy → DONE in ~8s
- HITL invalid-answer 422 + audit history (task #1081): rejected answer stamped is_valid=false, task stayed BLOCKED
- Mobile dashboard read via Tailscale (operator verified): `http://bankungzenbook.<tailnet>.ts.net:5431/p/hitl-test` from cellular phone
- Auditor unit tests: 8/8 pass
- HITL unit tests: 37/37 pass
- #989 timeout policy: 4/4 pass

---

## 2. Architectural insights from this session

### Insight 1: 3-tier architecture for context budget

```
Operator (human, mobile-first) 
  ↕ digest + escalations + approvals
Project Lead (per business, autonomous, persistent)
  ↕ delegate work + receive summaries
Secretary tier (per project, low-strategic, high-volume)
  ↕ when expertise needed
Specialist tier (existing: dev-*, future business-*)
```

Why: 200K context window is the binding constraint for autonomous long-running agents. Hierarchical delegation = context isolation per tier. Project Lead reads digest from secretary, never raw inbox.

### Insight 2: "เลขา" project as prototype for arena-wide pattern

Building secretary for personal use (email triage, job hunt) tests every gap that the arena pattern needs:
- Generic secretary agent template
- Browser tool wiring
- Persistent Project Lead
- HITL discipline
- Context isolation
- Daily digest format

If "เลขา" workflow works, copy template for business niches.

### Insight 3: Browser tool via Chrome MCP sidesteps OAuth

`Claude_in_Chrome` MCP (already in skill registry) + `firecrawl-interact` give full Chrome control. Operator pre-logs in to Gmail/LinkedIn/Indeed; AI uses authenticated session. No OAuth flow to build. Trades OAuth complexity for browser-state fragility.

### Insight 4: Two operating modes

- **Mode A (interactive)**: Lead session uses Chrome MCP directly. Works TODAY, no new wiring. Best for low-volume, on-demand work.
- **Mode B (autonomous)**: LangGraph specialist nodes need Chrome MCP wired as new tools. ~3-5 days dev work. Required for scheduled/unattended daily runs.

### Insight 5: DeepSeek V3 changes cost economics

11× cheaper than Claude Sonnet for ~80-90% quality on code+structured tasks. Makes mixed-strategy realistic: V3 default for specialist + auditor; Claude Sonnet/Opus tier-up selectively; local Ollama dev-only.

### Insight 6: Karpathy drift modes are predictable

Documented 4 modes (A/B/C/D) with strike incidents. Tier-2 (soft golden rule) chosen first; Tier-1 (hard hooks) escalation path defined for any recurring drift.

---

## 3. Honest weakness analysis (ranked by impact)

### W1 (HIGHEST RISK) — Persistent autonomous Project Lead is much harder than I estimated

**Claim made**: "~2-3 weeks of dev to build persistent Project Lead pattern."

**Reality**: This is the **biggest unsolved problem in agent architecture today**. Companies with far more resources (Lindy, Devin, Cursor, Cognition AI) have all hit walls on:
- Context recovery across runs (what state to preserve, what to compact)
- Memory hierarchy (working / episodic / semantic) — research-grade work
- Decision provenance + drift detection
- Long-horizon coherence (LLMs hallucinate prior decisions after N runs)

**Honest estimate**: 2-3 MONTHS of focused R&D, not weeks. And the output might still be unreliable enough that operator HITL gates are needed at higher frequency than planned.

**Mitigation**: Start with Mode A (interactive Lead session = the human operator IS the Project Lead). Defer persistent autonomous Lead until empirical evidence shows the interactive approach has scaling pain. Don't pre-build for hypothetical autonomy.

### W2 — All cost / capability claims are unmeasured

**Claims made**:
- DeepSeek V3 = ~80-90% quality of Claude Sonnet for our use case
- $15-30/mo per arena project
- 250-500K tokens/day workflow estimate
- Mobile-first workflow is practical

**Reality**: Zero of these have measurement data behind them. Public benchmarks (HumanEval / SWE-bench) test artificial conditions; real-world performance varies.

**Mitigation**: 
- First action after #1086 lands = run A/B test (DeepSeek vs Anthropic) on 10 real demo tasks; measure tokens, completion rate, quality
- Track tokens-per-task via #944 cost tracker before extrapolating monthly cost
- Don't make budget decisions based on speculative cost models

### W3 — Browser automation is more fragile than OAuth integration

**Claim made**: "Chrome MCP solves the Gmail OAuth problem — easier to start, no auth flow to build."

**Reality**: Easier to START but harder to MAINTAIN:
- Browser must run 24/7 for unattended tasks (Mode B)
- Browser state breaks (cookies expire, sessions logout, UI changes)
- Anti-bot detection on LinkedIn / Indeed is real and improving (timing patterns, mouse movements, fingerprinting)
- Captchas can block entire workflow
- DOM changes break selectors silently
- Multi-tab management is fragile under load

**Mitigation**: Use browser approach for Mode A (low volume, supervised). For Mode B (autonomous high-volume), invest in proper OAuth tools per service. Hybrid: browser for prototyping; API integration once a workflow proves valuable enough to justify the effort.

### W4 — HITL approval discipline assumes operator availability

**Claim made**: Operator approves before destructive/sensitive actions.

**Reality**: 
- What if operator sleeps / vacations / busy?
- Task queue grows unbounded
- Time-sensitive tasks (job application deadlines) get missed
- Operator becomes bottleneck = stack value collapses

**Mitigation**: #957 approval policies SHOULD handle this — but the policy DESIGN is unsolved. What's "safe enough" to auto-approve? Confidence threshold? Reversibility check? Audit log? File a follow-up to actually design #957 before relying on it for capacity scaling.

### W5 — Tier-2 POC niche candidates have weak moats

**CONFIDENTIAL doc candidates**: content drafting, code drafting, research/lead-gen, freelance bid drafting, doc generation.

**Reality**:
- All are high-supply markets (lots of AI services already there)
- Low-moat (commodity output)
- Subjective quality (hard to measure success)
- Pricing pressure from race-to-the-bottom AI agents

**Mitigation**: Before committing to a POC niche, do market research to confirm 2-3 specific candidates have:
- Pricing power (vs commodity)
- Measurable quality (objective, not subjective)
- Operator-relevant interest (won't abandon mid-POC)
- Concrete buyer profile

Better niche candidates the doc doesn't explore:
- Specialized domain compliance (legal/medical/financial — but high-stakes; defer)
- B2B research-as-a-service (sell to specific industries)
- Personalized education content
- Operator's existing Substack/YouTube niche monetization

### W6 — Mobile-first might be aspirational

**Claim made throughout**: "Mobile is the control plane."

**Reality**: 
- Long-form review (full cover letter, code review, complex decisions) is hard on phone
- Operator likely opens laptop for serious work anyway
- "Phone as control plane" might be the alert + simple-approve surface only

**Mitigation**: Build infrastructure that supports BOTH (already does — Tailscale + responsive UI). Don't over-optimize for mobile at the expense of laptop UX. Mobile = alerts + simple approvals; laptop = strategic decisions + drafting review.

### W7 — Recurrence scheduling status unverified

**Implicit assumption**: scheduled_at + recurrence_rule support cron-style "every Monday 7am".

**Reality**: I haven't verified this works today. #723 was filed for the apscheduler subsystem but I don't have evidence it's production-ready.

**Mitigation**: First action when needed — verify by filing a test task with `scheduled_at` + `recurrence_rule` set; observe whether it actually fires on schedule.

### W8 — Off-site backup not yet done (#959)

**Risk**: All Kanban state, decisions, audit history, scheduled tasks live on one local Postgres. Disk failure / accidental drop = total loss.

**Mitigation**: #959 filed in Tier-1. **Should be done BEFORE serious Tier-2 POC investment**. Currently filed at lower priority than push / digest / policies — review ordering.

### W9 — Auditor LLM verdict accuracy unproven in production

**Claim**: Auditor classifies PASS / AUTO_RESOLVE / ESCALATE correctly.

**Reality**: Unit tests verify the CODE paths. The LLM verdict reliability against real specialist failures is untested. False positives (escalate when retry works) waste operator time; false negatives (retry when escalate needed) mask bad outcomes.

**Mitigation**: #1083 (#952c live smokes) is filed. Run before relying on auditor in real autonomous workflows.

### W10 — Security review of #952 + #987 + #988 + #989 not done

**Implicit assumption**: Today's commits are safe.

**Reality**: We added a new role (dev-security-reviewer) but couldn't smoke it in this session (agent registry loads at start). The new code added today (auditor + validation gate + timeout + FE badge) has NOT been security-reviewed.

**Mitigation**: #1084 specifies the security-reviewer smoke target as commit 73811e2. Should expand scope to also cover today's #952 + #987 + #988 + #989 commits.

### W11 — Karpathy Tier-2 discipline not battle-tested

**Claim**: Tier-2 layer reduces drift by ~95%.

**Reality**: Tier-2 was installed today. Today I still over-batched at times. The "behavior changes when user checks me" pattern persisted: I improved AFTER the user's mid-session karpathy check, but the change might not propagate to autonomous sessions where no user is checking.

**Mitigation**: Tier-1 hard hooks specified in memory file. If next session sees drift in mode A/B/C/D, escalate immediately. Don't wait for "3 strikes".

### W12 — Persistent agent files vs session-scoped registry

**Today's pattern**: Created `.claude/agents/dev-security-reviewer.md` but can't spawn it in current session (registry loads at start).

**Risk**: Pattern is correct but creates friction. Adding new agents requires session restart between create + use.

**Mitigation**: Awareness only; pattern matches existing convention. The hardcoded scaffolder lists (project_scaffold.py + zero_config_scaffold.py) need manual update per new role — could be automated via glob discovery in a future cleanup task.

### W13 — Cross-tier context isolation is human discipline only

**Claim**: Project Lead reads digest, never raw secretary input.

**Reality**: There's no code-level enforcement. Operator must remember to brief Lead with summarized context, not raw data dumps.

**Mitigation**: Spawn brief template should explicitly require "summary not raw". Could add a hook that scans spawn briefs for known raw-data markers (e.g., large blobs, full email bodies) and rejects them.

### W14 — Single-operator dependency / bus factor 1

**Reality**: Everything depends on operator being available + healthy.

**Mitigation**: Long-term concern, not session-1 concern. Backup (#959) addresses data loss but not operator unavailability. Worth flagging for medium-term planning (operator handover docs / second-trusted-person access via Tailscale ACL).

### W15 — Niche / business model not validated

**Reality**: Strategic doc lists candidates, but operator hasn't picked one. No market research. No buyer profile. No price discovery.

**Mitigation**: Tier-2 POC IS the validation step. But should add a "niche research" sub-task BEFORE committing dev time to building POC-specific tools. ~1 week of market research on top 3 candidates → pick best one.

---

## 4. Open problems list (consolidated)

In rough order of "needs to be solved before serious POC investment":

1. **Persistent autonomous Project Lead architecture** (W1) — biggest unknown
2. **Approval policy design (#957)** — capacity scaling depends on it (W4)
3. **Off-site backup (#959)** — single point of catastrophic failure (W8)
4. **Auditor LLM verdict validation (#1083)** — autonomous workflow reliability (W9)
5. **Cost / capability measurement** (W2) — economic decisions need real data
6. **Niche selection + market research** (W5) — before POC commits
7. **Browser automation reliability vs OAuth tradeoff** (W3) — Mode B sustainability
8. **Recurrence scheduling verification** (W7) — daily routines depend on it
9. **Security review of today's commits + ongoing** (W10) — once dev-security-reviewer smokes
10. **Context isolation enforcement** (W13) — discipline → eventual code gate
11. **Karpathy drift recurrence monitoring** (W11) — escalate to hooks if any mode repeats
12. **Mobile vs laptop workflow split** (W6) — design for both, not over-optimize for either
13. **Bus factor 1 long-term concern** (W14) — defer but plan
14. **Auto-scaffolder for new agents** (W12) — minor friction, low priority

---

## 5. Concrete next-action plan

### Session 1 (next session)

**Goal**: Close housekeeping + measurement infrastructure.

1. Bootstrap, re-resolve agent-teams project
2. Verify Kanban state matches this doc's claims (curl /api/tasks?process_status=1)
3. **Close #1085** — test_empty_db_smoke SERIAL reset fix (estimate ~1 hour)
4. **Close #1086** — DeepSeek integration (estimate ~2 hours; wire langgraph/llm.py + test)
5. **Close #1084** — smoke dev-security-reviewer on commit 73811e2 (estimate ~1 hour, depends on agent registry having new file from session restart)
6. **Verify #959 (backup) priority** — should this jump ahead of digest/push? Probably yes.

### Session 2-3

**Goal**: Tier-1 backbone for arena workflow.

7. **#955 Web Push** — operator alerts on mobile
8. **#958 Daily digest** — backbone of tier-isolation pattern
9. **#959 Off-site backup** — protect arena state BEFORE serious Tier-2 commitment

### Session 4-5

**Goal**: Tier-1b (new from this session's insights).

10. **#957 Approval policies design** — actual policy specification, not just placeholder
11. **Generic `secretary` agent** — `.claude/agents/secretary.md` template
12. **Browser tools in LangGraph** — wire Chrome MCP as `langgraph/tools/browser/*` (Mode B prerequisite)
13. **Secretary→Lead digest format** — `shared/daily-secretary-digest.md` template

### Session 6-8

**Goal**: "เลขา" project Phase 0-1 (Mode A only first).

14. **Create `secretary` (or `เลขา`) project** via POST /api/projects
15. **Write knowledge base**: profile.md, voice.md, email-rules.md, job-criteria.md
16. **Mode A workflows**: interactive Lead uses Chrome MCP for email summary, job scout, application proposal
17. **Run for 1-2 weeks** — measure: tokens, time saved, completion rate, HITL frequency
18. **Decision**: continue / pivot / abandon based on data

### Session 9+

**Goal**: Decide on persistent Project Lead R&D investment based on Mode A friction.

19. If Mode A handles 80%+ of workflow without operator pain → defer persistent Project Lead
20. If Mode A breaks under volume → invest in persistent Project Lead (2-3 month R&D)

### Tier-2 POC (after Tier-1b + Mode A validated)

21. Pick 1 business niche (after market research)
22. Copy "เลขา" template for that niche
23. Run 2-4 weeks
24. Measure: token cost, completion rate, HITL frequency, revenue
25. Decision criteria: revenue / (LLM cost + operator time × hourly rate) > 1

### Tier-3 (after Tier-2 succeeds)

26. Arena layer: revenue tracking, cross-project leaderboard, business team templates

---

## 6. Recommended next-session opener

```
agent-teams ครับมาต่อ.

วันนี้เริ่มด้วย:
1. Verify Kanban state vs session-review-2026-05-17.md
2. ปิด #1085 (test SERIAL flake) — quick
3. ปิด #1086 (DeepSeek integration)
4. ปิด #1084 (security-reviewer smoke on 73811e2)

ก่อนทำงาน — confirm session-review's weakness analysis ยังเห็นด้วยอยู่ไหม
ถ้าเห็นด้วย → proceed.
ถ้าไม่ → ปรับ priority ก่อน.
```

This opener: bootstraps cleanly, references this doc for context, gates on operator confirmation of weakness analysis (so I don't carry stale assumptions).

---

## 7. Things explicitly NOT decided this session

Listed so next session knows they're still open:

- LLM provider mix per agent tier (will be informed by #1086 A/B test)
- Tier-1 priority order (is #959 backup higher than #955 push?)
- "เลขา" project name (Thai vs English — operator preference)
- Persistent Project Lead build vs defer decision (gated on Mode A measurement)
- Niche selection for Tier-2 POC (gated on market research)
- How to handle bus factor 1 (operator handover plan)
- Whether to invest in code-level context isolation enforcement
- Whether to expand dev-security-reviewer smoke scope to today's #952/#987/#988/#989 commits

---

## 8. File metadata

- **Created**: 2026-05-17 (session festive-bartik-04b551 wind-down)
- **Author**: Lead (in coordination with operator)
- **Format**: append-only thereafter — next session may add a "Session review — 2026-05-XX" file rather than mutate this
- **Scope**: agent-teams project's shared context; persists across sessions via Lead Bootstrap reading shared/
- **Cross-references**: 
  - `_scratch/auditor-design.md` (#952 design lock)
  - `_scratch/security-reviewer-design.md` (#7 Section B design lock)
  - `_scratch/hitl-test-procedure.md` (#1073 operator procedure)
  - `_private/CONFIDENTIAL-README.md` (strategic doc; user-private)
  - `~/.claude/projects/.../memory/feedback_karpathy_lane.md` (discipline memory; user-private)
