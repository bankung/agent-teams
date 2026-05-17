# Architectural & process decisions — secretary

> **Lead is the only writer of this file.** Subagents propose updates in their final report; Lead reviews + writes.
>
> Format: append-only log. Newest entry at the top. Each entry has a date, scope, and the locked decision + reasoning + downstream implications.

## 2026-05-17 — Project bootstrap + Mode A first

**Scope:** secretary / shared

**Decisions:**

1. **Project name = `secretary`** (English, ASCII slug). Thai "เลขา" reserved for user-facing prose. ASCII slug keeps URLs (`/p/secretary`), file paths, and DB queries simple. Renaming is cheap if operator wants `เลขา` later.

2. **Team = `general`** (per agent-teams CHECK constraint). Could justify a new `personal` or `secretary` team in the future, but `general` is the right starting point — secretary tasks span email / browser / drafting and don't fit any single domain specialist.

3. **Mode A first** (interactive Lead session + Chrome MCP, no langgraph browser tools). Per session-review-2026-05-17.md insight #4: Mode A works today with zero new wiring. Mode B (autonomous langgraph with browser tools as `langgraph/tools/browser/*`) is ~3-5 days dev work and deferred until Mode A measurement reveals scaling pain. This is the cheaper validation loop.

4. **Knowledge base lives in `shared/` not `general/`**. Operator-curated content is shared across every secretary spawn; `general/` is per-run state (drafts, logs, session notes). This matches the existing zone architecture for dev projects: `shared/` for "what the team agrees on" + per-role for "what one agent worked on".

5. **HITL-default on every external effect**. Reply email / submit application / post LinkedIn / pay-or-subscribe all pause for operator approval. Approval policies (#957) can auto-approve READ actions but operator must explicitly opt-in to auto-approve SEND/POST patterns. Conservative default: over-pause beats over-act.

6. **Summarize-don't-dump output contract**. Secretary's reports to Lead use counts + file pointers, never inline raw email bodies / full job descriptions / article text. This is the 3-tier architecture's central discipline: Project Lead's 200K context window is the binding constraint; raw data dumps from secretary collapse the whole pattern. Codified in `.claude/agents/secretary.md` output format.

7. **Operator-fillable knowledge base scaffolds with explicit `[TODO]` markers**. Secretary halts when it encounters TODOs (refuses to guess operator preferences). This forces operator's "fill the profile" step to be explicit — discoverable + auditable rather than "fail silently on a hallucinated default."

8. **Per-project budget cap = $5/day, $50/month** (initial). Secretary is supposed to be high-volume + cheap (summarization tasks on Haiku-class models). $5/day = ~250-500K tokens depending on model mix. If first week's actual spend is consistently <$1/day, raise floor proportionally; if it brushes the cap, investigate before raising (budget cap is a fence around overflow, not a target).

9. **3 workflow briefs as starting set**: email-triage / job-apply / linkedin-post. Cover the operator's tomorrow-test request. Adding new workflows is a `shared/workflow-briefs/<name>.md` write + a paragraph in the secretary agent definition's "Workflow patterns" section. No DB schema change required.

10. **Approval policy uses `auto_deny` patterns for irreversible-destructive defaults** (delete account, unsubscribe from all, cancel subscription). Even if operator forgets to set ROLES on the rule, these strings hit auto-deny and halt the workflow. Conservative belt-and-suspenders given the secretary's broad browser-access surface.

**Reasoning:** This project is the **first test of the 3-tier architecture** at a real personal workload. The substrate (auditor / HITL / approval policies / health monitor / financial separation) is in place from the May 16-17 sprint. Mode A keeps the variable count low (no new langgraph wiring) so we can isolate "does the architecture work" from "does the new browser-tool integration work". When Mode A reveals scaling pain (operator can't sit and approve every HITL all day), we'll know exactly which slice needs Mode B / autonomous-Lead investment.

**Implications:**
- Secretary spawn briefs are the canonical examples for any future personal-niche project (job hunt for spouse, content for second LinkedIn account, etc.) — copy `secretary/shared/workflow-briefs/` as a starting set
- Knowledge base TODO discipline becomes the template for ANY domain-knowledge-required agent
- The `summarize-don't-dump` contract should be promoted to `context/standards/` once we have 2+ examples (auditor's audit_report shape + secretary's report format) — Lead surfaces the proposal after first test session
- Browser-tool wiring into langgraph is gated on Mode A friction measurement, not on calendar time
