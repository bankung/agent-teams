# MCP server adapter — design (Kanban #806, AC1)

> **Status:** design / recommendation (2026-06-21). Lead-authored. Drives the AC2 spike.
> SDK specifics grounded by `_scratch/research-mcp-sdk-2026-06-21.md` — **confirm exact
> FastMCP API against the installed package at spike time** (web-sourced, version-sensitive).

## TL;DR — recommendations

1. **Architecture: thin shim over `/api/*`.** Every MCP tool calls an existing FastAPI
   endpoint (in-process ASGI or localhost httpx) — **never the DB/ORM directly**. This
   preserves *by construction* the golden-rule invariant "DB writes go through FastAPI
   endpoints only", plus all router-level validation, AC discipline, and operator-proof gates.
2. **Curated tool set, not blanket `from_fastapi`.** Hand-pick the safe subset (projects +
   tasks CRUD). Never expose email, kill/revive/grant-consent, or any always-HALT/destructive
   surface over MCP.
3. **Transport: stdio standalone for the spike; mounted streamable-HTTP for hosted.** Spike =
   a standalone stdio server the operator adds to Claude Desktop/Cursor/Cline. Hosted (later) =
   FastMCP `http_app` mounted in the existing FastAPI for remote clients + bearer auth.
4. **Project routing: explicit `project` tool argument** (name or id), validated server-side
   (mirrors today's `X-Project-Id`). Stateless, simplest; a bearer-carried default is the
   hosted-mode add-on.
5. **AC4 recommendation: DUAL-MODE, not migrate.** Keep the `.claude/` Lead playbook; add MCP
   as a parallel *capability* surface. The playbook is **behavior** (Karpathy lane, AC
   discipline, golden rules, spawn orchestration) — not encodable as tools. MCP broadens *which
   clients* can drive the Kanban; it does not replace the playbook.

---

## 1. Tool surface

MCP tools mirror the curl surface the Lead playbook uses today. All call `/api/*`; the
`project` argument resolves to `X-Project-Id` server-side.

**Read-only**
| Tool | Backs onto | Notes |
|---|---|---|
| `list_projects()` | `GET /api/projects` | discovery |
| `resolve_project(name)` | `GET /api/projects/by-name/{name}` | name → id (the bootstrap step) |
| `list_tasks(project, status?, milestone?)` | `GET /api/tasks` | windowed; mirror existing filters |
| `get_task(project, task_id)` | `GET /api/tasks/{id}` | includes `acceptance_criteria` |

**Mutating (curated — each rides the full router validation)**
| Tool | Backs onto | Notes |
|---|---|---|
| `create_task(project, title, acceptance_criteria?, …)` | `POST /api/tasks` | **`project_id` in body AND header** (the documented 422 footgun) |
| `update_task(project, task_id, fields…)` | `PATCH /api/tasks/{id}` | status/priority/field edits |
| `complete_task(project, task_id, ac_verdicts)` | `PATCH` AC array → `PATCH process_status=5` | encodes the AC-discipline close (verify-then-flip); refuses on unmet AC |

**Explicitly EXCLUDED from the MCP surface** (security/governance — preserved by *not exposing*):
email tools (`/api/tools/email/*` — secretary-role only), kill/revive/grant-consent (operator-
proof), `shell_run`/destructive tier, and any raw file-write. MCP clients get the Kanban CRUD,
nothing in the always-HALT tier.

**Spike subset (AC2):** `list_projects`, `list_tasks`, `create_task` (per the task AC).

## 2. Auth / session model

- **Spike (stdio, single operator, localhost):** no auth — the server runs locally and calls
  `localhost:8456`. `project` is an explicit tool argument resolved server-side.
- **Hosted (streamable-HTTP, later):** static **bearer token** in the `Authorization` header
  (FastMCP supports it). The token resolves operator identity + a *default* project; the
  `project` tool-arg overrides per call. OAuth 2.1 deferred until multi-user.
- **Project routing decision:** explicit `project` tool argument (over header/JWT-payload)
  because MCP tool calls are the natural unit and a single client may drive several projects in
  one session. Server validates via `resolve_project` and refuses unknown names (no silent
  cross-project writes — mirrors the `X-Project-Id` gate).

## 3. Architecture + deployment

```
MCP client (Claude Desktop / Cursor / Cline)
        │  stdio (spike)  │  streamable-HTTP + bearer (hosted)
        ▼                 ▼
  FastMCP server  ──httpx/ASGI──►  existing FastAPI /api/*  ──►  services ──► PostgreSQL
   (thin shim; curated tools)        (ALL validation, AC discipline, gates live here)
```

- **Thin shim, not direct-DB:** the MCP layer translates tool calls ↔ `/api/*` requests. It
  adds **zero** business logic and **zero** new DB writes. Rationale: the routers are the single
  chokepoint for validation + the operator-proof/HALT gates; bypassing them (direct service/ORM
  calls, or blanket `FastMCP.from_fastapi`) would fork that logic and risk a gate bypass.
- **Spike = separate stdio process** (`mcp_server.py`) calling `localhost:8456` — simplest to
  demo in Claude Desktop, and it requires **no change to the running api deployment** (low blast
  radius). 
- **Hosted = mount `FastMCP.http_app(path="/mcp")` into the existing FastAPI** (single process,
  in-process ASGI calls, one uvicorn). Requires wiring FastMCP's lifespan into the app lifespan
  — *confirm the exact API at spike time*.

## 4. dual-mode vs migrate (AC4 preview → recorded in `decisions.md` at close)

**Recommend DUAL-MODE.** The `.claude/` playbook (`CLAUDE.md` + `.claude/teams/*` +
`.claude/agents/*`) encodes *Lead behavior*: the Karpathy lane, AC discipline, the golden rules,
spawn orchestration, storage-zone discipline. MCP exposes a *capability surface* (Kanban tools);
it cannot encode or enforce that behavior on an arbitrary client. So MCP **widens the set of
clients** that can drive the Kanban backend, but a client still needs playbook-equivalent
discipline to be a competent Lead. Keep both; sunset the CLI-specific harness only if/when an
MCP client demonstrably carries the full behavioral contract (unproven today → dual-mode).

## 5. Storage-zone discipline mapping (how MCP clients "propose vs apply")

- The zone rules (`​.claude/` humans-only; subagents propose, Lead applies to `shared/`/
  `standards/`) are **preserved by omission**: the MCP surface has **no file-write tools at
  all**. MCP clients can only touch DB-transactional state (projects/tasks) via `/api/*`. The
  file zones are simply *not reachable* over MCP.
- The behavioral contract (propose-vs-apply, AC discipline) is **conveyed**, not enforced: ship
  the team playbook as a read-only MCP **resource** (`.claude/teams/<team>.md`) the client can
  fetch, and put terse discipline reminders in each tool's description/annotations. 
- **Documented limitation:** MCP = capability surface; *behavior remains client-side discipline*
  — exactly as today, where the `.claude/` playbook relies on Claude Code choosing to follow it.
  An MCP server cannot force a third-party client to honor the golden rules; the guardrails that
  MUST hold regardless (email-gate, operator-proof, HALT tier) hold because those tools are
  **never exposed**, not because the client is trusted.

## 6. Spike scope (AC2) — what dev-sr-backend builds

1. `mcp_server.py` (standalone, FastMCP, stdio) exposing `list_projects`, `list_tasks`,
   `create_task` — each a thin httpx shim over `localhost:8456` (`create_task` puts `project_id`
   in **both** body and `X-Project-Id` header).
2. Verifiable via **mcp inspector**: `npx @modelcontextprotocol/inspector <run cmd>` → tools
   list, a `list_projects` call returns live rows, a `create_task` round-trips (then clean up).
3. Hand-off note for AC3 (operator): the Claude Desktop / Cursor / Cline stdio config snippet to
   mount it.

## 7. Open questions / confirm-at-spike

- Exact FastMCP API (`http_app` signature, lifespan wiring, `from_fastapi`) vs the **installed**
  version — verify against the package, not the web docs.
- In-process ASGI call vs localhost httpx for the hosted mount (latency vs simplicity) — spike
  can stay httpx; revisit if mounted.
- Error mapping: FastAPI 4xx/422 → MCP tool-error shape (surface the `detail` string).
- Whether to expose `complete_task` in v1 (it encodes the AC-discipline close) or defer it until
  the read+create spike proves the shim end-to-end.
