"""Tests for services.zero_config_scaffold (Kanban #792, MVP-A).

Uses the real agent-teams repo at /repo as the source — these tests perform
only read ops on the source side and write into a TemporaryDirectory target.
The path-traversal guard test confirms the source-overwrite case raises.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Real source repo inside the container (FastAPI volume-mount). Verified at
# `docker compose exec -T api ls /repo/CLAUDE.md`.
AGENT_TEAMS_ROOT = Path("/repo")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _has_file(target: Path, rel: str) -> bool:
    return (target / rel).is_file()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_scaffold_fresh_target_copies_all_files() -> None:
    """Empty target dir → universal + dev files land on disk and appear in
    `copied`. Spot-check a representative file from each manifest segment."""
    from src.services.zero_config_scaffold import scaffold_orchestration

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "proj-fresh"
        report = scaffold_orchestration(
            target_path=target,
            project_name="proj-fresh",
            team="dev",
            agent_teams_root=AGENT_TEAMS_ROOT,
        )

        # Universal — CLAUDE.md + hooks + cross-team agents + settings.json
        assert _has_file(target, "CLAUDE.md")
        assert _has_file(target, ".claude/hooks/block-raw-sql-dml.ps1")
        assert _has_file(target, ".claude/agents/dev-analyst.md")
        assert _has_file(target, ".claude/settings.json")

        # Dev-only — agents + team playbook
        assert _has_file(target, ".claude/agents/dev-backend.md")
        assert _has_file(target, ".claude/teams/dev.md")
        # Kanban #7 Section B (2026-05-16): security reviewer agent
        assert _has_file(target, ".claude/agents/dev-security-reviewer.md")

        # Glob expansion — pick a known file from each glob
        assert _has_file(target, "context/standards/general.md")
        assert _has_file(target, "context/teams/dev/decisions.md")

        # Report shape — every landed file should appear in `copied`
        assert "CLAUDE.md" in report.copied
        assert ".claude/teams/dev.md" in report.copied
        assert "context/standards/general.md" in report.copied
        assert report.skipped == []
        assert report.errors == []
        assert report.target_path == target.resolve()
        assert report.project_name == "proj-fresh"
        assert report.team == "dev"


def test_scaffold_existing_file_skipped() -> None:
    """Pre-create one target file → call → that file's bytes are untouched
    and the rel path appears under `skipped`, not `copied`."""
    from src.services.zero_config_scaffold import scaffold_orchestration

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "proj-skip"
        # Pre-seed CLAUDE.md with a sentinel before scaffolding.
        target.mkdir(parents=True)
        (target / "CLAUDE.md").write_text("PRE-EXISTING SENTINEL", encoding="utf-8")

        report = scaffold_orchestration(
            target_path=target,
            project_name="proj-skip",
            team="dev",
            agent_teams_root=AGENT_TEAMS_ROOT,
        )

        # Sentinel survives — no overwrite.
        assert (target / "CLAUDE.md").read_text(encoding="utf-8") == "PRE-EXISTING SENTINEL"
        assert "CLAUDE.md" in report.skipped
        assert "CLAUDE.md" not in report.copied
        # Other files should still have landed normally.
        assert ".claude/teams/dev.md" in report.copied


def test_scaffold_novel_team_skips_dev_agents() -> None:
    """team='novel' → no dev-* agent files in target (only the cross-team
    dev-analyst / dev-spec-reviewer utilities, which are universal). Novel
    team playbook + agents should land. The novel context dir doesn't exist
    in source yet, so its glob expands to an empty list — not an error."""
    from src.services.zero_config_scaffold import scaffold_orchestration

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "proj-novel"
        report = scaffold_orchestration(
            target_path=target,
            project_name="proj-novel",
            team="novel",
            agent_teams_root=AGENT_TEAMS_ROOT,
        )

        # Dev-only agents must NOT be present.
        assert not _has_file(target, ".claude/agents/dev-backend.md")
        assert not _has_file(target, ".claude/agents/dev-frontend.md")
        assert not _has_file(target, ".claude/agents/dev-devops.md")
        assert not _has_file(target, ".claude/agents/dev-reviewer.md")
        assert not _has_file(target, ".claude/agents/dev-tester.md")
        assert not _has_file(target, ".claude/agents/dev-security-reviewer.md")
        assert not _has_file(target, ".claude/teams/dev.md")

        # Novel agents + team playbook DO land (source files exist in agent-teams).
        assert _has_file(target, ".claude/agents/novel-writer.md")
        assert _has_file(target, ".claude/agents/novel-editor.md")
        assert _has_file(target, ".claude/teams/novel.md")

        # Cross-team utilities still land (universal manifest).
        assert _has_file(target, ".claude/agents/dev-analyst.md")
        assert _has_file(target, ".claude/agents/dev-spec-reviewer.md")

        # No error from missing context/teams/novel source dir.
        novel_glob_errors = [
            err for rel, err in report.errors if rel.startswith("context/teams/novel/")
        ]
        assert novel_glob_errors == []


def test_scaffold_path_traversal_guard() -> None:
    """target_path == agent_teams_root → ValueError. Also covers subdirectory
    case (target inside source) since both routes through the same guard."""
    from src.services.zero_config_scaffold import scaffold_orchestration

    with pytest.raises(ValueError, match="resolves to or under"):
        scaffold_orchestration(
            target_path=AGENT_TEAMS_ROOT,
            project_name="evil",
            team="dev",
            agent_teams_root=AGENT_TEAMS_ROOT,
        )

    # Subdir of root — should also be rejected.
    with pytest.raises(ValueError, match="resolves to or under"):
        scaffold_orchestration(
            target_path=AGENT_TEAMS_ROOT / "context" / "projects" / "evil-sub",
            project_name="evil-sub",
            team="dev",
            agent_teams_root=AGENT_TEAMS_ROOT,
        )


def test_scaffold_partial_error_continues() -> None:
    """Force one shutil.copyfile call to raise → other files still copy and
    the failing rel path appears in `errors`. Patches the symbol in the
    service module so we intercept the exact call site."""
    from src.services import zero_config_scaffold as svc
    from src.services.zero_config_scaffold import scaffold_orchestration

    real_copyfile = svc.shutil.copyfile
    failing_rel = "CLAUDE.md"

    def fake_copyfile(src, dst, *args, **kwargs):
        # Raise only for the targeted file; everything else uses the real impl.
        if str(src).endswith("/CLAUDE.md") or str(src).endswith("\\CLAUDE.md"):
            raise PermissionError("simulated copy failure")
        return real_copyfile(src, dst, *args, **kwargs)

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "proj-partial"
        with patch.object(svc.shutil, "copyfile", side_effect=fake_copyfile):
            report = scaffold_orchestration(
                target_path=target,
                project_name="proj-partial",
                team="dev",
                agent_teams_root=AGENT_TEAMS_ROOT,
            )

        # CLAUDE.md failed → in errors, NOT in copied, NOT on disk.
        assert any(rel == failing_rel for rel, _ in report.errors)
        assert failing_rel not in report.copied
        assert not _has_file(target, failing_rel)

        # Other files still copied normally.
        assert ".claude/teams/dev.md" in report.copied
        assert _has_file(target, ".claude/teams/dev.md")


def test_scaffold_idempotent_double_call() -> None:
    """Call twice → second call's `copied` is empty + every previously-copied
    file appears under `skipped`. This is the core MVP-A contract."""
    from src.services.zero_config_scaffold import scaffold_orchestration

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "proj-idem"
        first = scaffold_orchestration(
            target_path=target,
            project_name="proj-idem",
            team="dev",
            agent_teams_root=AGENT_TEAMS_ROOT,
        )
        second = scaffold_orchestration(
            target_path=target,
            project_name="proj-idem",
            team="dev",
            agent_teams_root=AGENT_TEAMS_ROOT,
        )

        assert first.copied  # sanity — first run actually copied something
        assert first.skipped == []
        assert second.copied == []
        # Every rel the first call copied must appear in the second call's skipped.
        assert set(first.copied).issubset(set(second.skipped))
        assert second.errors == []
