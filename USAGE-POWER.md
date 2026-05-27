# Power features — agent-teams

Five advanced capabilities that unlock faster workflows.

## 1. Queue multiple tasks

**What it does:** Add N tasks to the Kanban in rapid succession; Lead picks them up and queues them automatically.

**When to use:** You have 3–5 distinct pieces of work to hand off (backend API, frontend UI, test coverage, etc.). Instead of waiting for each agent to finish, queue them all, then the headless engine runs them in order.

**Example:**
```
In the Kanban UI, create 3 tasks:
  1. Backend: Add user login API endpoint
  2. Frontend: Build login form + validation
  3. Tester: E2E test login flow

Click "Run" on each (or set run_mode=auto_pickup on all).
Lead + agents work in parallel; you check back in 20 minutes.
```

**Gotcha:** Tasks don't parallelize UNLESS you explicitly request it (see feature #3). Queued tasks run sequentially by default — agent 1 finishes, then agent 2 picks up its task.

---

## 2. Auto-mode (headless agent pickup)

**What it does:** Flip a task from `run_mode=manual` to `run_mode=auto_pickup` in the Kanban drawer. The headless `langgraph` container picks it up, runs through the full agent loop, and updates the task — no Claude Code session needed.

**When to use:** Overnight runs, routine maintenance, or any task you trust the agent to handle without approval prompts on every file write.

**Example:**
```
In the Kanban board, open a task drawer.
Click the "Run" button (or manually set run_mode=auto_pickup).
Close the terminal. The langgraph service polls and executes.
Check back in the morning — task status updated to DONE (or REVIEW if human input needed).
```

**Gotcha:** Auto-mode runs the headless LangGraph engine, not a Claude Code session. The agent still prompts for approval on sensitive writes, but the prompt lands as a "question" task in the Kanban rather than a blocking CLI prompt. You then answer the question in the UI, and the agent resumes from a checkpoint.

---

## 3. Parallel agent spawns

**What it does:** Ask Lead to spawn multiple specialist agents on the same task at the same time (e.g., backend + frontend + reviewer all working on one feature).

**When to use:** You have a task with clear role boundaries (API design, UI, tests) and want them done together, not sequentially.

**Example in Claude Code:**
```
You: "File a task: 'Add user profile page' with AC: profile display + photo upload + save.
     Backend and frontend should work in parallel."

Lead: (spawns dev-backend and dev-frontend simultaneously)
      (both agents report back, Lead integrates)
      (reviewer gives feedback)
      (Lead patches the task status to DONE)
```

**Gotcha:** Parallel spawns on the same task can step on each other if they both edit the same file at the same time (rare but possible). Keep role boundaries clear ("frontend owns `web/app/profile.tsx`; backend owns `api/src/routers/users.py`").

---

## 4. Mobile remote access

**What it does:** Access the Kanban board from your phone (iOS or Android) via Tailscale VPN. View task status, create tasks, answer HITL questions, trigger notifications.

**When to use:** You're away from your desk but want to check if agents finished work, or answer a question that's blocking an agent.

**Setup:** See [readme_remote-access.md](readme_remote-access.md) — install Tailscale on your home machine + phone, then visit `http://<machine>.<tailnet>.ts.net:5431/p/<project>` from your phone.

**Example:**
```
At a coffee shop on cellular:
  - Phone shows "task #523 waiting for your input"
  - Click the notification, answer the question
  - Agent resumes, finishes in 10 min
  - Phone notification: "task #523 DONE"
```

**Gotcha:** Push notifications (task changes, HITL blocks) are optional. Set up `NTFY_TOPIC` in `.env` to receive them on your phone.

---

## 5. Multi-project context switch

**What it does:** Switch to a different project mid-session. Lead re-bootstraps, loads the new project's context, and all subsequent agents use the new project's standards and team.

**When to use:** You're working on myapp, but an urgent bug lands in another-project. Switch, file a task, work on it, switch back.

**Example in Claude Code:**
```
You: "Actually, let's switch to another-project"

Lead: (clears the myapp context, re-bootstraps to another-project)
      (confirms: "Session bound to another-project (team=dev, id=42)")

You: "File a task: 'Fix the crash in dashboard'"

Lead: (spawns agents from another-project's team, uses another-project's standards)
```

**Gotcha:** Switching projects mid-session clears context from the previous project. If you need to refer back (e.g., "reuse the authentication contract I wrote for myapp"), copy it to the Kanban or a file before switching.

---

## Reading more

- **Kanban task structure:** see [README.md](README.md#what-happens-next) — tasks are the unit of work; acceptance criteria unlock structured handoff
- **Full developer guide:** see [readme_dev.md](readme_dev.md) — architecture, storage zones, agent customization
- **Remote access & notifications:** see [readme_remote-access.md](readme_remote-access.md) — Tailscale, ntfy, email digest
