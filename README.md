# agent-teams

**A self-hosted orchestration and governance layer that turns an agentic coding CLI — Claude Code or OpenAI Codex — into a persistent, governed, multi-domain agent *team*.**

You're one person trying to move several things forward at once. The coding agent that was razor-sharp an hour ago is now drifting — its one long session has piled up and mixed context until you're re-explaining what you already said, babysitting each run, and carrying every open thread in your head. A single coding CLI is a powerful brain with no memory across sessions, no project state, no team structure, and no safety rails. agent-teams is the layer that closes that gap: a Postgres-backed Kanban that remembers the plan so you don't have to, a meta-orchestrator that spawns a fresh domain specialist per task, a five-zone context model, and a defense-in-depth safety system. File the queue, step away, and trust it's handled — the leverage of a whole team, without the burnout of being one. Everything runs on your machine in Docker. No cloud sign-up, no SaaS subscription, no code leaving your network.

It is also **dogfooded**: agent-teams is built *by* agent-teams. The repo's own commit history and Kanban are the system managing its own development.

---

## Why it's different

Claude Code already gives you sub-agents, and you can keep several sessions open. agent-teams is the layer that makes that raw power actually land — every task a clean, scoped contract instead of one sprawling chat you keep having to wrangle.

| | Self-hosted | Persistent task/project state | Beyond code | Governance/safety layer | Form |
|---|:--:|:--:|:--:|:--:|---|
| Cloud SWE agents (Devin, Cursor, Windsurf) | ✗ | ✗ session/codebase | ✗ code-only | black-box | product / IDE |
| AI assistant (GitHub Copilot) | ✗ | ✗ chat-scoped | ✗ code-only | black-box | product |
| Agent frameworks (CrewAI, AutoGen, LangGraph) | ✓ lib | ✗ you build it | ✓ DIY | ✗ you build it | library |
| Self-hosted platform (OpenHands) | ✓ | ~ less structured | ~ dev-focused | local isolation | product / SDK |
| **agent-teams** | ✓ | ✓ Postgres Kanban + 5-zone context | ✓ team playbooks (dev/content/SEO/…) | ✓ defense-in-depth + AC + HITL + cost | **layer on Claude Code / Codex** |

The gap it fills: a **self-hosted, persistent, governed, multi-domain orchestration layer** — the cloud agents and IDEs aren't self-hosted or persistent, and the frameworks hand you a toolbox and a weekend of plumbing. This is the part you'd otherwise build yourself, at night, instead of shipping: the state, the governance, and the team structure, already wired.

---

## What's genuinely special

- **Batch & parallel execution without context rot.** Queue tasks ahead, run them hands-off back-to-back or in parallel — each task spawns a fresh specialist with scoped context, not a sprawling conversation. Per-task context stays bounded; the board holds the plan so you don't.

- **Tasks as precise contracts.** Tasks carry **acceptance-criteria gates** and hard cost guardrails (daily/monthly budget caps, returning `429` if over-budget), ensuring output lands right the first time instead of re-prompting. Full audit trail in `tasks_history`.

- **Meta-orchestrator + team playbooks.** A "Lead" agent resolves the project, loads its team playbook, and spawns the right specialists. **8 teams** and **~39 specialist agent definitions** in `.claude/agents/`, each with a scoped lane and permission model.

- **Persistent state + 5-zone context architecture.** PostgreSQL Kanban plus a five-zone storage model — **DB, standards, team-methodology, project-shared, role-state** — that survives sessions and gives each agent its bounded, relevant slice. Prompt-caching measured a **77.5% input-cost reduction**. See the **[quota-efficiency guide](readme_quota-efficiency.md)** for day-to-day impact.

- **Incident-driven defense-in-depth.** Born from a real postmortem (2026-05-17): **22 prevention layers** span the database, API, LangGraph engine, and CLI hooks — Postgres role gates, migration/seed target guards, payload caps, sanitization, safety prelude, pre-push secret scan, and soft-delete + audit triggers.

- **Dogfooded.** agent-teams develops agent-teams — the orchestration, Kanban, and governance you're reading are the same ones that built them. The commit and task history prove it, in public.

- **Planning views.** Milestones (group tasks into releases with progress rollup), a Calendar (tasks by due-date + milestone deadlines), and a Gantt timeline (milestone-level) — new in 0.5.0.

---

