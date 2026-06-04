"""Unit tests for the placeholder-substitution helper (Kanban #1303).

Pure-function tests — no DB, no HTTP. Covers the spec's required cases:
  - the happy-path example renders exactly,
  - a missing placeholder RAISES (not silent passthrough),
  - the AC-template array variant renders each item's `text`.
"""

from __future__ import annotations

import pytest

from src.services.template_render import (
    MissingPlaceholderError,
    render_ac_template,
    render_template,
)


def test_render_template_happy_path_spec_example() -> None:
    """The exact example from the #1303 brief renders to the exact string."""
    out = render_template(
        "Analyze {{file}} for {{metric}}",
        {"file": "sales.csv", "metric": "trend"},
    )
    assert out == "Analyze sales.csv for trend"


def test_render_template_tolerates_inner_whitespace() -> None:
    """`{{ key }}` (padded) substitutes the same as `{{key}}`."""
    assert render_template("hi {{ name }}!", {"name": "Sam"}) == "hi Sam!"


def test_render_template_coerces_non_string_values() -> None:
    """Non-string values are str()-coerced so ints substitute cleanly."""
    assert render_template("count={{n}}", {"n": 42}) == "count=42"


def test_render_template_no_tokens_passthrough() -> None:
    """Text with no placeholders is returned verbatim (and tolerates extra values)."""
    assert render_template("plain text", {"unused": "x"}) == "plain text"


def test_render_template_non_placeholder_braces_left_verbatim() -> None:
    """A `{{a-b}}` (hyphen — not an identifier) is NOT a token; left as-is, no error."""
    assert render_template("json {{a-b}} blob", {}) == "json {{a-b}} blob"


def test_render_template_missing_placeholder_raises() -> None:
    """A referenced-but-unsupplied placeholder is a hard error naming the key.

    POSITIVE assertion (it renders the supplied key) is paired implicitly with
    the NEGATIVE lock below: the helper MUST NOT silently pass `{{metric}}`
    through when `metric` is absent.
    """
    with pytest.raises(MissingPlaceholderError) as exc_info:
        render_template("Analyze {{file}} for {{metric}}", {"file": "sales.csv"})
    assert exc_info.value.key == "metric"
    assert "metric" in str(exc_info.value)


def test_render_template_missing_key_is_not_passthrough() -> None:
    """Lock: the missing-placeholder path RAISES rather than returning the literal token.

    Without the raise, `render_template` would return the input unchanged — this
    test fails loudly if anyone reverts the helper to silent passthrough.
    """
    with pytest.raises(MissingPlaceholderError):
        render_template("{{missing}}", {})


def test_render_ac_template_renders_each_item_text() -> None:
    """Each AC object's `text` is rendered; other keys pass through; input untouched."""
    src = [
        {"text": "verify {{file}} parses", "status": "pending"},
        {"text": "metric {{metric}} computed", "status": "pending", "notes": "n"},
    ]
    out = render_ac_template(src, {"file": "sales.csv", "metric": "trend"})

    assert out == [
        {"text": "verify sales.csv parses", "status": "pending"},
        {"text": "metric trend computed", "status": "pending", "notes": "n"},
    ]
    # Input not mutated (new list / new dicts).
    assert src[0]["text"] == "verify {{file}} parses"


def test_render_ac_template_missing_placeholder_raises() -> None:
    """A missing placeholder inside an AC item's text propagates as the same error."""
    with pytest.raises(MissingPlaceholderError) as exc_info:
        render_ac_template([{"text": "needs {{missing}}"}], {})
    assert exc_info.value.key == "missing"


def test_render_ac_template_item_without_text_passthrough() -> None:
    """An AC object lacking a `text` key is copied verbatim (no error)."""
    out = render_ac_template([{"status": "pending"}], {})
    assert out == [{"status": "pending"}]
