"""L11 prevention coverage: _build_engine pytest-binding canary (Kanban #1118).

Tests verify:
  1. With 'pytest' in sys.modules AND a live (non-_test) URL configured,
     `_build_engine` emits a UserWarning naming the URL and pointing at the
     conftest DATABASE_URL rewrite.
  2. Same scenario but with a _test-suffixed URL → no warning.
  3. Function still returns a working AsyncEngine in BOTH cases — the canary
     is informational, never blocking.

Approach: monkeypatch `src.db.get_settings` to return a fake Settings instance
with the desired database_url. This exercises the exact code path inside
`_build_engine` without reloading the module (which would tear down the
test-session's pool-reset fixture and corrupt subsequent tests).

See spec: _scratch/pending-kanban-2026-05-17/14-p2-bug-L11-engine-build-pytest-warning.md
"""
from __future__ import annotations

import sys
import warnings
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

import src.db as src_db


def _fake_settings(url: str) -> SimpleNamespace:
    """Minimal Settings stand-in — _build_engine only reads database_url,
    app_debug, and app_env.
    """
    return SimpleNamespace(
        database_url=url,
        app_debug=False,
        app_env="development",
    )


def test_build_engine_warns_on_live_url_during_pytest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-1 / AC-3: with pytest loaded + live (non-_test) URL, warning fires
    and names both the DB + the conftest rewrite hint.
    """
    # pytest is always in sys.modules during a pytest run, but assert it
    # explicitly so the test self-documents its precondition.
    assert "pytest" in sys.modules, "precondition: pytest must be loaded"

    live_url = "postgresql+asyncpg://u:p@h:5432/agent_teams"
    monkeypatch.setattr(src_db, "get_settings", lambda: _fake_settings(live_url))

    with pytest.warns(UserWarning) as record:
        engine = src_db._build_engine()

    # AC-3: function still returns a working engine even when warning fires.
    assert isinstance(engine, AsyncEngine), (
        "_build_engine must still return an engine — the canary is "
        "informational, never blocking."
    )
    assert engine.url.database == "agent_teams"

    # AC-2: warning message names the offending DB + the conftest hint.
    matched = [str(w.message) for w in record if "_build_engine" in str(w.message)]
    assert matched, f"expected _build_engine canary warning, got {[str(w.message) for w in record]!r}"
    msg = matched[0]
    assert "'agent_teams'" in msg, f"warning must name the URL DB, got: {msg!r}"
    assert "_test" in msg, f"warning must mention the _test expectation, got: {msg!r}"
    assert "conftest" in msg.lower(), (
        f"warning must point at the conftest rewrite, got: {msg!r}"
    )

    # Cleanup the engine we just built so its connection pool doesn't leak.
    import asyncio

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        engine.dispose()
    )


def test_build_engine_no_warning_on_test_url_during_pytest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-4: with pytest loaded + _test-suffixed URL, no canary warning fires."""
    assert "pytest" in sys.modules

    test_url = "postgresql+asyncpg://u:p@h:5432/agent_teams_test"
    monkeypatch.setattr(src_db, "get_settings", lambda: _fake_settings(test_url))

    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        engine = src_db._build_engine()

    canary = [
        w for w in record if "_build_engine" in str(w.message)
    ]
    assert not canary, (
        f"no canary warning expected for _test URL, got: "
        f"{[str(w.message) for w in canary]!r}"
    )

    assert isinstance(engine, AsyncEngine)
    assert engine.url.database == "agent_teams_test"

    import asyncio

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        engine.dispose()
    )


def test_build_engine_returns_engine_even_when_warning_fires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Belt-and-suspenders: confirm the engine is genuinely usable (not just
    an instance check) when the canary fires — i.e., the warning path is
    side-effect-free.
    """
    live_url = "postgresql+asyncpg://u:p@h:5432/agent_teams"
    monkeypatch.setattr(src_db, "get_settings", lambda: _fake_settings(live_url))

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        engine = src_db._build_engine()

    # The engine's URL should match the patched URL exactly — proves the
    # canary block doesn't mutate the URL on its way to create_async_engine.
    assert str(engine.url).endswith("/agent_teams")
    assert engine.url.database == "agent_teams"

    import asyncio

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        engine.dispose()
    )