## What it is — and isn't

**It is:** an orchestration + governance layer on top of an agentic coding CLI. It works today via **Claude Code** (the live execution brain) and **OpenAI Codex**.

**It isn't:**
- a frontier autonomous SWE agent like **Devin** — it orchestrates a coding agent, it doesn't *be* one;
- an IDE like **Cursor** or **Windsurf** — there's no editor; you keep your own;
- a from-scratch agent framework like **CrewAI** / **AutoGen** / **LangGraph** — in fact it *uses* LangGraph for its headless engine rather than reinventing one.

And in the interest of an honest reviewer's read: the **headless autonomous engine is in active development**. Today the execution brain is the Claude Code / Codex CLI driven interactively (with per-action approval), while the `langgraph` service runs tasks through a supervisor → specialist graph with Postgres-checkpointed state. The interactive path is the production path right now.

---

## Architecture at a glance

```mermaid
flowchart TD
    Operator([Operator]) -->|files tasks, answers HITL| Kanban[(Kanban · Postgres)]
    Operator -->|talks to| Lead[Lead · meta-orchestrator]
    Lead -->|reads| Playbook[Team playbook]
    Lead -->|resolves project, spawns| Specialists[Specialists: backend / frontend / tester / reviewer / …]
    Specialists -->|run on| CLI[Claude Code / Codex CLI]
    Lead <-->|read/write state| Context[(5-zone context:<br/>standards · team · project · role)]
    Specialists -->|update| Kanban
    CLI -.headless path.-> Engine[LangGraph engine · Postgres checkpoints]
```

The Lead reads the team playbook, resolves which project the session is bound to, and spawns specialists. Specialists run on the coding CLI and persist their work to the Kanban and the five context zones.

---

## CLI-agnostic by design

The orchestration runs on agentic coding CLIs — **Claude Code and OpenAI Codex** — because the rules live in portable instruction files: [`CLAUDE.md`](CLAUDE.md) for Claude Code and [`AGENTS.md`](AGENTS.md) for Codex. The same governance, lanes, and team structure apply regardless of which CLI drives them. This is a deliberate vendor-portable design: you aren't locked to one coding-agent vendor.

---

## Get started

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and restart your computer.
2. Open a terminal **in this folder** and run the installer:
   - **macOS / Linux / WSL:** `./bin/install.sh`
   - **Windows (PowerShell):** `.\bin\install.ps1` (if scripts are blocked, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once first)
3. Open **http://localhost:5431** — your Kanban board (the installer seeds a `demo-tour` project to explore). Create, queue, and track tasks here, and answer agents' questions as they come up. Two ways to put agents to work:

   - **3.1 — From a Claude Code or Codex session (works today).** Open this repo in Claude Code or OpenAI Codex; the Lead resolves your project, loads its team playbook, and orchestrates the specialists end-to-end. This is the production path right now. → see **[CLAUDE-CODE-START.md](CLAUDE-CODE-START.md)**.
   - **3.2 — One-click "Start" on the board *(in active development)*.** Flipping a task to auto-run hands it to the headless `langgraph` engine so it runs with no terminal open. This path is **in active development** (the specialist execution is text-only today) — see the "What it is — and isn't" section above.

The installer is safe to re-run; the services keep running after you close the terminal.

**Multi-provider, local-first.** Models are switchable via one `.env` variable (`LANGGRAPH_LLM_PROVIDER`): **Anthropic** (default), **OpenAI**, or **Ollama** for fully local inference with no API key and no network egress. Your code is never stored by any provider; with Ollama it never leaves your machine.

**Stop / reset:** `docker compose down` to stop; `.\bin\reset.ps1` (or `./bin/reset.sh`) to wipe and start fresh.

---

## Learn more

The companion docs go deep so this README stays scannable:

- **[QUICKSTART.md](QUICKSTART.md)** — 5-minute tour via the browser UI.
- **[CLAUDE-CODE-START.md](CLAUDE-CODE-START.md)** — driving the team from a Claude Code terminal session.
- **[USAGE-POWER.md](USAGE-POWER.md)** — parallel agents, auto-mode, multi-project workflows, mobile remote access.
- **[readme_dev.md](readme_dev.md)** — architecture deep-dive: storage zones, team rosters, configuration, and customization.

For the full development history, read the git log and the Kanban it produced — that's the dogfooding in action.
