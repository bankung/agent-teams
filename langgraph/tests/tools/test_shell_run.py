"""shell_run — allowlist match, denylist refusal, timeout, composite refusal.

Locked decision (Kanban #949 Q5 → A): denylist is HARDCODED. These tests pin
that — any future PR that loosens DENYLIST in source will trip a test here.
"""

from __future__ import annotations

import pytest

from tools import GLOBAL_REGISTRY
from tools.base import InvokeContext


async def test_allowlist_match_runs(tmp_path):
    """`pytest --version` is on the allowlist (`pytest` prefix matches) and
    pytest itself is installed in the test runtime, so this is a real
    end-to-end exec."""
    tool = GLOBAL_REGISTRY.get("shell_run")
    ctx = InvokeContext(repo_root=str(tmp_path))
    result = await tool.invoke({"cmd": "pytest --version", "timeout_s": 10}, context=ctx)
    assert result.success is True, result.error_msg
    assert "pytest" in (result.output or "").lower()


async def test_denylist_first_token_halts(tmp_path):
    """`rm -rf /tmp/foo` — first token `rm` is on the denylist."""
    tool = GLOBAL_REGISTRY.get("shell_run")
    ctx = InvokeContext(repo_root=str(tmp_path))
    result = await tool.invoke({"cmd": "rm -rf /tmp/foo"}, context=ctx)
    assert result.success is False
    assert result.error_code == "blocked_command"


async def test_denylist_blocks_even_if_allowlist_prefix(tmp_path):
    """A composite like `pytest && rm -rf /` MUST halt — the allowlist match
    on `pytest` must NOT permit the chained `rm`. Our defense is the
    shell-control char check (which catches `&&` before tokenization)."""
    tool = GLOBAL_REGISTRY.get("shell_run")
    ctx = InvokeContext(repo_root=str(tmp_path))
    result = await tool.invoke({"cmd": "pytest && rm -rf /tmp/x"}, context=ctx)
    assert result.success is False
    assert result.error_code == "shell_control_forbidden"


async def test_shell_pipe_halts(tmp_path):
    tool = GLOBAL_REGISTRY.get("shell_run")
    ctx = InvokeContext(repo_root=str(tmp_path))
    result = await tool.invoke({"cmd": "pytest --version | grep py"}, context=ctx)
    assert result.success is False
    assert result.error_code == "shell_control_forbidden"


async def test_shell_subshell_halts(tmp_path):
    tool = GLOBAL_REGISTRY.get("shell_run")
    ctx = InvokeContext(repo_root=str(tmp_path))
    result = await tool.invoke({"cmd": "pytest $(echo --version)"}, context=ctx)
    assert result.success is False
    assert result.error_code == "shell_control_forbidden"


async def test_shell_redirect_halts(tmp_path):
    tool = GLOBAL_REGISTRY.get("shell_run")
    ctx = InvokeContext(repo_root=str(tmp_path))
    result = await tool.invoke({"cmd": "pytest --version > /tmp/x"}, context=ctx)
    assert result.success is False
    assert result.error_code == "shell_control_forbidden"


async def test_non_allowlisted_prefix_halts(tmp_path):
    """`ls -la` — `ls` is neither on the allowlist nor the denylist."""
    tool = GLOBAL_REGISTRY.get("shell_run")
    ctx = InvokeContext(repo_root=str(tmp_path))
    result = await tool.invoke({"cmd": "ls -la"}, context=ctx)
    assert result.success is False
    assert result.error_code == "command_not_allowed"


async def test_multi_token_allowlist_prefix(tmp_path):
    """`python -m pytest --version` — three-token prefix match. Should run
    successfully if pytest is installed."""
    tool = GLOBAL_REGISTRY.get("shell_run")
    ctx = InvokeContext(repo_root=str(tmp_path))
    result = await tool.invoke(
        {"cmd": "python -m pytest --version", "timeout_s": 10}, context=ctx
    )
    assert result.success is True, result.error_msg


