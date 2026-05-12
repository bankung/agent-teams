"""Tests for the row_changed → SSE pipeline (Kanban #782).

5 cases:
- (a) trigger fires NOTIFY on INSERT (verified via a separate asyncpg LISTEN
      connection in the test).
- (b) SSE client receives a matching `row_changed` event when a task is
      POSTed in its project.
- (c) cross-project leak guard — SSE client subscribed to project_id=1 does
      NOT receive an event from project_id=2.
- (d) disconnect cleanup — 100 connect/disconnect cycles leave broker
      ._listeners empty and don't grow asyncio.all_tasks().
- (e) heartbeat — idle SSE client receives at least one `: keepalive` comment
      after the heartbeat interval.

Common scaffolding:
- The autouse `_enable_sse_listener` fixture flips APP_SSE_DISABLE=false +
  starts the module-level broker for the test run; teardown stops it.
- A direct `asyncpg.connect` is used in (a) for an independent LISTEN
  connection; this is the canonical low-level verification that the PG
  trigger fires.
- (b)/(c)/(d)/(e) drive the broker through its in-process API rather than
  through httpx streaming — sse-starlette + httpx ASGITransport streaming
  is awkward inside pytest-asyncio (the response body is an async iterator
  that doesn't compose cleanly with client.aclose); we exercise the same
  code paths by calling `broker.add_listener` directly and observing
  queue.get(). The router itself is a thin pass-through (see
  test_router_smoke for an import-only check).

Skip note: these tests need the postgres trigger to be installed (migration
0016_row_changed_triggers). The conftest already runs `alembic upgrade head`
once per session — no extra setup here.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import asyncpg
import pytest

from src.services.row_changed_listener import (
    CHANNEL,
    RowChangedBroker,
    _coerce_asyncpg_dsn,
    broker as global_broker,
)


def _dsn() -> str:
    from src.settings import get_settings

    return _coerce_asyncpg_dsn(get_settings().database_url)


@pytest.fixture(autouse=True)
async def _enable_sse_listener():
    """Make sure APP_SSE_DISABLE is false and the global broker is started
    on a connection bound to the current event loop. Each test gets a fresh
    broker connection (the per-test engine pool reset already gives us this
    isolation guarantee — we mirror it for asyncpg).
    """
    prev = os.environ.get("APP_SSE_DISABLE")
    os.environ["APP_SSE_DISABLE"] = "false"
    # Force a clean broker state: stop (in case a previous test started it),
    # then start. start() is idempotent + lock-guarded.
    await global_broker.stop()
    await global_broker.start()
    yield
    await global_broker.stop()
    if prev is None:
        os.environ.pop("APP_SSE_DISABLE", None)
    else:
        os.environ["APP_SSE_DISABLE"] = prev


async def _seed_project_and_task(client, *, project_name_suffix: str) -> tuple[int, int]:
    """Helper — create a fresh project, return (project_id, agent_teams_id_for_use)."""
    # Use the seeded agent-teams project so the FK lookup is fast. Callers
    # that want a separate project_id POST one explicitly.
    resp = await client.get("/api/projects/by-name/agent-teams")
    assert resp.status_code == 200
    return resp.json()["id"], 0  # second value not used; placeholder


# ---------------------------------------------------------------------------
# (a) PG trigger fires NOTIFY on INSERT — verified via standalone LISTEN conn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_trigger_fires_notify_on_task_insert(client) -> None:
    """Open a separate asyncpg LISTEN connection, insert a task via the API,
    assert a NOTIFY payload arrives within 1s with the expected shape.

    This is the lowest-level verification — proves the migration's trigger
    function emits well-formed JSON on tasks INSERT.
    """
    # 1. fetch project_id of the seeded agent-teams row.
    pres = await client.get("/api/projects/by-name/agent-teams")
    assert pres.status_code == 200
    project_id = pres.json()["id"]

    # 2. open a dedicated LISTEN connection.
    received: list[dict] = []
    loop = asyncio.get_running_loop()
    notify_event = asyncio.Event()

    def _on_notify(conn, pid, channel, payload_str):
        try:
            received.append(json.loads(payload_str))
        finally:
            loop.call_soon_threadsafe(notify_event.set)

    listen_conn = await asyncpg.connect(dsn=_dsn())
    await listen_conn.add_listener(CHANNEL, _on_notify)

    try:
        # 3. insert a task via the API.
        headers = {"X-Project-Id": str(project_id)}
        post_resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "sse-test-a (trigger fires NOTIFY)",
            },
            headers=headers,
        )
        assert post_resp.status_code == 201, post_resp.text
        new_task_id = post_resp.json()["id"]

        # 4. wait for the NOTIFY to arrive.
        try:
            await asyncio.wait_for(notify_event.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pytest.fail(
                f"NOTIFY did not arrive within 1s on channel={CHANNEL}; "
                f"received={received}"
            )

        # 5. validate payload shape.
        match = next(
            (
                p
                for p in received
                if p.get("table") == "tasks"
                and p.get("id") == new_task_id
                and p.get("op") == "insert"
            ),
            None,
        )
        assert match is not None, (
            f"expected an INSERT payload for task id={new_task_id}; "
            f"received={received}"
        )
        assert match["project_id"] == project_id
        assert isinstance(match["ts"], str) and len(match["ts"]) > 0

        # 6. soft-delete the test row.
        await client.delete(
            f"/api/tasks/{new_task_id}", headers=headers
        )
    finally:
        try:
            await listen_conn.remove_listener(CHANNEL, _on_notify)
        except Exception:
            pass
        await listen_conn.close()


# ---------------------------------------------------------------------------
# (b) SSE-broker fan-out: matching project_id delivers the event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b_broker_fans_out_matching_project_event(client) -> None:
    """Subscribe a listener to project_id=<agent-teams>, POST a task into
    that project, expect the broker queue to receive the payload <1s.
    """
    pres = await client.get("/api/projects/by-name/agent-teams")
    project_id = pres.json()["id"]

    queue = global_broker.add_listener(project_id)
    try:
        headers = {"X-Project-Id": str(project_id)}
        post_resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "sse-test-b (broker fanout)",
            },
            headers=headers,
        )
        assert post_resp.status_code == 201, post_resp.text
        new_task_id = post_resp.json()["id"]

        # Look for the INSERT event among everything the queue receives in
        # the first second (audit triggers etc may fire other UPDATEs on
        # adjacent traffic if any test runs in parallel — but we filter on
        # id+op).
        start = time.monotonic()
        matched = None
        while time.monotonic() - start < 1.0:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if (
                payload.get("table") == "tasks"
                and payload.get("id") == new_task_id
                and payload.get("op") == "insert"
            ):
                matched = payload
                break

        assert matched is not None, (
            "expected to receive INSERT event for the new task within 1s"
        )
        assert matched["project_id"] == project_id

        # cleanup
        await client.delete(f"/api/tasks/{new_task_id}", headers=headers)
    finally:
        global_broker.remove_listener(queue)


# ---------------------------------------------------------------------------
# (c) Cross-project leak guard — filter rejects events from other projects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c_cross_project_filter_blocks_other_project_events(
    client, scaffold_cleanup
) -> None:
    """Create a second project, subscribe a listener to project_a, POST a task
    in project_b, assert no `tasks` event for project_b reaches the listener
    within 500ms.
    """
    import uuid

    a_resp = await client.get("/api/projects/by-name/agent-teams")
    project_a_id = a_resp.json()["id"]

    # Spin up a fresh project_b.
    name_b = f"sse-test-c-{uuid.uuid4().hex[:8]}"
    scaffold_cleanup(name_b)
    create_b = await client.post(
        "/api/projects",
        json={
            "name": name_b,
            "description": "sse-test-c project_b",
            "paths": {"web": "/tmp/b/web", "api": "/tmp/b/api", "db": "/tmp/b/db"},
            "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
            "config": {},
            "is_active": False,
            "team": "dev",
        },
    )
    assert create_b.status_code == 201, create_b.text
    project_b_id = create_b.json()["id"]

    queue = global_broker.add_listener(project_a_id)
    try:
        # POST task into project_b — listener (filter=project_a_id) MUST NOT
        # receive a tasks-with-project_b event.
        headers_b = {"X-Project-Id": str(project_b_id)}
        post_resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_b_id,
                "title": "sse-test-c (cross-project leak guard)",
            },
            headers=headers_b,
        )
        assert post_resp.status_code == 201, post_resp.text
        new_task_id = post_resp.json()["id"]

        # Wait up to 500ms — anything tasks-related from project_b is a leak.
        # The projects-table NOTIFY for project_b creation IS allowed through
        # (project-table events fan to project-bound listeners by design); so
        # we drain everything and assert NONE are tasks events from project_b.
        start = time.monotonic()
        leaked = []
        while time.monotonic() - start < 0.5:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            if (
                payload.get("table") == "tasks"
                and payload.get("project_id") == project_b_id
            ):
                leaked.append(payload)

        assert leaked == [], (
            f"cross-project leak: listener for project_a={project_a_id} "
            f"received tasks events for project_b={project_b_id}: {leaked}"
        )

        # cleanup
        await client.delete(f"/api/tasks/{new_task_id}", headers=headers_b)
        await client.delete(f"/api/projects/{project_b_id}")
    finally:
        global_broker.remove_listener(queue)


# ---------------------------------------------------------------------------
# (d) Disconnect cleanup — 100 connect/disconnect cycles → no leak
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_d_disconnect_cleanup_no_listener_leak() -> None:
    """Spin up 100 listeners and detach each — broker._listeners must drain
    to empty (modulo any unrelated test-side listeners — we measure the
    delta).
    """
    baseline_listeners = len(global_broker._listeners)
    baseline_tasks = len(asyncio.all_tasks())

    for _ in range(100):
        q = global_broker.add_listener(project_id=1)
        # No I/O — immediately remove.
        global_broker.remove_listener(q)

    final_listeners = len(global_broker._listeners)
    final_tasks = len(asyncio.all_tasks())

    assert final_listeners == baseline_listeners, (
        f"listener leak: baseline={baseline_listeners} final={final_listeners}"
    )
    # asyncio.all_tasks() should not have grown beyond a tiny constant —
    # broker.remove_listener is sync, no task spawned per cycle.
    assert final_tasks - baseline_tasks <= 5, (
        f"task leak: baseline={baseline_tasks} final={final_tasks}"
    )


# ---------------------------------------------------------------------------
# (e) Heartbeat — idle stream emits `: keepalive` comment within interval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e_heartbeat_fires_on_idle_stream(client, monkeypatch) -> None:
    """Drive `src.routers.events.stream` directly with a shortened heartbeat
    so the test runs in <1s. Verify the generator yields a `comment=keepalive`
    frame.
    """
    # Shorten the heartbeat constant so the test doesn't sit for 25s.
    import src.routers.events as events_mod

    monkeypatch.setattr(events_mod, "_HEARTBEAT_SECONDS", 0.1)

    # Build a stub Request whose is_disconnected returns False for a few
    # ticks then True. The generator is internal to `stream`; we drive it by
    # invoking the route handler and inspecting the EventSourceResponse's
    # body_iterator.
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/events/stream",
        "headers": [],
        "query_string": b"",
        "client": ("testclient", 0),
    }
    disconnect_calls = {"n": 0}

    async def _receive():
        # Starlette's request.is_disconnected polls receive() — return a
        # "no message" sentinel for the first few ticks then "disconnect".
        disconnect_calls["n"] += 1
        if disconnect_calls["n"] < 4:
            # http.request — keeps is_disconnected() False
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    req = Request(scope, receive=_receive)
    resp = await events_mod.stream(req, project_id=None)

    body_iter = resp.body_iterator
    seen_keepalive = False
    saw_frames: list[str] = []
    try:
        async with asyncio.timeout(2.0):
            async for chunk in body_iter:
                # sse-starlette wraps the inner async generator and yields
                # either the original dict (before serialization, depending
                # on sse-starlette version) or already-encoded bytes. We
                # accept both: a `{"comment":"keepalive"}` dict OR a wire
                # frame whose text begins with `: keepalive`.
                if isinstance(chunk, dict):
                    saw_frames.append(repr(chunk))
                    if chunk.get("comment") == "keepalive":
                        seen_keepalive = True
                        break
                else:
                    text = (
                        chunk.decode("utf-8")
                        if isinstance(chunk, (bytes, bytearray))
                        else str(chunk)
                    )
                    saw_frames.append(text)
                    if text.startswith(": keepalive") or "keepalive" in text:
                        seen_keepalive = True
                        break
    except asyncio.TimeoutError:
        pass

    assert seen_keepalive, (
        f"expected at least one ': keepalive' frame; saw={saw_frames!r}"
    )
