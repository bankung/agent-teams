"""Kanban #1123 (L16, 2026-05-17) — langgraph-side sanitizer parity tests.

The CANONICAL source lives at `api/src/services/agent_context_sanitizer.py`;
this file is a copy because the langgraph container does not import the api
package. These tests mirror `api/tests/test_agent_context_sanitizer.py` so
any drift between the two copies surfaces in the CI matrix (both test files
must stay green together).

Same contract:
  - DROP/TRUNCATE/DELETE/ALTER/GRANT/REVOKE/EXEC/EXECUTE → [REDACTED]
  - 500-char cap after redaction
  - None / empty → ""
"""

from __future__ import annotations

import pytest

from agent_context_sanitizer import sanitize_for_agent_context


def test_sanitize_none_returns_empty_string() -> None:
    assert sanitize_for_agent_context(None) == ""


def test_sanitize_empty_string_returns_empty_string() -> None:
    assert sanitize_for_agent_context("") == ""


def test_sanitize_clean_text_unchanged() -> None:
    text = "task halted: tool permission review pending"
    assert sanitize_for_agent_context(text) == text


@pytest.mark.parametrize(
    "keyword",
    ["DROP", "TRUNCATE", "DELETE", "ALTER", "GRANT", "REVOKE", "EXEC", "EXECUTE"],
)
def test_sanitize_redacts_destructive_keyword(keyword: str) -> None:
    out = sanitize_for_agent_context(f"please {keyword} the table")
    assert "[REDACTED]" in out
    assert keyword not in out


def test_sanitize_drop_table_redacts_only_drop() -> None:
    """AC4 wire contract — surrounding context survives."""
    result = sanitize_for_agent_context("DROP TABLE tasks; rest of payload")
    assert result == "[REDACTED] TABLE tasks; rest of payload"


def test_sanitize_truncates_at_500_chars() -> None:
    long_text = "x" * 600
    result = sanitize_for_agent_context(long_text)
    assert len(result) == 500


def test_sanitize_redacts_then_truncates() -> None:
    """Redaction happens BEFORE truncation — a keyword early in a long string
    still redacts even though the tail is cut. Use a leading space so the
    \\b word-boundary fires before 'DROP' (word char + word char = no \\b)."""
    long_text = ("a" * 200) + " DROP " + ("b" * 394)
    result = sanitize_for_agent_context(long_text)
    assert len(result) == 500
    assert "[REDACTED]" in result
    assert "DROP" not in result


def test_sanitize_word_boundary_no_false_positives() -> None:
    """\\b prevents 'DROPSHIPPING' / 'teardrop' false matches."""
    assert sanitize_for_agent_context("teardrop_test") == "teardrop_test"
    assert sanitize_for_agent_context("dropbox-integration") == "dropbox-integration"
