"""Kanban #1222 — stale_doc_curator unit + contract-smoke tests.

Coverage:
1. Unit — AC5 HITL safety: _WRITE_ROOT is under _scratch; _assert_write_safe
   raises ValueError when the resolved path escapes _WRITE_ROOT or targets .claude/.
2. Unit — run_stale_doc_curator with patched paths: stale doc triggers report;
   fresh doc produces no report.
3. Unit — contradiction heuristic: REPLACES / SUPERSEDES / CANCELLED /
   changed-to tokens in decisions.md are detected.
4. Unit — render_text includes stale_docs section when payload carries it.
5. Unit — env var STALE_DOC_DAYS overrides the default threshold.

Pytest discipline: live `agent_teams` DB row count is guarded by the session-
scope `_live_db_row_count_invariant` fixture in conftest.py.
These tests are filesystem-only (no DB), so task counts will not change.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from src.services import stale_doc_curator as _module
from src.services.stale_doc_curator import (
    _WRITE_ROOT,
    _assert_write_safe,
    _collect_target_files,
    _extract_contradiction_hints,
    _file_age_days,
    run_stale_doc_curator,
)
from src.services.digest_template import render_text, render_html

_REPO_ROOT = Path(_module.__file__).resolve().parents[3]
_FORBIDDEN_WRITE_PREFIXES = [
    str((_REPO_ROOT / ".claude").resolve()),
    str((_REPO_ROOT / "context").resolve()),
]


# ---------------------------------------------------------------------------
# AC5: safety tests -- _WRITE_ROOT is under _scratch; write guard fires
# ---------------------------------------------------------------------------


def test_ac5_write_root_is_under_scratch() -> None:
    """_WRITE_ROOT must be a subpath of _scratch/auditor -- never .claude or context."""
    write_root_str = str(_WRITE_ROOT.resolve())
    for fp in _FORBIDDEN_WRITE_PREFIXES:
        assert not write_root_str.startswith(fp), (
            f"_WRITE_ROOT={write_root_str!r} starts with forbidden prefix {fp!r}"
        )
    assert "_scratch" in write_root_str, (
        f"_WRITE_ROOT={write_root_str!r} is not under _scratch/"
    )


def test_ac5_write_guard_refuses_path_outside_write_root(tmp_path: Path) -> None:
    """_assert_write_safe raises ValueError when the target escapes _WRITE_ROOT."""
    safe_root = tmp_path / "safe"
    evil_target = tmp_path / "evil" / "report.md"

    with patch.object(_module, "_WRITE_ROOT", safe_root):
        with pytest.raises(ValueError, match="escapes _WRITE_ROOT"):
            _assert_write_safe(evil_target)


def test_ac5_write_guard_refuses_dotclaude_path(tmp_path: Path) -> None:
    """_assert_write_safe refuses to write inside .claude/ (defense-in-depth)."""
    dotclaude = tmp_path / ".claude" / "report.md"

    with patch.object(_module, "_WRITE_ROOT", tmp_path):
        with patch.object(_module, "_REPO_ROOT", tmp_path):
            with pytest.raises(ValueError, match="SAFETY VIOLATION"):
                _assert_write_safe(dotclaude)


def test_ac5_write_guard_refuses_context_path(tmp_path: Path) -> None:
    """_assert_write_safe refuses to write inside context/."""
    context_target = tmp_path / "context" / "standards" / "report.md"

    with patch.object(_module, "_WRITE_ROOT", tmp_path):
        with patch.object(_module, "_REPO_ROOT", tmp_path):
            with pytest.raises(ValueError, match="SAFETY VIOLATION"):
                _assert_write_safe(context_target)


def test_ac5_write_guard_accepts_scratch_path(tmp_path: Path) -> None:
    """_assert_write_safe accepts a path inside the patched _WRITE_ROOT."""
    target = tmp_path / "stale-docs-2026-06-04.md"

    with patch.object(_module, "_WRITE_ROOT", tmp_path):
        with patch.object(_module, "_REPO_ROOT", _REPO_ROOT):
            # Should not raise.
            _assert_write_safe(target)


# ---------------------------------------------------------------------------
# Unit — run_stale_doc_curator with patched filesystem (AC2 threshold + AC4)
# ---------------------------------------------------------------------------


def test_run_curator_stale_doc_triggers_report(tmp_path: Path) -> None:
    """A doc whose mtime is older than threshold causes a report to be written.

    Positive assertion: result.stale_count >= 1 and report_path is set.
    Negative assertion: report file is NOT under .claude/ or context/.
    """
    # Set up a fake context/standards/ with one old file.
    standards_dir = tmp_path / "context" / "standards"
    standards_dir.mkdir(parents=True)
    old_file = standards_dir / "old-rule.md"
    old_file.write_text("# Old rule\n", encoding="utf-8")

    # Set mtime to 100 days ago.
    old_ts = (datetime.now(timezone.utc).timestamp()) - (100 * 86400)
    os.utime(str(old_file), (old_ts, old_ts))

    scratch = tmp_path / "_scratch" / "auditor"

    with patch.object(_module, "_REPO_ROOT", tmp_path):
        with patch.object(_module, "_WRITE_ROOT", scratch):
            with patch.object(_module, "_SCRATCH_AUDITOR", scratch):
                with patch.object(_module, "_STANDARDS_ROOT", standards_dir):
                    with patch.object(_module, "_TEAMS_ROOT", tmp_path / "context" / "teams"):
                        with patch.object(_module, "_PROJECTS_ROOT", tmp_path / "context" / "projects"):
                            result = run_stale_doc_curator(threshold_days=60, today=date(2026, 6, 4))

    # Positive: at least 1 stale doc.
    assert result.stale_count >= 1, f"Expected stale_count >= 1, got {result.stale_count}"
    assert result.report_path is not None, "report_path must be set when stale docs are found"

    # Positive: report file exists.
    report = Path(result.report_path)
    assert report.exists(), f"Report file should exist at {report}"

    # Negative: report NOT under .claude/ or context/.
    report_abs = str(report.resolve())
    for fp in _FORBIDDEN_WRITE_PREFIXES:
        assert not report_abs.startswith(fp), (
            f"Report {report_abs!r} is inside forbidden prefix {fp!r}"
        )

    # Content sanity.
    content = report.read_text(encoding="utf-8")
    assert "old-rule" in content
    assert "stale" in content.lower()


def test_run_curator_fresh_doc_produces_no_report(tmp_path: Path) -> None:
    """A doc whose mtime is within the threshold produces no report.

    Positive assertion: stale_count == 0 and report_path is None.
    Negative assertion: no .md file written under the scratch dir.
    """
    standards_dir = tmp_path / "context" / "standards"
    standards_dir.mkdir(parents=True)
    fresh_file = standards_dir / "fresh-rule.md"
    fresh_file.write_text("# Fresh rule\n", encoding="utf-8")
    # mtime is now — well within any threshold.

    scratch = tmp_path / "_scratch" / "auditor"

    with patch.object(_module, "_REPO_ROOT", tmp_path):
        with patch.object(_module, "_WRITE_ROOT", scratch):
            with patch.object(_module, "_SCRATCH_AUDITOR", scratch):
                with patch.object(_module, "_STANDARDS_ROOT", standards_dir):
                    with patch.object(_module, "_TEAMS_ROOT", tmp_path / "context" / "teams"):
                        with patch.object(_module, "_PROJECTS_ROOT", tmp_path / "context" / "projects"):
                            result = run_stale_doc_curator(threshold_days=60, today=date(2026, 6, 4))

    # Positive: no stale docs.
    assert result.stale_count == 0, f"Expected stale_count == 0, got {result.stale_count}"
    # Negative: no report written.
    assert result.report_path is None, (
        f"No report should be written for fresh docs, got {result.report_path}"
    )
    # Negative: scratch dir should not contain a stale-docs report.
    if scratch.exists():
        md_files = list(scratch.glob("stale-docs-*.md"))
        assert md_files == [], f"Unexpected report files: {md_files}"


# ---------------------------------------------------------------------------
# Unit — AC3 contradiction heuristic
# ---------------------------------------------------------------------------


def test_contradiction_heuristic_detects_replaces_token(tmp_path: Path) -> None:
    """REPLACES token in decisions.md flags the referenced file stem.

    Positive assertion: the referenced path appears in contradiction_map.
    Negative assertion: an unreferenced file is NOT in contradiction_map.
    """
    decisions_file = tmp_path / "decisions.md"
    decisions_file.write_text(
        "# Decisions\n\n"
        "2026-06-01: REPLACES old-auth-standard — new OAuth flow adopted.\n",
        encoding="utf-8",
    )

    referenced = tmp_path / "old-auth-standard.md"
    unreferenced = tmp_path / "unrelated-doc.md"
    referenced.write_text("# Old auth\n", encoding="utf-8")
    unreferenced.write_text("# Unrelated\n", encoding="utf-8")

    stem_index = {
        "old-auth-standard": referenced,
        "unrelated-doc": unreferenced,
    }
    hints = _extract_contradiction_hints(decisions_file, stem_index)

    referenced_paths = [h[0] for h in hints]

    # Positive: old-auth-standard was found.
    assert referenced in referenced_paths, (
        "REPLACES token should flag the referenced stem"
    )
    # Negative: unrelated-doc was NOT flagged.
    assert unreferenced not in referenced_paths, (
        "Unreferenced file should not be flagged"
    )


def test_contradiction_heuristic_detects_all_tokens(tmp_path: Path) -> None:
    """All four contradiction tokens are detected."""
    decisions_file = tmp_path / "decisions.md"
    decisions_file.write_text(
        "REPLACES doc-a\n"
        "SUPERSEDES doc-b\n"
        "CANCELLED doc-c\n"
        "changed-to doc-d\n",
        encoding="utf-8",
    )

    doc_a = tmp_path / "doc-a.md"
    doc_b = tmp_path / "doc-b.md"
    doc_c = tmp_path / "doc-c.md"
    doc_d = tmp_path / "doc-d.md"
    for f in [doc_a, doc_b, doc_c, doc_d]:
        f.write_text("# content\n", encoding="utf-8")

    stem_index = {
        "doc-a": doc_a, "doc-b": doc_b, "doc-c": doc_c, "doc-d": doc_d
    }
    hints = _extract_contradiction_hints(decisions_file, stem_index)
    referenced_paths = set(h[0] for h in hints)

    assert doc_a in referenced_paths, "REPLACES must flag doc-a"
    assert doc_b in referenced_paths, "SUPERSEDES must flag doc-b"
    assert doc_c in referenced_paths, "CANCELLED must flag doc-c"
    assert doc_d in referenced_paths, "changed-to must flag doc-d"


# ---------------------------------------------------------------------------
# Unit — AC2 env var threshold
# ---------------------------------------------------------------------------


def test_env_var_stale_doc_days_overrides_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """STALE_DOC_DAYS env var controls the threshold used.

    Positive assertion: threshold_days in result matches env var.
    Negative assertion: default (60) is NOT used when env is set.
    """
    monkeypatch.setenv("STALE_DOC_DAYS", "30")

    scratch = tmp_path / "_scratch" / "auditor"
    with patch.object(_module, "_REPO_ROOT", tmp_path):
        with patch.object(_module, "_WRITE_ROOT", scratch):
            with patch.object(_module, "_SCRATCH_AUDITOR", scratch):
                with patch.object(_module, "_STANDARDS_ROOT", tmp_path / "context" / "standards"):
                    with patch.object(_module, "_TEAMS_ROOT", tmp_path / "context" / "teams"):
                        with patch.object(_module, "_PROJECTS_ROOT", tmp_path / "context" / "projects"):
                            result = run_stale_doc_curator(today=date(2026, 6, 4))

    # Positive: threshold from env was used.
    assert result.threshold_days == 30, (
        f"Expected threshold_days=30 from env, got {result.threshold_days}"
    )
    # Negative: default 60 was NOT used.
    assert result.threshold_days != 60, "Default 60 should not apply when env is set to 30"


# ---------------------------------------------------------------------------
# Unit — render_text / render_html include stale_docs section (AC6 template)
# ---------------------------------------------------------------------------


def test_render_text_includes_stale_docs_when_stale() -> None:
    """render_text shows stale count + report path when stale_docs has findings."""
    payload = {
        "date": "2026-06-04",
        "flags": [],
        "base_url": "http://localhost:5431",
        "stale_docs": {
            "stale_count": 3,
            "contradiction_count": 1,
            "report_path": "/repo/_scratch/auditor/stale-docs-2026-06-04.md",
            "scanned_count": 42,
            "threshold_days": 60,
        },
    }
    text = render_text(payload)
    assert "Stale-doc audit" in text
    assert "3 stale" in text
    assert "stale-docs-2026-06-04" in text


def test_render_text_shows_all_fresh_when_zero_stale() -> None:
    """render_text shows 'all fresh' when stale_count=0 and contradiction_count=0."""
    payload = {
        "date": "2026-06-04",
        "flags": [],
        "base_url": "http://localhost:5431",
        "stale_docs": {
            "stale_count": 0,
            "contradiction_count": 0,
            "report_path": None,
            "scanned_count": 10,
            "threshold_days": 60,
        },
    }
    text = render_text(payload)
    assert "Stale-doc audit" in text
    assert "fresh" in text.lower()


def test_render_text_omits_stale_docs_section_when_absent() -> None:
    """render_text is backward-compatible: no stale_docs key -> section absent."""
    payload = {
        "date": "2026-06-04",
        "flags": [],
        "base_url": "http://localhost:5431",
    }
    text = render_text(payload)
    assert "Stale-doc audit" not in text


def test_render_html_includes_stale_docs_section() -> None:
    """render_html includes the stale-doc section when payload carries stale_docs."""
    payload = {
        "date": "2026-06-04",
        "flags": [],
        "base_url": "http://localhost:5431",
        "stale_docs": {
            "stale_count": 2,
            "contradiction_count": 0,
            "report_path": "/repo/_scratch/auditor/stale-docs-2026-06-04.md",
            "scanned_count": 20,
            "threshold_days": 60,
        },
    }
    html = render_html(payload)
    assert "Stale-doc audit" in html
    assert "2 stale" in html


def test_render_html_coexists_with_skill_stubs_section() -> None:
    """render_html includes BOTH skill_stubs and stale_docs sections together."""
    payload = {
        "date": "2026-06-04",
        "flags": [],
        "base_url": "http://localhost:5431",
        "skill_stubs": {
            "proposed_count": 1,
            "stub_dir": "/repo/_scratch/auditor/proposed-stubs-2026-06-04",
        },
        "stale_docs": {
            "stale_count": 1,
            "contradiction_count": 0,
            "report_path": "/repo/_scratch/auditor/stale-docs-2026-06-04.md",
            "scanned_count": 5,
            "threshold_days": 60,
        },
    }
    html = render_html(payload)
    # Both sections must coexist — AC6 + #1223 not clobbered.
    assert "Skill/runbook proposals" in html
    assert "Stale-doc audit" in html
