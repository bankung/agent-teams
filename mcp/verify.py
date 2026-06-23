"""End-to-end verification for the MCP spike (Kanban #806, AC2) -- mcp-inspector equivalent.

Drives the three MCP tools through FastMCP's in-memory Client (which dispatches to the
exact same tool functions a stdio client would), against a THROWAWAY project so the live
agent_teams DB is never polluted. No pytest (operator-gated); plain asyncio script.

Steps:
  0. create a temp project via POST /api/projects (is_active=false), capture its id.
  1. list_projects()                -> assert the temp project appears.
  2. create_task(temp, "...smoke")  -> assert 201-shaped row echoes title + project_id.
  3. list_tasks(temp)               -> assert the created task appears.
  4. DELETE /api/projects/{id}      -> cascade cleanup.
  5. report task count BEFORE (0) and AFTER create (1) -- live-DB-safety evidence.

Run via uvx (see README). Exit code 0 = all assertions passed.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

import httpx
from fastmcp import Client

from server import API_BASE, mcp

OK = "PASS"
NO = "FAIL"


def _project_create_payload(name: str) -> dict:
    """Mirror api/tests/test_recurrence_dedup.py::_project_create_payload (is_active=False)."""
    return {
        "name": name,
        "description": f"MCP spike throwaway fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


def _count_tasks(client: httpx.Client, pid: int) -> int:
    resp = client.get(f"{API_BASE}/api/tasks", headers={"X-Project-Id": str(pid)})
    resp.raise_for_status()
    return len(resp.json())


async def main() -> int:
    name = f"mcp-spike-throwaway-{uuid.uuid4().hex[:8]}"
    failures: list[str] = []
    pid: int | None = None
    count_before = count_after = None

    http = httpx.Client(timeout=httpx.Timeout(30.0))
    try:
        # --- Step 0: create throwaway project (is_active=false) -------------------
        resp = http.post(f"{API_BASE}/api/projects", json=_project_create_payload(name))
        resp.raise_for_status()
        pid = int(resp.json()["id"])
        print(f"[setup] created throwaway project name={name!r} id={pid} (is_active=false)")

        count_before = _count_tasks(http, pid)
        print(f"[evidence] task count BEFORE create: {count_before}")

        # The MCP Client dispatches to the same tool functions a stdio client hits.
        async with Client(mcp) as client:
            tool_names = sorted(t.name for t in await client.list_tools())
            expect = ["create_task", "list_projects", "list_tasks"]
            tag = OK if tool_names == expect else NO
            print(f"[{tag}] tools exposed == exactly 3: {tool_names}")
            if tool_names != expect:
                failures.append(f"tool set {tool_names} != {expect}")

            # --- Step 1: list_projects() -> temp project appears -----------------
            res = await client.call_tool("list_projects")
            projects = res.data
            ids = {p["id"] for p in projects}
            tag = OK if pid in ids else NO
            print(f"[{tag}] list_projects() includes temp id={pid} (total {len(projects)} projects)")
            if pid not in ids:
                failures.append("temp project not in list_projects()")

            # --- Step 2: create_task -> echoes title + project_id ----------------
            res = await client.call_tool(
                "create_task", {"project": pid, "title": "mcp-spike-smoke"}
            )
            task = res.data
            ok_title = task.get("title") == "mcp-spike-smoke"
            ok_pid = task.get("project_id") == pid
            ok_id = isinstance(task.get("id"), int)
            tag = OK if (ok_title and ok_pid and ok_id) else NO
            print(
                f"[{tag}] create_task -> id={task.get('id')} title={task.get('title')!r} "
                f"project_id={task.get('project_id')} ps={task.get('process_status')}"
            )
            if not (ok_title and ok_pid and ok_id):
                failures.append(
                    f"create_task echo mismatch: title_ok={ok_title} "
                    f"pid_ok={ok_pid} id_ok={ok_id}"
                )
            created_id = task.get("id")

            count_after = _count_tasks(http, pid)
            tag = OK if count_after == (count_before + 1) else NO
            print(f"[{tag}] [evidence] task count AFTER create: {count_after} (expected {count_before + 1})")
            if count_after != count_before + 1:
                failures.append(f"count_after {count_after} != {count_before + 1}")

            # --- Step 3: list_tasks(temp) -> created task appears ----------------
            res = await client.call_tool("list_tasks", {"project": pid})
            tasks = res.data
            task_ids = {t["id"] for t in tasks}
            tag = OK if created_id in task_ids else NO
            print(f"[{tag}] list_tasks(temp) includes created task id={created_id} ({len(tasks)} task[s])")
            if created_id not in task_ids:
                failures.append("created task not in list_tasks()")

            # Bonus: name-based resolution path exercised exactly once.
            res = await client.call_tool("list_tasks", {"project": name})
            tag = OK if created_id in {t["id"] for t in res.data} else NO
            print(f"[{tag}] list_tasks(<name>) resolves name->id and includes the task")
            if created_id not in {t["id"] for t in res.data}:
                failures.append("name-based list_tasks() did not resolve")

    finally:
        # --- Step 4: cleanup (cascade) -------------------------------------------
        if pid is not None:
            d = http.delete(f"{API_BASE}/api/projects/{pid}")
            print(f"[cleanup] DELETE /api/projects/{pid} -> {d.status_code} (204 = cascade ok)")
            verify = http.get(f"{API_BASE}/api/projects/{pid}")
            print(f"[cleanup] GET /api/projects/{pid} after delete -> {verify.status_code} (404 = gone)")
        http.close()

    print("-" * 60)
    print(f"[summary] BEFORE={count_before}  AFTER={count_after}  (live-DB-safety: throwaway only)")
    if failures:
        print(f"[{NO}] {len(failures)} assertion(s) failed:")
        for f in failures:
            print(f"   - {f}")
        return 1
    print(f"[{OK}] all assertions passed -- MCP spike verified end-to-end.")
    return 0


if __name__ == "__main__":
    # ensure `import server` works when run from elsewhere
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    raise SystemExit(asyncio.run(main()))
