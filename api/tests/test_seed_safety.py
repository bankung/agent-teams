"""L3 prevention layer for the 2026-05-17 dev DB wipe incident.

`scripts.seed._seed()` gained a defensive URL gate: it MUST raise
RuntimeError if the resolved engine URL's dbname does not end with `_test`
AND `SEED_TARGET` env var is not set to `"production"`. This catches the
class of bug where conftest's `DATABASE_URL` rewrite races a poisoned
`get_settings()` cache and `_seed()` would otherwise wipe-then-reseed the
live `agent_teams` DB.

These tests mock `src.db.engine` and `src.db.SessionLocal` — they do NOT
hit any real database and are safe to run in any environment.

See `context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md`
for the incident chain.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _fake_engine_with_dbname(dbname: str) -> MagicMock:
    """Build a mock that mimics `src.db.engine` enough for `_seed()`'s URL gate.

    The gate reads `engine.url` (stringified for the error message) and
    `engine.url.database`. A two-level MagicMock satisfies both.
    """
    fake_url = MagicMock()
    fake_url.database = dbname
    fake_url.__str__ = lambda self: (
        f"postgresql+asyncpg://postgres:postgres@db:5432/{dbname}"
    )

    fake_engine = MagicMock()
    fake_engine.url = fake_url
    return fake_engine


@pytest.mark.asyncio
async def test_seed_refuses_live_db_without_override(monkeypatch) -> None:
    """_seed() must raise RuntimeError when the engine URL targets a non-_test
    DB and `SEED_TARGET` is unset. This is the load-bearing defensive gate."""
    from scripts import seed as seed_module

    # Ensure SEED_TARGET is NOT set (delenv raising=False makes this idempotent).
    monkeypatch.delenv("SEED_TARGET", raising=False)

    fake_engine = _fake_engine_with_dbname("agent_teams")  # the LIVE DB name
    fake_session_local = MagicMock()  # should never be called

    # Patch the lazy-imported symbols at their source. `_seed()` does
    # `from src.db import SessionLocal, engine` inside the function body —
    # so the patch target is `src.db.engine` / `src.db.SessionLocal`.
    with patch("src.db.engine", fake_engine), patch(
        "src.db.SessionLocal", fake_session_local
    ):
        with pytest.raises(RuntimeError) as excinfo:
            await seed_module._seed()

    msg = str(excinfo.value)
    # Greppable markers — audit script (or future operator post-mortem)
    # can locate the gate via these strings.
    assert "2026-05-17" in msg, (
        f"Error message must reference the 2026-05-17 incident postmortem; got: {msg!r}"
    )
    assert "SEED_TARGET=production" in msg, (
        f"Error message must instruct operator how to override; got: {msg!r}"
    )
    assert "agent_teams" in msg, (
        f"Error message must include the offending DB name; got: {msg!r}"
    )

    # And: the gate must fire BEFORE any DB operation — SessionLocal must
    # never have been called as a context manager.
    fake_session_local.assert_not_called()


@pytest.mark.asyncio
async def test_seed_allows_production_with_explicit_flag(monkeypatch) -> None:
    """With `SEED_TARGET=production`, _seed() must NOT raise the gate's
    RuntimeError — operator has explicitly acknowledged production targeting.

    We mock SessionLocal to return a context manager whose `__aenter__` raises
    a sentinel exception. If the gate passes, execution reaches the
    `async with SessionLocal()` line and our sentinel fires — confirming the
    gate did NOT block. If the gate erroneously raises RuntimeError, our
    sentinel never fires and the test fails loudly.
    """
    from scripts import seed as seed_module

    monkeypatch.setenv("SEED_TARGET", "production")

    fake_engine = _fake_engine_with_dbname("agent_teams")  # live name, but flag set

    class _SentinelReached(Exception):
        """Raised inside SessionLocal().__aenter__ to prove the gate passed."""

    class _FakeSessionLocalCtx:
        async def __aenter__(self):  # noqa: D401
            raise _SentinelReached("gate passed — _seed body entered")

        async def __aexit__(self, *args):
            return False

    fake_session_local = MagicMock(return_value=_FakeSessionLocalCtx())

    with patch("src.db.engine", fake_engine), patch(
        "src.db.SessionLocal", fake_session_local
    ):
        # The gate must NOT raise — but we expect _SentinelReached from the
        # mocked SessionLocal context. Any RuntimeError from the gate would
        # surface here as a test failure (wrong exception type).
        with pytest.raises(_SentinelReached):
            await seed_module._seed()

    # And: SessionLocal WAS invoked — proves the gate passed and execution
    # reached the `async with SessionLocal()` line.
    fake_session_local.assert_called_once()
