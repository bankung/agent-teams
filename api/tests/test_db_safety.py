"""Coverage for assert_test_db_or_die helper (L6 prevention — Kanban #1111).

Tests verify:
  1. passes-on-test-db          — primary agent_teams_test DB name passes.
  2. raises-on-live-db          — live agent_teams DB name raises RuntimeError.
  3. raises-on-none             — None database name raises RuntimeError.
  4. passes-on-test-subname     — throwaway migration-smoke DB passes
                                  (agent_teams_test_migration_smoke_abc123).
  5. Mock "poisoned engine"     — simulate session.bind.url.database returning
                                  'agent_teams' and call the first line of a
                                  purge-fixture-shaped coroutine → RuntimeError
                                  raised BEFORE any delete() executes.

All tests are mock-only: no DB connection required.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.helpers.db_safety import assert_test_db_or_die


# ---------------------------------------------------------------------------
# Unit tests for the helper itself
# ---------------------------------------------------------------------------


def _session(db_name: str | None) -> MagicMock:
    """Build a MagicMock session whose bind.url.database returns db_name."""
    session = MagicMock()
    session.bind.url.database = db_name
    return session


def test_assert_passes_on_test_db() -> None:
    """Primary test DB name (agent_teams_test) must be accepted."""
    assert_test_db_or_die(_session("agent_teams_test"))  # no raise


def test_assert_raises_on_live_db() -> None:
    """Live DB name 'agent_teams' must be refused."""
    with pytest.raises(RuntimeError, match="REFUSE TO PURGE.*agent_teams"):
        assert_test_db_or_die(_session("agent_teams"))


def test_assert_raises_on_none_dbname() -> None:
    """None database name (unresolved engine) must be refused."""
    with pytest.raises(RuntimeError, match="REFUSE TO PURGE"):
        assert_test_db_or_die(_session(None))


def test_assert_passes_on_test_subname() -> None:
    """Throwaway migration-smoke DB names must be accepted.

    test_tool_calls.py creates DBs named like
    ``agent_teams_test_migration_smoke_abc123`` — they contain
    ``_test_migration_smoke_`` so they pass the gate even though they don't
    end with ``_test``.
    """
    assert_test_db_or_die(
        _session("agent_teams_test_migration_smoke_abc123")
    )  # no raise


def test_assert_passes_on_underscore_test_suffix() -> None:
    """Any name ending with _test passes (belt-and-suspenders variant)."""
    assert_test_db_or_die(_session("myapp_test"))  # no raise


def test_assert_raises_on_production_like_name() -> None:
    """Production-style names (no _test substring) must always be refused."""
    for name in ("agent_teams_prod", "agent_teams_staging", "mydb", ""):
        with pytest.raises(RuntimeError, match="REFUSE TO PURGE"):
            assert_test_db_or_die(_session(name))


# ---------------------------------------------------------------------------
# AC-4: poisoned engine — purge fixture raises BEFORE any delete()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_fixture_refuses_live_db_before_delete() -> None:
    """Simulate a poisoned engine (session bound to live 'agent_teams').

    We mock SessionLocal so that the async-with block yields a session whose
    bind.url.database is 'agent_teams' (the live DB name).  We then drive a
    minimal async coroutine that mirrors the FIRST action of any retrofitted
    purge fixture (assert_test_db_or_die → execute) and assert that:
      a) RuntimeError is raised by the gate, and
      b) session.execute was NEVER called (no delete() ran).

    This is the canonical poisoning scenario from the 2026-05-17 incident.
    The coroutine is deliberately minimal so this test does NOT depend on the
    exact fixture implementation in the sibling test modules (which cannot be
    called directly as pytest fixtures).
    """
    from sqlalchemy import delete as sa_delete

    # Build a mock session with a live DB name.
    mock_session = AsyncMock()
    mock_session.bind.url.database = "agent_teams"  # poisoned — live DB!

    # AsyncContextManager that yields our mock session.
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    # Minimal coroutine mirroring what every purge fixture does at its opening:
    #   async with SessionLocal() as session:
    #       assert_test_db_or_die(session)  ← gate fires here
    #       await session.execute(delete(SomeModel))  ← must NOT be reached
    async def _purge_like_fixture(session_local_cm) -> None:
        async with session_local_cm as session:
            assert_test_db_or_die(session)  # should raise
            await session.execute(sa_delete(MagicMock()))  # must NOT run

    with pytest.raises(RuntimeError, match="REFUSE TO PURGE"):
        await _purge_like_fixture(mock_ctx)

    # The gate must have fired before any execute() call.
    mock_session.execute.assert_not_called()
