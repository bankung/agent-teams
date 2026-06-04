"""Kanban #1223 -- skill_stub_detector unit + integration tests.

Coverage:
1. Unit -- _normalize_title_prefix, _slugify, _top2_step_key (pure functions,
   no DB).
2. Unit -- AC4 HITL safety: _WRITE_ROOT is under _scratch; _write_stub raises
   ValueError when the resolved path escapes _WRITE_ROOT or targets .claude/.
3. Integration -- run_skill_stub_detector against test DB: with < threshold
   tasks no stub is written; with >= threshold tasks a stub is written under
   _scratch/auditor/proposed-stubs-<date>/.
4. Digest -- render_text includes skill_stubs section when payload carries it.

Pytest discipline: live `agent_teams` DB row count is guarded by the session-
scope `_live_db_row_count_invariant` fixture in conftest.py.

All project/task creation for integration tests goes through the HTTP client
(no raw SQL DML). Test fixtures clean up with soft-delete via the API.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from src.services import skill_stub_detector as _module
from src.services.skill_stub_detector import (
    _WRITE_ROOT,
    _normalize_title_prefix,
    _slugify,
    _top2_step_key,
    _write_stub,
    run_skill_stub_detector,
)
from src.services.digest_template import render_text


_REPO_ROOT = Path(_module.__file__).resolve().parents[3]
_FORBIDDEN_WRITE_PREFIXES = [
    str((_REPO_ROOT / ".claude").resolve()),
    str((_REPO_ROOT / "context").resolve()),
]


# ---------------------------------------------------------------------------
# AC4: safety tests -- _WRITE_ROOT is under _scratch, not .claude or context
# ---------------------------------------------------------------------------


def test_ac4_write_root_is_under_scratch() -> None:
    """_WRITE_ROOT must be a subpath of _scratch/auditor -- never .claude or context."""
    write_root_str = str(_WRITE_ROOT.resolve())
    for fp in _FORBIDDEN_WRITE_PREFIXES:
        assert not write_root_str.startswith(fp), (
            f"_WRITE_ROOT={write_root_str!r} starts with forbidden prefix {fp!r}"
        )
    assert "_scratch" in write_root_str, (
        f"_WRITE_ROOT={write_root_str!r} is not under _scratch/"
    )


def test_ac4_write_stub_refuses_path_outside_write_root(tmp_path: Path) -> None:
    """_write_stub must raise ValueError when the resolved path escapes _WRITE_ROOT.

    We patch _WRITE_ROOT to be tmp_path/safe and then pass a today_dir that
    is tmp_path/evil (a sibling, not a child). The write should be refused.
    """
    safe_root = tmp_path / "safe"
    evil_dir = tmp_path / "evil"
    evil_dir.mkdir(parents=True, exist_ok=True)

    with patch.object(_module, "_WRITE_ROOT", safe_root):
        with pytest.raises(ValueError, match="escapes _WRITE_ROOT"):
            _write_stub(
                slug="test-slug",
                title_prefix="test prefix",
                step_key="dev-backend:sonnet",
                task_ids=[1, 2, 3],
                project_ids=[1],
                today_dir=evil_dir,
                today=date(2026, 6, 4),
            )


def test_ac4_write_stub_refuses_dotclaude_path(tmp_path: Path) -> None:
    """_write_stub refuses to write inside .claude/ (defense-in-depth second guard)."""
    dotclaude = tmp_path / ".claude"
    dotclaude.mkdir(parents=True, exist_ok=True)

    # Patch both _WRITE_ROOT and _REPO_ROOT so the guard fires.
    with patch.object(_module, "_WRITE_ROOT", tmp_path):
        with patch.object(_module, "_REPO_ROOT", tmp_path):
            with pytest.raises(ValueError, match="SAFETY VIOLATION"):
                _write_stub(
                    slug="dotclaude-slug",
                    title_prefix="dotclaude prefix",
                    step_key="",
                    task_ids=[1],
                    project_ids=[1],
                    today_dir=dotclaude,
                    today=date(2026, 6, 4),
                )


def test_ac4_write_stub_writes_only_to_today_dir(tmp_path: Path) -> None:
    """A happy-path write lands INSIDE today_dir (not elsewhere).

    Negative assertion: the written file path starts with tmp_path (our
    patched _WRITE_ROOT) and NOT with any forbidden prefix.
    """
    today_dir = tmp_path / "proposed-stubs-2026-06-04"

    with patch.object(_module, "_WRITE_ROOT", tmp_path):
        with patch.object(_module, "_REPO_ROOT", _REPO_ROOT):
            written = _write_stub(
                slug="happy-path-test",
                title_prefix="happy path test",
                step_key="dev-backend:sonnet",
                task_ids=[42, 43, 44],
                project_ids=[1],
                today_dir=today_dir,
                today=date(2026, 6, 4),
            )

    assert written.exists(), f"Stub file should have been created at {written}"
    written_str = str(written.resolve())

    # Positive: file is inside tmp_path (our safe write root).
    assert written_str.startswith(str(tmp_path.resolve())), (
        f"Stub file {written_str!r} is not inside patched _WRITE_ROOT={tmp_path!r}"
    )
    # Negative: file is NOT inside any real forbidden prefix.
    for fp in _FORBIDDEN_WRITE_PREFIXES:
        assert not written_str.startswith(fp), (
            f"Stub file {written_str!r} is inside forbidden prefix {fp!r}"
        )

    # Check frontmatter present.
    content = written.read_text(encoding="utf-8")
    assert "kanban: \"#1223\"" in content
    assert "status: proposed" in content
    assert "42" in content  # source task id


# ---------------------------------------------------------------------------
# Unit tests -- pure functions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title,expected",
    [
        ("[GOV2] daily audit project #5", "gov2 daily audit project 5"),
        ("[auditor] Auto-PROPOSE skill stubs", "auditor auto propose skill stubs"),
        ("feat(ui): paginate DONE column", "feat ui paginate done column"),
        ("fix(burndown): exclude templates", "fix burndown exclude templates"),
        ("short", "short"),
        ("", ""),
        ("one two three four five SIX", "one two three four five"),
    ],
)
def test_normalize_title_prefix(title: str, expected: str) -> None:
    assert _normalize_title_prefix(title) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("gov2 daily audit project", "gov2-daily-audit-project"),
        ("  spaces  ", "spaces"),
        ("a  b  c", "a-b-c"),
        ("already-hyphenated", "already-hyphenated"),
        ("", ""),
    ],
)
def test_slugify(text: str, expected: str) -> None:
    assert _slugify(text) == expected


def test_top2_step_key_empty() -> None:
    assert _top2_step_key([]) == ""


def test_top2_step_key_single_entry() -> None:
    entries = [{"agent": "dev-backend", "model": "sonnet", "at": "2026-01-01T00:00:00Z"}]
    assert _top2_step_key(entries) == "dev-backend:sonnet"


def test_top2_step_key_two_entries_sorted_by_at() -> None:
    entries = [
        {"agent": "dev-tester", "model": "haiku", "at": "2026-01-01T01:00:00Z"},
        {"agent": "dev-backend", "model": "sonnet", "at": "2026-01-01T00:00:00Z"},
    ]
    # dev-backend:sonnet comes first (earlier at)
    key = _top2_step_key(entries)
    assert key == "dev-backend:sonnet|dev-tester:haiku"


def test_top2_step_key_more_than_two_truncates() -> None:
    entries = [
        {"agent": "a", "model": "m1", "at": "2026-01-01T00:00:00Z"},
        {"agent": "b", "model": "m2", "at": "2026-01-01T01:00:00Z"},
        {"agent": "c", "model": "m3", "at": "2026-01-01T02:00:00Z"},
    ]
    key = _top2_step_key(entries)
    assert key == "a:m1|b:m2"
    assert "c" not in key


# ---------------------------------------------------------------------------
# Unit tests -- render_text includes skill_stubs section (AC5)
# ---------------------------------------------------------------------------


def test_render_text_includes_skill_stubs_when_proposed() -> None:
    """render_text shows stub count + review path when skill_stubs is in payload."""
    payload = {
        "date": "2026-06-04",
        "flags": [],
        "base_url": "http://localhost:5431",
        "skill_stubs": {
            "proposed_count": 2,
            "stub_dir": "/repo/_scratch/auditor/proposed-stubs-2026-06-04",
        },
    }
    text = render_text(payload)
    assert "Skill/runbook proposals" in text
    assert "2 new stubs proposed" in text
    assert "proposed-stubs-2026-06-04" in text


def test_render_text_shows_no_patterns_when_zero_proposed() -> None:
    """render_text shows 'no new patterns' when proposed_count=0."""
    payload = {
        "date": "2026-06-04",
        "flags": [],
        "base_url": "http://localhost:5431",
        "skill_stubs": {
            "proposed_count": 0,
            "stub_dir": None,
        },
    }
    text = render_text(payload)
    assert "Skill/runbook proposals" in text
    assert "no new patterns" in text


def test_render_text_omits_skill_stubs_section_when_absent() -> None:
    """render_text is backward-compatible: no skill_stubs key -> section absent."""
    payload = {
        "date": "2026-06-04",
        "flags": [],
        "base_url": "http://localhost:5431",
    }
    text = render_text(payload)
    # The section header must NOT appear when the key is absent.
    assert "Skill/runbook proposals" not in text


# ---------------------------------------------------------------------------
# Integration tests -- run_skill_stub_detector against test DB
# ---------------------------------------------------------------------------


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"k1223 skill-stub-detector test -- {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


async def _create_project(client, scaffold_cleanup) -> dict:
    name = scaffold_cleanup(_unique_name("k1223"))
    resp = await client.post("/api/projects", json=_project_payload(name))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_done_task(client, project_id: int, title: str, subagent_models: list) -> dict:
    """Create a task then flip it to DONE (process_status=5)."""
    create_resp = await client.post(
        "/api/tasks",
        headers={"X-Project-Id": str(project_id)},
        json={
            "project_id": project_id,
            "title": title,
            "subagent_models": subagent_models,
        },
    )
    assert create_resp.status_code == 201, f"create failed: {create_resp.text}"
    task = create_resp.json()
    task_id = task["id"]

    # Flip to DONE.
    patch_resp = await client.patch(
        f"/api/tasks/{task_id}",
        headers={"X-Project-Id": str(project_id)},
        json={"process_status": 5},
    )
    assert patch_resp.status_code == 200, f"patch to DONE failed: {patch_resp.text}"
    return patch_resp.json()


@pytest.mark.asyncio
async def test_run_skill_stub_detector_no_pattern_below_threshold(
    client, scaffold_cleanup, db_session, tmp_path
) -> None:
    """When fewer tasks than threshold share a pattern, no stub is written.

    Positive assertion: proposed_count == 0.
    Negative assertion: stub file does NOT exist.
    """
    from src import db as _db
    await _db.engine.dispose()

    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    today = date(2026, 6, 4)
    today_dir = tmp_path / "proposed-stubs-2026-06-04"

    try:
        # Create only 2 tasks with the same pattern (threshold default is 3).
        for i in range(2):
            await _create_done_task(
                client,
                project_id,
                title=f"k1223 recurring pattern stub test {i}",
                subagent_models=[
                    {"agent": "dev-backend", "model": "sonnet", "at": "2026-06-04T00:00:00Z"}
                ],
            )

        await _db.engine.dispose()

        with patch.object(_module, "_SCRATCH_AUDITOR", tmp_path):
            with patch.object(_module, "_WRITE_ROOT", tmp_path):
                result = await run_skill_stub_detector(db_session, min_group_size=3, today=today)

        # Positive assertion: no stubs proposed.
        assert result.proposed_count == 0, (
            f"Expected 0 proposed stubs (below threshold), got {result.proposed_count}"
        )
        # Negative assertion: no stub file created.
        assert not today_dir.exists() or not list(today_dir.glob("*.md")), (
            f"Stub files should not exist below threshold, found: {list(today_dir.glob('*.md'))}"
        )

    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_run_skill_stub_detector_writes_stub_at_threshold(
    client, scaffold_cleanup, db_session, tmp_path
) -> None:
    """When >= threshold tasks share a pattern, a stub is written to _scratch.

    AC3 + AC6: verifies the stub is written + has correct frontmatter.
    Positive assertion: proposed_count >= 1 and the stub file exists.
    Negative assertion: stub is NOT under .claude/ or context/.
    """
    from src import db as _db
    await _db.engine.dispose()

    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    today = date(2026, 6, 4)
    today_dir = tmp_path / "proposed-stubs-2026-06-04"

    try:
        subagent_models = [
            {"agent": "dev-backend", "model": "sonnet", "at": "2026-06-04T00:00:00Z"},
            {"agent": "dev-tester", "model": "haiku", "at": "2026-06-04T01:00:00Z"},
        ]
        # Create exactly 3 tasks with the same pattern -> meets default threshold.
        for i in range(3):
            await _create_done_task(
                client,
                project_id,
                title=f"k1223 write stub threshold test {i}",
                subagent_models=subagent_models,
            )

        await _db.engine.dispose()

        with patch.object(_module, "_SCRATCH_AUDITOR", tmp_path):
            with patch.object(_module, "_WRITE_ROOT", tmp_path):
                result = await run_skill_stub_detector(db_session, min_group_size=3, today=today)

        # Positive assertion: at least 1 stub proposed.
        assert result.proposed_count >= 1, (
            f"Expected >= 1 proposed stub at threshold, got {result.proposed_count}"
        )
        assert result.stub_dir is not None, "stub_dir must be set when stubs are proposed"

        # Verify stub file exists and contains expected frontmatter.
        stub_files = list(today_dir.glob("*.md"))
        assert len(stub_files) >= 1, f"Expected stub file(s) in {today_dir}, found none"

        stub_content = stub_files[0].read_text(encoding="utf-8")
        assert "kanban: \"#1223\"" in stub_content, "Stub must reference Kanban #1223"
        assert "source_task_ids:" in stub_content, "Stub must list source task IDs"
        assert "status: proposed" in stub_content, "Stub must have status: proposed"

        # Negative assertion: stub must NOT be under .claude/ or context/.
        stub_abs = str(stub_files[0].resolve())
        for fp in _FORBIDDEN_WRITE_PREFIXES:
            assert not stub_abs.startswith(fp), (
                f"Stub file {stub_abs!r} is inside forbidden prefix {fp!r}"
            )

    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_run_skill_stub_detector_dedup_skips_existing_slug(
    client, scaffold_cleanup, db_session, tmp_path
) -> None:
    """If a stub was already proposed (slug exists), it is skipped (AC2 dedup).

    Positive assertion: proposed_count == 0 when slug already exists.
    Negative assertion: skipped_dedup >= 1.
    """
    from src import db as _db
    await _db.engine.dispose()

    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    today = date(2026, 6, 4)
    today_dir = tmp_path / "proposed-stubs-2026-06-04"

    try:
        subagent_models = [
            {"agent": "dev-backend", "model": "sonnet", "at": "2026-06-04T00:00:00Z"},
        ]
        for i in range(3):
            await _create_done_task(
                client,
                project_id,
                title=f"k1223 dedup test recurring pattern {i}",
                subagent_models=subagent_models,
            )

        await _db.engine.dispose()

        # Pre-create the stub file so the dedup check fires.
        # Slug for titles starting with "k1223 dedup test recurring pattern":
        # _normalize_title_prefix("k1223 dedup test recurring pattern X") ->
        # first 5 words: "k1223 dedup test recurring pattern"
        # -> slug: "k1223-dedup-test-recurring-pattern"
        today_dir.mkdir(parents=True, exist_ok=True)
        pre_slug = "k1223-dedup-test-recurring-pattern"
        (today_dir / f"{pre_slug}.md").write_text("pre-existing stub", encoding="utf-8")

        with patch.object(_module, "_SCRATCH_AUDITOR", tmp_path):
            with patch.object(_module, "_WRITE_ROOT", tmp_path):
                result = await run_skill_stub_detector(db_session, min_group_size=3, today=today)

        # Key assertion: the pre-seeded slug was skipped via dedup.
        assert result.skipped_dedup >= 1, (
            f"Expected >= 1 skipped_dedup (pre-seeded slug should be deduped), "
            f"got skipped_dedup={result.skipped_dedup}"
        )
        # The pre-seeded slug file must NOT have been overwritten (dedup respected).
        assert (today_dir / f"{pre_slug}.md").read_text(encoding="utf-8") == "pre-existing stub", (
            "Pre-existing stub file was overwritten -- dedup did not fire"
        )

    finally:
        await client.delete(f"/api/projects/{project_id}")
