"""Kanban #2778/#2779 — Telegram command surface Phase 1 + Phase 2
contract-smoke tests.

First-pass contract smokes for `POST /api/telegram/command`
(`telegram-command-surface-2720.md` D1/D2/D4/D6, Phase 1 + Phase 2 scope):
  - deny-by-default: an unmatched verb -> "unknown command" reply, nothing
    dispatched.
  - no-sticky-project rejection on a read verb before /project is run.
  - /project <name> sets the sticky project; a subsequent read verb then
    targets it.
  - /projects, /tasks, /task <id>, /gates happy paths (service-level, real
    DB via the test-DB harness — NOT pytest-runner-in-session; the operator
    runs this file in a terminal per the block-pytest hook).
  - update_id dedup: the SAME update_id sent twice dispatches ONCE (the
    second call is a no-op reply, not a re-dispatch).
  - Phase 2 (#2779): /new (AI-parse-then-create, landing TODO + run_mode=
    manual — D4 create != execute), /approve /deny (gate resolve via the
    live provenance=telegram precedent), /hold (soft TODO+reason, never
    BLOCKED), and idempotency for the new mutation verbs.

DO NOT RUN IN-SESSION — the block-pytest hook denies it; the operator runs
these in a plain terminal. The comprehensive edge/regression matrix (e.g. the
poller-forward path, malformed Telegram update shapes) is dev-tester's
domain. These lock the wire contract for the Phase-1 verb catalog + dedup
foundation and the Phase-2 safe_mutation verbs.
"""

from __future__ import annotations

import itertools
import uuid

import httpx
import pytest
import respx


# ---------------------------------------------------------------------------
# Helpers (mirror test_task_gates_smoke.py's harness)
# ---------------------------------------------------------------------------


