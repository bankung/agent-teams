# agent-teams MCP server (Kanban #806 spike + #2518 phase 2)

A **minimal stdio [FastMCP](https://github.com/jlowin/fastmcp) server** that exposes the
curated read+create+update slice of the agent-teams Kanban backend over MCP. Every tool is a
**thin httpx shim** over the existing FastAPI at `http://localhost:8456` — no direct
DB/ORM access, no business logic, no new endpoints. The routers stay the single
chokepoint for validation, AC discipline, and the operator-proof/HALT gates
(see `context/projects/agent-teams/shared/mcp-adapter-design.md`).

## Tools (exactly six)

| Tool | Backs onto | Notes |
|---|---|---|
| `list_projects()` | `GET /api/projects` | discovery |
| `list_tasks(project, status?)` | `GET /api/tasks` (`X-Project-Id` header) | `project` = id or name; `status` = `process_status` 1..5 |
| `create_task(project, title, description?, acceptance_criteria?)` | `POST /api/tasks` | `project_id` in **both** the JSON body **and** the `X-Project-Id` header (header alone → 422) |
| `get_task(project, task_id)` | `GET /api/tasks/{id}` | includes `acceptance_criteria` |
| `update_task(project, task_id, fields)` | `PATCH /api/tasks/{id}` | `fields` sent as the PATCH body verbatim; **refuses client-side** to set `process_status=5` — use `complete_task` instead |
| `complete_task(project, task_id, acceptance_criteria?, status_change_reason?)` | `PATCH /api/tasks/{id}` | AC-verify-then-flip (see below) |

Nothing else is exposed — no email, no kill/revive/grant-consent, no `shell_run`, no
file-write. The always-HALT / destructive / operator-gated surface is unreachable here.

`project` accepts an **int id**, an **all-digit string**, or an **exact project name**
(resolved name→id via `GET /api/projects/by-name/{name}`; unknown names raise).

### `complete_task` — the AC-verify-then-flip contract

`complete_task` is the *only* path this MCP surface exposes for landing a task at
`process_status=5` (DONE); `update_task` refuses that transition outright and points
callers here.

1. Fetches the task's current row (same path as `get_task`).
2. The **effective** `acceptance_criteria` array is the `acceptance_criteria` argument
   if you pass it (your full updated array, with verdicts), else the task's
   currently-stored array.
3. If **any** effective item has status `pending` or `failed` → **no API write is made
   at all** and the tool raises an error naming the offending item(s).
4. Otherwise sends **one combined PATCH**: `acceptance_criteria` (only when you
   supplied one) + `process_status=5` + `status_change_reason` (single-round-trip,
   matches the T2/#2541 convention).
5. A null/empty AC array is allowed to flip (nothing to resolve) — the response notes
   this explicitly (`_complete_task_note`).

This is a **friendlier client-side pre-check, not the only guard** — the router's own
`#2765` resolved-final gate re-validates server-side on every PATCH and 422s
independently if this check were ever bypassed by a different client.

## Prereqs

- The agent-teams API reachable at `http://localhost:8456` (the `agent-teams-api`
  container). Override with `API_BASE` (e.g. `http://host.docker.internal:8456` from a
  sibling container).
- [`uv`](https://docs.astral.sh/uv/) on PATH (`uv --version`). uv manages its own
  Python + the `fastmcp` / `httpx` deps — **no host Python needed** (the Windows host's
  `python` is a Store stub).

## Run it (stdio)

```bash
# from the repo's mcp/ directory
uvx --with fastmcp --with httpx python server.py
```

`uvx --with ...` provisions an ephemeral env with the deps and runs the server on
**stdio** (it blocks waiting for an MCP client — that's expected). `requirements.txt`
pins the same two deps for a `pip install -r requirements.txt` / Docker path if ever
needed.

## Verify it (mcp-inspector equivalent)

`verify.py` drives the original three phase-1 tools (`list_projects` / `list_tasks` /
`create_task`) through FastMCP's in-memory `Client` against a **throwaway
`is_active=false` project**, then deletes it (cascade). No pytest (operator-gated);
plain script.

```bash
# from the repo's mcp/ directory — API must be up
uvx --with fastmcp --with httpx python verify.py
```

Expected tail: `[PASS] all assertions passed` and a `BEFORE=0  AFTER=1` task-count line
(the live-DB-safety evidence). Exit code `0` on success.

> Node is present, so `npx @modelcontextprotocol/inspector uvx --with fastmcp --with httpx python server.py`
> also works for an interactive poke — but `verify.py` is the deterministic, Node-free
> check and the one this spike relies on.

The **phase-2 tools** (`get_task` / `update_task` / `complete_task`, Kanban #2518) were
verified the same way — in-memory `Client` round-trip, no pytest (no `fastmcp` in the
`api` test environment, no existing `mcp` pytest precedent) — via a one-off script at
`_scratch/verify_mcp_phase2_2518.py`. It exercises all 6 registered tools including
both the `complete_task` NEGATIVE (unresolved AC → refused, no write) and POSITIVE
(resolved AC → flips to DONE) paths against a title-prefixed throwaway task on the live
`agent-teams` project.

## AC3 hand-off — stdio client config

Add this to a stdio MCP client (Claude Desktop `claude_desktop_config.json`, Cursor
`mcp.json`, or Cline's MCP settings). **Use the absolute path to `server.py`** and an
absolute `uvx` (or rely on PATH):

```json
{
  "mcpServers": {
    "agent-teams-kanban": {
      "command": "uvx",
      "args": [
        "--with", "fastmcp",
        "--with", "httpx",
        "python",
        "C:\\Users\\banku\\Documents\\Personal\\Projects\\GitHub\\agent-teams\\mcp\\server.py"
      ],
      "env": { "API_BASE": "http://localhost:8456" }
    }
  }
}
```

Restart the client; all six tools appear under `agent-teams-kanban`. The server needs
the agent-teams API running on `API_BASE`.

## Scope (stdio only)

stdio only. No OAuth, no HTTP transport, no FastAPI mounting — those are the hosted-mode
follow-ups in the design doc (sections 2–3). Phase 1 (#806) proved the thin-shim
contract end-to-end with read+create; phase 2 (#2518) added read-one + update +
the AC-verify-then-flip close (`get_task` / `update_task` / `complete_task`) on the
same shim contract — still no direct DB/ORM access, no business logic, no new
endpoints.
