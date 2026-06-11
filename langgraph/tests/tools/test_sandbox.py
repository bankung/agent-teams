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
from tools.sandbox import (
    _ALLOWLIST_PATH,
    _RAW_PATH_CAP,
    _allowlist_cache_clear,
    _read_allowlist,
    _safe_raw_path,
    _tool_writes_to_path_arg,
)


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


def test_fs_boundary_none_working_path_elsewhere_asks_where_to_save() -> None:
    """Kanban #2215 — NULL working_path + path outside _scratch → ask-where-to-save.

    This REPLACES the pre-#2215 behaviour where working_path=None disabled the
    check entirely (the gap #2215 closes). A write to an arbitrary path on a
    project with no working_path now returns error_code='working_path_unset'
    so the specialist node can HALT for the operator.
    """
    file_edit = GLOBAL_REGISTRY.get("file_edit")
    ctx = InvokeContext(working_path=None)
    args = {"path": "/anywhere.txt", "old_string": "x", "new_string": "y"}
    result = fs_boundary_check(file_edit, ctx, args)
    assert result is not None
    assert result.success is False
    assert result.error_code == "working_path_unset"
    assert result.retry_safe is False


# ---------------------------------------------------------------------------
# Kanban #2215 — Mode-B fs-tool guard: destination-legitimacy cases
# ---------------------------------------------------------------------------


def _file_write_args(path: str) -> dict[str, Any]:
    """file_write input args targeting `path`."""
    return {"path": path, "content": "x"}


def test_2215_unmounted_working_path_hard_fails(monkeypatch) -> None:
    """working_path SET but NOT an existing dir in the container → unmounted.

    Simulates a Windows host path (or any unmounted bind) configured on the
    project but never mounted into the worker. The guard must FAIL HARD —
    never silently fall through to allowing a /repo write.
    """
    _allowlist_cache_clear()
    monkeypatch.setattr("tools.sandbox._read_allowlist", lambda *a, **k: [])
    file_write = GLOBAL_REGISTRY.get("file_write")
    # A path that does not exist as a directory in this container.
    boundary = "/not/mounted/projectX"
    ctx = InvokeContext(working_path=boundary)
    # Target "inside" the (unmounted) boundary — still must hard-fail because
    # the boundary itself isn't a real dir here.
    result = fs_boundary_check(file_write, ctx, _file_write_args(f"{boundary}/out.txt"))
    assert result is not None
    assert result.error_code == "working_path_unmounted"
    assert result.success is False
    assert result.retry_safe is False
    assert boundary in (result.error_msg or "")
    assert "not mounted" in (result.error_msg or "")


def test_2215_unmounted_windows_host_path_hard_fails(monkeypatch) -> None:
    """A Windows-shaped host path that isn't mounted → working_path_unmounted."""
    _allowlist_cache_clear()
    monkeypatch.setattr("tools.sandbox._read_allowlist", lambda *a, **k: [])
    file_write = GLOBAL_REGISTRY.get("file_write")
    boundary = r"C:\Users\nobody\WebApp\x"
    ctx = InvokeContext(working_path=boundary)
    result = fs_boundary_check(file_write, ctx, _file_write_args(f"{boundary}\\out.txt"))
    assert result is not None
    assert result.error_code == "working_path_unmounted"


def test_2215_null_working_path_scratch_allowed_through(monkeypatch) -> None:
    """NULL working_path + target under <repo_root>/_scratch → allowed (None).

    This is the S5 invariant: project 661 has working_path NULL and writes to
    /repo/_scratch/t5rp-*.txt; the destination guard must let it THROUGH so the
    tier gate still HALTs. The guard returning None == "destination legit".
    """
    _allowlist_cache_clear()
    monkeypatch.setattr("tools.sandbox._read_allowlist", lambda *a, **k: [])
    file_write = GLOBAL_REGISTRY.get("file_write")
    ctx = InvokeContext(working_path=None, repo_root="/repo")
    assert fs_boundary_check(
        file_write, ctx, _file_write_args("/repo/_scratch/t5rp-abc.txt")
    ) is None


def test_2215_null_working_path_scratch_sibling_not_allowed(monkeypatch) -> None:
    """A `/repo/_scratchX` sibling is NOT treated as under `/repo/_scratch`."""
    _allowlist_cache_clear()
    monkeypatch.setattr("tools.sandbox._read_allowlist", lambda *a, **k: [])
    file_write = GLOBAL_REGISTRY.get("file_write")
    ctx = InvokeContext(working_path=None, repo_root="/repo")
    result = fs_boundary_check(
        file_write, ctx, _file_write_args("/repo/_scratchX/sneaky.txt")
    )
    assert result is not None
    assert result.error_code == "working_path_unset"


