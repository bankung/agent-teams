# agent-teams

A self-hosted Kanban board paired with an AI orchestration engine that works for any type of project. Create tasks in a simple board UI, and specialized AI agents break down the work and execute it end-to-end — whether that's writing code and tests, drafting prose, or managing research. Ships with a full software development agent team ready to use out of the box. Everything runs on your computer in Docker containers. No cloud sign-ups. No subscriptions. No leaving your code.

> 🚀 **Just want to see it work?** → See [QUICKSTART.md](QUICKSTART.md) (5 minutes)

## Get started in 2 steps

### Step 1: Install Docker Desktop

Download and install [Docker Desktop](https://www.docker.com/products/docker-desktop/) from the official website. Docker runs the databases and services that power agent-teams. After it installs, restart your computer.

### Step 2: Run the installer

Open a terminal **in this folder** (the one you just cloned), then paste the command for your system:

**Windows (PowerShell):**

To open PowerShell in this folder, use one of these methods:

1. **File Explorer method:** In File Explorer, navigate to the cloned folder, then press `Shift + Right-click` in the empty space → select "Open PowerShell window here".
2. **Command line method:** Open PowerShell anywhere, then paste: `cd "C:\path\to\agent-teams"` (replace with your actual folder path).

If you see an error like `cannot be loaded because running scripts is disabled`, run this command ONCE:
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```
Then run the installer:
```powershell
.\bin\install.ps1
```

**macOS / Linux / WSL:**

Open Terminal (or any terminal app), navigate to the cloned folder:
```bash
cd /path/to/agent-teams
```
Then run:
```bash
./bin/install.sh
```

The installer sets up the database, starts the services, and opens your browser automatically. It takes about 2 minutes the first time.

## What happens next

The browser opens to `http://localhost:5431`. You'll see an empty Kanban board. Create a task, assign it to a role (backend, frontend, etc.), and click "Start" — an AI agent picks it up, does the work, and updates the task status.

You can close the terminal anytime. The services keep running in the background. If you restart your computer, open the same terminal and run the installer command again — it's safe to run more than once.

## Two ways to use agent-teams

| | **Kanban board (browser UI)** | **Claude Code session (terminal)** |
|---|---|---|
| **When to use** | Daily task management, watching agents work, answering HITL questions | Direct agent control, scripting multi-step workflows, exploring codebase, multi-task queueing |
| **What you get** | Click-to-run tasks, live task history, notification channels (email/mobile), clean UI for non-dev users | Full context on project decisions, multi-agent orchestration, headless auto-mode tasks, real-time feedback from Lead |
| **Getting started** | See [QUICKSTART.md](QUICKSTART.md) (5 min) | See [CLAUDE-CODE-START.md](CLAUDE-CODE-START.md) (3 min) |

Both modes share the same Kanban board and database — you can switch between them anytime. Start with the browser UI, graduate to Claude Code when you need more control.

Power features (parallel agents, auto-mode, mobile remote access): see [USAGE-POWER.md](USAGE-POWER.md).

## Stop / restart / reset

**Stop the services:**
```powershell
docker compose down
```

**Re-start:**
```powershell
.\bin\install.ps1
```
(Or re-run the command from Step 2.)

**Reset everything (wipe the database and start fresh):**
```powershell
.\bin\reset.ps1
```
The script will prompt you to type `WIPE` to confirm before dropping the database volume. Pass `-Yes` to skip the prompt in scripted contexts. The script refuses to run from a git worktree or a directory without `docker-compose.yml` (defense against accidentally wiping the wrong compose project).

## FAQ

### What is agent-teams?

agent-teams is a local Kanban system paired with an AI orchestration engine. You describe work in plain English or click a button in the UI, and AI agents (specialized for frontend, backend, testing, etc.) break it down, execute it, and report back. Unlike Copilot or Cursor, agents run a full workflow — they don't just auto-complete one line; they write full features, run your tests, and review their own work for bugs. And because everything is local, your code never touches the cloud.

### How is this different from GitHub Copilot / Cursor / other AI coding tools?

Those tools are **in-editor completions** — they auto-suggest the next line or function as you type. agent-teams is a **task orchestrator** — you describe a whole feature ("add user login with email + password + reset flow"), and agents handle the full lifecycle: API design, database migrations, frontend UI, end-to-end tests, code review. Agents also run in the background (you can close the terminal), and they coordinate with each other — backend writes the API contract, frontend consumes it, then testing validates the flow end-to-end.

### Does my code get sent to the cloud?

No. Everything runs in Docker on your machine. Cloud AI models (Claude via Anthropic, GPT-4 via OpenAI) do run in the cloud — your code is sent to the model as plain text in the prompt (like asking a question). The model never stores your code — it generates a response and that response is discarded after the agent finishes. If you're concerned about proprietary logic, you can audit what gets sent by reading the agent's prompt before it runs, **or run a local model via Ollama** — all inference stays on your machine and never touches the network.

### Why use a Kanban task vs. just chatting with Claude Code?

**Chat-only:** "Add a login form" — agent writes code, you approve each edit, no clear definition of done, hard to resume if interrupted.

**Kanban task:** File a task with **acceptance criteria**:
- AC1: Email + password input fields with validation
- AC2: "Forgot password" link present
- AC3: POST returns 401 on bad credentials

**Benefits:**
- **Clear definition of done** — agent knows exactly what "done" means, not guessing
- **Queueable** — add 5 tasks, agents work through them in order or in parallel
- **Auditable history** — every task tracks what was tried, what worked, what didn't
- **AI hits target more reliably** — structured AC beats prose every time
- **Resumable** — if an agent gets stuck, another agent picks up mid-task from the checkpoint

Chat is great for questions and exploration. Tasks are great for real work.

### Do I need to know how to code to use this?

No, not for **using** agent-teams. You describe tasks in plain English: "add a dark mode toggle," "fix the login crash," "write tests for the payment flow." Agents handle the code. However, understanding your project's structure (which files do what) helps you write better task descriptions. For **customizing** agent-teams itself or tweaking how agents work, some Python/JavaScript knowledge is useful — see "For developers" below.

### Which AI models does it support?

Three providers are supported, switchable via a single environment variable (`LANGGRAPH_LLM_PROVIDER` in `.env`):

- **Anthropic Claude** (Opus, Sonnet, Haiku) — default; requires `ANTHROPIC_API_KEY`
- **OpenAI** (GPT-4o, GPT-4o-mini) — requires `OPENAI_API_KEY`
- **Ollama** (Llama 3, Qwen 2.5/3, Mistral, Gemma, etc.) — local; no API key, no cloud, no cost. Install [Ollama](https://ollama.com/) on your machine, `ollama pull <model>`, then set `OLLAMA_MODEL=<model>` in `.env`. See [langgraph/README.md](langgraph/README.md) for the full setup.

Switching providers requires no code change — edit `.env` and `docker compose restart langgraph`.

### How do agents run — do I have to babysit them?

There are two modes:

1. **Interactive (CLI mode)** — You run Claude Code in a terminal and the Lead agent works alongside you. Every file write or shell command prompts you for approval before it happens. Best for people who want full control over what the agent does, or are working on sensitive codebases. This is what you get out of the box.

2. **Full-auto (headless mode)** — Agents run unattended in a Docker container (`langgraph` service). The agent polls the Kanban for tasks marked `task_kind=ai` + `run_mode=auto_pickup` (or `auto_headless` with project consent), picks them up, executes through the LangGraph engine, and PATCHes the result back. No per-action approval prompts. Best for overnight runs, parallel projects, or routine work you trust the agent to handle. State is checkpointed in Postgres so a container restart resumes mid-task.

Both modes use the same Kanban board — you can see what agents did (and didn't do) in the task history.

### Can I use it with multiple projects?

Yes. The installer creates one project in the Kanban at first (called "agent-teams"). To add your own project, use the CLI:

```powershell
.\bin\agent-teams-init.ps1 -Name myapp -WorkingPath C:\code\myapp -Team dev
```

This registers "myapp" in the Kanban and scaffolds the orchestration layer into that folder. Multiple projects run against the same Kanban instance. Switch between projects by naming the project when you talk to the AI, or use the project switcher in the UI.

### Can I customize the AI agents?

Yes. Each agent is defined in a markdown file (`.claude/agents/<role>.md` in your project). You can edit the agent's instructions, the role's scope, even add new roles for your team. Changes take effect on the next task. See "For developers" for more detail.

### What happens if I close the terminal / restart my computer?

The services (Kanban board, database, API) keep running in the background. Close the terminal anytime. If your computer restarts, just run the installer again (`.\bin\install.ps1`) — it reconnects to the same database and picks up where it left off. Tasks in progress stay in the Kanban; you can resume them.

### Is this free?

agent-teams itself is free and open source. Running it requires a Claude API key (Anthropic's paid service — same as Cursor or other Copilot tools). You're charged per token used by agents, just like any other AI tool. No subscription; you pay only for what you use.

### How do I add a new project?

Use the CLI command:

```powershell
.\bin\agent-teams-init.ps1 -Name myapp -WorkingPath C:\code\myapp -Team dev
```

Replace `myapp` with your project name and `C:\code\myapp` with the folder where your code lives. The team defaults to `dev` (best for software projects); see "For developers" for other team options.

The command registers the project in the Kanban and scaffolds the orchestration layer (agent definitions, standards, decision logs) into your project folder. It's safe to run multiple times — existing files are skipped.

## Notification channels

agent-teams can alert you when tasks change, feature gates activate, or human feedback is needed. Two independent channels work in parallel:

- **Email digest** — Daily summary of all task activity via Gmail SMTP. Operator-configured via `.env` variables. Unsubscribe via a signed link in the email footer; re-enable via API.
- **Push notifications** — Real-time alerts to iOS or Android via ntfy.sh (free public service or self-hosted). Fires automatically when HITL-blocking tasks (kind=`question` or `decision`) are created, or on budget threshold breaches. Topic name is your choice; obscurity provides baseline safety since ntfy topics are world-readable.

Both channels pull from `projects.notification_targets` (priority-ordered list) and `tasks.notification_targets` (per-task override). See [readme_dev.md](readme_dev.md#notification-channels) for env-var setup and API endpoints; [readme_remote-access.md](readme_remote-access.md) for mobile app installation.

Sent via `POST /api/digest/fire` (scheduled daily or manual trigger) and `POST /api/push/fire` (ad-hoc or event-driven). Every delivery attempt is audited to `tasks_history` with operation code `'N'`.

## What's next

**Want more power?** See [USAGE-POWER.md](USAGE-POWER.md) for queuing, auto-mode, parallel agents, mobile remote, and multi-project workflows.

Recently shipped:

- ✅ **Cost guardrails (2026-05-19)** — hard daily / monthly budget cap enforced at task-creation time. POST `/api/tasks` returns `429` with a projection JSON when an AI task would push spend over `projects.budget_daily_usd`. Threshold alerts at 80% / 100% route through the new notification primitive (Telegram first). On-demand `POST /api/projects/{id}/reconcile-budget` recomputes daily + monthly spend from `tasks.estimated_cost_usd`. Emergency-override hatch (`budget_override_authorized_by` + reason on TaskCreate) lets the operator force a single spawn through with an audit log.
- ✅ **Prompt caching for specialist spawns (2026-05-19)** — LangGraph specialist nodes now bundle the stable context (project rules + team playbook + agent definition + safety prelude — ~11k tokens) into a single Anthropic `cache_control: ephemeral` block. Sequential loop iterations within the 5-minute TTL pay only ~10% of the input cost on the cached portion. Measured **77.5% input-cost reduction** on a 10-iteration scenario; break-even at iteration 2. `cost_tracker.compute_cost` now understands `cache_read_input_tokens` + `cache_creation_input_tokens` so the per-task numbers reflect reality.
- ✅ **Push notification primitive (2026-05-19)** — DeliveryTarget DSL on `projects.notification_targets` (priority-ordered list of explicit recipients; per-task override on `tasks.notification_targets`). `POST /api/notifications/deliver` resolves targets in priority order with Telegram bot adapter + local-file fallback, audits every attempt to `tasks_history` with `operation='N'`. Backbone for the upcoming daily-digest + HITL-halt + threshold-alert channels.
- ✅ **Four new agent teams (2026-05-19)** — content team (writer / editor / veracity-checker / hook-doctor / thai-proofreader — the latter generalised from `novel-proofreader` so it works on novel, content, secretary, or any Thai prose), SEO team (strategist / technical / on-page optimizer / reporting analyst), SEM team (campaign lead + per-platform specialists — modular split over single orchestrator for Google Ads / Meta / and 9 less-major platforms), and Data Analytics team (BI analyst / SQL optimizer / dashboard designer / platform integrator — BI-tool agnostic). 17 agent definitions + 3 team playbooks; all bilingual-aware (`target_language` input parameter).
- ✅ **Defense-in-depth hardening (2026-05-17)** — 21 prevention layers landed across the database, API, LangGraph engine, and Claude Code hooks: postgres-role-level last-resort gate on destructive SQL, lifespan DB-allowlist validation, payload size caps, content moderation on task creation, agent-context sanitisation, LLM safety prelude prepended to every prompt, scaffold rate limit, recurrence template caps, and more. Full per-layer detail + verification evidence: `context/projects/agent-teams/shared/incidents/2026-05-17-resume-handoff.md`.
- ✅ **Off-site encrypted backup** — Nightly `pg_dump` + `tar` + `age` encryption + S3/R2 upload with retention. Drilled end-to-end during the 2026-05-17 incident response (download → decrypt → restore in <2 seconds, zero data loss). Min-size check refuses to upload an empty-DB dump; retention pruner refuses to delete when fewer than 2 backups exist.
- ✅ **AI task creation** — Click "AI Task" in the board header, describe what you want in plain English, and an LLM proposes the fields (title, description, type, priority, role) for you to review and tweak before confirming.
- ✅ **Manual task creation form** — "+ New task" button in the board header opens a modal with all task fields; no API call needed.
- ✅ **Dashboard live updates** — Cross-project dashboard auto-refreshes on every task / project change via the same SSE stream the per-project board uses.
- ✅ **Headless agent engine (Phase 4)** — `langgraph` Docker service polls the Kanban, runs tasks through a supervisor → specialist graph, and persists state via `AsyncPostgresSaver`. Multi-provider (Anthropic / OpenAI / Ollama).
- ✅ **Multi-AI provider support** — `LANGGRAPH_LLM_PROVIDER` env-var switches between three providers with no code change.
- ✅ **Run button in task drawer** — One-click flip `run_mode` from manual to auto_pickup, queuing the task for the headless engine.

Still on the roadmap:

- **Specialist tools** — Give the AI agents file-edit / shell / git tools so they can actually make code changes (today they generate plans + answers but don't yet write code on their own)
- **Human-in-the-loop resume** — Question/decision tasks that pause the agent and resume from a Postgres checkpoint when you answer
- **MCP server adapter** — Expose the Kanban as an MCP tool so other AI clients (Claude Desktop, Cursor) can trigger agents
- **Per-project agent roster** — Enable/disable specific agents per project; add custom roles (tech writer, SA, data analyst, etc.)

## Known issues

- **Windows + Docker Next.js stale bundle**: On Windows with Docker Desktop, the Next.js dev server sometimes serves cached HTML after file edits. Workaround: run `docker compose restart web` after editing files in `web/app/` or `web/components/`. (Not an issue on Mac/Linux.)
- **Ollama on Linux compose**: `host.docker.internal` does not auto-resolve on plain Linux Docker (works transparently on Mac/Windows Docker Desktop). Workaround: add `extra_hosts: ["host.docker.internal:host-gateway"]` to the `langgraph` service in `docker-compose.yml`.
- **Specialists are still text-only**: The headless engine currently produces text plans and answers (stored in `status_change_reason`) but does not yet edit files or run shell commands. Real tool use is the next milestone (see "Still on the roadmap" above).

---

## For developers

All developer-focused content — architecture, storage zones, team rosters, configuration, and workflow examples — lives in [readme_dev.md](readme_dev.md).

If you're setting up agent-teams for development, customizing agents, or integrating it into your own project, start there.
