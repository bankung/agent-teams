"""Per-role effort override FILE layer tests — Kanban #2327.

Covers:
  - _read_effort_overrides: TTL cache (path-keyed), missing file, invalid JSON,
    non-dict top-level, success.
  - _file_effort_for: precedence (project-id-specific > "default"), role mapping
    (codes 1..5, None, unknown int), valid values, invalid values, forward-compat
    object shape.
  - _resolve_effort_for_spawn: full precedence chain with file layer (carrier >
    file > project preset; file > auto — heuristic NOT consulted when file hits;
    carrier PATCH fired on file-resolution with empty carrier; NOT fired when
    carrier set; max from file passes unclamped; auto path still clamps).

No `unittest.mock.patch` import — direct attribute swap + try/finally (mirrors
the existing test_worker_effort.py idiom and avoids changing CPython's memory
layout in ways that can cause id()-based cache invalidation to misbehave in
other test modules).
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import worker


# ---------------------------------------------------------------------------
# Shared harness (mirrors test_worker_effort.py)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_effort_mode_cache(request) -> None:
    # Conftest (autouse) clears _effort_overrides_cache for all tests.
    # This fixture additionally clears the effort_mode cache so project-mode
    # caching from one test doesn't bleed into the next within this module.
    # Uses request.addfinalizer (not yield) — generator fixture objects whose
    # ids are recycled by CPython can collide with make_chat_model lambdas and
    # bypass the id()-based cache guard in nodes.py (#2187-class bug).
    worker._effort_mode_cache_clear()
    request.addfinalizer(worker._effort_mode_cache_clear)


def _cfg() -> SimpleNamespace:
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


@contextmanager
def _override_path(path: str):
    """Swap worker._EFFORT_OVERRIDES_PATH for the duration of a block."""
    orig = worker._EFFORT_OVERRIDES_PATH
    worker._EFFORT_OVERRIDES_PATH = path
    worker._effort_overrides_cache_clear()
    try:
        yield
    finally:
        worker._EFFORT_OVERRIDES_PATH = orig
        worker._effort_overrides_cache_clear()


# ---------------------------------------------------------------------------
# 1. _read_effort_overrides — file reading + TTL cache
# ---------------------------------------------------------------------------


def test_read_effort_overrides_missing_file() -> None:
    """Missing file → None (no crash, debug-level logged)."""
    result = worker._read_effort_overrides(path="/nonexistent/path/effort-overrides.json")
    assert result is None


def test_read_effort_overrides_invalid_json(tmp_path) -> None:
    """Invalid JSON → None (warns, does not raise)."""
    f = tmp_path / "effort-overrides.json"
    f.write_text("not json {{{", encoding="utf-8")
    result = worker._read_effort_overrides(path=str(f))
    assert result is None


def test_read_effort_overrides_top_level_list(tmp_path) -> None:
    """Top-level JSON array (non-dict) → None."""
    f = tmp_path / "effort-overrides.json"
    f.write_text(json.dumps(["frontend", "backend"]), encoding="utf-8")
    result = worker._read_effort_overrides(path=str(f))
    assert result is None


def test_read_effort_overrides_success(tmp_path) -> None:
    """Valid dict file → returns parsed dict."""
    data = {"1": {"frontend": "low"}, "default": {"general": "medium"}}
    f = tmp_path / "effort-overrides.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    result = worker._read_effort_overrides(path=str(f))
    assert result == data


def test_read_effort_overrides_ttl_cache_hits(tmp_path) -> None:
    """Second read within TTL hits cache — assert via file deletion."""
    f = tmp_path / "effort-overrides.json"
    data = {"default": {"general": "low"}}
    f.write_text(json.dumps(data), encoding="utf-8")
    path_str = str(f)

    # First read populates the cache.
    result1 = worker._read_effort_overrides(path=path_str)
    assert result1 == data

    # Delete the file — second read must come from cache (no FileNotFoundError).
    f.unlink()
    result2 = worker._read_effort_overrides(path=path_str)
    assert result2 == data, "second read should hit the TTL cache, not re-read the deleted file"


def test_read_effort_overrides_ttl_expired_rereads(tmp_path) -> None:
    """After TTL expiry the file is re-read."""
    f = tmp_path / "effort-overrides.json"
    f.write_text(json.dumps({"default": {"general": "low"}}), encoding="utf-8")
    path_str = str(f)

    # First read to populate.
    worker._read_effort_overrides(path=path_str)

    # Manually expire the cache entry.
    worker._effort_overrides_cache[path_str] = (
        time.monotonic() - worker._EFFORT_OVERRIDES_TTL_SEC - 1.0,
        None,
    )

    # Update the file — should pick up new content (cache expired).
    new_data = {"default": {"general": "high"}}
    f.write_text(json.dumps(new_data), encoding="utf-8")
    result = worker._read_effort_overrides(path=path_str)
    assert result == new_data, "TTL expired — should have re-read the updated file"


def test_read_effort_overrides_different_paths_separate_cache(tmp_path) -> None:
    """Two different paths = separate cache entries (no cross-path collision)."""
    f1 = tmp_path / "a.json"
    f2 = tmp_path / "b.json"
    f1.write_text(json.dumps({"default": {"general": "low"}}), encoding="utf-8")
    f2.write_text(json.dumps({"default": {"general": "high"}}), encoding="utf-8")

    r1 = worker._read_effort_overrides(path=str(f1))
    r2 = worker._read_effort_overrides(path=str(f2))
    assert r1 != r2
    assert r1["default"]["general"] == "low"
    assert r2["default"]["general"] == "high"


# ---------------------------------------------------------------------------
# 2. _file_effort_for — role mapping + value validation
# ---------------------------------------------------------------------------


def _make_task(role: int | None = None) -> dict[str, Any]:
    return {"id": 42, "assigned_role": role}


def _with_overrides(overrides: dict, tmp_path, task: dict, project_id: int = 1) -> str | None:
    """Write overrides to a tmp file, override the path, call _file_effort_for."""
    f = tmp_path / "effort-overrides.json"
    f.write_text(json.dumps(overrides), encoding="utf-8")
    with _override_path(str(f)):
        return worker._file_effort_for(task, project_id)


def test_file_effort_role_1_frontend(tmp_path) -> None:
    result = _with_overrides({"1": {"frontend": "high"}}, tmp_path, _make_task(1))
    assert result == "high"


def test_file_effort_role_2_backend(tmp_path) -> None:
    result = _with_overrides({"1": {"backend": "low"}}, tmp_path, _make_task(2))
    assert result == "low"


def test_file_effort_role_3_devops(tmp_path) -> None:
    result = _with_overrides({"1": {"devops": "medium"}}, tmp_path, _make_task(3))
    assert result == "medium"


def test_file_effort_role_4_tester(tmp_path) -> None:
    result = _with_overrides({"1": {"tester": "extra"}}, tmp_path, _make_task(4))
    assert result == "extra"


def test_file_effort_role_5_reviewer(tmp_path) -> None:
    result = _with_overrides({"1": {"reviewer": "max"}}, tmp_path, _make_task(5))
    assert result == "max"


def test_file_effort_role_none_maps_to_general(tmp_path) -> None:
    result = _with_overrides({"1": {"general": "low"}}, tmp_path, _make_task(None))
    assert result == "low"


def test_file_effort_role_unknown_int_maps_to_general(tmp_path) -> None:
    result = _with_overrides({"1": {"general": "medium"}}, tmp_path, _make_task(99))
    assert result == "medium"


def test_file_effort_project_specific_beats_default(tmp_path) -> None:
    """project-id key wins over "default"."""
    overrides = {"1": {"backend": "high"}, "default": {"backend": "low"}}
    result = _with_overrides(overrides, tmp_path, _make_task(2), project_id=1)
    assert result == "high"


def test_file_effort_default_used_when_project_key_absent(tmp_path) -> None:
    """When no project-id key, "default" applies."""
    overrides = {"default": {"backend": "medium"}}
    result = _with_overrides(overrides, tmp_path, _make_task(2), project_id=999)
    assert result == "medium"


def test_file_effort_missing_role_in_project_falls_to_default(tmp_path) -> None:
    """Role absent in project block → fall through to "default" block."""
    overrides = {"1": {"frontend": "high"}, "default": {"backend": "low"}}
    result = _with_overrides(overrides, tmp_path, _make_task(2), project_id=1)
    assert result == "low"


def test_file_effort_invalid_string_value_ignored(tmp_path) -> None:
    """'turbo' is not a legal effort value → None (warn, fall through)."""
    result = _with_overrides({"1": {"backend": "turbo"}}, tmp_path, _make_task(2))
    assert result is None


def test_file_effort_invalid_int_value_ignored(tmp_path) -> None:
    """42 is not a legal effort value → None (warn, fall through)."""
    result = _with_overrides({"1": {"backend": 42}}, tmp_path, _make_task(2))
    assert result is None


def test_file_effort_object_with_valid_effort_key_accepted(tmp_path) -> None:
    """Forward-compat: {"effort": "high", ...} → "high" accepted."""
    overrides = {"1": {"backend": {"effort": "high", "model": "opus"}}}
    result = _with_overrides(overrides, tmp_path, _make_task(2))
    assert result == "high"


def test_file_effort_object_without_effort_key_ignored(tmp_path) -> None:
    """Forward-compat: {"model": "opus"} only — no "effort" key → None."""
    overrides = {"1": {"backend": {"model": "opus"}}}
    result = _with_overrides(overrides, tmp_path, _make_task(2))
    assert result is None


def test_file_effort_missing_file_returns_none() -> None:
    """No file present → None (fail-safe D6)."""
    with _override_path("/nonexistent/path.json"):
        result = worker._file_effort_for(_make_task(2), 1)
    assert result is None


# ---------------------------------------------------------------------------
# 3. _resolve_effort_for_spawn — full chain with file layer
# ---------------------------------------------------------------------------


def _make_overrides_file(tmp_path, data: dict) -> str:
    f = tmp_path / "effort-overrides.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    return str(f)


@pytest.mark.asyncio
async def test_carrier_beats_file(tmp_path) -> None:
    """A valid carrier wins even when the file has a value for that role."""
    log = _RequestLog()
    overrides_path = _make_overrides_file(tmp_path, {"1": {"backend": "low"}})
    task = {"id": 42, "assigned_role": 2, "effort_override": "high"}

    with _override_path(overrides_path):
        async with _make_client(_project_handler("auto"), log) as client:
            resolved = await worker._resolve_effort_for_spawn(client, _cfg(), _headers(), task, 1)

    assert resolved == "high"
    # Carrier short-circuits — no project GET, no PATCH.
    assert log.requests == []


@pytest.mark.asyncio
async def test_file_beats_project_preset(tmp_path) -> None:
    """File value wins over project preset (no project GET when file resolves)."""
    log = _RequestLog()
    overrides_path = _make_overrides_file(tmp_path, {"1": {"backend": "extra"}})
    task = {"id": 42, "assigned_role": 2}

    with _override_path(overrides_path):
        async with _make_client(_project_handler("low"), log) as client:
            resolved = await worker._resolve_effort_for_spawn(client, _cfg(), _headers(), task, 1)

    assert resolved == "extra"
    # File resolved — no project GET; carrier PATCH fired.
    methods = [r.method for r in log.requests]
    assert methods == ["PATCH"], methods
    assert _body(log.requests[0]) == {"effort_override": "extra"}


@pytest.mark.asyncio
async def test_file_beats_auto_heuristic_not_consulted(tmp_path) -> None:
    """File wins over 'auto' project mode; the heuristic is NOT consulted."""
    log = _RequestLog()
    overrides_path = _make_overrides_file(tmp_path, {"1": {"backend": "medium"}})
    # task would heuristic→'high' if auto were consulted
    task = {"id": 42, "assigned_role": 2, "model_override": "opus"}

    heuristic_called = []
    orig_heuristic = worker._resolve_auto_effort

    def _spy_heuristic(t):
        heuristic_called.append(t)
        return "high"

    worker._resolve_auto_effort = _spy_heuristic
    try:
        with _override_path(overrides_path):
            async with _make_client(_project_handler("auto"), log) as client:
                resolved = await worker._resolve_effort_for_spawn(client, _cfg(), _headers(), task, 1)
    finally:
        worker._resolve_auto_effort = orig_heuristic

    assert resolved == "medium"
    assert heuristic_called == [], "heuristic must NOT be called when file resolves"
    # File resolved — PATCH fired; no project GET.
    methods = [r.method for r in log.requests]
    assert methods == ["PATCH"], methods


@pytest.mark.asyncio
async def test_file_max_passes_unclamped(tmp_path) -> None:
    """'max' from the file is not clamped (operator control, D4)."""
    log = _RequestLog()
    overrides_path = _make_overrides_file(tmp_path, {"1": {"backend": "max"}})
    task = {"id": 42, "assigned_role": 2}

    with _override_path(overrides_path):
        async with _make_client(_project_handler("auto"), log) as client:
            resolved = await worker._resolve_effort_for_spawn(client, _cfg(), _headers(), task, 1)

    assert resolved == "max"


@pytest.mark.asyncio
async def test_auto_path_still_clamps(tmp_path) -> None:
    """When file misses, auto path's UNCONDITIONAL clamp is unchanged."""
    log = _RequestLog()
    # File present but no entry for this role.
    overrides_path = _make_overrides_file(tmp_path, {"1": {"frontend": "low"}})
    task = {"id": 42, "assigned_role": 2}  # backend — not in file

    orig_heuristic = worker._resolve_auto_effort
    worker._resolve_auto_effort = lambda t: "max"  # hacked heuristic
    try:
        with _override_path(overrides_path):
            async with _make_client(_project_handler("auto"), log) as client:
                resolved = await worker._resolve_effort_for_spawn(client, _cfg(), _headers(), task, 1)
    finally:
        worker._resolve_auto_effort = orig_heuristic

    assert resolved == "extra", f"auto path must clamp 'max' to 'extra'; got {resolved!r}"


