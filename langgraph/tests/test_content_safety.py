"""Unit tests for L17 pickup-time content scanner (Kanban #1114).

The scanner is a pure regex pass — no I/O, no LLM. Tests cover:
  - Each destructive pattern matches its canonical form (case-insensitive).
  - Clean content returns [].
  - AC items as dicts AND as objects with `.text` attribute both work.
  - None / empty inputs don't crash.
  - The list is deterministic + ordered by _DESTRUCTIVE_PATTERNS.
  - Halt-reason strings (the worker's own output) don't self-trigger on
    subsequent scans — this matters because the worker stamps the matched
    pattern names INTO status_change_reason, and the scan signature does NOT
    accept those fields, so a re-pickup of a halted task can't re-match.

The patterns are intentionally narrow — every false positive is a manual
unblock, so we don't try to catch every flavor of SQL injection.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from content_safety import scan_task_content


# ---------------------------------------------------------------------------
# Clean inputs return []
# ---------------------------------------------------------------------------


def test_clean_all_fields_returns_empty() -> None:
    """Normal task content (no destructive patterns) returns []."""
    assert scan_task_content(
        title="Add login endpoint",
        description="Implement POST /auth/login with JWT issuance.",
        acceptance_criteria=[
            {"text": "Endpoint returns 200 on valid creds", "status": "pending"},
            {"text": "Rate limit applies after 5 failures", "status": "pending"},
        ],
    ) == []


def test_all_none_inputs_returns_empty() -> None:
    """None title + None description + None AC must not crash."""
    assert scan_task_content(title=None, description=None, acceptance_criteria=None) == []


def test_empty_strings_returns_empty() -> None:
    """Empty title + empty description + empty AC list returns []."""
    assert scan_task_content(title="", description="", acceptance_criteria=[]) == []


# ---------------------------------------------------------------------------
# Each pattern matches its canonical form
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field, value, expected",
    [
        # DROP TABLE / DATABASE / SCHEMA
        ("description", "DROP TABLE projects", ["DROP_TABLE"]),
        ("description", "drop table projects;", ["DROP_TABLE"]),
        ("description", "DROP DATABASE agent_teams", ["DROP_TABLE"]),
        ("description", "DROP SCHEMA public CASCADE", ["DROP_TABLE"]),
        # TRUNCATE
        ("description", "TRUNCATE tasks_history", ["TRUNCATE"]),
        ("description", "truncate tasks", ["TRUNCATE"]),
        # DELETE FROM
        ("description", "DELETE FROM tasks WHERE id > 0", ["DELETE_FROM"]),
        ("description", "delete from projects", ["DELETE_FROM"]),
        # ALTER TABLE ... DISABLE/DROP TRIGGER/CONSTRAINT
        (
            "description",
            "ALTER TABLE tasks DISABLE TRIGGER audit_trigger",
            ["ALTER_TRIGGER"],
        ),
        (
            "description",
            "alter table projects drop constraint fk_owner",
            ["ALTER_TRIGGER"],
        ),
        # Shell escapes
        ("description", "rm -rf /repo", ["SHELL_RM"]),
        ("description", "dropdb agent_teams", ["SHELL_RM"]),
        ("description", "docker volume rm agent-teams_pg-data", ["SHELL_RM"]),
    ],
)
def test_canonical_pattern_match(field: str, value: str, expected: list[str]) -> None:
    """Each destructive pattern is detected in its canonical form."""
    kwargs = {"title": None, "description": None, "acceptance_criteria": None}
    kwargs[field] = value
    assert scan_task_content(**kwargs) == expected


# ---------------------------------------------------------------------------
# Multiple patterns in one haystack — order matches _DESTRUCTIVE_PATTERNS
# ---------------------------------------------------------------------------


def test_multiple_patterns_deterministic_order() -> None:
    """When the haystack matches several patterns, the return order mirrors
    _DESTRUCTIVE_PATTERNS (DROP_TABLE, TRUNCATE, DELETE_FROM, ALTER_TRIGGER,
    SHELL_RM) — not the order they appear in the text. This is important for
    audit logs + test assertions."""
    desc = (
        "First DELETE FROM tasks, then DROP TABLE projects, "
        "finally TRUNCATE audit_log and rm -rf /repo."
    )
    matched = scan_task_content(title=None, description=desc, acceptance_criteria=None)
    assert matched == ["DROP_TABLE", "TRUNCATE", "DELETE_FROM", "SHELL_RM"]


# ---------------------------------------------------------------------------
# Field coverage — title, description, AC text all participate
# ---------------------------------------------------------------------------


def test_match_in_title_only() -> None:
    assert scan_task_content(
        title="TRUNCATE tasks_history nightly",
        description="cron job",
        acceptance_criteria=None,
    ) == ["TRUNCATE"]


def test_match_in_ac_text_dict_form() -> None:
    """AC items as dicts (JSONB column shape)."""
    assert scan_task_content(
        title="Clean up",
        description="Routine",
        acceptance_criteria=[
            {"text": "Run TRUNCATE tasks", "status": "pending"},
        ],
    ) == ["TRUNCATE"]


def test_match_in_ac_text_object_form() -> None:
    """AC items as objects exposing `.text` (Pydantic-style)."""
    ac = SimpleNamespace(text="DROP TABLE projects", status="pending")
    assert scan_task_content(
        title="X",
        description="Y",
        acceptance_criteria=[ac],
    ) == ["DROP_TABLE"]


def test_ac_text_missing_skipped() -> None:
    """AC items without a `text` key/attr are silently skipped (no crash)."""
    assert scan_task_content(
        title="X",
        description="Y",
        acceptance_criteria=[
            {"status": "pending"},  # no text key
            {"text": None, "status": "pending"},  # text=None
            SimpleNamespace(status="pending"),  # no text attr
        ],
    ) == []


# ---------------------------------------------------------------------------
# Word-boundary discipline — substrings must NOT trigger
# ---------------------------------------------------------------------------


def test_substring_in_identifier_does_not_match() -> None:
    """`truncate` inside a longer identifier (e.g. `truncated_text`) must NOT
    match — \\b word-boundary protects us. Same for `dropdb` inside a python
    function name like `auto_dropdbox_sync`."""
    # `truncated` — substring of TRUNCATE preceded/followed by word chars.
    assert "TRUNCATE" not in scan_task_content(
        title="Show truncated logs in UI",
        description="The text is truncated_text after 200 chars",
        acceptance_criteria=None,
    )
    # `dropdbox` — not the dropdb command.
    assert "SHELL_RM" not in scan_task_content(
        title="x",
        description="The dropdbox sync handler crashed",
        acceptance_criteria=None,
    )


def test_case_insensitive_match() -> None:
    """All patterns are (?i) — mixed case matches."""
    assert scan_task_content(
        title=None,
        description="DrOp TaBlE projects",
        acceptance_criteria=None,
    ) == ["DROP_TABLE"]


# ---------------------------------------------------------------------------
# Idempotency: the worker's own halt-reason text must NOT re-trigger
# ---------------------------------------------------------------------------


def test_idempotent_on_unchanged_input() -> None:
    """Re-scanning the same fields returns the same matches in the same order
    — pin against accidental nondeterminism (e.g. someone swaps the list for
    a set). Matters because the worker may re-poll a halted task after manual
    review and we want the audit-log entries to match exactly."""
    desc = "DELETE FROM tasks; DROP TABLE projects;"
    first = scan_task_content(title="X", description=desc, acceptance_criteria=None)
    second = scan_task_content(title="X", description=desc, acceptance_criteria=None)
    assert first == second == ["DROP_TABLE", "DELETE_FROM"]


def test_halt_reason_field_not_in_signature() -> None:
    """Scanner signature deliberately excludes halt_reason / status_change_reason.
    The worker stamps matched-pattern names into status_change_reason; if those
    fields were scanned, every re-pickup would re-amplify the match (the pattern
    name literal string itself contains 'TRUNCATE', 'DROP_TABLE' etc as words).
    This test pins the signature contract — `inspect.signature` exposes only the
    three documented args, no halt_reason / status_change_reason creep."""
    import inspect

    params = list(inspect.signature(scan_task_content).parameters.keys())
    assert params == ["title", "description", "acceptance_criteria"]
