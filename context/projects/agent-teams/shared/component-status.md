---
decay_class: review-on-touch
review_when: a component's maturity changes, or before any public release / README sweep
last_reviewed: 2026-05-29
---

# agent-teams — component status registry

Authoritative current maturity of each agent-teams component. This is the **single
source of truth** the user-facing docs are reconciled against.

Convention (states, marker form, sweep procedure):
[`context/teams/dev/doc-status-convention.md`](../../../teams/dev/doc-status-convention.md).

> **How to use this file.** Static Markdown can't transclude, so this registry does
> **not** auto-update the prose in README/QUICKSTART/etc. — each doc carries its own
> inline `**Status:**` marker. When a component's maturity changes: edit its row here
> **first**, then `grep -ri "<canonical phrase>"` the docs and update every match in
> the same change (see the convention's "Maintenance sweep").

## Registry

| Component | Status | Note | Referenced in |
|---|---|---|---|
| Postgres-backed Kanban (projects / tasks / history) | Production | Create / queue / track / audit tasks. | README, QUICKSTART, USAGE-POWER |
| Lead meta-orchestrator + team playbooks (interactive) | Production | Resolves project, loads playbook, spawns specialists. The production execution path. | README, CLAUDE-CODE-START |
| Specialist agents — interactive execution (Claude Code / Codex) | Production | Real code edits / tests / commits via the interactive CLI with per-action approval. | README, CLAUDE-CODE-START, USAGE-POWER #3 |
| 5-zone context architecture (DB / standards / team / project / role) | Production | Bounded per-agent context; prompt-caching measured ~77.5% input-cost reduction. | README |
| Acceptance-criteria gates + HITL question/resume | Production | Tasks aren't "done" until each AC checks against a real source; blocking questions surface as Kanban tasks. | README, QUICKSTART, USAGE-POWER #4 |
| Cost guardrails (daily/monthly budget caps, `429` projection) + audit trail (`tasks_history`) | Production | Enforced at task creation. | README |
| Defense-in-depth (21 prevention layers) | Production | DB role gates, migration/seed target guards, payload caps, context sanitization, LLM safety prelude, pre-push scan, soft-delete + audit triggers. | README |
| Parallel agent spawns (interactive) | Production | Multiple specialists on one task via an interactive session. | USAGE-POWER #3, CLAUDE-CODE-START |
| Multi-project context switch | Production | Re-bootstrap to a different project mid-session. | USAGE-POWER #5 |
| Mobile remote access (Tailscale) + push notifications (ntfy) | Production | View/create tasks, answer HITL from phone; notifications opt-in via `NTFY_TOPIC`. | USAGE-POWER #4 |
| Multi-provider LLM switch (Anthropic / OpenAI / Ollama / DeepSeek) | Production | One `.env` variable (`LANGGRAPH_LLM_PROVIDER`); Ollama = fully local, no egress. DeepSeek (V3/R1, OpenAI-compatible) wired 2026-06-02 (#1086) — needs `DEEPSEEK_API_KEY` + a live smoke (#1838) before relying on it. | README |
| **Headless `langgraph` auto-run engine** (`run_mode=auto_pickup` / one-click "Start") | **In active development** | Posts a plan + status updates and checkpoints state in Postgres so runs are resumable. Autonomous end-to-end execution (writing code, running tests, making commits) is **not live yet** — treat results as drafts. | README §"What it is — and isn't" + §Get started 3.2, QUICKSTART headless note, USAGE-POWER #1 (option B) + #2, CLAUDE-CODE-START auto-mode bullet |
| Fully autonomous end-to-end headless execution (the engine writing code / running tests / committing with no terminal open) | Roadmap | The end-state of the in-active-development headless engine above. No working implementation today. | (implied by the headless-engine caveats above) |
