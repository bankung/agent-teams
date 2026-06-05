# agent-teams

**A self-hosted orchestration and governance layer that turns Claude Code or OpenAI Codex into a persistent, governed, multi-domain agent team.**

You know the feeling: the coding session that started sharp is now drifting, re-explaining itself, and holding your entire plan hostage. A single CLI is a powerful brain with no memory across sessions, no project structure, and no safety rails.

agent-teams closes that gap. It wraps your coding CLI with a Postgres-backed Kanban, a Lead meta-orchestrator that spawns fresh domain specialists per task, a five-zone context model, and a defense-in-depth safety layer. File the queue, step away, trust it's handled — the leverage of a whole team, without the burnout of being one.

Everything runs locally in Docker. No cloud sign-up, no SaaS subscription, no code leaving your network.

It is also **dogfooded**: agent-teams builds agent-teams. The repo's own commit history and Kanban are living proof.

---

## Why it's different

Claude Code already gives you sub-agents, and you can keep several sessions open. agent-teams is the layer that makes that raw power actually land: every task becomes a clean, scoped contract instead of one sprawling chat you keep having to wrangle.

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

- **Tasks are contracts — with proof.** Every task carries structured acceptance criteria. Before a task can be marked done, each criterion is verified with evidence and stamped passed/failed. The system proves the work met the contract; it doesn't just claim "done." Hard cost guardrails (daily/monthly budget caps → `429`) and a full `tasks_history` audit trail are built in.

- **Batch and parallel without context rot.** Queue tasks, run them back-to-back or in parallel — each spawns a fresh domain specialist with scoped context. No sprawling conversation, no bleed-through. The Kanban holds the plan; the agents hold nothing stale.

- **Two execution modes.** Mode A (production today): Claude Code or Codex drives each specialist interactively with per-action approval — you keep control. Mode B (actively in development): flip a task to `auto_headless` and the LangGraph engine runs it with no terminal open, Postgres-checkpointed.

- **Rich planning views.** Board · List · Calendar · Gantt in one switcher. Calendar supports week/month with drag-to-reschedule. Gantt doubles as the milestone home — drag a task straight onto a milestone and watch the progress rollup update.

