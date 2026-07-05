# Telegram command surface — design decision (Kanban #2720)

**Status:** DECIDED 2026-07-01 (design only; no code in #2720). Origin: operator-requested 2026-06-25, hardened with one adversarial pushback round. This doc resolves #2720's 7 AC and ends with the phased implementation tasks.

**Related:** [`async-hitl-gates.md`](async-hitl-gates.md) (the `provenance=telegram` gate-resolve precedent), [`operator-vs-ai-auth-1852.md`](operator-vs-ai-auth-1852.md) (the operator-proof trust boundary), [`mode-a-walker.md`](mode-a-walker.md) (the runner this must not silently drive).

## Goal

Operate agent-teams over Telegram as discrete **requests** (not conversation) — list tasks, open a task, run/hold a task by id, approve/deny gates — so the system is drivable **without** a Claude Code session (reduces "Mode-A-via-chat"). An MCP server is the same idea behind a different front-end and is recorded here as a **future sibling**, not built in this track.

## Verified starting state (this session)

- **Inbound channel already exists:** `api/scripts/telegram_poller.py` long-polls `getUpdates` with a chat-id lock and already *receives* plain operator messages but ignores them (`ignored_non_callback`, ~line 251) — that is the exact extension point.
- **~90% of the verbs already have REST endpoints:** GET/POST/PATCH/DELETE `/api/tasks`, `/ai-parse`, `/api/task-gates/{id}/resolve`, fire-now, snooze, `/api/projects`, `/api/milestones`.
- The poller is deliberately **dumb** (no LLM) and already resolves gates via `POST /api/task-gates/{id}/resolve` with `provenance=telegram`.

## Decisions

### D1 — Poller boundary (AC2): the poller stays DUMB
`telegram_poller.py` only (a) chat-id-verifies the sender, (b) forwards the raw message text to a NEW api endpoint **`POST /api/telegram/command`**, and (c) relays the api's reply back to the chat. **All** parsing / authz / dispatch / idempotency lives in the api, never in the poller. Rationale: the poller is session-less and security-simple by design; keeping logic server-side means one tested authz path shared with any future front-end (MCP).

### D2 — Project targeting (AC3): explicit per-chat sticky project
A Telegram command has no session, so it must carry an explicit target. Use a **per-chat sticky project** set via `/project <name>` and stored in api-side chat state (keyed by chat-id). **Do NOT read `_runtime/lead_project_id.txt`** for command targeting — that file is a *session-write* signal the HITL poller follows to route notifications; it is not a command target, and reading it would cross a concurrent interactive session's binding. Commands with no sticky project set → reject asking for `/project <name>` first.

### D3 — Auth per verb (AC4): chat-id-lock = operator-equivalent for a WHITELIST only
The chat-id lock authenticates the operator, but authority is **per-verb**:
- **Read + safe-mutation verbs** (whitelist) → treated as operator-equivalent under the chat-id lock. This is how telegram-provenance satisfies the existing operator-proof requirement, and it reuses the precedent already live in `POST /api/task-gates/{id}/resolve` (`provenance=telegram` is accepted as operator proof there).
- **Destructive verbs** (delete, mass ops, project pause/kill) → require an **extra explicit confirm** turn (e.g. reply `CONFIRM <token>`), never single-shot.
- **Deny-by-default:** anything not on the whitelist is rejected.

### D4 — Safety (AC5): create ≠ execute, and idempotency
- A task created via Telegram lands **TODO** and is **NOT auto-picked** by the Mode-A/B engine until an explicit `/run <id>` (or a HITL confirm). This prevents an untrusted inbound message from silently driving autonomous work — the same create-vs-execute boundary the walker relies on.
- Mutating commands are **idempotent via Telegram `update_id` dedup** (api-side), so a `getUpdates` offset-write failure / re-delivery cannot double-create or double-run.

### D5 — Scope guard (AC7): call existing endpoints directly; no new abstraction
The Telegram front-end dispatches to **existing** REST endpoints directly. **No** "verb layer" abstraction and **no** MCP server are built in this track. MCP is a future sibling that will reuse the *same* endpoints + the *same* D3 auth decisions. Only two thin new endpoints are in scope (the D6 gaps).

## D6 — Verb catalog v1 (AC6)

Deterministic slash-commands, each mapped to a concrete existing endpoint (auth class per D3):

| Command | Endpoint | Class |
|---|---|---|
| `/project <name>` | set sticky chat state (D2) | safe |
| `/projects` | `GET /api/projects` | read |
| `/tasks` | `GET /api/tasks/summary?pending=true` (sticky project) | read |
| `/task <id>` | `GET /api/tasks/{id}` | read |
| `/gates` | `GET /api/operator-gates/pending` | read |
| `/new <text>` | `POST /api/tasks/ai-parse` → creates **TODO** (D4) | safe-mutation |
| `/approve <gate>` / `/deny <gate>` | `POST /api/task-gates/{id}/resolve` (`provenance=telegram`) | safe-mutation |
| `/hold <id>` | `PATCH /api/tasks/{id}` (→ hold/blocked semantics) | safe-mutation |
| `/run <id>` | **GAP** — run-a-specific-id-now (thin new endpoint) | safe-mutation |
| (halt a running task) | **GAP** — halt-running-task (thin new endpoint) | safe-mutation |

**Two gaps** flagged as needing a thin new endpoint, to confirm during scoping: **run-specific-id-now** and **halt-running-task** (the rest reuse existing endpoints).

## Phased implementation (AC7)

Split into follow-up tasks (created alongside this doc), sequenced read → safe-mutation → gaps:

1. **Phase 1 — read verbs + plumbing:** `POST /api/telegram/command` endpoint (dumb-poller forward), per-chat sticky `/project`, and the read verbs (`/projects /tasks /task /gates`). Deny-by-default dispatcher + `update_id` dedup foundation.
2. **Phase 2 — safe mutations + auth:** `/new` (→ TODO), `/approve` `/deny` `/hold`, the D3 auth-per-verb whitelist + destructive-confirm, wired to the operator-proof model.
3. **Phase 3 — gap endpoints:** the two thin new endpoints (`/run <id>` run-specific-now, halt-running-task), plus their auth + idempotency.

MCP server = future sibling (separate track, reuses these endpoints + D3).
