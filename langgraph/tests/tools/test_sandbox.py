"""Kanban #981 — sandbox guard unit tests.

Four guards, each in isolation:

  - fs_boundary_check     — rejects writes outside ctx.working_path
  - apply_output_cap      — truncates ToolResult.output at 100KB
  - check_hard_kill_drift — force-marks failure on duration > timeout * 1.5
  - tool-loop iteration limit — node halts at MAX_TOOL_LOOP_ITERATIONS

The fs-boundary test uses a real tmp_path + symlink so the realpath
resolution is exercised end-to-end (not just patched). The hard-kill
test fakes a ToolResult directly so we don't have to provoke a real
runaway subprocess.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from tools import (
    GLOBAL_REGISTRY,
    MAX_TOOL_LOOP_ITERATIONS,
    OUTPUT_CAP_BYTES,
    OUTPUT_CAP_MARKER,
    TOOL_LOOP_HALT_REASON,
    InvokeContext,
    Tier,
    Tool,
    ToolInput,
    ToolResult,
    apply_output_cap,
    check_hard_kill_drift,
    fs_boundary_check,
)
from tools.sandbox import _tool_writes_to_path_arg


# ---------------------------------------------------------------------------
# fs_boundary_check
# ---------------------------------------------------------------------------


def test_fs_boundary_rejects_path_outside_working_path(tmp_path: Path) -> None:
    """A WRITE-tier tool with a path arg outside working_path returns fs_boundary."""
    file_edit = GLOBAL_REGISTRY.get("file_edit")
    ctx = InvokeContext(working_path=str(tmp_path))
    # Path that resolves outside the working_path.
    outside = tmp_path.parent / "outside.txt"
    args = {"path": str(outside), "old_string": "x", "new_string": "y"}

    result = fs_boundary_check(file_edit, ctx, args)
    assert result is not None
    assert result.success is False
    assert result.error_code == "fs_boundary"
    assert "outside the sandbox" in result.error_msg


def test_fs_boundary_allows_path_inside_working_path(tmp_path: Path) -> None:
    """A path inside working_path passes the boundary check (returns None)."""
    file_edit = GLOBAL_REGISTRY.get("file_edit")
    ctx = InvokeContext(working_path=str(tmp_path))
    inside = tmp_path / "ok.txt"
    args = {"path": str(inside), "old_string": "x", "new_string": "y"}

    assert fs_boundary_check(file_edit, ctx, args) is None


def test_fs_boundary_allows_nested_path_inside_working_path(tmp_path: Path) -> None:
    """A deeply-nested path inside working_path also passes."""
    file_edit = GLOBAL_REGISTRY.get("file_edit")
    ctx = InvokeContext(working_path=str(tmp_path))
    deep = tmp_path / "a" / "b" / "c" / "d.txt"
    args = {"path": str(deep), "old_string": "x", "new_string": "y"}

    assert fs_boundary_check(file_edit, ctx, args) is None


def test_fs_boundary_rejects_dotdot_escape(tmp_path: Path) -> None:
    """A path like `<working_path>/../etc/passwd` resolves outside → reject."""
    file_edit = GLOBAL_REGISTRY.get("file_edit")
    ctx = InvokeContext(working_path=str(tmp_path))
    escape = str(tmp_path / ".." / "escape.txt")
    args = {"path": escape, "old_string": "x", "new_string": "y"}

    result = fs_boundary_check(file_edit, ctx, args)
    assert result is not None
    assert result.error_code == "fs_boundary"


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX symlinks unreliable on Windows runners without privileges",
)
def test_fs_boundary_rejects_symlink_pointing_outside(tmp_path: Path) -> None:
    """A symlink inside working_path that points OUTSIDE → realpath catches it.

    This is the load-bearing case the design lock #949 Q1→A protects:
    realpath resolution happens at check time, so a symlink crafted by
    the LLM via file_write (or pre-existing in the workspace) cannot
    smuggle a write past the boundary.
    """
    workdir = tmp_path / "work"
    workdir.mkdir()
    target = tmp_path / "secret.txt"
    target.write_text("secret\n", encoding="utf-8")
    link = workdir / "link.txt"
    os.symlink(target, link)

    file_edit = GLOBAL_REGISTRY.get("file_edit")
    ctx = InvokeContext(working_path=str(workdir))
    args = {"path": str(link), "old_string": "x", "new_string": "y"}

    result = fs_boundary_check(file_edit, ctx, args)
    assert result is not None
    assert result.error_code == "fs_boundary"


def test_fs_boundary_skipped_for_read_tier_tools(tmp_path: Path) -> None:
    """Read-tier tools (git_status, git_diff) are NOT subject to fs-boundary.

    They don't take a `path` field and they don't mutate; the underlying
    git repo defines its own scope.
    """
    git_status = GLOBAL_REGISTRY.get("git_status")
    assert git_status.tier is Tier.READ
    ctx = InvokeContext(working_path=str(tmp_path))
    # Even with a (hypothetical) path arg, the boundary check skips.
    args: dict[str, Any] = {"path": "/etc/passwd"}
    assert fs_boundary_check(git_status, ctx, args) is None


def test_fs_boundary_skipped_for_git_commit_no_path_field() -> None:
    """git_commit is WRITE-tier but has no `path` field → boundary skips it.

    Documented in `sandbox._tool_writes_to_path_arg`. git_commit operates
    against ctx.repo_root only; the boundary check has nothing to gate on.
    """
    git_commit = GLOBAL_REGISTRY.get("git_commit")
    assert git_commit.tier is Tier.WRITE
    assert _tool_writes_to_path_arg(git_commit) is False


def test_fs_boundary_skipped_when_working_path_is_none() -> None:
    """ctx.working_path=None disables the check (test default)."""
    file_edit = GLOBAL_REGISTRY.get("file_edit")
    ctx = InvokeContext(working_path=None)
    args = {"path": "/anywhere.txt", "old_string": "x", "new_string": "y"}
    assert fs_boundary_check(file_edit, ctx, args) is None


# ---------------------------------------------------------------------------
# apply_output_cap
# ---------------------------------------------------------------------------


def test_output_cap_no_op_on_small_output() -> None:
    """Output under the cap passes through unchanged."""
    r = ToolResult(success=True, output="hello", duration_ms=1)
    out = apply_output_cap(r)
    assert out.output == "hello"


def test_output_cap_no_op_on_none_output() -> None:
    r = ToolResult(success=True, output=None, duration_ms=1)
    out = apply_output_cap(r)
    assert out.output is None


def test_output_cap_truncates_at_100kb() -> None:
    """Output > 100KB is truncated to 100KB + marker."""
    big = "x" * (OUTPUT_CAP_BYTES + 5000)
    r = ToolResult(success=True, output=big, duration_ms=1)
    out = apply_output_cap(r)
    assert out.output is not None
    assert out.output.endswith(OUTPUT_CAP_MARKER)
    # 100KB of payload + the marker.
    encoded = out.output.encode("utf-8")
    assert len(encoded) == OUTPUT_CAP_BYTES + len(OUTPUT_CAP_MARKER.encode("utf-8"))


def test_output_cap_preserves_other_fields() -> None:
    """Truncation only mutates output — success/error_code/duration_ms intact."""
    big = "y" * (OUTPUT_CAP_BYTES + 10)
    r = ToolResult(
        success=True,
        output=big,
        error_code=None,
        error_msg=None,
        retry_safe=True,
        duration_ms=42,
    )
    out = apply_output_cap(r)
    assert out.success is True
    assert out.duration_ms == 42
    assert out.retry_safe is True


def test_output_cap_with_custom_cap_value() -> None:
    """The cap arg is overridable (used in unit tests for tighter limits)."""
    r = ToolResult(success=True, output="abcdef", duration_ms=1)
    out = apply_output_cap(r, cap_bytes=3)
    assert out.output is not None
    assert out.output.startswith("abc")
    assert out.output.endswith(OUTPUT_CAP_MARKER)


# ---------------------------------------------------------------------------
# check_hard_kill_drift
# ---------------------------------------------------------------------------


def test_subprocess_hard_kill_force_marks_failure_on_runaway() -> None:
    """A success result with duration > timeout*1.5 → force-marked failure.

    This is the "subprocess escaped wait_for" scenario. We construct a
    ToolResult with duration_ms exceeding the threshold and verify the
    sandbox force-marks success=False + error_code='hard_kill_drift'.
    """
    shell_run = GLOBAL_REGISTRY.get("shell_run")
    assert shell_run.timeout_sec >= 1
    # 30s class default; threshold = 45s = 45_000ms.
    runaway = ToolResult(
        success=True,
        output="should not have completed",
        duration_ms=60_000,  # 60s > 45s threshold
    )
    out = check_hard_kill_drift(shell_run, runaway, requested_timeout_s=30)
    assert out.success is False
    assert out.error_code == "hard_kill_drift"
    assert "force-marked" in (out.error_msg or "")
    assert out.retry_safe is False


def test_hard_kill_no_op_on_within_threshold() -> None:
    """Duration within timeout*1.5 → result passes through unchanged."""
    shell_run = GLOBAL_REGISTRY.get("shell_run")
    fine = ToolResult(success=True, output="ok", duration_ms=20_000)
    out = check_hard_kill_drift(shell_run, fine, requested_timeout_s=30)
    assert out is fine or (out.success is True and out.output == "ok")


def test_hard_kill_no_op_on_already_failed() -> None:
    """A failed ToolResult is not re-wrapped (the tool already reported failure)."""
    shell_run = GLOBAL_REGISTRY.get("shell_run")
    already_failed = ToolResult(
        success=False,
        error_code="timeout",
        error_msg="exceeded 30s",
        duration_ms=60_000,
    )
    out = check_hard_kill_drift(shell_run, already_failed, requested_timeout_s=30)
    assert out.success is False
    assert out.error_code == "timeout"  # unchanged


def test_hard_kill_no_op_when_no_timeout_configured() -> None:
    """A tool with timeout_sec=0 (or None) bypasses the drift check."""

    class _NoTimeoutInput(ToolInput):
        pass

    class _NoTimeout(Tool):
        name = "__no_timeout_stub__"
        description = "stub"
        tier = Tier.READ
        input_schema = _NoTimeoutInput
        timeout_sec = 0

        async def _run(self, input_obj, context):  # pragma: no cover
            return ToolResult(success=True)

    tool = _NoTimeout()
    r = ToolResult(success=True, output="x", duration_ms=999_999)
    out = check_hard_kill_drift(tool, r, requested_timeout_s=0)
    # No-op — original duration retained.
    assert out.success is True
    assert out.duration_ms == 999_999


# ---------------------------------------------------------------------------
# Tool-loop iteration limit (integration via specialist node)
# ---------------------------------------------------------------------------


def test_tool_loop_iteration_limit_constant_is_five() -> None:
    """The hardcoded V1 limit is locked at 5 per #949 Q3 → A."""
    assert MAX_TOOL_LOOP_ITERATIONS == 5
    assert TOOL_LOOP_HALT_REASON == "tool_loop_max_iterations: 5"


