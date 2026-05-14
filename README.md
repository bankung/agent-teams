# agent-teams

A self-hosted Kanban board paired with an AI orchestration engine that works for any type of project. Create tasks in a simple board UI, and specialized AI agents break down the work and execute it end-to-end — whether that's writing code and tests, drafting prose, or managing research. Ships with a full software development agent team ready to use out of the box. Everything runs on your computer in Docker containers. No cloud sign-ups. No subscriptions. No leaving your code.

## Get started in 2 steps

### Step 1: Install Docker Desktop

Download and install [Docker Desktop](https://www.docker.com/products/docker-desktop/) from the official website. Docker runs the databases and services that power agent-teams. After it installs, restart your computer.

### Step 2: Run the installer

Open a terminal (Command Prompt or PowerShell on Windows; Terminal on Mac) in this folder, then paste the command for your system:

**Windows (PowerShell):**
```powershell
.\bin\install.ps1
```

**macOS / Linux / WSL:**
```bash
./bin/install.sh
```

The installer sets up the database, starts the services, and opens your browser automatically. It takes about 2 minutes the first time.

## What happens next

The browser opens to `http://localhost:5431`. You'll see an empty Kanban board. Create a task, assign it to a role (backend, frontend, etc.), and click "Start" — an AI agent picks it up, does the work, and updates the task status.

You can close the terminal anytime. The services keep running in the background. If you restart your computer, open the same terminal and run the installer command again — it's safe to run more than once.

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

## FAQ

### What is agent-teams?

agent-teams is a local Kanban system paired with an AI orchestration engine. You describe work in plain English or click a button in the UI, and AI agents (specialized for frontend, backend, testing, etc.) break it down, execute it, and report back. Unlike Copilot or Cursor, agents run a full workflow — they don't just auto-complete one line; they write full features, run your tests, and review their own work for bugs. And because everything is local, your code never touches the cloud.

### How is this different from GitHub Copilot / Cursor / other AI coding tools?

Those tools are **in-editor completions** — they auto-suggest the next line or function as you type. agent-teams is a **task orchestrator** — you describe a whole feature ("add user login with email + password + reset flow"), and agents handle the full lifecycle: API design, database migrations, frontend UI, end-to-end tests, code review. Agents also run in the background (you can close the terminal), and they coordinate with each other — backend writes the API contract, frontend consumes it, then testing validates the flow end-to-end.

### Does my code get sent to the cloud?

No. Everything runs in Docker on your machine. The AI models (Claude via Anthropic) do run in the cloud, but your code is sent to the model as plain text in the prompt (like asking a question). The model never stores your code — it generates a response and that response is discarded after the agent finishes. If you're concerned about proprietary logic, you can audit what gets sent by reading the agent's prompt before it runs, or use a local model (not yet supported; on the roadmap).

### Do I need to know how to code to use this?

No, not for **using** agent-teams. You describe tasks in plain English: "add a dark mode toggle," "fix the login crash," "write tests for the payment flow." Agents handle the code. However, understanding your project's structure (which files do what) helps you write better task descriptions. For **customizing** agent-teams itself or tweaking how agents work, some Python/JavaScript knowledge is useful — see "For developers" below.

### Which AI models does it support?

agent-teams uses Anthropic's Claude (Opus, Sonnet, Haiku) by default. You provide an API key in the `.env` file. Support for OpenAI (GPT-4, etc.) and local models (Ollama, Llama, etc.) is on the roadmap.

### How do agents run — do I have to babysit them?

There are two modes:

1. **Interactive (CLI mode)** — You run Claude Code in a terminal and the Lead agent works alongside you. Every file write or shell command prompts you for approval before it happens. Best for people who want full control over what the agent does, or are working on sensitive codebases. This is what you get out of the box.

2. **Full-auto (headless mode)** — Agents run unattended in the background. Write and edit operations are pre-approved; the agent picks up tasks from the queue, works through them, and moves on to the next one without you watching. Best for overnight runs, parallel projects, or routine work you trust the agent to handle. Headless mode is in active development (on the roadmap).

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

## What's next

The roadmap includes:

- **Manual task creation form** — Add a "New Task" button in the Kanban UI so you can create tasks without the API
- **AI task creation** — Type a task in plain English in the Kanban UI, and AI proposes the fields for review before saving
- **Run button in task drawer** — One-click to start an AI agent directly from the task detail view
- **Headless agent engine (Phase 4)** — Run agents without needing the terminal open, with proper checkpointing and human-in-the-loop pauses
- **Multi-AI provider support** — Swap between Claude, OpenAI, and local models with an environment variable
- **MCP server adapter** — Expose the Kanban as an MCP tool so other AI clients (Claude Desktop, Cursor) can trigger agents
- **Per-project agent roster** — Enable/disable specific agents per project; add custom roles (tech writer, SA, data analyst, etc.)

## Known issues

- **Windows + Docker Next.js stale bundle**: On Windows with Docker Desktop, the Next.js dev server sometimes serves cached HTML after file edits. Workaround: run `docker compose restart web` after editing files in `web/app/` or `web/components/`. (Not an issue on Mac/Linux.)
- **Flaky test in full suite**: `test_777_edge_soft_delete_recreate_isolates_working_path` fails when running the full pytest suite but passes in isolation. Cross-test state pollution under investigation. Does not affect production behavior.
- **Broken internal links in Sources badge**: `ref://` sources render as browser links which produce 404 errors. Fix in progress.
- **Cosmetic timestamp bug on dashboard**: Under rare clock-skew conditions, timestamps can show "−N seconds ago" instead of positive values. One-line fix pending.

---

## For developers

All developer-focused content — architecture, storage zones, team rosters, configuration, and workflow examples — lives in [readme_dev.md](readme_dev.md).

If you're setting up agent-teams for development, customizing agents, or integrating it into your own project, start there.