def test_2215_null_working_path_elsewhere_asks_where_to_save(monkeypatch) -> None:
    """NULL working_path + target outside _scratch + no allowlist → unset HALT."""
    _allowlist_cache_clear()
    monkeypatch.setattr("tools.sandbox._read_allowlist", lambda *a, **k: [])
    file_write = GLOBAL_REGISTRY.get("file_write")
    ctx = InvokeContext(working_path=None, repo_root="/repo")
    result = fs_boundary_check(
        file_write, ctx, _file_write_args("/repo/api/src/models/x.py")
    )
    assert result is not None
    assert result.error_code == "working_path_unset"
    assert result.retry_safe is False
    assert "working_path" in (result.error_msg or "")


def test_2215_allowlist_allows_set_outside_subtree(monkeypatch, tmp_path) -> None:
    """working_path SET, target OUTSIDE subtree but allowlisted → allowed (None)."""
    _allowlist_cache_clear()
    workdir = tmp_path / "work"
    workdir.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    monkeypatch.setattr(
        "tools.sandbox._read_allowlist",
        lambda *a, **k: [os.path.realpath(str(outside))],
    )
    file_write = GLOBAL_REGISTRY.get("file_write")
    ctx = InvokeContext(working_path=str(workdir))
    target = str(outside / "ok.txt")
    assert fs_boundary_check(file_write, ctx, _file_write_args(target)) is None


def test_2215_allowlist_allows_null_working_path_elsewhere(monkeypatch, tmp_path) -> None:
    """NULL working_path, target elsewhere but allowlisted → allowed (None)."""
    _allowlist_cache_clear()
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    monkeypatch.setattr(
        "tools.sandbox._read_allowlist",
        lambda *a, **k: [os.path.realpath(str(allowed_dir))],
    )
    file_write = GLOBAL_REGISTRY.get("file_write")
    ctx = InvokeContext(working_path=None, repo_root="/repo")
    target = str(allowed_dir / "out.txt")
    assert fs_boundary_check(file_write, ctx, _file_write_args(target)) is None


def test_2215_allowlist_file_absent_no_crash(monkeypatch) -> None:
    """A missing allowlist file → empty list, no crash; NULL-elsewhere still HALTs."""
    _allowlist_cache_clear()
    # Point the reader at a path that does not exist.
    assert _read_allowlist("/repo/_runtime/__definitely_missing__.txt") == []
    _allowlist_cache_clear()
    # And the guard still behaves (file absent == no override).
    file_write = GLOBAL_REGISTRY.get("file_write")
    ctx = InvokeContext(working_path=None, repo_root="/repo")
    result = fs_boundary_check(
        file_write, ctx, _file_write_args("/repo/somewhere/x.txt")
    )
    assert result is not None
    assert result.error_code == "working_path_unset"


def test_2215_allowlist_parses_comments_and_blanks(monkeypatch, tmp_path) -> None:
    """The allowlist reader ignores blank lines and `#` comments."""
    _allowlist_cache_clear()
    f = tmp_path / "write-allowlist.txt"
    real_prefix = tmp_path / "allowed"
    real_prefix.mkdir()
    f.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                str(real_prefix),
                "   # indented comment",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    prefixes = _read_allowlist(str(f))
    assert prefixes == [os.path.realpath(str(real_prefix))]


def test_2215_set_working_path_subtree_still_ok_when_mounted(tmp_path, monkeypatch) -> None:
    """Regression: working_path SET (and mounted) + inside subtree → still None."""
    _allowlist_cache_clear()
    monkeypatch.setattr("tools.sandbox._read_allowlist", lambda *a, **k: [])
    file_write = GLOBAL_REGISTRY.get("file_write")
    ctx = InvokeContext(working_path=str(tmp_path))
    assert fs_boundary_check(
        file_write, ctx, _file_write_args(str(tmp_path / "ok.txt"))
    ) is None


def test_2215_set_working_path_outside_subtree_when_mounted(tmp_path, monkeypatch) -> None:
    """working_path SET + mounted + target outside (no allowlist) → fs_boundary."""
    _allowlist_cache_clear()
    monkeypatch.setattr("tools.sandbox._read_allowlist", lambda *a, **k: [])
    file_write = GLOBAL_REGISTRY.get("file_write")
    ctx = InvokeContext(working_path=str(tmp_path))
    outside = tmp_path.parent / "outside.txt"
    result = fs_boundary_check(file_write, ctx, _file_write_args(str(outside)))
    assert result is not None
    assert result.error_code == "fs_boundary"
    # Error message suggests the in-subtree _scratch for temp files (rule 8).
    assert "_scratch" in (result.error_msg or "")


# ---------------------------------------------------------------------------
# Kanban #2215 in-gate hardening (W-1 path-disclosure, N-1 raw_path cap, M1 cache key)
# ---------------------------------------------------------------------------


