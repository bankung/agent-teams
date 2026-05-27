# Failure modes — dev team methodology

**Purpose.** Categories of failure that gate every `team='dev'` project, with the agent-side action and the recovery path. Each category names when it fires, the symptom, the halt sentence, and how Lead / operator resolves.

**Scope.** Universal across every project under the dev team. Project-specific failure catalogs live in that project's `shared/failure-modes.md` (for example secretary covers its own Chrome-MCP-only categories 1–9). Categories below apply regardless of which dev project is active.

> **Re-evaluation trigger.** If a non-dev team (novel, content, sem, seo, data-analytics, or any future team) starts hitting a category listed here, the category must move UP to `context/standards/` because it is no longer a dev-team concern. Move it on the **second** team's first incident — do not pre-generalize past evidence (Q2 from CLAUDE.md storage zones: push UP only when a second instance confirms cross-team scope).

---

## Category 10 — Mode B harness classifier block

**When it happens.** A Lead or subagent running under Claude Code's autonomous-mode harness attempts an external write (post / publish / submit / send) where the action sits at this block-axis intersection:

External-System-Write × Public-facing-audience × AI-drafted-content × Unverifiable-from-transcript × Operator-identity

The harness's permission classifier scans pre-execution and refuses to dispatch the tool call. The block fires regardless of whether the agent is a subagent (Category 8 layer) or Lead-direct.

**Symptom.**
- Agent or Lead reports "Halted by harness permission classifier — cannot proceed without operator approval" at the step that would have clicked Publish / Submit / Send.
- Zero external tool calls executed for the blocked step (read-side calls earlier in the run typically succeed; the gate is on the write boundary).
- Operator's chat-approval ("approve", "go ahead", etc.) earlier in the session does NOT defeat the gate. The classifier reads the transcript visible to it, and operator approval phrasing inside a spawn brief is treated as unverifiable signal.

**Distinct from Category 8.** Category 8 (documented in secretary's `failure-modes.md`) is the **subagent-brief** classifier pre-block — the subagent dies at its FIRST tool call when the spawn brief contains send-intent phrasing. Category 10 is the **harness-level** classifier on the runtime tool boundary — it fires regardless of brief phrasing and regardless of agent layer. Lead-direct does NOT sidestep Category 10 (it does sidestep Category 8). Both can fire on the same workflow.

**Action.** Do NOT auto-retry. Do NOT rephrase the brief and re-spawn — that defeats Category 8 only, not Category 10. Halt and escalate. Recovery is one of the 5 mitigation patterns below.

**Halt message.**

```
HALT: harness permission classifier blocked external write (Category 10).
Step: <verb> at <URL or service>.
Axis: External-System-Write x Public-facing x AI-drafted x Unverifiable.
Recovery: route via Pattern <N> from mode-b-authorization-chain.md, or fall back to Pattern 1 (operator clicks).
```

**Mitigation patterns.** Five patterns are ranked in `context/projects/agent-teams/shared/design/mode-b-authorization-chain.md` (sections 2.1 – 2.5). Summary for quick triage:

| Pattern | Name | Suitable for |
|---|---|---|
| 1 | Operator-in-the-last-click | Highest-stake one-off actions. Strongest gate; defeats autonomy by design. |
| 2 | Narrow `settings.json` allowlist scoped to URL + verb | One-time human config, narrow blast radius. Risk: rule creep over time. |
| 3 | Pre-signed authorization token | Future infra — operator signs an action + payload-hash; classifier or hook verifies. |
| 4 | Classifier-readable Kanban audit trail | Operator approval recorded in DB; PreToolUse hook reads and admits. |
| 5 | `approval_policies` harness enforcement | Per-project rules with matchers (max delta, whitelist) enforced by a PreToolUse hook. |

**Lead's role on a Category 10 halt.**
1. Read the halt; classify which pattern fits the workflow's stakes and frequency.
2. If no pattern is yet operational for the project, fall back to Pattern 1 (operator clicks the final action).
3. File a followup task to wire the appropriate pattern, citing `mode-b-authorization-chain.md` as authority.
4. Do NOT loop the subagent on the same gate — every retry that hits the same axis is wasted budget.

**Cross-references.**
- Design authority: `context/projects/agent-teams/shared/design/mode-b-authorization-chain.md` (Kanban #1205, all 10 sections).
- Originating incidents: Kanban #1201 (LinkedIn click-Publish block, 2026-05-18), #1180 (job submit pipeline expected to hit gate), #1174 (subagent-brief block — Category 8 sibling).
- Design parent task: Kanban #1205.
- Promotion record: Kanban #1276 (this file's creation).