async def test_timeout_triggers(tmp_path):
    """`pytest` invoked with a deliberately-too-short timeout. We use
    `pytest --collect-only some-nonexistent-path` which exits quickly but
    with non-zero; to test timeout itself we'd need a slow allowlisted
    command. Easiest sleep-style: invoke `pytest --basetemp=...` doing
    something… actually simpler — install a custom allowlist via a fake
    tool — out of scope. Instead, assert the timeout field shape works
    by invoking with a high timeout that completes fast."""
    tool = GLOBAL_REGISTRY.get("shell_run")
    ctx = InvokeContext(repo_root=str(tmp_path))
    result = await tool.invoke(
        {"cmd": "pytest --version", "timeout_s": 30}, context=ctx
    )
    # Sanity: timeout field accepted, command ran
    assert result.success is True


async def test_timeout_actually_fires_with_slow_command(monkeypatch, tmp_path):
    """Patch ALLOWLIST to include `sleep` so we can prove the timeout path."""
    from tools.shell import shell_run as sr_mod

    monkeypatch.setattr(sr_mod, "ALLOWLIST", sr_mod.ALLOWLIST + (("sleep",),))
    tool = GLOBAL_REGISTRY.get("shell_run")
    ctx = InvokeContext(repo_root=str(tmp_path))
    result = await tool.invoke({"cmd": "sleep 5", "timeout_s": 1}, context=ctx)
    assert result.success is False
    assert result.error_code == "timeout"


async def test_empty_cmd_rejected(tmp_path):
    tool = GLOBAL_REGISTRY.get("shell_run")
    ctx = InvokeContext(repo_root=str(tmp_path))
    result = await tool.invoke({"cmd": "   "}, context=ctx)
    assert result.success is False
    assert result.error_code == "invalid_input"


async def test_tier_is_destructive():
    tool = GLOBAL_REGISTRY.get("shell_run")
    assert tool.tier.value == "destructive"


async def test_denylist_contents_locked():
    """Pin the denylist tuple — any source-level change to soften DENYLIST
    must update this test, ensuring code review sees the security implication."""
    from tools.shell.shell_run import DENYLIST

    assert DENYLIST == (
        "rm",
        "sudo",
        "kill",
        "dd",
        "mkfs",
        "chmod",
        "chown",
        "mv",
        "cp",
    )


# ---------------------------------------------------------------------------
# Kanban #2503 — Fix 3: docker compose exec removed from allowlist
# ---------------------------------------------------------------------------


async def test_docker_compose_exec_rejected(tmp_path):
    """Fix 3: 'docker compose exec <service> <cmd>' must be rejected.

    The prefix ("docker", "compose", "exec") was removed from ALLOWLIST because
    it permitted ANY sub-command — including denylist-bypassing ones like
    'docker compose exec api rm -rf /'. (Kanban #2503)

    NEGATIVE (the lock): any docker compose exec variant must return
    command_not_allowed, not pass through.
    """
    tool = GLOBAL_REGISTRY.get("shell_run")
    ctx = InvokeContext(repo_root=str(tmp_path))

    for cmd in [
        "docker compose exec api python -m scripts.seed",
        "docker compose exec api alembic upgrade head",
        "docker compose exec api bash",
    ]:
        result = await tool.invoke({"cmd": cmd}, context=ctx)
        assert result.success is False, f"expected failure for: {cmd!r}"
        assert result.error_code == "command_not_allowed", (
            f"expected command_not_allowed, got {result.error_code!r} for: {cmd!r}"
        )


async def test_allowlist_contents_locked():
    """Pin the allowlist tuple — any addition must update this test, ensuring
    code review sees the security implication. (Kanban #2503: removed docker
    compose exec.)"""
    from tools.shell.shell_run import ALLOWLIST

    assert ALLOWLIST == (
        ("pytest",),
        ("pnpm", "test"),
        ("npm", "test"),
        ("tsc",),
        ("npm", "run", "build"),
        ("pnpm", "run", "build"),
        ("python", "-m", "pytest"),
        ("git", "status"),
        ("git", "diff"),
    )
