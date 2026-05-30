# External-tool scan & competitive analysis — agent-teams

> Running log of external tools/projects worth watching for borrow-candidates (yours-first lens — mine for fits, don't pivot). Entries below: **Hermes Agent v0.15.0** (full comparison) · **SkillOpt / Microsoft** (future-improvement reference).

---

## Hermes Agent v0.15.0 vs agent-teams

**As-of:** 2026-05-30 · **decay_class:** time-sensitive (re-verify version-specific claims before relying on them)
**Source:** operator-shared news article → verified against primary source by workflow `hermes-v0150-vs-agent-teams` (run `wf_5745c1f7-449`). Hermes facts confirmed against GitHub release tag `v2026.5.28` + raw `RELEASE_v0.15.0.md`.
**Lens:** yours-first — mine Hermes for concrete borrow-candidates; do NOT pivot. This file is the durable record behind the adoption discussion.

---

## TL;DR

Hermes v0.15.0 "The Velocity Release" signals the agent field converging on exactly the bet agent-teams already made — **Kanban as the multi-agent control plane** (worktree-per-task, per-task model override, gated verifier/synthesizer, prompt-injection chokepoints, a secrets vault). agent-teams holds genuine **parity or lead** on persistence, the 5-zone shared blackboard, recurrence, the encrypted+audited credentials vault, and a deep injection defense. **Hermes is ahead** on runtime velocity, true swarm fan-out, zero-LLM session search, and a vetted MCP/skill ecosystem. Honest read: **same thesis, different maturity surface** — borrow Hermes's runtime + swarm ideas, do not pivot.

## Feature matrix

| Area | Hermes v0.15.0 | agent-teams | Verdict |
|---|---|---|---|
| Kanban-as-orchestrator | Kanban drives the multi-agent platform | HAVE — `api/src/models/task.py`, Lead in `CLAUDE.md`, `langgraph/graph.py` | Parity |
| Task auto-decomposition | Triage auto-splits into a sub-task tree | PARTIAL — parent/child FK + `max_active_children`, no auto-split | Hermes leads |
| Parallel workers / swarm | `kanban swarm`: root + parallel workers | PARTIAL — supervisor → 1 specialist/task, no fan-out | Hermes leads |
| Gated verifier + synthesizer | Both gated nodes in Swarm v1 | PARTIAL — `audit_report` JSONB + `requires_human_review`; nodes stubbed | Hermes leads |
| Shared blackboard | Swarm shared blackboard | HAVE — 5-zone storage, Q0–Q3 rules in `CLAUDE.md` | Parity / agent-teams leads |
| Scheduled tasks | Scheduled start times, claim TTL | HAVE — `recurrence_rule` + `next_fire_at`, ~38 live instances | Parity / agent-teams leads |
| Per-task model override | Per-task model overrides | PARTIAL — project `agent_overrides` + `subagent_models` log; no per-task column | Hermes leads (narrow) |
| Worktree-per-task | Per-task worktree paths/branches | NONE — session-scoped worktrees only | Hermes leads |
| Session search / memory | Zero-LLM 3-mode search (claimed ~4,500× faster) | PARTIAL — structured `shared/` memory, grep-based, no search API | Hermes leads |
| Prompt-injection defense | 3 chokepoints, `threat_patterns.py` | HAVE — multi-layer injection defense + PreTool/PostTool hooks; `langgraph/content_safety.py` | Parity / agent-teams leads |
| Secrets management | Bitwarden Secrets Manager source-of-truth | HAVE — Fernet vault + `CredentialAccessLog`; `api/src/models/credential.py` | Parity |
| Plugins / skills / MCP | Skill bundles, vetted MCP catalog | NONE/PARTIAL — markdown agent defs; MCP adapter deferred (#806) | Hermes leads |

## Next-gen axes — agent-teams scorecard (1–5)

- **Fast: 2** — no runtime perf work; LangGraph spawn loop unoptimized vs Hermes's deferred-import / adaptive-polling wins.
- **Handles complex work: 3** — strong hierarchy + audit scaffold, but auto-decompose and synthesizer are stubs.
- **Works as a team: 3** — real Lead→specialist orchestration + 5-zone blackboard; no parallel swarm fan-out.
- **Remembers context: 4** — durable Postgres + structured per-project shared memory; lacks fast search.
- **Secure for real workflows: 5** — multi-layer injection defense, encrypted+audited vault, PreTool/PostTool hooks, DB-write-via-API discipline. (agent-teams' strongest axis.)

## Borrow-candidates (ranked, high-value-low-effort first)

1. **Per-task model override column** — map to **#1187** (per-agent tier routing). Add `tasks.model_override`; pairs with the existing `subagent_models` log. **Effort S.** Closes the one narrow gap, low blast radius.
2. **Zero-LLM session search over `shared/`** — new task (adjacent: #975, #1583). A 3-mode grep/index over `decisions.md` + `incidents/` removes aux-LLM cost+latency. **Effort M;** high daily value.
3. **Wire gated verifier + synthesizer nodes** — map to **#1239 / #1261 / #1297** (audit fire / schema). Promote the stubbed reviewer/synthesizer in `langgraph/nodes.py` to gated nodes feeding `audit_report`. **Effort M.**
4. **Swarm fan-out (parallel workers per task)** — new task (engine-adjacent #1191). Highest value, highest effort; prototype root→N-worker→verifier on ONE template before generalizing. **Effort L.**
5. **MCP adapter exposing Kanban as tools** — map to **#806**. Matches Hermes's MCP-catalog direction; makes agent-teams consumable by any client. **Effort L.**

**Defer:** Bitwarden swap (vault already HAVE — no value). Runtime micro-opts (premature for current scale).

## Caveat — vendor self-reported metrics

All Hermes numbers (16,083→3,821 LOC / −76%, 47% fewer calls, 701→258ms, ~90s→~20ms / 4,500×) were confirmed **only as "the article matches Nous's own release notes"** — NOT independently reproduced. Cite them as **Nous's claims**, not measured fact. (Same posture applied to the SkyClaw / Skywork benchmark claims reviewed the same day.)

---

# SkillOpt (Microsoft) — auto-optimize natural-language skills

**Logged:** 2026-05-30 · **decay_class:** review-on-touch · **Status:** future-improvement reference, NOT adopted.
**Source:** https://github.com/microsoft/SkillOpt — official Microsoft, MIT, ~2.9k stars, arXiv paper. Verified via GitHub repo + project page.

**What it is:** a text-space optimizer that treats a natural-language skill (a `skill.md`) as a *trainable parameter*. Loop: run the frozen agent on scored task batches → an optimizer LLM proposes structured edits to the skill text → accept the edit ONLY when validation score improves (gated) → repeat with epoch / batch / learning-rate analogs → emit a deployable `best_skill.md`. No model weights touched. Backends: Azure OpenAI / OpenAI / Anthropic / self-hosted Qwen.

**Why relevant to agent-teams:** our `.claude/agents/*.md`, `.claude/skills/`, and team playbooks ARE hand-maintained natural-language skills — exactly the artifact SkillOpt auto-tunes. It's a concrete mechanism for a **self-improving / learning-loop layer**: agents that measurably get better over time without retraining the base model (quality compounding = a durable edge).

**Gap / prerequisite (the real work):** SkillOpt needs a benchmark with ground-truth scores. Our task ACs are **HITL-verified, not auto-scored**, so the missing piece is an **eval/scoring harness over Kanban tasks** (gated validation derived from AC + `audit_report` + dev-reviewer). Our AC + audit + verifier are the *seed* of this, not the finished thing. It also optimizes one skill per benchmark (per-skill eval sets needed) and is token-expensive (repeated batch rollouts).

**Borrow = direction, not the tool (yet):** don't plug SkillOpt in now. The high-value borrow is building a small **task-eval/scoring harness** first — which ties directly to the gated-verifier borrow-candidate above (Hermes #3/#4 → backlog #1239 / #1261). Once that exists, a SkillOpt-style loop can auto-tune our agent-defs as a later, separable step.

**Caveat:** the headline "52/52 wins across 6 benchmarks / 7 models" is the **paper's own reported result** — cite as the authors' claim, not independently reproduced (same posture as the Hermes / SkyClaw metrics above).
