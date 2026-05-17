"""Tests for the L7 DATABASE_URI validation gate (Kanban #1112).

The validator is pure (no DB, no env mutation in steady state) — it inspects
the URI string and raises RuntimeError if the search_path hint is missing or
the db name is not in the allowlist. Tests are mock-free; they call
`_validate_database_uri` directly with crafted URIs.

The allowlist env-var (`LANGGRAPH_DB_NAME_ALLOWLIST`) is read at module
import time into `ALLOWED_LANGGRAPH_DB_NAMES`. The override test patches
the module-level set in-place rather than re-importing — re-importing
`graph` would re-run side-effects (no DB, but the module also defines the
FastAPI app, which is heavy and unnecessary for this test).
"""

from __future__ import annotations

import pytest

from graph import ALLOWED_LANGGRAPH_DB_NAMES, _validate_database_uri


def test_missing_search_path_raises() -> None:
    """URI lacks the `search_path=langgraph` hint → RuntimeError."""
    with pytest.raises(RuntimeError, match="search_path=langgraph"):
        _validate_database_uri("postgresql://u:p@h:5432/agent_teams")


def test_wrong_db_name_raises() -> None:
    """URI has search_path but db is not in the default allowlist."""
    bad_uri = "postgresql://u:p@h:5432/wrong_db?options=-c%20search_path=langgraph"
    with pytest.raises(RuntimeError, match="not in the allowlist"):
        _validate_database_uri(bad_uri)


def test_canonical_agent_teams_passes() -> None:
    """The compose-shipped canonical URI must validate."""
    _validate_database_uri(
        "postgresql://u:p@h:5432/agent_teams?options=-c%20search_path=langgraph"
    )


def test_agent_teams_test_passes() -> None:
    """The test-db variant (CI / pytest) must also validate by default."""
    _validate_database_uri(
        "postgresql://u:p@h:5432/agent_teams_test?options=-c%20search_path=langgraph"
    )


def test_malformed_uri_no_db_segment_raises() -> None:
    """A URI without a `/<db>` path segment should be rejected cleanly."""
    # Contrived: has search_path token but no path; the regex won't match.
    bad_uri = "postgresql://u:p@h:5432?options=-c%20search_path=langgraph"
    with pytest.raises(RuntimeError, match="not look like a valid postgres URL"):
        _validate_database_uri(bad_uri)


def test_allowlist_override_via_monkeypatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator can extend the allowlist via the LANGGRAPH_DB_NAME_ALLOWLIST env.

    The env-var is read once at module import. Simulating an override here by
    patching the module-level set directly mirrors what a fresh container
    boot with the env-var set would produce. Restore on teardown via
    monkeypatch's automatic cleanup.
    """
    import graph as graph_module

    # Save + replace; monkeypatch.setattr restores on test exit.
    monkeypatch.setattr(
        graph_module, "ALLOWED_LANGGRAPH_DB_NAMES", {"agent_teams", "custom_db"}
    )

    # The custom db now passes.
    graph_module._validate_database_uri(
        "postgresql://u:p@h:5432/custom_db?options=-c%20search_path=langgraph"
    )
    # The default test-db no longer passes (proves the override is honored).
    with pytest.raises(RuntimeError, match="not in the allowlist"):
        graph_module._validate_database_uri(
            "postgresql://u:p@h:5432/agent_teams_test?options=-c%20search_path=langgraph"
        )


def test_default_allowlist_contents() -> None:
    """Document the default allowlist so a silent narrowing breaks this test."""
    assert ALLOWED_LANGGRAPH_DB_NAMES == {"agent_teams", "agent_teams_test"}
