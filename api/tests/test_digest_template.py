"""Kanban #1217 — digest template renderer unit tests.

Covers render_subject, render_text, render_html (no DB needed — pure renderers).
fetch_open_audit_flags is integration-tested via the digest router smoke test.
"""

from __future__ import annotations

import pytest

from src.services.digest_template import render_html, render_subject, render_text


# ---------------------------------------------------------------------------
# render_subject
# ---------------------------------------------------------------------------


def test_render_subject_zero_flags() -> None:
    result = render_subject(0, "2026-05-22")
    assert result == "Digest 2026-05-22 — no open flags"


def test_render_subject_one_flag() -> None:
    result = render_subject(1, "2026-05-22")
    assert "1 open flag" in result
    assert "flags" not in result.replace("open flag", "")  # singular


def test_render_subject_many_flags() -> None:
    result = render_subject(5, "2026-05-22")
    assert "5 open flags" in result


# ---------------------------------------------------------------------------
# render_text
# ---------------------------------------------------------------------------


def _sample_payload(n_flags: int = 2, base_url: str = "http://localhost:5431") -> dict:
    flags = [
        {
            "id": 100 + i,
            "project": f"proj-{i}",
            "title": f"Flag title {i}",
            "streak": i + 1,
            "severity": "high",
            "verdict": "review",
        }
        for i in range(n_flags)
    ]
    return {"date": "2026-05-22", "flags": flags, "base_url": base_url}


def test_render_text_no_flags() -> None:
    payload = {"date": "2026-05-22", "flags": [], "base_url": "http://localhost:5431"}
    text = render_text(payload)
    assert "2026-05-22" in text
    assert "No open audit flags" in text
    # Ensure no flag-row junk
    assert "streak=" not in text


def test_render_text_contains_flag_details() -> None:
    text = render_text(_sample_payload(2))
    assert "#100" in text
    assert "proj-0" in text
    assert "streak=1" in text
    assert "severity=high" in text
    # Deep link present
    assert "/review?flag=100" in text


def test_render_text_includes_footer() -> None:
    text = render_text(_sample_payload(0))
    assert "agent-teams" in text.lower()


# ---------------------------------------------------------------------------
# render_html
# ---------------------------------------------------------------------------


def test_render_html_no_flags_produces_valid_outer_structure() -> None:
    payload = {"date": "2026-05-22", "flags": [], "base_url": "http://localhost:5431"}
    html = render_html(payload)
    assert "<!DOCTYPE html>" in html
    assert "<body" in html
    assert "</html>" in html
    assert "No open audit flags" in html


def test_render_html_with_flags_contains_links() -> None:
    html = render_html(_sample_payload(2, "http://localhost:5431"))
    # Deep link href
    assert "http://localhost:5431/review?flag=100" in html
    assert "Review flag #100" in html


def test_render_html_escapes_special_chars() -> None:
    """HTML injection in flag fields is escaped."""
    payload = {
        "date": "2026-05-22",
        "flags": [
            {
                "id": 1,
                "project": "<script>alert(1)</script>",
                "title": "Test & verify",
                "streak": 1,
                "severity": "high",
                "verdict": "review",
            }
        ],
        "base_url": "http://localhost:5431",
    }
    html = render_html(payload)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "Test &amp; verify" in html


def test_render_html_no_external_resources() -> None:
    """Spam hygiene: no external images or remote CSS in the output."""
    html = render_html(_sample_payload(3))
    assert "http" not in html.replace("http://localhost:5431", "REPLACED")
    # No src= attributes pointing at external resources
    assert "<img" not in html
    assert "@import" not in html
