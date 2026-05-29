# agent-teams — Claude Code (power users)

## Get Claude Code running

### 1. Open Claude Code in this folder

The agent-teams repo includes orchestration rules (CLAUDE.md, team playbooks, agent definitions) that tell the AI what to do and what not to do. Claude Code auto-loads these files on startup.

```bash
cd /path/to/agent-teams  # (use your actual path)
claude
```

**Windows PowerShell:**
```powershell
cd C:\path\to\agent-teams  # (use your actual path)
claude
```

### 2. Answer Lead's first question

Lead will ask: **"Which project are we working on?"**

Answer with a project name (e.g., `agent-teams`, `myapp`). Lead fetches the project from the Kanban, loads its team playbook (dev, content, data), and starts binding your session.

This binding is critical — it tells Lead which agents to spawn, which standards apply, and which decisions are in scope for this session.

### 3. Why this matters

- **Project binding** — Lead loads your project's shared decisions, API contracts, and team conventions. Without it, every prompt feels like a fresh start.
- **Kanban tasks** — When you file a Kanban task with acceptance criteria, Lead and subagents hit the target more reliably than chat-only work. Tasks are queueable, reviewable, auditable.
- **Parallel agents** — Lead can spawn 3+ subagents in parallel (backend + frontend + tester all working at once), then integrate their results.

## Common first-time mistakes

| Mistake | Fix |
|---------|-----|
| **Opened Claude Code in wrong directory** — CLAUDE.md didn't load, Lead won't start | `cd` to the agent-teams repo root before running `claude` |
| **Jumped to a question before Lead asked for project name** — Lead has no context yet | Wait for the first prompt ("Which project"), or include project name in your opening message |
| **Treating this as generic chat** — asking for code snippets, explanations, small edits | File a Kanban task for real work. Tasks unlock acceptance criteria, queueing, and multi-agent orchestration. Chat-mode costs context without the structure. |

## Next: try a power feature

Once you're in a Claude Code session bound to a project, you can:

- **Queue multiple tasks** — add tasks to the Kanban in parallel, Lead picks them up in order
- **Auto-mode pickup** — flip a task's `run_mode` from manual to `auto_pickup` in the Kanban; the headless engine picks it up with no terminal open — note this engine is **in active development** (it posts a plan and status updates; full autonomous execution isn't live yet)
- **Parallel spawns** — request "backend and frontend work on this together" and watch agents execute in parallel
- **Multi-project** — switch to a different project mid-session; Lead re-bootstraps and picks up the new project's context

Details: see [USAGE-POWER.md](USAGE-POWER.md).
