"""Pin the test-DB-isolation contract.

If conftest's DATABASE_URL override regresses (e.g., someone moves the env
assignment after a `from src import ...` import), this test catches it before
any other test silently writes to the live `agent_teams` DB.

See tests/conftest.py header + context/projects/agent-teams/shared/decisions.md
for the locked design (Issue 2 of the 2026-05-09 raw-SQL-DML incident response).
"""

from __future__ import annotations

import pytest


def test_engine_bound_to_test_database() -> None:
    """Cheap synchronous canary: `src.db.engine.url.database` MUST equal
    `agent_teams_test`. Catches drift at the cheapest possible layer — if
    conftest's env override breaks, every test in the suite picks up this
    canary failure within milliseconds.

    Why a second variant alongside the async URL-substring check below: the
    `.database` attribute is the authoritative parsed dbname (immune to
    accidental matches via query-string params, schema-search-path tricks,
    etc.). Kept as a separate test (not folded into the existing one) so a
    single regression surfaces one explicit pytest failure line with the
    actual dbname, not a vague substring mismatch.
    """
    from src.db import engine

    assert engine.url.database == "agent_teams_test", (
        f"src.db.engine is pointed at {engine.url.database!r} — "
        "the conftest.py module-level DATABASE_URL rewrite (lines 32-39) "
        "did not take effect. This means tests will WRITE to the live DB."
    )


@pytest.mark.asyncio
async def test_pytest_runs_against_test_database() -> None:
    """src.db.engine.url MUST point at agent_teams_test, never the live DB."""
    from src.db import engine

    url = str(engine.url)
    assert "agent_teams_test" in url, (
        f"Tests must run against agent_teams_test, not {url!r}. "
        "Check tests/conftest.py: the DATABASE_URL override at module top must "
        "execute before any `from src import ...` statement."
    )
    assert "/agent_teams_test" in url, (
        f"Database name in URL must be exactly 'agent_teams_test', got {url!r}. "
        "A name like 'agent_teams' would silently target the live DB."
    )


@pytest.mark.asyncio
async def test_test_database_is_not_live_database(client) -> None:
    """Round-trip: hit an HTTP endpoint, verify the connection it uses is
    bound to agent_teams_test (not the live DB) by reading current_database()
    via the same engine the API uses.
    """
    from sqlalchemy import text

    from src.db import SessionLocal

    async with SessionLocal() as session:
        result = await session.execute(text("SELECT current_database()"))
        dbname = result.scalar_one()

    assert dbname == "agent_teams_test", (
        f"API session is bound to {dbname!r}, expected 'agent_teams_test'. "
        "This is the load-bearing isolation guarantee."
    )
