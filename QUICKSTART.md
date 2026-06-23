# agent-teams — Quick start (5 min)

## What this is

AI agents do work for you in projects you create. You give them a task,
they work, you approve key decisions.

### Why use a Kanban task?

**Task-mode (structured)** vs. **chat-mode (freeform):**

- **Chat:** "add a login form" → agent writes code → you approve each edit → no clear done state
- **Task:** Create task with acceptance criteria:
  - AC1: Email + password validation
  - AC2: "Forgot password" link
  - AC3: 401 on bad creds
  
  Agent knows exactly what done looks like. Queueable. Auditable. Other agents can resume if one gets stuck.

**Pick tasks for real work** (features, bugs, refactors). Use chat for questions and exploration.

## Open the UI

Open your browser to **http://localhost:5431**

(If you changed ports during install, check `.env` or `docker-compose.yml` for the actual `WEB_PORT`.)

## Try the demo project

1. Click the **demo-tour** project from the list.
2. You'll see 3 sample tasks:
   - [DEMO] Draft a small FastAPI hello-world endpoint
   - [DEMO] Draft 3 LinkedIn post variations about AI productivity
   - [DEMO] Summarize sample_sales.csv: top categories + 30-day trend

3. Click **Run** on any task to hand it to the headless engine — you'll see it pick up the task and post its progress in the task drawer.
4. If a yellow "awaiting your input" banner appears, click it, answer the question, click **Resume**.

> **Status: In active development.** The headless engine posts a plan and status updates, but full autonomous end-to-end execution (writing code, running tests) isn't live yet via the browser button. To see agents actually do real work today, use the Claude Code path below.

## Real end-to-end work — use Claude Code or Codex

The browser board is great for creating, managing, and tracking tasks. For agents to actually execute work (write code, run tests, make commits), drive them from a terminal session:

→ **[CLAUDE-CODE-START.md](CLAUDE-CODE-START.md)** — open this repo in Claude Code or OpenAI Codex, tell the Lead which project to work on, and it orchestrates the specialists end-to-end. This is the production path today.

## Create your own project

1. From the project list, click **New Project**.
2. Give it a name (e.g., "my-first-project").
3. Pick a domain (dev / content / data / general).
4. Create your first task (e.g., "write a React button component").
5. Track progress on the board; for real execution, open a Claude Code session pointed at this project (see [CLAUDE-CODE-START.md](CLAUDE-CODE-START.md)).

## When things look stuck

- **Browser says "loading…" for >30s** → reload the page (Ctrl+R or Cmd+R).
- **Task says "waiting for AI" for >5 min** → check Docker is healthy:
  ```
  docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
  ```
  If anything shows "unhealthy", restart:
  ```
  docker compose -f docker-compose.yml -f docker-compose.prod.yml restart api
  ```
- **An agent did something you didn't want** → click **Halt** on the task,
  write down what went wrong, file a bug.

## Tier preset (optional)

If you use Claude Code Pro instead of Max, you can adjust the AI model tier:
```
bin/agent-teams-tier-set.sh pro    # (macOS/Linux/WSL)
.\bin\agent-teams-tier-set.ps1 pro # (Windows PowerShell)
```
Then restart your Claude Code session.

## For more

- **Power features:** see [USAGE-POWER.md](USAGE-POWER.md) — queuing, auto-mode, parallel agents, mobile remote access, multi-project workflows.
- **Claude Code (recommended for real work):** see [CLAUDE-CODE-START.md](CLAUDE-CODE-START.md) — direct agent control from your terminal.
- **Technical details:** see `README.md` (Docker, config, API).
- **Bug reports:** open an issue or email bankung99@gmail.com.
