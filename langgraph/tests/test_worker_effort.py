"""Effort-lever resolution tests — Kanban #2300 (Slice 1).

Pins the worker's effort resolution (precedence carrier > project mode > off),
the auto heuristic + its UNCONDITIONAL server-side clamp (AC7), the best-effort
carrier PATCH in 'auto' mode, and fail-closed-to-off on a project-fetch failure.

The pure helpers (`_clamp_effort`, `_resolve_auto_effort`) are tested directly;
`_resolve_effort_for_spawn` / `_fetch_project_effort_mode` use httpx.MockTransport
(no real network), mirroring test_worker_policy_hook.py.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import worker
from worker import (
    _clamp_effort,
    _resolve_auto_effort,
    _resolve_effort_for_spawn,
)


# ---------------------------------------------------------------------------
# Fixtures / harness
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_effort_cache() -> None:
    worker._effort_mode_cache_clear()
    yield
    worker._effort_mode_cache_clear()


def _cfg() -> SimpleNamespace:
    # _resolve_effort_for_spawn / _fetch_project_effort_mode only read cfg.api_base.
    return SimpleNamespace(api_base="http://test", project_id=1)


def _headers() -> dict[str, str]:
    return {"X-Project-Id": "1", "Content-Type": "application/json"}


class _RequestLog:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []


def _body(req: httpx.Request) -> dict[str, Any]:
    return json.loads(req.content) if req.content else {}


def _make_client(handler, log: _RequestLog) -> httpx.AsyncClient:
    def _wrap(req: httpx.Request) -> httpx.Response:
        log.requests.append(req)
        return handler(req)

    return httpx.AsyncClient(transport=httpx.MockTransport(_wrap), timeout=5.0)


def _project_handler(effort_mode):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/api/projects/1":
            return httpx.Response(200, json={"id": 1, "effort_mode": effort_mode})
        if req.method == "PATCH" and req.url.path == "/api/tasks/42":
            return httpx.Response(200, json={"id": 42})
        raise AssertionError(f"unexpected request: {req.method} {req.url.path}")

    return handler


# ---------------------------------------------------------------------------
# 1. _clamp_effort — UNCONDITIONAL cap at 'extra' (AC7)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, "off"),
        ("off", "off"),
        ("low", "low"),
        ("medium", "medium"),
        ("high", "high"),
        ("extra", "extra"),
        ("max", "extra"),       # 'max' is capped — auto NEVER reaches max
        ("xhigh", "extra"),     # unknown → cap
        ("garbage", "extra"),   # hacked heuristic output → cap, not unbounded
    ],
)
def test_clamp_effort(value, expected) -> None:
    assert _clamp_effort(value) == expected


# ---------------------------------------------------------------------------
# 2. _resolve_auto_effort — heuristic table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "task,expected",
    [
        ({"task_type": "feature"}, "medium"),          # default
        ({}, "medium"),                                # empty → default
        ({"task_type": "docs"}, "low"),
        ({"task_type": "chore"}, "low"),
        ({"model_override": "opus"}, "high"),
        ({"description": "x" * 4001}, "high"),         # > 4000
        ({"description": "x" * 4000}, "medium"),       # boundary: NOT > 4000
        ({"assigned_role": "dev-sr-backend"}, "high"), # string-tolerant sr- check
        ({"task_type": "docs", "model_override": "opus"}, "low"),  # docs wins (first)
    ],
)
def test_resolve_auto_effort(task, expected) -> None:
    assert _resolve_auto_effort(task) == expected


def test_auto_never_emits_max_even_via_clamp() -> None:
    """The auto path ALWAYS clamps — feed a hacked heuristic output 'max' through
    the clamp and prove it collapses to 'extra' (the server-side cap)."""
    assert _clamp_effort("max") == "extra"
    # And the real heuristic never returns 'max' for any documented branch.
    assert _resolve_auto_effort({"model_override": "opus"}) != "max"


# ---------------------------------------------------------------------------
# 3. _resolve_effort_for_spawn — precedence carrier > project mode > off
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_carrier_wins_no_project_fetch() -> None:
    """A valid carrier (incl. manual 'max') wins outright — NO project GET fires."""
    log = _RequestLog()
    handler = _project_handler("auto")  # would resolve differently if consulted
    task = {"id": 42, "effort_override": "max", "task_type": "docs"}
    async with _make_client(handler, log) as client:
        resolved = await _resolve_effort_for_spawn(
            client, _cfg(), _headers(), task, 1
        )
    assert resolved == "max"
    # Carrier short-circuits — no project fetch, no PATCH.
    assert log.requests == [], [r.url.path for r in log.requests]


@pytest.mark.asyncio
async def test_project_preset_used_when_no_carrier() -> None:
    """No carrier + project preset → the preset is used (no carrier PATCH)."""
    log = _RequestLog()
    async with _make_client(_project_handler("high"), log) as client:
        resolved = await _resolve_effort_for_spawn(
            client, _cfg(), _headers(), {"id": 42}, 1
        )
    assert resolved == "high"
    methods = [r.method for r in log.requests]
    assert methods == ["GET"], methods  # only the project fetch; no PATCH


@pytest.mark.asyncio
async def test_auto_mode_resolves_clamps_and_patches_carrier() -> None:
    """'auto' project mode → heuristic + clamp + best-effort carrier PATCH.

    POSITIVE: the resolved level is the heuristic output.
    NEGATIVE/lock: the carrier PATCH fires with the resolved value (visibility).
    """
    log = _RequestLog()
    task = {"id": 42, "model_override": "opus"}  # heuristic → 'high'
    async with _make_client(_project_handler("auto"), log) as client:
        resolved = await _resolve_effort_for_spawn(
            client, _cfg(), _headers(), task, 1
        )
    assert resolved == "high"
    methods = [r.method for r in log.requests]
    # GET project mode, then PATCH the carrier.
    assert methods == ["GET", "PATCH"], methods
    patch_body = _body(log.requests[1])
    assert patch_body == {"effort_override": "high"}, patch_body


@pytest.mark.asyncio
async def test_auto_mode_clamp_is_unconditional() -> None:
    """In auto mode the clamp is unconditional — even if the heuristic somehow
    produced 'max', the resolved+PATCHed value is capped at 'extra'.

    We monkeypatch the heuristic to return 'max' to prove the clamp on the auto
    path (not the heuristic) is the cap.
    """
    log = _RequestLog()
    import worker as _w

    orig = _w._resolve_auto_effort
    _w._resolve_auto_effort = lambda task: "max"  # hacked output
    try:
        async with _make_client(_project_handler("auto"), log) as client:
            resolved = await _resolve_effort_for_spawn(
                client, _cfg(), _headers(), {"id": 42}, 1
            )
    finally:
        _w._resolve_auto_effort = orig
    assert resolved == "extra", resolved
    patch_body = _body(log.requests[1])
    assert patch_body == {"effort_override": "extra"}, patch_body


@pytest.mark.asyncio
async def test_null_project_mode_is_off() -> None:
    """NULL project effort_mode → None (= off). No carrier PATCH."""
    log = _RequestLog()
    async with _make_client(_project_handler(None), log) as client:
        resolved = await _resolve_effort_for_spawn(
            client, _cfg(), _headers(), {"id": 42}, 1
        )
    assert resolved is None
    assert [r.method for r in log.requests] == ["GET"]


@pytest.mark.asyncio
async def test_invalid_project_mode_is_off() -> None:
    """An unknown project effort_mode value → None (= off, fail-safe)."""
    log = _RequestLog()
    async with _make_client(_project_handler("bogus"), log) as client:
        resolved = await _resolve_effort_for_spawn(
            client, _cfg(), _headers(), {"id": 42}, 1
        )
    assert resolved is None


@pytest.mark.asyncio
async def test_invalid_carrier_falls_through_to_project() -> None:
    """A carrier outside the legal set is ignored — fall through to project mode."""
    log = _RequestLog()
    task = {"id": 42, "effort_override": "auto"}  # 'auto' is NOT a carrier value
    async with _make_client(_project_handler("low"), log) as client:
        resolved = await _resolve_effort_for_spawn(
            client, _cfg(), _headers(), task, 1
        )
    assert resolved == "low"  # project preset used, carrier ignored


@pytest.mark.asyncio
async def test_project_fetch_failure_fails_closed_to_off() -> None:
    """A project-fetch failure (500) → None (= off) — fail CLOSED, never turn
    thinking on for a project that didn't ask for it."""
    log = _RequestLog()

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    async with _make_client(handler, log) as client:
        resolved = await _resolve_effort_for_spawn(
            client, _cfg(), _headers(), {"id": 42}, 1
        )
    assert resolved is None