async def _make_fresh_project(client, scaffold_cleanup, slug: str) -> tuple[int, str]:
    name = scaffold_cleanup(f"{slug}-{uuid.uuid4().hex[:8]}")
    resp = await client.post(
        "/api/projects",
        json={
            "name": name,
            "description": f"smoke fixture for {name}",
            "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
            "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
            "config": {},
            "is_active": False,
            "team": "dev",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["id"], body["name"]


async def _make_work_task(client, project_id: int, title: str = "Telegram cmd task") -> int:
    resp = await client.post(
        "/api/tasks",
        headers={"X-Project-Id": str(project_id)},
        json={"project_id": project_id, "title": title, "interaction_kind": "work"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _open_gate(client, project_id: int, task_id: int, *, gate_tier: str = "hitl") -> dict:
    resp = await client.post(
        f"/api/tasks/{task_id}/gates",
        headers={"X-Project-Id": str(project_id)},
        json={"kind": "decision", "gate_tier": gate_tier, "question_payload": {"question": "OK?"}},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


_UPDATE_ID_COUNTER = itertools.count(1)


def _next_update_id() -> int:
    """Strictly-increasing update_id, one module-level counter shared by every
    test. This MUST be monotonic, not random: the dedup gate (AC4) blocks any
    update_id <= the chat's stored watermark, so a test that issues MULTIPLE
    _command() calls on the SAME chat_id relies on each subsequent default
    draw being HIGHER than the one before it — a random draw would have a
    coin-flip chance of drawing lower and getting silently dedup-blocked
    (exactly the non-determinism the operator's pytest run hit: 3 failures
    from `uuid.uuid4().int % ...`, root-caused as a broken monotonic contract,
    NOT a production bug — see decisions.md / Kanban #2778).

    Sharing ONE counter across ALL tests (rather than per-chat) is safe and
    intentional: every test uses its own uuid-suffixed chat_id, so two tests
    can never compare update_ids against the same watermark — only a single
    test's OWN sequence of calls on its OWN chat_id ever needs relative
    ordering, and `itertools.count` guarantees that within any one test's
    call sequence, regardless of what other tests already consumed from the
    shared counter.
    """
    return next(_UPDATE_ID_COUNTER)


async def _command(client, chat_id: str, text: str, update_id: int | None = None) -> dict:
    resp = await client.post(
        "/api/telegram/command",
        json={"chat_id": chat_id, "update_id": update_id or _next_update_id(), "text": text},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Phase 2 (#2779) helper — stub the Anthropic Messages response /new's
# `parse_task_text` call hits, mirroring test_ai_task_parser.py's
# `_anthropic_tool_response` exactly (same fake tool_use shape) so the
# REAL parse path runs end-to-end (no network) rather than mocking
# `parse_task_text` itself.
# ---------------------------------------------------------------------------

_ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"


def _anthropic_tool_response(
    *,
    title: str = "Fix login button alignment",
    description: str = "Fix the login button alignment on mobile",
    task_type: str = "bug",
    priority: int = 2,
    assigned_role: int | None = None,
    blocked_by: int | None = None,
) -> dict:
    tool_input = {
        "title": title,
        "description": description,
        "task_type": task_type,
        "priority": priority,
        "assigned_role": assigned_role,
        "blocked_by": blocked_by,
    }
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "tool_use", "id": "toolu_test", "name": "propose_task", "input": tool_input}
        ],
        "model": "claude-sonnet-4-6",
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }


# ---------------------------------------------------------------------------
# (1) Deny-by-default — an unmatched verb is rejected, nothing dispatched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_command_lists_available_verbs(client) -> None:
    """POSITIVE: an unmatched verb -> dispatched=False + a reply listing the
    Phase-1 verbs (so the operator always knows what IS available).
    NEGATIVE: dispatched is explicitly False, not vacuously omitted.
    """
    chat_id = f"chat-{uuid.uuid4().hex[:8]}"
    body = await _command(client, chat_id, "/frobnicate something")

    assert body["dispatched"] is False
    assert body["verb"] is None
    assert "unknown command" in body["reply_text"].lower(), body
    for verb in ["/projects", "/tasks", "/gates"]:
        assert verb in body["reply_text"], body["reply_text"]


@pytest.mark.asyncio
async def test_empty_text_is_deny_by_default(client) -> None:
    """NEGATIVE: empty/whitespace-only text also falls through to the
    unknown-command reply (no verb to match), never a 500."""
    chat_id = f"chat-{uuid.uuid4().hex[:8]}"
    body = await _command(client, chat_id, "   ")
    assert body["dispatched"] is False


# ---------------------------------------------------------------------------
# (2) No-sticky-project rejection (D2 AC2) — read verbs need /project first
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("cmd", ["/tasks", "/task 1", "/gates"])
async def test_read_verb_without_sticky_project_asks_for_project(client, cmd) -> None:
    """POSITIVE: a read verb on a chat with NO sticky project set replies
    asking the operator to run /project <name> first — it does NOT 500 and
    does NOT silently read some default project (AC2's explicit-target rule).
    """
    chat_id = f"chat-{uuid.uuid4().hex[:8]}"
    body = await _command(client, chat_id, cmd)
    assert body["dispatched"] is True, "the verb DID match — it just can't resolve a project"
    assert "/project" in body["reply_text"], body["reply_text"]


# ---------------------------------------------------------------------------
# (3) /project <name> sets the sticky project; a subsequent read verb targets it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_set_then_tasks_targets_it(client, scaffold_cleanup) -> None:
    """POSITIVE: /project <name> resolves the project + replies with confirmation;
    a SUBSEQUENT /tasks on the SAME chat_id then returns that project's tasks
    (not "no sticky project" — the state persisted across two separate command
    calls, proving D2's per-chat stickiness).
    """
    pid, pname = await _make_fresh_project(client, scaffold_cleanup, "tg-sticky")
    tid = await _make_work_task(client, pid, title="Sticky-target task")
    chat_id = f"chat-{uuid.uuid4().hex[:8]}"

    set_body = await _command(client, chat_id, f"/project {pname}")
    assert set_body["dispatched"] is True
    assert pname in set_body["reply_text"]
    assert str(pid) in set_body["reply_text"]

    tasks_body = await _command(client, chat_id, "/tasks")
    assert tasks_body["dispatched"] is True
    assert f"#{tid}" in tasks_body["reply_text"], tasks_body["reply_text"]
    assert "Sticky-target task" in tasks_body["reply_text"]


@pytest.mark.asyncio
async def test_tasks_excludes_cancelled_and_archived_siblings(
    client, scaffold_cleanup, db_session
) -> None:
    """NEGATIVE (dev-reviewer MAJOR fix): /tasks must mirror the SAME
    exclusions as `GET /api/tasks?pending=true` (routers/tasks.py:449-466) —
    a CANCELLED (ps=6, #854) task and an auto-archived (is_active=false,
    #1240) task must NOT appear, even though a plain pending sibling in the
    SAME project does. `is_active` has no public write path (it is the daily
    audit-archive sweep's own flag — see models/task.py:537-538) so it is set
    directly via db_session, mirroring test_audit_archive_smoke.py's pattern.
    """
    from sqlalchemy import update

    from src.constants import TaskStatus
    from src.models.task import Task

    pid, pname = await _make_fresh_project(client, scaffold_cleanup, "tg-sticky-excl")
    visible_tid = await _make_work_task(client, pid, title="Visible sibling")
    cancelled_tid = await _make_work_task(client, pid, title="Cancelled sibling")
    archived_tid = await _make_work_task(client, pid, title="Archived sibling")

    patch = await client.patch(
        f"/api/tasks/{cancelled_tid}",
        headers={"X-Project-Id": str(pid)},
        json={"process_status": TaskStatus.CANCELLED},
    )
    assert patch.status_code == 200, patch.text

    await db_session.execute(
        update(Task).where(Task.id == archived_tid).values(is_active=False)
    )
    await db_session.commit()

    chat_id = f"chat-{uuid.uuid4().hex[:8]}"
    await _command(client, chat_id, f"/project {pname}")
    tasks_body = await _command(client, chat_id, "/tasks")

    # POSITIVE: the ordinary pending sibling IS listed (proves the query ran
    # and the exclusions aren't accidentally over-broad).
    assert f"#{visible_tid}" in tasks_body["reply_text"], tasks_body["reply_text"]
    # NEGATIVE: cancelled and archived siblings are excluded.
    assert f"#{cancelled_tid}" not in tasks_body["reply_text"], tasks_body["reply_text"]
    assert f"#{archived_tid}" not in tasks_body["reply_text"], tasks_body["reply_text"]


@pytest.mark.asyncio
async def test_project_unknown_name_returns_not_found_reply(client) -> None:
    """NEGATIVE: /project <garbage-name> replies "not found" and does NOT set
    a sticky project (a later read verb on the same chat still asks for /project)."""
    chat_id = f"chat-{uuid.uuid4().hex[:8]}"
    garbage = f"does-not-exist-{uuid.uuid4().hex[:8]}"

    set_body = await _command(client, chat_id, f"/project {garbage}")
    assert set_body["dispatched"] is True
    assert "not found" in set_body["reply_text"].lower()

    tasks_body = await _command(client, chat_id, "/tasks")
    assert "/project" in tasks_body["reply_text"], (
        "a failed /project lookup must NOT leave a sticky project set"
    )


# ---------------------------------------------------------------------------
# (4) /projects, /task <id>, /gates happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_projects_lists_active_projects(client, scaffold_cleanup) -> None:
    pid, pname = await _make_fresh_project(client, scaffold_cleanup, "tg-list")
    chat_id = f"chat-{uuid.uuid4().hex[:8]}"
    body = await _command(client, chat_id, "/projects")
    assert body["dispatched"] is True
    assert f"#{pid} {pname}" in body["reply_text"], body["reply_text"]


@pytest.mark.asyncio
async def test_task_by_id_returns_summary(client, scaffold_cleanup) -> None:
    pid, pname = await _make_fresh_project(client, scaffold_cleanup, "tg-task")
    tid = await _make_work_task(client, pid, title="Detail-view task")
    chat_id = f"chat-{uuid.uuid4().hex[:8]}"
    await _command(client, chat_id, f"/project {pname}")

    body = await _command(client, chat_id, f"/task {tid}")
    assert body["dispatched"] is True
    assert "Detail-view task" in body["reply_text"]
    assert "no AC" in body["reply_text"]  # freshly-created task carries no acceptance_criteria


@pytest.mark.asyncio
async def test_task_by_id_wrong_project_not_found(client, scaffold_cleanup) -> None:
    """NEGATIVE: a task id that belongs to a DIFFERENT project than the chat's
    sticky one must NOT leak — reply is "not found", not the other project's
    task detail."""
    pid_a, pname_a = await _make_fresh_project(client, scaffold_cleanup, "tg-cross-a")
    pid_b, _pname_b = await _make_fresh_project(client, scaffold_cleanup, "tg-cross-b")
    tid_b = await _make_work_task(client, pid_b, title="Belongs to project B")

    chat_id = f"chat-{uuid.uuid4().hex[:8]}"
    await _command(client, chat_id, f"/project {pname_a}")  # sticky = project A

    body = await _command(client, chat_id, f"/task {tid_b}")
    assert "not found" in body["reply_text"].lower()
    assert "Belongs to project B" not in body["reply_text"]


@pytest.mark.asyncio
async def test_gates_lists_open_gate(client, scaffold_cleanup) -> None:
    pid, pname = await _make_fresh_project(client, scaffold_cleanup, "tg-gates")
    tid = await _make_work_task(client, pid, title="Gated task")
    gate = await _open_gate(client, pid, tid, gate_tier="commit")
    chat_id = f"chat-{uuid.uuid4().hex[:8]}"
    await _command(client, chat_id, f"/project {pname}")

    body = await _command(client, chat_id, "/gates")
    assert body["dispatched"] is True
    assert f"gate#{gate['id']}" in body["reply_text"], body["reply_text"]
    assert "commit" in body["reply_text"]

    # NEGATIVE: resolving the gate drops it off the next /gates read.
    await client.post(
        f"/api/task-gates/{gate['id']}/resolve",
        headers={"X-Project-Id": str(pid)},
        json={"answer": "ok", "provenance": "telegram"},
    )
    body2 = await _command(client, chat_id, "/gates")
    assert f"gate#{gate['id']}" not in body2["reply_text"]
    assert "No pending gates" in body2["reply_text"]


# ---------------------------------------------------------------------------
# (5) update_id dedup (AC4) — same update_id twice -> single dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_update_id_does_not_redispatch(client, scaffold_cleanup) -> None:
    """POSITIVE: the SAME update_id sent twice with a MUTATING verb (/project)
    only applies the mutation ONCE — the second call is a no-op dedup reply,
    not a second dispatch. This is the AC4 structural guarantee: a
    getUpdates offset-write failure / re-delivery cannot double-apply.
    """
    pid, pname = await _make_fresh_project(client, scaffold_cleanup, "tg-dedup")
    pid2, pname2 = await _make_fresh_project(client, scaffold_cleanup, "tg-dedup-2")
    chat_id = f"chat-{uuid.uuid4().hex[:8]}"
    uid = _next_update_id()

    first = await _command(client, chat_id, f"/project {pname}", update_id=uid)
    assert first["dispatched"] is True
    assert pname in first["reply_text"]

    # Re-send the SAME update_id but pointed at a DIFFERENT project — if dedup
    # were broken this would silently re-target the chat to project 2.
    second = await _command(client, chat_id, f"/project {pname2}", update_id=uid)
    assert second["dispatched"] is False, "a replayed update_id must NOT re-dispatch"
    assert "duplicate" in second["reply_text"].lower()

    # POSITIVE proof the sticky project is STILL project 1 (the replay did
    # not silently apply project 2's mutation).
    tasks_body = await _command(client, chat_id, "/tasks")
    assert tasks_body["dispatched"] is True
    assert "No pending tasks" in tasks_body["reply_text"]  # project 1 has none yet — still targets it, not an error


@pytest.mark.asyncio
async def test_higher_update_id_after_dedup_still_dispatches(client) -> None:
    """POSITIVE boundary: dedup only blocks update_id <= watermark. A THIRD
    call with a strictly higher update_id on the same chat dispatches
    normally — the watermark does not wedge the chat shut."""
    chat_id = f"chat-{uuid.uuid4().hex[:8]}"
    uid = _next_update_id()
    # Both update_ids here are EXPLICIT (uid, uid + 1) — no default draw from
    # the shared counter is involved in this test's own assertions. A LATER
    # test's default _next_update_id() draw may coincidentally reuse the
    # integer `uid + 1`, but that is harmless: every test uses its own
    # uuid-suffixed chat_id, so the dedup watermark it collides against
    # belongs to a DIFFERENT chat_id row and never interacts with this one.

    first = await _command(client, chat_id, "/projects", update_id=uid)
    assert first["dispatched"] is True

    higher = await _command(client, chat_id, "/projects", update_id=uid + 1)
    assert higher["dispatched"] is True, "a strictly-higher update_id must dispatch normally"


# ---------------------------------------------------------------------------
# (6) Rate limit — @limiter.limit("30/minute") (dev-reviewer WARN, security round)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_command_endpoint_rate_limited_after_thirty_within_window(client) -> None:
    """30 POSTs in a fresh window succeed -> 31st returns 429.

    Mirrors test_scaffold_rate_limit.py's shape (Kanban #1124) for this
    endpoint's own @limiter.limit("30/minute") decorator. Uses a single
    chat_id with strictly-increasing update_ids (via _next_update_id) so every
    request is a genuine NEW dispatch, not a dedup no-op — the rate limiter
    must fire on request VOLUME regardless of dedup outcome, since it runs at
    the decorator layer BEFORE the handler body (and therefore before the
    dedup check) ever executes.
    """
    chat_id = f"chat-ratelimit-{uuid.uuid4().hex[:8]}"

    for i in range(30):
        resp = await client.post(
            "/api/telegram/command",
            json={"chat_id": chat_id, "update_id": _next_update_id(), "text": "/projects"},
        )
        assert resp.status_code == 200, (
            f"request #{i + 1} expected 200, got {resp.status_code}: {resp.text}"
        )

    # 31st request in the same window -> 429 from the slowapi handler.
    resp_31 = await client.post(
        "/api/telegram/command",
        json={"chat_id": chat_id, "update_id": _next_update_id(), "text": "/projects"},
    )
    assert resp_31.status_code == 429, (
        f"request #31 expected 429, got {resp_31.status_code}: {resp_31.text}"
    )
    assert "Rate limit exceeded" in resp_31.json().get("detail", ""), resp_31.text


# ---------------------------------------------------------------------------
# (7) Phase 2 (#2779) — /new: AI-parse-then-create, landing TODO + run_mode=
# manual (D4 create != execute). The real parse path runs end-to-end via a
# respx-stubbed Anthropic response (no network) — same technique as
# test_ai_task_parser.py, so this locks the FULL chain (parse -> TaskCreate
# -> create_task), not just the create_task half.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_creates_task_todo_and_manual_run_mode(client, scaffold_cleanup, monkeypatch) -> None:
    """POSITIVE: /new <text> creates a row (verified via GET, not just the
    reply) with process_status=TODO(1) and run_mode='manual'.
    NEGATIVE: run_mode is explicitly asserted 'manual', not merely "truthy" —
    this is the AC1/D4 structural guarantee that a Telegram-created task can
    never be silently auto-picked by the auto_pickup/auto_headless worker
    queues (routers/tasks.py filters run_mode IN (auto_pickup, auto_headless)).
    """
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    pid, pname = await _make_fresh_project(client, scaffold_cleanup, "tg-new")
    chat_id = f"chat-{uuid.uuid4().hex[:8]}"
    await _command(client, chat_id, f"/project {pname}")

    with respx.mock(assert_all_called=True) as router:
        router.post(_ANTHROPIC_MESSAGES_URL).mock(
            return_value=httpx.Response(200, json=_anthropic_tool_response())
        )
        body = await _command(client, chat_id, "/new Fix the login button alignment on mobile")

    assert body["dispatched"] is True
    assert "created #" in body["reply_text"], body["reply_text"]

    task_id = int(body["reply_text"].split("#", 1)[1].split(" ", 1)[0])
    get_resp = await client.get(f"/api/tasks/{task_id}", headers={"X-Project-Id": str(pid)})
    assert get_resp.status_code == 200, get_resp.text
    task = get_resp.json()
    assert task["process_status"] == 1, task  # TODO, never auto-anything
    assert task["run_mode"] == "manual", task  # NEGATIVE: not auto_pickup/auto_headless
    assert task["project_id"] == pid


@pytest.mark.asyncio
async def test_new_duplicate_update_id_creates_only_one_task(
    client, scaffold_cleanup, monkeypatch
) -> None:
    """POSITIVE: a redelivered /new (SAME update_id) must NOT double-create —
    exactly ONE task exists after both calls. respx `assert_all_called=True`
    on a router good for exactly ONE match additionally proves the SECOND
    /new call never even reaches parse_task_text (the dedup watermark short-
    circuits before dispatch), not just that create_task was idempotent.
    """
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    pid, pname = await _make_fresh_project(client, scaffold_cleanup, "tg-new-dedup")
    chat_id = f"chat-{uuid.uuid4().hex[:8]}"
    await _command(client, chat_id, f"/project {pname}")
    uid = _next_update_id()

    with respx.mock(assert_all_called=False) as router:
        route = router.post(_ANTHROPIC_MESSAGES_URL).mock(
            return_value=httpx.Response(200, json=_anthropic_tool_response())
        )
        first = await _command(client, chat_id, "/new Dedup probe task text", update_id=uid)
        assert first["dispatched"] is True
        assert "created #" in first["reply_text"], first["reply_text"]

        # REPLAY the SAME update_id — must be a dedup no-op, not a second create.
        second = await _command(client, chat_id, "/new Dedup probe task text", update_id=uid)
        assert second["dispatched"] is False
        assert "duplicate" in second["reply_text"].lower()

        # NEGATIVE: the provider was hit exactly ONCE — the replay never
        # reached parse_task_text at all (dedup fires before dispatch).
        assert route.call_count == 1, "the replayed /new must not re-invoke the AI provider"

    list_resp = await client.get(
        "/api/tasks", params={"limit": 500}, headers={"X-Project-Id": str(pid)}
    )
    assert list_resp.status_code == 200, list_resp.text
    matching = [t for t in list_resp.json() if t["title"] == "Fix login button alignment"]
    assert len(matching) == 1, f"expected exactly 1 task, found {len(matching)}: {matching}"


@pytest.mark.asyncio
async def test_new_without_sticky_project_asks_for_project(client) -> None:
    """NEGATIVE: /new on a chat with no sticky project asks for /project
    first — same D2 AC2 guard the read verbs already enforce — and does NOT
    attempt to call the AI provider (no respx stub registered here; a call
    would raise `httpx.ConnectError`/`respx` assertion failure and fail the
    test loudly if the guard were bypassed)."""
    chat_id = f"chat-{uuid.uuid4().hex[:8]}"
    body = await _command(client, chat_id, "/new some task text")
    assert body["dispatched"] is True  # the verb DID match — it just can't resolve a project
    assert "/project" in body["reply_text"], body["reply_text"]


# ---------------------------------------------------------------------------
# (8) Phase 2 (#2779) — /approve and /deny: resolve a gate via the EXISTING
# resolve_gate function, provenance='telegram' (the live #17/#2718 precedent).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_resolves_gate_with_telegram_provenance(client, scaffold_cleanup) -> None:
    """POSITIVE: /approve <gate id> resolves the gate (verified via a
    SUBSEQUENT /gates read showing it gone, and via answer/provenance on the
    resolve response echoed in the reply) with answer='approve'."""
    pid, pname = await _make_fresh_project(client, scaffold_cleanup, "tg-approve")
    tid = await _make_work_task(client, pid, title="Approve-me task")
    gate = await _open_gate(client, pid, tid, gate_tier="commit")
    chat_id = f"chat-{uuid.uuid4().hex[:8]}"
    await _command(client, chat_id, f"/project {pname}")

    body = await _command(client, chat_id, f"/approve {gate['id']}")
    assert body["dispatched"] is True
    assert f"gate#{gate['id']}" in body["reply_text"], body["reply_text"]
    assert "resolved" in body["reply_text"].lower()

    # NEGATIVE proof: the gate no longer shows up as pending.
    gates_body = await _command(client, chat_id, "/gates")
    assert f"gate#{gate['id']}" not in gates_body["reply_text"], gates_body["reply_text"]


@pytest.mark.asyncio
async def test_deny_resolves_gate_with_deny_answer(client, scaffold_cleanup) -> None:
    """POSITIVE: /deny <gate id> resolves with answer='deny' — a DIFFERENT
    outcome from /approve, proving the two verbs are not aliases of each
    other (NEGATIVE: the reply does not say 'approve')."""
    pid, pname = await _make_fresh_project(client, scaffold_cleanup, "tg-deny")
    tid = await _make_work_task(client, pid, title="Deny-me task")
    gate = await _open_gate(client, pid, tid, gate_tier="commit")
    chat_id = f"chat-{uuid.uuid4().hex[:8]}"
    await _command(client, chat_id, f"/project {pname}")

    body = await _command(client, chat_id, f"/deny {gate['id']}")
    assert body["dispatched"] is True
    assert f"gate#{gate['id']}" in body["reply_text"], body["reply_text"]
    assert "approve" not in body["reply_text"].lower()


@pytest.mark.asyncio
async def test_approve_on_already_resolved_gate_surfaces_readable_409(
    client, scaffold_cleanup
) -> None:
    """NEGATIVE (never a 500): approving an ALREADY-resolved gate surfaces
    the router's 409 stale-reject as a readable reply — the endpoint itself
    still returns 200 (the wrapper), proving the HTTPException-to-reply-text
    conversion works for the reused resolve_gate call."""
    pid, pname = await _make_fresh_project(client, scaffold_cleanup, "tg-stale")
    tid = await _make_work_task(client, pid, title="Stale-gate task")
    gate = await _open_gate(client, pid, tid, gate_tier="commit")
    chat_id = f"chat-{uuid.uuid4().hex[:8]}"
    await _command(client, chat_id, f"/project {pname}")

    first = await _command(client, chat_id, f"/approve {gate['id']}")
    assert first["dispatched"] is True
    assert "resolved" in first["reply_text"].lower()

    second = await _command(client, chat_id, f"/approve {gate['id']}")
    assert second["dispatched"] is True  # the verb matched; the CALL failed, not the dispatch
    assert "409" in second["reply_text"], second["reply_text"]
    assert "not open" in second["reply_text"].lower(), second["reply_text"]


# ---------------------------------------------------------------------------
# (9) Phase 2 (#2779) — /hold: soft hold stays TODO(1) + status_change_reason,
# NEVER process_status=4/BLOCKED (VERIFIED convention: .claude/skills/
# zb-task-update/SKILL.md step 3 — BLOCKED is reserved for the blocked_by FK).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hold_sets_reason_and_stays_todo(client, scaffold_cleanup) -> None:
    """POSITIVE: /hold <id> <reason> sets status_change_reason to the note.
    NEGATIVE: process_status is explicitly asserted == 1 (TODO), not merely
    "unchanged" — this is the structural proof that /hold never uses
    process_status=4 (BLOCKED), which is reserved for the blocked_by FK path.
    """
    pid, pname = await _make_fresh_project(client, scaffold_cleanup, "tg-hold")
    tid = await _make_work_task(client, pid, title="Hold-me task")
    chat_id = f"chat-{uuid.uuid4().hex[:8]}"
    await _command(client, chat_id, f"/project {pname}")

    body = await _command(client, chat_id, f"/hold {tid} waiting on design review")
    assert body["dispatched"] is True
    assert f"#{tid}" in body["reply_text"], body["reply_text"]
    assert "held" in body["reply_text"].lower()

    get_resp = await client.get(f"/api/tasks/{tid}", headers={"X-Project-Id": str(pid)})
    assert get_resp.status_code == 200, get_resp.text
    task = get_resp.json()
    assert task["process_status"] == 1, task  # NEGATIVE: never 4/BLOCKED
    assert task["status_change_reason"] == "waiting on design review", task


@pytest.mark.asyncio
async def test_hold_without_reason_is_usage_error(client, scaffold_cleanup) -> None:
    """NEGATIVE: /hold <id> with NO reason text is rejected with a usage
    reply (not a 500, not a silent hold-with-empty-reason)."""
    pid, pname = await _make_fresh_project(client, scaffold_cleanup, "tg-hold-noreason")
    tid = await _make_work_task(client, pid, title="Hold-no-reason task")
    chat_id = f"chat-{uuid.uuid4().hex[:8]}"
    await _command(client, chat_id, f"/project {pname}")

    body = await _command(client, chat_id, f"/hold {tid}")
    assert body["dispatched"] is True  # the verb matched; the ARGS were invalid
    assert "usage" in body["reply_text"].lower(), body["reply_text"]

    # NEGATIVE proof: the task was NOT mutated.
    get_resp = await client.get(f"/api/tasks/{tid}", headers={"X-Project-Id": str(pid)})
    assert get_resp.json()["status_change_reason"] is None


@pytest.mark.asyncio
async def test_hold_unknown_task_id_returns_readable_404(client, scaffold_cleanup) -> None:
    """NEGATIVE (never a 500): /hold on a non-existent task id surfaces the
    router's 404 as a readable reply."""
    pid, pname = await _make_fresh_project(client, scaffold_cleanup, "tg-hold-404")
    chat_id = f"chat-{uuid.uuid4().hex[:8]}"
    await _command(client, chat_id, f"/project {pname}")

    body = await _command(client, chat_id, "/hold 999999999 some reason")
    assert body["dispatched"] is True
    assert "404" in body["reply_text"], body["reply_text"]


# ---------------------------------------------------------------------------
# (10) Phase 2 (#2779) — deny-by-default extension point: a verb registered
# in _DISPATCH with NO matching _VERB_CLASS entry is denied (fail-closed),
# never executed. This is the structural proof that a future destructive
# verb added carelessly (dict updated, class map forgotten) fails SAFE.
# ---------------------------------------------------------------------------


def test_every_dispatch_verb_has_a_recognized_auth_class() -> None:
    """POSITIVE: every verb in _DISPATCH has a _VERB_CLASS entry that is
    either 'read' or 'safe_mutation' (Phase 2 ships no 'destructive' member).
    NEGATIVE: this is a source-level invariant check, not a live-request
    test — it fails BEFORE any bad verb could ever reach a live request."""
    from src.routers.telegram_command import _DISPATCH, _VERB_CLASS

    missing = set(_DISPATCH) - set(_VERB_CLASS)
    assert missing == set(), f"verbs in _DISPATCH with no _VERB_CLASS entry: {missing}"

    for verb, cls in _VERB_CLASS.items():
        assert cls in ("read", "safe_mutation", "destructive"), f"{verb} has unrecognized class {cls!r}"
