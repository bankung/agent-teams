# agent-teams MCP server (spike — Kanban #806, AC2)

A **minimal stdio [FastMCP](https://github.com/jlowin/fastmcp) server** that exposes the
curated read+create slice of the agent-teams Kanban backend over MCP. Every tool is a
**thin httpx shim** over the existing FastAPI at `http://localhost:8456` — no direct
DB/ORM access, no business logic, no new endpoints. The routers stay the single
chokepoint for validation, AC discipline, and the operator-proof/HALT gates
(see `context/projects/agent-teams/shared/mcp-adapter-design.md`).

## Tools (exactly three)

| Tool | Backs onto | Notes |
|---|---|---|
| `list_projects()` | `GET /api/projects` | discovery |
| `list_tasks(project, status?)` | `GET /api/tasks` (`X-Project-Id` header) | `project` = id or name; `status` = `process_status` 1..5 |
| `create_task(project, title, description?, acceptance_criteria?)` | `POST /api/tasks` | `project_id` in **both** the JSON body **and** the `X-Project-Id` header (header alone → 422) |

Nothing else is exposed — no email, no kill/revive/grant-consent, no `shell_run`, no
file-write. The always-HALT / destructive / operator-gated surface is unreachable here.

`project` accepts an **int id**, an **all-digit string**, or an **exact project name**
(resolved name→id via `GET /api/projects/by-name/{name}`; unknown names raise).

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

`verify.py` drives all three tools through FastMCP's in-memory `Client` against a
**throwaway `is_active=false` project**, then deletes it (cascade). No pytest
(operator-gated); plain script.

```bash
# from the repo's mcp/ directory — API must be up
uvx --with fastmcp --with httpx python verify.py
```

Expected tail: `[PASS] all assertions passed` and a `BEFORE=0  AFTER=1` task-count line
(the live-DB-safety evidence). Exit code `0` on success.

> Node is present, so `npx @modelcontextprotocol/inspector uvx --with fastmcp --with httpx python server.py`
> also works for an interactive poke — but `verify.py` is the deterministic, Node-free
> check and the one this spike relies on.

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

Restart the client; the three tools appear under `agent-teams-kanban`. The server needs
the agent-teams API running on `API_BASE`.

## Scope (spike only)

stdio only. No OAuth, no HTTP transport, no FastAPI mounting — those are the hosted-mode
follow-ups in the design doc (sections 2–3). This proves the thin-shim contract
end-to-end and nothing more.
