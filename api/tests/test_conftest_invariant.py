"""Coverage for the _live_db_row_count_invariant fail-loud behaviour
(2026-05-17 incident response — L2 prevention).

Strategy: the invariant is an async generator fixture; its internal `_counts`
helper is a closure so we can't patch it by dotted name. Instead we isolate
the same *logic* by re-implementing the minimal variant inline (using the same
pattern as the fixture) and swapping in a controllable `_counts` coroutine.
This avoids touching the live DB and doesn't require pytest-asyncio session
fixtures to be active.
"""

from __future__ import annotations

import warnings
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared helper — re-implements the fixture logic with an injectable _counts
# ---------------------------------------------------------------------------

async def _run_invariant(counts_fn) -> tuple[list[str], list[AssertionError]]:
    """Drive the invariant logic end-to-end using a custom counts_fn.

    Returns (warning_messages, assertion_errors).
    """
    warning_messages: list[str] = []
    assertion_errors: list[AssertionError] = []

    # Replicate the fixture logic exactly (mirrors conftest.py lines 126-179)
    fake_engine = MagicMock()
    fake_engine.dispose = AsyncMock()

    pre = None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")

        try:
            pre = await counts_fn()
        except Exception as _first_exc:
            warnings.warn(
                f"_live_db_row_count_invariant: pre-snapshot failed ({_first_exc!r}). "
                "Live-DB guard DISABLED for this session — row-count drift will NOT be "
                "detected. See 2026-05-17 incident postmortem for why this is loud now.",
                UserWarning,
                stacklevel=2,
            )
            try:
                pre = await counts_fn()
            except Exception as _retry_exc:
                warnings.warn(
                    f"_live_db_row_count_invariant: retry also failed ({_retry_exc!r}). "
                    "Guard staying DISABLED — accepting genuine offline/CI state. "
                    "DISABLED marker: 2026-05-17.",
                    UserWarning,
                    stacklevel=2,
                )
                await fake_engine.dispose()
                # simulate yield + return (guard disabled)
                warning_messages.extend(str(w.message) for w in caught)
                return warning_messages, assertion_errors

        # yield point — simulate post-fixture teardown with a second counts call
        try:
            post = await counts_fn()
        except Exception:
            await fake_engine.dispose()
            warning_messages.extend(str(w.message) for w in caught)
            return warning_messages, assertion_errors
        finally:
            await fake_engine.dispose()

        all_tables = sorted(set(pre) | set(post))
        deltas = {
            t: post.get(t, 0) - pre.get(t, 0)
            for t in all_tables
            if post.get(t, 0) != pre.get(t, 0)
        }
        if deltas:
            delta_lines = "\n".join(
                f"  {t}: {pre.get(t, 0)} -> {post.get(t, 0)} (delta {d:+d})"
                for t, d in sorted(deltas.items())
            )
            err = AssertionError(
                "LIVE DB ROW COUNT DRIFT — pytest wrote to `agent_teams` (the "
                "production DB) during this session.\n"
                f"{delta_lines}"
            )
            assertion_errors.append(err)

    warning_messages.extend(str(w.message) for w in caught)
    return warning_messages, assertion_errors


# ---------------------------------------------------------------------------
# Test 1: pre-snapshot fails once, retry succeeds → one warning, no error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invariant_warns_on_pre_snapshot_failure_then_succeeds() -> None:
    """Simulate transient pre-snapshot failure followed by successful retry.

    Expected:
    - ONE UserWarning emitted containing both 'DISABLED' and '2026-05-17'
    - Fixture proceeds to post-check
    - No AssertionError when pre and post counts match
    """
    call_count = 0

    async def _counts():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("transient blip")
        # retry (call 2) and post-check (call 3) return stable counts
        return {"tasks": 100, "projects": 1}

    warning_messages, assertion_errors = await _run_invariant(_counts)

    # Exactly one warning from the first failure; retry succeeded so no second warn
    assert len(warning_messages) == 1, (
        f"Expected 1 warning (retry succeeded), got {len(warning_messages)}: {warning_messages}"
    )
    assert "DISABLED" in warning_messages[0], (
        f"Warning must contain 'DISABLED': {warning_messages[0]!r}"
    )
    assert "2026-05-17" in warning_messages[0], (
        f"Warning must contain '2026-05-17': {warning_messages[0]!r}"
    )

    # Counts matched (100 == 100) so no drift assertion
    assert assertion_errors == [], (
        f"No AssertionError expected when counts match, got: {assertion_errors}"
    )

    # Verify _counts was called at least twice (first fail + retry)
    assert call_count >= 2, f"Expected at least 2 calls (fail + retry), got {call_count}"


# ---------------------------------------------------------------------------
# Test 2: both pre-snapshot and retry fail → 2 warnings, no AssertionError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invariant_warns_twice_then_skips_when_db_offline() -> None:
    """Simulate fully offline live DB where every _counts() call raises.

    Expected:
    - TWO UserWarnings — one for first failure, one for retry failure
    - Both warnings contain 'DISABLED' and '2026-05-17'
    - No AssertionError fired (guard disabled; offline state accepted)
    """
    async def _counts():
        raise OSError("connection refused — DB offline")

    warning_messages, assertion_errors = await _run_invariant(_counts)

    assert len(warning_messages) == 2, (
        f"Expected 2 warnings (first fail + retry fail), got {len(warning_messages)}: {warning_messages}"
    )
    for i, msg in enumerate(warning_messages):
        assert "DISABLED" in msg, f"Warning {i} must contain 'DISABLED': {msg!r}"
        assert "2026-05-17" in msg, f"Warning {i} must contain '2026-05-17': {msg!r}"

    # Guard disabled — no assertion about DB state
    assert assertion_errors == [], (
        f"Offline guard must not raise AssertionError, got: {assertion_errors}"
    )


# ---------------------------------------------------------------------------
# Test 3: post-snapshot drift → AssertionError with LIVE DB ROW COUNT DRIFT
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invariant_raises_on_post_snapshot_drift() -> None:
    """Simulate a test writing rows to the live DB during the session.

    Expected:
    - No warnings (pre-snapshot succeeds)
    - AssertionError raised on teardown containing 'LIVE DB ROW COUNT DRIFT'
      and delta information
    """
    call_count = 0

    async def _counts():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # pre-snapshot: stable baseline
            return {"tasks": 100, "projects": 1}
        # post-snapshot: tasks grew (simulated live-DB write)
        return {"tasks": 105, "projects": 1}

    warning_messages, assertion_errors = await _run_invariant(_counts)

    assert warning_messages == [], (
        f"No warnings expected when pre-snapshot succeeds, got: {warning_messages}"
    )

    assert len(assertion_errors) == 1, (
        f"Expected exactly 1 AssertionError from drift, got: {assertion_errors}"
    )
    err_msg = str(assertion_errors[0])
    assert "LIVE DB ROW COUNT DRIFT" in err_msg, (
        f"AssertionError must contain 'LIVE DB ROW COUNT DRIFT': {err_msg!r}"
    )
    assert "tasks" in err_msg, (
        f"AssertionError must mention the drifted table 'tasks': {err_msg!r}"
    )
    assert "+5" in err_msg or "105" in err_msg, (
        f"AssertionError must show the delta (+5) or post-count (105): {err_msg!r}"
    )
