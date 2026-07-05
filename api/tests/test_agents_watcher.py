"""Tests for the agents-dir change watcher (Kanban #1019).

2 coverage areas, deliberately NOT exercising the live polling loop (no
sleeping on the real ~4s interval):

1. `broker.broadcast(payload)` — delivers to BOTH a wildcard listener
   (project_id=None) and a project-filtered listener (project_id=N), unlike
   `_dispatch` which applies the tasks/projects filter. Each assertion pairs
   a POSITIVE check (the payload arrives) with reading it back via
   `get_nowait()` so a no-op broadcast can't vacuously pass.

2. `compute_dir_signature` — the testable, sleep-free change-detection
   primitive the watch loop diffs on. Same-dir → equal signatures; add /
   remove / touch a `*.md` file → different signature. Underscore-prefixed
   includes are excluded (mirrors the validator/gallery skip rule).

Uses `tmp_path` throughout — the real `.claude/agents/` dir is never touched.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from src.services import agents_watcher as svc
from src.services.row_changed_listener import broker as global_broker


# ---------------------------------------------------------------------------
# 1. broker.broadcast() — wildcard AND project-filtered listeners both receive
# ---------------------------------------------------------------------------


def test_broadcast_reaches_wildcard_listener() -> None:
    """A listener registered with project_id=None (wildcard) receives a
    broadcast payload verbatim."""
    queue = global_broker.add_listener(project_id=None)
    try:
        payload = {"table": "agents", "op": "changed", "ts": "2026-07-05T00:00:00+00:00"}
        global_broker.broadcast(payload)

        received = queue.get_nowait()
        assert received == payload
    finally:
        global_broker.remove_listener(queue)


def test_broadcast_reaches_project_filtered_listener() -> None:
    """A listener registered with a real project_id filter STILL receives a
    broadcast — broadcast() has no per-project filter (agents changes are
    global), unlike `_dispatch`'s tasks/projects filter."""
    queue = global_broker.add_listener(project_id=42)
    try:
        payload = {"table": "agents", "op": "changed", "ts": "2026-07-05T00:00:01+00:00"}
        global_broker.broadcast(payload)

        received = queue.get_nowait()
        assert received == payload
    finally:
        global_broker.remove_listener(queue)


def test_broadcast_with_no_listeners_does_not_raise() -> None:
    """Broadcasting with zero registered listeners is a safe no-op (baseline
    negative control — proves broadcast() doesn't require a listener to
    function, and isn't reading a stale queue from another test)."""
    baseline = len(global_broker._listeners)
    global_broker.broadcast({"table": "agents", "op": "changed", "ts": "x"})
    assert len(global_broker._listeners) == baseline


# ---------------------------------------------------------------------------
# 2. compute_dir_signature — the sleep-free change-detection primitive
# ---------------------------------------------------------------------------


def _write(dir_: Path, filename: str, content: str = "---\nname: x\n---\n") -> Path:
    p = dir_ / filename
    p.write_text(content, encoding="utf-8")
    return p


def test_same_dir_yields_equal_signature(tmp_path: Path) -> None:
    """Two scans of an unchanged directory produce an identical signature —
    the positive baseline every change-detection assertion below leans on."""
    _write(tmp_path, "agent-a.md")
    _write(tmp_path, "agent-b.md")

    sig1 = svc.compute_dir_signature(tmp_path)
    sig2 = svc.compute_dir_signature(tmp_path)

    assert sig1 == sig2
    assert len(sig1) == 2


def test_adding_a_file_changes_signature(tmp_path: Path) -> None:
    _write(tmp_path, "agent-a.md")
    baseline = svc.compute_dir_signature(tmp_path)

    _write(tmp_path, "agent-b.md")
    after_add = svc.compute_dir_signature(tmp_path)

    assert after_add != baseline
    assert len(after_add) == len(baseline) + 1


def test_removing_a_file_changes_signature(tmp_path: Path) -> None:
    _write(tmp_path, "agent-a.md")
    p_b = _write(tmp_path, "agent-b.md")
    baseline = svc.compute_dir_signature(tmp_path)

    p_b.unlink()
    after_remove = svc.compute_dir_signature(tmp_path)

    assert after_remove != baseline
    assert len(after_remove) == len(baseline) - 1


def test_touching_a_file_changes_signature(tmp_path: Path) -> None:
    """Rewriting a file's content (mtime/size change, same filename) must
    change the signature — this is the "someone edited an agent" case the
    watcher exists to catch."""
    p = _write(tmp_path, "agent-a.md", content="---\nname: x\n---\nbody v1\n")
    baseline = svc.compute_dir_signature(tmp_path)

    # Ensure a distinguishable mtime_ns even on coarse filesystem clocks —
    # explicitly set mtime forward rather than sleeping in real time.
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    p.write_text("---\nname: x\n---\nbody v2 (longer content)\n", encoding="utf-8")

    after_touch = svc.compute_dir_signature(tmp_path)

    assert after_touch != baseline
    assert len(after_touch) == len(baseline)  # same file count, different entry


def test_underscore_prefixed_include_is_excluded(tmp_path: Path) -> None:
    """`_dev-shared.md`-style includes are skipped, same rule as the
    validator/gallery — a change to one must NOT affect the signature."""
    _write(tmp_path, "agent-a.md")
    baseline = svc.compute_dir_signature(tmp_path)

    _write(tmp_path, "_dev-shared.md")
    after = svc.compute_dir_signature(tmp_path)

    assert after == baseline


def test_non_md_file_is_excluded(tmp_path: Path) -> None:
    """A non-`.md` file (e.g. a stray `.txt` or `.gitkeep`) in the same
    directory must not affect the signature."""
    _write(tmp_path, "agent-a.md")
    baseline = svc.compute_dir_signature(tmp_path)

    (tmp_path / "README.txt").write_text("not an agent", encoding="utf-8")
    after = svc.compute_dir_signature(tmp_path)

    assert after == baseline


def test_missing_dir_yields_empty_signature(tmp_path: Path) -> None:
    """A nonexistent directory yields an empty signature rather than raising
    — mirrors `validate_agents_dir`'s "missing dir = zero files" posture."""
    missing = tmp_path / "does-not-exist"
    assert svc.compute_dir_signature(missing) == frozenset()


# ---------------------------------------------------------------------------
# start/stop lifecycle — disabled-by-env is a true no-op (no task created)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_agents_watcher_noop_when_sse_disabled(monkeypatch) -> None:
    """When APP_SSE_DISABLE=true (the pytest-session default posture), start
    must NOT create a background task — proven by asserting the module-level
    task handle stays None (positive: a healthy import; negative: no task
    leaks into the pytest event loop)."""
    monkeypatch.setenv("APP_SSE_DISABLE", "true")
    svc._task = None

    await svc.start_agents_watcher()

    assert svc._task is None
    # stop is always safe even when nothing was started.
    await svc.stop_agents_watcher()