@pytest.mark.asyncio
async def test_default_block_used_when_project_key_absent(tmp_path) -> None:
    """When no project-id key, 'default' block applies."""
    log = _RequestLog()
    overrides_path = _make_overrides_file(tmp_path, {"default": {"backend": "medium"}})
    task = {"id": 42, "assigned_role": 2}

    with _override_path(overrides_path):
        async with _make_client(_project_handler("low"), log) as client:
            resolved = await worker._resolve_effort_for_spawn(client, _cfg(), _headers(), task, 1)

    # "default" block resolves → PATCH fired (no project GET).
    assert resolved == "medium"
    methods = [r.method for r in log.requests]
    assert methods == ["PATCH"], methods


@pytest.mark.asyncio
async def test_carrier_patch_fired_when_file_resolves_with_empty_carrier(tmp_path) -> None:
    """When file resolves and carrier is empty, PATCH is fired with file value."""
    log = _RequestLog()
    overrides_path = _make_overrides_file(tmp_path, {"1": {"backend": "high"}})
    task = {"id": 42, "assigned_role": 2}  # no effort_override

    with _override_path(overrides_path):
        async with _make_client(_project_handler("medium"), log) as client:
            resolved = await worker._resolve_effort_for_spawn(client, _cfg(), _headers(), task, 1)

    assert resolved == "high"
    patch_reqs = [r for r in log.requests if r.method == "PATCH"]
    assert len(patch_reqs) == 1
    assert _body(patch_reqs[0]) == {"effort_override": "high"}


@pytest.mark.asyncio
async def test_carrier_patch_not_fired_when_carrier_already_set(tmp_path) -> None:
    """When carrier is already set, no PATCH is fired (carrier short-circuits)."""
    log = _RequestLog()
    overrides_path = _make_overrides_file(tmp_path, {"1": {"backend": "low"}})
    task = {"id": 42, "assigned_role": 2, "effort_override": "extra"}

    with _override_path(overrides_path):
        async with _make_client(_project_handler("auto"), log) as client:
            resolved = await worker._resolve_effort_for_spawn(client, _cfg(), _headers(), task, 1)

    assert resolved == "extra"
    assert log.requests == []


@pytest.mark.asyncio
async def test_no_file_falls_through_to_project_mode(tmp_path) -> None:
    """No file → behavior byte-identical to #2300 (project mode used)."""
    log = _RequestLog()
    task = {"id": 42, "assigned_role": 2}

    with _override_path("/nonexistent/path.json"):
        async with _make_client(_project_handler("high"), log) as client:
            resolved = await worker._resolve_effort_for_spawn(client, _cfg(), _headers(), task, 1)

    assert resolved == "high"
    # Only project GET; no PATCH (preset, not auto).
    assert [r.method for r in log.requests] == ["GET"]