def test_2215_unset_result_does_not_disclose_allowlist_path(monkeypatch) -> None:
    """W-1: the LLM-facing unset error must NOT print the exact allowlist file.

    Naming `_ALLOWLIST_PATH` back to the model teaches a drifting agent the
    self-grant recipe. The message must point at the operator generically.
    """
    _allowlist_cache_clear()
    monkeypatch.setattr("tools.sandbox._read_allowlist", lambda *a, **k: [])
    file_write = GLOBAL_REGISTRY.get("file_write")
    ctx = InvokeContext(working_path=None, repo_root="/repo")
    result = fs_boundary_check(
        file_write, ctx, _file_write_args("/repo/api/src/models/x.py")
    )
    assert result is not None
    msg = result.error_msg or ""
    # NEGATIVE: the literal allowlist path is absent from the model-facing text.
    assert _ALLOWLIST_PATH not in msg
    assert "write-allowlist.txt" not in msg
    # POSITIVE: still actionable — names the operator + the two remediations.
    assert "operator" in msg
    assert "working_path" in msg
    assert "write-allowlist" in msg  # generic reference, no path


def test_2215_outside_subtree_result_does_not_disclose_allowlist_path(tmp_path, monkeypatch) -> None:
    """W-1: the SET-but-outside (fs_boundary) message also omits the allowlist path."""
    _allowlist_cache_clear()
    monkeypatch.setattr("tools.sandbox._read_allowlist", lambda *a, **k: [])
    file_write = GLOBAL_REGISTRY.get("file_write")
    ctx = InvokeContext(working_path=str(tmp_path))
    outside = tmp_path.parent / "outside.txt"
    result = fs_boundary_check(file_write, ctx, _file_write_args(str(outside)))
    assert result is not None
    assert result.error_code == "fs_boundary"
    msg = result.error_msg or ""
    assert _ALLOWLIST_PATH not in msg
    assert "write-allowlist.txt" not in msg


def test_2215_safe_raw_path_caps_long_input() -> None:
    """N-1: an over-budget raw_path is truncated with an explicit marker."""
    long_path = "/repo/" + ("a" * (_RAW_PATH_CAP + 200))
    out = _safe_raw_path(long_path)
    assert len(out) == _RAW_PATH_CAP + len("...[trunc]")
    assert out.endswith("...[trunc]")


def test_2215_safe_raw_path_strips_non_printable() -> None:
    """N-1: control / non-printable chars in raw_path are replaced, Thai kept."""
    dirty = "/repo/x\x00\x07\x1b[2J/แฟ้ม.txt"
    out = _safe_raw_path(dirty)
    # Control chars gone (replaced with '?'); the Thai filename survives.
    assert "\x00" not in out
    assert "\x1b" not in out
    assert "\x07" not in out
    assert "แฟ้ม" in out


def test_2215_unset_result_embeds_capped_sanitized_raw_path(monkeypatch) -> None:
    """N-1: the error message embeds the SANITIZED+CAPPED raw_path, not the raw one."""
    _allowlist_cache_clear()
    monkeypatch.setattr("tools.sandbox._read_allowlist", lambda *a, **k: [])
    file_write = GLOBAL_REGISTRY.get("file_write")
    ctx = InvokeContext(working_path=None, repo_root="/repo")
    evil = "/repo/danger/\x1b[2Jboom" + ("z" * (_RAW_PATH_CAP + 100)) + ".py"
    result = fs_boundary_check(file_write, ctx, _file_write_args(evil))
    assert result is not None
    msg = result.error_msg or ""
    # The raw control sequence never reaches the LLM-facing string.
    assert "\x1b" not in msg
    # The full untruncated raw path is NOT present (it was capped).
    assert evil not in msg
    assert "...[trunc]" in msg


def test_2215_allowlist_cache_keyed_by_path(tmp_path, monkeypatch) -> None:
    """M1: the allowlist cache is keyed by `path` — distinct paths don't collide.

    Read path A (populated), then path B (empty) within the TTL window; B must
    NOT serve A's cached prefixes (the old time-only key would have).
    """
    _allowlist_cache_clear()
    a = tmp_path / "allow-a.txt"
    allowed = tmp_path / "granted"
    allowed.mkdir()
    a.write_text(str(allowed) + "\n", encoding="utf-8")
    b = tmp_path / "allow-b.txt"  # does not exist on disk

    prefixes_a = _read_allowlist(str(a))
    assert prefixes_a == [os.path.realpath(str(allowed))]
    # Same TTL window, different path → must re-read (missing file → []),
    # NOT return A's cached prefixes.
    prefixes_b = _read_allowlist(str(b))
    assert prefixes_b == []
    # And A is still cached under its own key (not clobbered by the B read).
    assert _read_allowlist(str(a)) == [os.path.realpath(str(allowed))]


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
                    # NULL working_path (no env-override, no API value in this
                    # test) → the #2215 destination guard allows /repo/_scratch
                    # writes THROUGH to the tier gate, so the loop can run. A
                    # path elsewhere would now HALT with working_path_unset on
                    # the first iteration (covered by its own test).
                    "args": {
                        "path": "/repo/_scratch/sandbox-test/foo.txt",
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