- **Extend without migrations.** Add a new team or new agent types by editing constants and dropping a markdown file — no DB migration required. 8 teams and ~39 specialist agent definitions ship today. → [How to add a team](readme_dev.md#team-roster--dev-team) · [Full onboarding runbook](context/teams/dev/team-onboarding-runbook.md)

- **Self-hosted, local-first, dogfooded.** Runs in Docker on your machine. Anthropic, OpenAI, or fully-local Ollama — your choice, one `.env` variable. No code leaves your network. And the system building itself is the system you're reading about: the commit log and live Kanban are the proof.

---

## What it is — and isn't

**It is:** an orchestration and governance layer on top of a coding CLI. Works today with **Claude Code** and **OpenAI Codex**.

**It isn't:**
- a frontier autonomous SWE agent like **Devin** — it orchestrates your coding agent, it doesn't replace one;
- an IDE like **Cursor** or **Windsurf** — no editor here; keep your own;
- a from-scratch agent framework like **CrewAI** / **AutoGen** / **LangGraph** — it actually *uses* LangGraph for its headless engine rather than reinventing it.

**Honest status on the headless engine:** today the production path is Mode A — Claude Code / Codex CLI driven interactively (per-action approval). The `langgraph` service (supervisor → specialist graph, Postgres-checkpointed) is the Mode B path and is **actively in development**. Don't rely on it for critical work yet.

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

The Lead reads the team playbook, resolves the active project, and spawns the right specialists. Specialists run on your coding CLI and write their results back to the Kanban and five context zones — no context leaks between tasks.

---

## CLI-agnostic by design

The orchestration works across coding CLIs because the rules live in portable instruction files: [`CLAUDE.md`](CLAUDE.md) for Claude Code and [`AGENTS.md`](AGENTS.md) for Codex. Same governance, same lanes, same team structure — whichever CLI you run. You're not locked to one vendor.

---

## Get started

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and restart your computer.
2. Open a terminal **in this folder** and run the installer:
   - **macOS / Linux / WSL:** `./bin/install.sh`
   - **Windows (PowerShell):** `.\bin\install.ps1` *(if scripts are blocked, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once first)*
3. Open **http://localhost:5431** — your Kanban board. The installer seeds a `demo-tour` project to explore. Create tasks, queue them, and answer agent questions as they come up.

Two ways to put agents to work:

- **Mode A — Claude Code / Codex session (production today).** Open this repo in Claude Code or OpenAI Codex. The Lead resolves your project, loads the team playbook, and orchestrates specialists end-to-end. → **[CLAUDE-CODE-START.md](CLAUDE-CODE-START.md)**
- **Mode B — One-click "Start" on the board *(in active development)*.** Flip a task to auto-run and the headless `langgraph` engine handles it with no terminal open. See "What it is — and isn't" above for the honest status.

The installer is safe to re-run; services keep running after you close the terminal.

**Multi-provider, local-first.** Switch models with one `.env` variable (`LANGGRAPH_LLM_PROVIDER`): **Anthropic** (default), **OpenAI**, or **Ollama** for fully local inference — no API key, no network egress. With Ollama, nothing leaves your machine.

**Stop / reset:** `docker compose down` to stop; `.\bin\reset.ps1` (or `./bin/reset.sh`) to wipe and start fresh.

---

## Slash-command skills (tn-*)

These are reusable Claude Code commands that encode Kanban API conventions, preventing common mistakes (missing project_id, incomplete acceptance criteria, status-change guard violations). They activate after a Claude Code restart and are auto-detected on live-reload.

| Command | What it does |
|---------|-------------|
| **Tasks** | |
| `/tn-task-create <description>` | Create a Kanban task correctly (project_id in request body, acceptance_criteria at creation). |
| `/tn-task <id>` | Show one task with its acceptance criteria (read-only). |
| `/tn-tasks-next [N]` | List the next N actionable tasks (current milestone first, blockers first, then priority; N defaults 10). |
| `/tn-task-done <id>` | Verify every acceptance criterion, then flip the task to DONE (refuses if any criterion is unmet). |
| `/tn-task-update <id> <changes>` | Guarded status/priority update (BLOCKED only via blocked_by; status changes carry a reason; DONE is redirected to /tn-task-done). |
| `/tn-task-attach <task> <milestone>` | Attach a task to a milestone (same-project checked). |
| **Milestones** | |
| `/tn-milestone-create <title>` | Create a milestone (defaults to "planned"). |
| `/tn-milestone-done <id>` | Release a milestone after checking its child tasks are complete. |
| `/tn-milestones` | List milestones with their task rollup (done/total, progress %). |
| **Workflow** | |
| `/tn-intense-review <scope>` | 2-round adversarial review + test-hardening pass (reviewers + determinism loop). |
| `/tn-spec <idea>` | 2 rounds of spec pushback + revision before creating a task. |
| **Project** | |
| `/tn-bind <project>` | Bind the session to a project by name (resolves + persists the active project). |
| `/tn-audit [project]` | On-demand project health audit (3 metrics + continue/review/pause). |

Each skill lives at `.claude/skills/<name>/SKILL.md` and is invoked as `/<name>` in Claude Code.

---

## Learn more

Companion docs go deep so this README stays scannable:

- **[QUICKSTART.md](QUICKSTART.md)** — 5-minute tour via the browser UI.
- **[CLAUDE-CODE-START.md](CLAUDE-CODE-START.md)** — driving the team from a Claude Code terminal session.
- **[USAGE-POWER.md](USAGE-POWER.md)** — parallel agents, auto-mode, multi-project workflows, mobile remote access.
- **[readme_dev.md](readme_dev.md)** — architecture deep-dive: storage zones, team rosters, configuration, and extensibility (including how to add a new team or agent type).
- **[context/teams/dev/team-onboarding-runbook.md](context/teams/dev/team-onboarding-runbook.md)** — full step-by-step runbook for adding teams and agents.

For the full development history, browse the git log and the Kanban that drove it — dogfooding in action.