def test_tool_loop_iteration_limit_halts_at_5(monkeypatch) -> None:
    """A model that NEVER stops emitting tool_calls → halts at MAX iterations.

    Drives the full specialist node async path. We stub out:
      - _fetch_tools_config → return an enabled tools_config
      - bind_tools → returns a fake bound model whose ainvoke always
        returns a response with one tool_call (file_edit).
      - record_tool_invocation → no-op (we're not testing audit here).

    After MAX_TOOL_LOOP_ITERATIONS iterations the node must return
    halt_reason=TOOL_LOOP_HALT_REASON.
    """
    import asyncio
    from langchain_core.messages import AIMessage

    import nodes
    from tools import Tier

    # 1) Enable tools so the loop path runs.
    async def _fake_fetch(project_id):
        return {
            "tools_enabled": True,
            "auto_allow_tiers": ["write"],
            "halt_tiers": [],
            "http_hosts": [],
        }

    monkeypatch.setattr(nodes, "_fetch_tools_config", _fake_fetch)

    # 2) Fake model whose ainvoke always returns one tool_call (forever).
    invocation_count = {"n": 0}

    class _ForeverToolCalls:
        async def ainvoke(self, messages):
            invocation_count["n"] += 1
            msg = AIMessage(content="thinking...")
            # langchain's tool_calls slot — dict shape so our handler reads
            # via .get() with isinstance(dict) check.
            msg.tool_calls = [
                {
                    "name": "file_edit",
                    "args": {
                        "path": "/tmp/sandbox-test/foo.txt",
                        "old_string": "x",
                        "new_string": "y",
                    },
                    "id": f"call_{invocation_count['n']}",
                }
            ]
            return msg

    fake_bound = _ForeverToolCalls()

    class _ModelStub:
        def bind_tools(self, tools):
            return fake_bound

        def invoke(self, msgs):  # pragma: no cover (sync fallback unused)
            return AIMessage(content="")

    monkeypatch.setattr(nodes, "make_chat_model", lambda: _ModelStub())

    # 3) Stub audit + fs_boundary so the tool call succeeds without hitting
    #    a real api or a real filesystem.
    async def _fake_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(nodes, "record_tool_invocation", _fake_audit)

    # 4) Stub the tool's invoke method so the loop doesn't actually edit files.
    from tools import GLOBAL_REGISTRY

    file_edit = GLOBAL_REGISTRY.get("file_edit")
    original_invoke = file_edit.invoke

    async def _fake_invoke(input_dict, context=None):
        return ToolResult(success=True, output="stubbed", duration_ms=1)

    file_edit.invoke = _fake_invoke  # type: ignore[method-assign]
    try:
        # 5) Run the node with a non-None working_path so fs_boundary skips
        #    (we patch the path to "" via working_path=None).
        state = {
            "task_id": 1,
            "brief": "loop forever",
            "assigned_role": 2,
        }
        out = asyncio.run(nodes.backend_specialist_node(state))
    finally:
        file_edit.invoke = original_invoke  # type: ignore[method-assign]

    assert out.get("halt_reason") == TOOL_LOOP_HALT_REASON
    assert invocation_count["n"] == MAX_TOOL_LOOP_ITERATIONS
