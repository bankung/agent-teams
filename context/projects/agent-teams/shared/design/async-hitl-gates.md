# Design Memo — Async HITL via Telegram + the `task_gates` model (the "stuck" path for the Mode-A continuous runner)

> **Status:** design deliverable (read-only; NO code written). Lead-authored from a consultation session **2026-06-23**. Decision owner: operator. Grounded against the MAIN repo (`api/src/*`, `langgraph/*`) at this date. Pre-task — the v0.8.0 tasks are decomposed in §11 but not yet created.
>
> **Runner codename:** "**ZommmBeeean Service**" (tagline *"Powered by Caffeine, Driven by Deadlines"*). Name resolved 2026-06-23 — an elongated, collision-dodging twist (the plain "zombean" spelling is widely taken: X, PvZ wiki, itch, Shopee, Patreon). This memo is named after the durable concept (`async-hitl-gates`) so a codename change is a trivial find/replace, not a rename of the artifact. Runner spec lives in Kanban **#2531**.
>
> **Update 2026-06-24 (operator sign-off).** Channel locked **Telegram-only** for v0.8.0 (MS Teams revisited at v0.9.0 — its approve path needs inbound exposure; O365 webhooks retire May 2026; research `_scratch/research-telegram-teams-hitl.md`). Commit/push tier resolved (the §10 open item): the Mode-A runner **auto-commits locally; only `push` is gated** (informed-approval). The full runner autonomy boundary (4 rings) is `shared/design/mode-a-autonomy-boundary.md`. §11 Tasks A/B/C are now **created**: A `#2564` → B `#2565` → C `#2566` under milestone v0.8.0 (#50).

## 1. Problem / goal

- **Today:** HITL notifications go out via **ntfy**; the push opens a **`click_url` → `/approve/{id}`** page the operator reaches over **Tailscale** (`_fire_hitl_push`, [api/src/routers/tasks.py:290](api/src/routers/tasks.py:290) → `notify_ntfy.send_push`; click_url at :316-322). Operator feedback: ntfy+Tailscale is **heavier than the need**.
- **Goal:** async HITL over a chat channel the operator already carries (**Telegram**, outbound long-poll → no inbound port / no VPN). A **dumb local poller** (no AI) reads operator replies and writes answers to the DB **via the API**; the AI only ever **reads durable state**. Remove ntfy + Tailscale from the HITL loop.
- **Driver:** the Mode-A **continuous runner** (ZommmBeeean Service, #2531) drains a project's actionable tasks until stop/stuck/empty. Its **"stuck" path == HITL**. Robust async HITL is the load-bearing dependency of that runner.

## 2. Grounding — the real HITL state machine (verified 2026-06-23)

| Fact | Evidence | Consequence |
|---|---|---|
| `blocked_by` = single-FK task→task, **already entangled in HITL resume** (work `blocked_by` question-task → resume when blocker DONE) | [task.py:311](api/src/models/task.py:311); resume_stmt [tasks.py:782](api/src/routers/tasks.py:782) | `blocked_by` is **already overloaded** + single-valued → DON'T pile HITL onto it |
| **`operator_gate`** = first-class "blocked-on-operator" lane, values `key/commit/decision/hitl/external`, **separate** from blocked_by | [task.py:473-490](api/src/models/task.py:473) | **Build HITL on this lane**, not blocked_by |
| `process_status`: 4=BLOCKED, **8=HALTED_PENDING_USER** | summary endpoint `le=8` | reuse ps=8 as the "paused on a human" state |
| `question_payload` (intends "answer history" + #832 append) + `resume_context` (Mode-A halt snapshot for re-spawn) | [task.py:220-225](api/src/models/task.py:220) | resume mechanism for Mode A already exists; verify #832 before colliding |
| `tasks_history` PG trigger snapshots every UPDATE/DELETE | [task.py:760](api/src/models/task.py:760) | **round history is already auditable** — no new history table |
| `_fire_hitl_push` is the **shared** notify seam; `/decide` (`HitlResolveRequest`) is the existing resolve endpoint (web `/approve` calls it) | tasks.py:290, :318-320 | swap notify at ONE shared seam; reuse/extend the resolve path |
| Mode B HITL = LangGraph `interrupt()` + **durable checkpoint** holds the resume position | [langgraph/hitl.py](langgraph/hitl.py) | **Mode A has NO checkpoint** — see §8 |

## 3. Design principle

> **HITL is an operator-gate, not a task-dependency.** Leave `blocked_by` as task↔task (do NOT rename it — codebase-wide churn). Introduce a clearly-named new concept — a **"gate"** — on the existing `operator_gate` lane.

## 4. The gate model — `task_gates` child table (LOCKED: table, not JSONB)

One work-task : N gates. A gate is a **sub-event of a task**, NOT a board task (no question-task clutter per round).

```
task_gates
  id           PK
  task_id      FK -> tasks.id (the work-task)
  seq          int        -- order within the task
  kind         text       -- 'question' | 'decision'
  question_payload jsonb   -- {question, options[]}
  status       text       -- 'open' | 'answered' | 'cancelled' | 'expired'
  answer       text/jsonb -- the chosen option / free-text
  gate_tier    text       -- mirrors operator_gate: key/commit/decision/hitl/external
  answered_by  text       -- provenance (operator id / chat id)
  answered_via text       -- 'web' | 'telegram'
  created_at, answered_at  timestamptz
```

**Lifecycle**
```
hit gate -> INSERT gate(status=open) + work-task ps->8 + operator_gate=<tier> + notify(Telegram; button carries gate_id)
         -> operator answers (async) -> /resolve checks gate still 'open' -> write answer to gate row
            + fold answer into work-task.resume_context + ps 8->actionable   (one transaction)
         -> picker / runner re-selects the work-task, resumes from resume_context
         -> next gate = INSERT new row (seq+1) -> repeat
```

- **reuse:** `operator_gate` (tier) · `process_status=8` · `resume_context` (resume). **`blocked_by` untouched.**
- **multi-round native** (N rows) · **async-safe** (each gate has id+status → answering a closed/wrong gate is rejected) · **order explicit** (seq) · **history native** (rows + tasks_history).

### Concurrency (LOCKED: concurrent supported)
- `task_gates` holds **multiple `open` rows per task_id** natively.
- **Resume rule:** the work-task becomes actionable again **only when its open-gate count → 0** (all answered).
- **Out-of-order answers are fine** — each answer carries its `gate_id`; this is exactly where table+gate-id pays off (a single overwritten `question_payload` slot would corrupt here). The table+concurrent choices are mutually consistent.
- **Assumption:** concurrent gates are **independent**. If answer A changes whether gate B is still relevant (dependent), the Lead must raise them **serially** — a raise-side policy, not a schema concern.

## 5. Async channel (Telegram) + the local poller

- **Outbound:** swap `_fire_hitl_push` → Telegram. It is the **shared** seam → one swap covers BOTH the legacy and the new flow (§7). Message carries inline buttons whose callback = `{gate_id, chosen_option}`.
- **Inbound:** a **dumb local poller** (no AI) using Telegram **`getUpdates` long-poll** (outbound-only → no inbound port, no public URL, **no Tailscale**). On an operator reply it calls the resolve endpoint with `{gate_id, answer, provenance}`.
- **Telegram > MS Teams** for the "drop Tailscale" goal: Teams typically needs a webhook / public endpoint → would re-introduce the inbound exposure Tailscale was solving.
- **Security:** the poller writes approvals → **lock it to the operator's known chat_id** (ignore all other senders).
- **Removal scope:** ntfy → removable once Telegram replaces it. **Tailscale → removable only if its sole job was the `/approve` hop** — verify it isn't also serving remote board/API access before deleting.

## 6. Tier → channel policy (LOCKED: 3 levels, not binary)

| Level | Tiers | Rule |
|---|---|---|
| **forbidden** | `key`, `external` | never answerable via Telegram (terminal / stronger channel only) |
| **informed-approval** | `commit` (incl. push) | Telegram OK **but the card must carry evidence**: diff-stat + pre-push keyword-scan result + test result |
| **simple** | `decision`, `hitl` | Telegram + buttons; no evidence needed |

Rationale for commit/push: local commit is reversible + frequent → forcing it to a terminal every time is too much friction → allow via Telegram. **Push is outward-facing/publishing + the leak boundary** → allow via Telegram **only as informed-approval** (a phone tap must not be blind), and it stays backed by the **existing pre-push keyword scan** as the real leak guard (the HITL tap is not that guard).

## 7. Coexistence with the legacy flow (LOCKED: coexist + unified read)

Two HITL mechanisms run in parallel: legacy (`interaction_kind=question/decision` + `blocked_by`, used by Mode B) and new (`task_gates`, used by ZommmBeeean/Telegram). Coexist = don't destabilize Mode B's working HITL bridge.

- **Main cost:** **two sources of truth** for "is a human needed?" — every reader (inbox, Telegram poller, `next-autorun` pending, dashboards) must union both → divergence risk.
- **Mitigation (day one): "two writers, one reader"** — a single **unified read** (view/endpoint "pending operator gates") that unions legacy + new, so every surface reads ONE thing.
- **Notify cost avoided:** `_fire_hitl_push` is shared → the Telegram swap covers both flows at once (no channel fracture).
- **Picker cost:** `next-autorun` gains a gate-resume branch alongside resume_stmt + pending_questions (acceptable; keep it a clean separate predicate).
- **Combinatorial edge:** a task with BOTH a legacy `blocked_by` AND open gates → actionable only when **blocker DONE AND open-gate count = 0**.
- **Consolidation horizon:** migrate legacy → gates at **v0.9.0+** (tracked debt; don't let two mechanisms live forever).

## 8. The genuinely hard part — Mode-A resume durability (no checkpoint)

Mode B's multi-round HITL is trivially correct because the **LangGraph checkpoint durably holds the resume position**. **Mode A (ZommmBeeean) has no checkpoint** — "where we are" is the conversation context, which is lost on `/clear`, churned by compaction, gone when the session ends. Because a Telegram answer may arrive **after the session compacted or ended**, `/resolve` must fold the answer into a **self-sufficient `resume_context`** so a *fresh* run resumes from `row + activity-rail` alone.

→ The schema delta (a table) is small; **the `resume_context` self-sufficiency contract is the real engineering.** This is ZommmBeeean #2531 **AC4 (compaction resilience)** in its sharpest form.

## 9. "Does order matter?" — resolved

| Layer | Matters because | Handled by |
|---|---|---|
| causal / resume | HITL#2 unreachable until #1 answered | the **halt** enforces it — no structure needed |
| answer → round mapping (async) | a late/out-of-order Telegram answer could bind to the wrong round | **gate_id + status** (the one structural need) — satisfied by §4 |
| audit / replay | need the ordered log of asks+answers | activity rail + `tasks_history` |

## 10. Decisions locked (2026-06-23) + open items

**Locked:** (1) dedicated `task_gates` table; (2) coexist with legacy + a unified read; (3) concurrent gates; (4) 3-level tier→channel policy — `key`/`external` forbidden, `commit` informed-approval, `decision`/`hitl` simple.

**Open:** runner-name twist (collision); verify `#832` `question_payload` append semantics before reusing; the legacy→gates migration horizon; the resolve endpoint shape (new endpoint vs extend `/decide`); whether `push` gets its own tier or rides `commit`-informed.

## 11. Decomposition → proposed v0.8.0 tasks (not yet created)

- **Task A — gate model + resolve API + unified read.** `task_gates` migration + model/schema; `/resolve` (gate_id + answer + provenance + tier-check + idempotent + stale-reject); the unified "pending gates" read over legacy + new.
- **Task B — Telegram notify + local poller + tier policy.** Swap `_fire_hitl_push` → Telegram (inline buttons, gate_id callback); the dumb getUpdates poller (chat-id locked); the §6 tier→channel policy + evidence card for commit/push.
- **Task C — ZommmBeeean resume integration.** The `resume_context` self-sufficiency contract; gate-answered → ps flip → picker resume; ties #2531 AC3/AC4.

Relationship: A → B → C dependency order; all three under milestone **v0.8.0 (#50)**; #2531 (ZommmBeeean runner) consumes C.

---
*Source: consultation session 2026-06-23 (no implementation performed). Supersedes nothing; complements `mode-b-authorization-chain.md` (operator-vs-AI auth) and `operator-vs-ai-auth-1852.md`.*
