# Power features — agent-teams

Five advanced capabilities that unlock faster workflows.

## 1. Queue multiple tasks

**Status: Production.** Queueing and Claude Code / Codex pickup work today; the `auto_pickup` hand-off to the headless engine is in active development (see feature #2).

**What it does:** Add N tasks to the Kanban in rapid succession; Lead picks them up and queues them automatically.

**When to use:** You have 3–5 distinct pieces of work to hand off (backend API, frontend UI, test coverage, etc.). Queue them all on the board, then work through them — either driven from a Claude Code / Codex session (the path that executes for real today), or by flipping `run_mode=auto_pickup` to hand them to the headless engine (see feature #2 for that engine's current status).

**Example:**
```
In the Kanban UI, create 3 tasks:
  1. Backend: Add user login API endpoint
  2. Frontend: Build login form + validation
  3. Tester: E2E test login flow

Option A (works today): Open a Claude Code session, tell Lead "work through
the queued tasks" — Lead picks them up in order, one at a time.
Option B (in active development): Click "Run" on each (or set run_mode=auto_pickup)
to hand them to the headless engine; see feature #2 for current limitations.
```

**Gotcha:** Tasks don't parallelize UNLESS you explicitly request it (see feature #3). Queued tasks run sequentially by default — agent 1 finishes, then agent 2 picks up its task.

---

## 2. Auto-mode (headless agent pickup)

**Status: In active development.** Posts a plan + status updates and checkpoints state in Postgres; autonomous code edits / tests / commits are not live yet.

**What it does:** Flip a task from `run_mode=manual` to `run_mode=auto_pickup` in the Kanban drawer. The headless `langgraph` container picks it up and runs through a supervisor → specialist graph — no Claude Code session needed.

**When to use (once mature):** Overnight runs, routine maintenance, or any task you trust the agent to handle without approval prompts on every file write. For real end-to-end work today, use the Claude Code / Codex path (feature #3, or [CLAUDE-CODE-START.md](CLAUDE-CODE-START.md)).

**Example:**
```
In the Kanban board, open a task drawer.
Click the "Run" button (or manually set run_mode=auto_pickup).
Close the terminal. The langgraph service polls and posts progress.
If the engine needs your input, a "question" task appears in the UI —
answer it and click Resume; the agent picks up from its checkpoint.
```

**Gotcha:** Auto-mode runs the headless LangGraph engine, not a Claude Code session. Approval prompts land as "question" tasks in the Kanban rather than blocking CLI prompts, which is convenient — but because the execution layer is still being built out, treat results as drafts to review rather than finished work.

---

## 3. Parallel agent spawns

**Status: Production.**

**What it does:** Ask Lead to spawn multiple specialist agents on the same task at the same time (e.g., backend + frontend + reviewer all working on one feature). This runs via an interactive Claude Code or Codex session — it is the path that executes for real today.

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

**Status: Production.**

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

**Status: Production.**

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

## 6. Secretary email actions

**Status: Tier-1 + 2 shipped (Kanban #1585).** Tier-3 (reply/forward/send) is a future follow-up (needs Gmail OAuth go-live).

**What it is:** Only `secretary*` agents can perform mailbox actions (Gmail + Outlook) — mark read/unread, archive, trash, draft — via the gated `/api/tools/email/*` path. No other agent (dev-*, novel-*, content-*, sem-*) has email-write capability. The access pattern combines three enforcement layers:

1. **Layer-0 role grant** — the agent's role must be listed in the project's `config.tool_grants` to access a given email tool (e.g., `gmail.trash`).
2. **Tier gate** — different actions have different approval modes (see "Tier model" below).
3. **Chrome-MCP hook** — a `PreToolUse` backstop (`secretary-email-action-gate.ps1`) prevents non-secretary agents from using Chrome-MCP mailbox actions.

**Tier model:** Actions fall into two approval modes:

| Tier | Actions | Approval mode | Status |
|---|---|---|---|
| **Tier-1 (open)** | `mark_read`, `mark_unread`, `archive`, `draft` | Auto-approve | Shipped |
| **Tier-2 (operator-proof)** | `trash` (move to Trash / Deleted Items) | Operator-proof required | Shipped |
| **Tier-3 (future)** | `reply`, `send_internal`, `external_send` | Operator-proof + out-of-band confirm | In dev (Gmail OAuth pending) |

Tier-1 fires with no prompt; the agent calls the endpoint and succeeds immediately. Tier-2 requires operator-proof: the agent must present the `X-Operator-Token` header matching the server's `OPERATOR_ACTION_KEY` (set in the api `.env`). If the key is unset, the gate is dormant (fail-open) — existing workflows are unaffected until you activate enforcement by setting the key.

**Permanent delete is ALWAYS denied** (neither auto nor operator-proof unlocks it).

**Authorization status check:** Query `GET /api/tools/email/auth/<provider>/status` (where `<provider>` is `gmail` or `outlook`) to see if the OAuth credentials are live for the current project.

**Audit log (AC8):** Every Tier-1/2 action appends one JSONL row to `_runtime/email-actions.jsonl`:
```
{ts, agent_role, action, tier, message_ids, approval_mode, result}
```

Rotation (weekly → archive → gzip after 90 days, prune after 1 year) is dogfooded as a `bin/email-audit-rotate.ps1` script, typically triggered by a weekly Kanban recurring task.

**Current quota:** All email tools are scoped per project and consume daily Gmail API units. See `GET /api/tools/email/gmail/usage` for the current snapshot (trash = 20 units per message, mark/archive = 5 per message, draft = 10 per call; list = 5 per call).

**Example (secretary marks <your-account> messages as read):**
```
agent_role: secretary-mail-analyst
X-Project-Id: 1
POST /api/tools/email/gmail/mark
{
  "message_ids": ["msg-id-1", "msg-id-2"],
  "read": true
}
→ 200 OK, 2 marked
→ audit row written (approval_mode="auto")
```

**Example (secretary trashes with Tier-2 operator-proof):**
```
agent_role: secretary-mail-analyst
X-Project-Id: 1
X-Operator-Token: <key from OPERATOR_ACTION_KEY>
POST /api/tools/email/gmail/trash
{
  "message_ids": ["msg-id-3"]
}
→ 200 OK, 1 trashed
→ audit row written (approval_mode="operator_proof")
```

See `_runtime/secretary-email-policy.json` for the complete tier → action mapping.

---

## Reading more

- **Kanban task structure:** see [README.md](README.md#what-happens-next) — tasks are the unit of work; acceptance criteria unlock structured handoff
- **Full developer guide:** see [readme_dev.md](readme_dev.md) — architecture, storage zones, agent customization
- **Remote access & notifications:** see [readme_remote-access.md](readme_remote-access.md) — Tailscale, ntfy, email digest
