"""Test-DB safety helpers — defense against destructive-fixture wipe class.

See context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md.

Rule (repo-wide grep enforcement):
  Any test fixture that issues delete(Task), delete(Project), delete(TaskHistory),
  or delete(SessionCompact) MUST call assert_test_db_or_die(session) as its
  FIRST action inside the async-with SessionLocal() block — before any ORM delete.

  Grep pattern to audit compliance (bash):
    grep -n "delete(Task" api/tests/*.py
    grep -n "delete(Project" api/tests/*.py
    grep -n "delete(TaskHistory" api/tests/*.py
    grep -n "delete(SessionCompact" api/tests/*.py

  Every match MUST be preceded (within the same fixture) by assert_test_db_or_die.
  New matches without the guard → file an exemption in this docstring or add the guard.
"""

# Patterns that identify a safe test database name.
# Any database name containing one of these substrings is approved for purge.
#
# "_test"                   — primary test DB (agent_teams_test)
# "_test_migration_smoke_"  — throwaway DB created in test_tool_calls.py for
#                             migration-smoke exercises; ends with a random suffix
#                             (e.g., agent_teams_test_migration_smoke_abc123) so it
#                             does NOT end with "_test" — but it CONTAINS the pattern.
_TEST_DB_PATTERNS = (
    "_test",
    "_test_migration_smoke_",
)


def assert_test_db_or_die(session) -> None:
    """Refuse to proceed if session is NOT bound to a recognised _test DB.

    Called as the FIRST line of every purge fixture.  Catches the class of
    bug where SessionLocal binds to the live ``agent_teams`` DB via lru_cache
    poisoning, import-order race, env mutation, or any other mechanism.

    Raises ``RuntimeError`` loudly so the failure is visible in pytest output,
    not silently skipped or hidden in a fixture chain.

    Args:
        session: Any SQLAlchemy session-like object whose ``bind.url.database``
                 attribute holds the database name string (or None).  Works
                 with both sync and async SQLAlchemy sessions when called with
                 the session object directly (not the context-manager).

    Raises:
        RuntimeError: If the database name does not match any entry in
                      ``_TEST_DB_PATTERNS``.
    """
    db_name: str = session.bind.url.database or ""
    if not any(pattern in db_name for pattern in _TEST_DB_PATTERNS):
        raise RuntimeError(
            f"REFUSE TO PURGE: session bound to {db_name!r} — "
            "purge fixtures must run on a _test DB only. "
            "If this is unexpected, the conftest DATABASE_URL rewrite "
            "did NOT take effect for this fixture's SessionLocal binding. "
            "See context/projects/agent-teams/shared/incidents/"
            "2026-05-17-dev-db-wipe.md"
        )
