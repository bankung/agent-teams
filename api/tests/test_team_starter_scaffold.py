"""Tests for services.team_starter_scaffold + its POST /api/projects wiring.

Generalized from test_data_scaffold.py (Kanban #1308) to cover the
team-agnostic scaffold (Kanban #1319 — social starter folders). Coverage:

  1. `scaffold_team_starter` (tmp_path, DB-free), parametrized by team —
     fresh target gets every template file for that team; idempotent re-call
     skips everything and never overwrites a mutated dest; a team with no
     bundled `templates/<team>/` dir (e.g. `dev`) is a silent no-op (empty
     report, no files); path-traversal guard raises ValueError regardless of
     team. Mirrors the conventions in test_zero_config_scaffold.py.
  2. Team gating via the real POST /api/projects wiring (DB-backed, mirrors
     the existing #1618 working_path-gate tests in test_routes_smoke.py) —
     team='data-analytics' gets the data tree, team='social' gets the social
     tree, team='dev' gets NEITHER (no regression on the pre-existing
     orchestration copy).
  3. sample_sales.csv shape — the COMMITTED generated file: exactly 1000 data
     rows, 8 columns, header match, 10-20 anomaly rows, ~6-month date span,
     no PII columns. (data-analytics only — unchanged from #1308.)
  4. sample-linkedin-post.md shape — front-matter + hook + numbered body + CTA
     + hashtags (social only, new for #1319).
"""

from __future__ import annotations

import csv
import re
import tempfile
import uuid
from datetime import date
from pathlib import Path

import pytest

# Real source repo inside the container (FastAPI volume-mount) — same
# convention as test_zero_config_scaffold.py.
AGENT_TEAMS_ROOT = Path("/repo")

_EXPECTED_RELS_BY_TEAM = {
    "data-analytics": (
        "data/raw/sample_sales.csv",
        "data/raw/README.md",
        "data/cleaned/README.md",
        "analysis/outputs/README.md",
        "README.md",
    ),
    "social": (
        "posts/drafts/README.md",
        "posts/ready/README.md",
        "posts/ready/sample-linkedin-post.md",
        "media/README.md",
        "voice.md",
        "README.md",
    ),
}
_STARTER_TEAMS = tuple(_EXPECTED_RELS_BY_TEAM)  # ("data-analytics", "social")

# Both teams' top-level README.md share the same relative path — this marker
# lets the router-level test tell them apart without a byte-for-byte diff.
_README_MARKER_BY_TEAM = {
    "data-analytics": "data analysis project",
    "social": "social content project",
}


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _has_file(target: Path, rel: str) -> bool:
    return (target / rel).is_file()


# =============================================================================
# 1. scaffold_team_starter — unit, tmp_path, DB-free
# =============================================================================


@pytest.mark.parametrize("team", _STARTER_TEAMS)
def test_scaffold_fresh_target_copies_all_files(team: str) -> None:
    from src.services.team_starter_scaffold import scaffold_team_starter

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "proj-fresh"
        report = scaffold_team_starter(
            target_path=target, agent_teams_root=AGENT_TEAMS_ROOT, team=team
        )

        expected = _EXPECTED_RELS_BY_TEAM[team]
        for rel in expected:
            assert _has_file(target, rel), f"missing {rel} for team={team!r}"
            assert rel in report.copied
        assert report.skipped == []
        assert report.errors == []


@pytest.mark.parametrize("team", _STARTER_TEAMS)
def test_scaffold_idempotent_second_call_skips_all(team: str) -> None:
    """Call twice → second call's `copied` is empty and every file from the
    first call appears under `skipped` (core idempotent-add contract)."""
    from src.services.team_starter_scaffold import scaffold_team_starter

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "proj-idem"
        first = scaffold_team_starter(
            target_path=target, agent_teams_root=AGENT_TEAMS_ROOT, team=team
        )
        second = scaffold_team_starter(
            target_path=target, agent_teams_root=AGENT_TEAMS_ROOT, team=team
        )

        assert first.copied  # sanity — first run actually copied something
        assert second.copied == []
        assert set(_EXPECTED_RELS_BY_TEAM[team]).issubset(set(second.skipped))
        assert second.errors == []


def test_scaffold_never_overwrites_mutated_dest() -> None:
    """Mutate a landed dest file, re-call → bytes stay as the mutation, proving
    the skip is a real no-overwrite (not a vacuous no-op on an untouched file).
    Single-team check (data-analytics) — the no-overwrite mechanics don't vary
    by team, only the file list does (covered above)."""
    from src.services.team_starter_scaffold import scaffold_team_starter

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "proj-mutate"
        scaffold_team_starter(
            target_path=target,
            agent_teams_root=AGENT_TEAMS_ROOT,
            team="data-analytics",
        )

        sentinel = "MUTATED BY TEST — must survive re-scaffold"
        (target / "README.md").write_text(sentinel, encoding="utf-8")

        report = scaffold_team_starter(
            target_path=target,
            agent_teams_root=AGENT_TEAMS_ROOT,
            team="data-analytics",
        )

        assert (target / "README.md").read_text(encoding="utf-8") == sentinel
        assert "README.md" in report.skipped
        assert "README.md" not in report.copied


def test_scaffold_no_template_dir_is_noop_for_unmapped_team() -> None:
    """A team with no bundled `templates/<team>/` dir (e.g. `dev`) is a silent
    no-op: empty report, no files land. This IS the per-team gate now (no
    `if project.team == ...` conditional in the caller)."""
    from src.services.team_starter_scaffold import scaffold_team_starter

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "proj-noop"
        report = scaffold_team_starter(
            target_path=target, agent_teams_root=AGENT_TEAMS_ROOT, team="dev"
        )

        assert report.copied == []
        assert report.skipped == []
        assert report.errors == []
        # No files from EITHER known team's tree leaked in.
        for rels in _EXPECTED_RELS_BY_TEAM.values():
            for rel in rels:
                assert not (target / rel).exists()


@pytest.mark.parametrize("team", _STARTER_TEAMS)
def test_scaffold_path_traversal_guard(team: str) -> None:
    """target_path == agent_teams_root → ValueError (refuses to write into the
    harness source repo). Mirrors test_zero_config_scaffold's guard test."""
    from src.services.team_starter_scaffold import scaffold_team_starter

    with pytest.raises(ValueError, match="resolves to or under"):
        scaffold_team_starter(
            target_path=AGENT_TEAMS_ROOT, agent_teams_root=AGENT_TEAMS_ROOT, team=team
        )


@pytest.mark.parametrize("team", ["..", "bogus"])
def test_scaffold_rejects_unvalidated_team(team: str) -> None:
    """Trust-boundary guard (MAJOR finding, #1319 dev-reviewer pass): a `team`
    not in `ProjectTeam.ALL` — including a traversal payload like ".." that
    would otherwise make `_templates_dir` resolve to `src/` itself — is
    rejected before path resolution ever runs. Empty report and the target
    directory is never even created, so (crucially for "..") nothing from
    the api source tree can leak in — the copy loop never walks src/."""
    from src.services.team_starter_scaffold import scaffold_team_starter

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "proj-bad-team"
        report = scaffold_team_starter(
            target_path=target, agent_teams_root=AGENT_TEAMS_ROOT, team=team
        )

        assert report.copied == []
        assert report.skipped == []
        assert report.errors == []
        # Guard fires before target_path is ever resolved/mkdir'd — proves
        # the rejection happens pre-path-resolution, not merely post-hoc.
        assert not target.exists()


# =============================================================================
# 2. Team gating — real POST /api/projects wiring (DB-backed)
# =============================================================================


def _project_create_payload(
    name: str, *, team: str = "dev", working_path: str | None = None
) -> dict:
    payload = {
        "name": name,
        "description": f"test fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": team,
    }
    if working_path is not None:
        payload["working_path"] = working_path
    return payload


@pytest.mark.asyncio
@pytest.mark.parametrize("team", _STARTER_TEAMS)
async def test_create_project_starter_team_gets_matching_tree(
    client, scaffold_cleanup, tmp_path, team: str
) -> None:
    """team='data-analytics' / 'social' + working_path=<real dir> → that
    team's starter files land (and the right README content, since both
    trees' README.md share the same relative path)."""
    name = scaffold_cleanup(_unique_name(f"proj-1319-{team}"))
    payload = _project_create_payload(name, team=team, working_path=str(tmp_path))
    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 201, resp.text

    for rel in _EXPECTED_RELS_BY_TEAM[team]:
        assert (tmp_path / rel).is_file(), f"missing {rel} for team={team!r}"

    readme_text = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert _README_MARKER_BY_TEAM[team] in readme_text

    # Cross-check: the OTHER team's unique files must NOT have landed.
    other_team = "social" if team == "data-analytics" else "data-analytics"
    other_only = set(_EXPECTED_RELS_BY_TEAM[other_team]) - set(
        _EXPECTED_RELS_BY_TEAM[team]
    )
    for rel in other_only:
        assert not (tmp_path / rel).exists(), f"unexpected {rel} for team={team!r}"


@pytest.mark.asyncio
@pytest.mark.parametrize("team", ["dev", "novel", "general", "seo", "sem"])
async def test_create_project_non_starter_teams_do_not_get_starter_tree(
    client, scaffold_cleanup, tmp_path, team: str
) -> None:
    """Non-starter teams + working_path=<real dir> → orchestration lands
    (sanity: the target_is_dir branch actually fired), NEITHER starter tree
    does (no regression on the pre-#1308/#1319 behavior for every other
    team)."""
    name = scaffold_cleanup(_unique_name(f"proj-1319-{team}"))
    payload = _project_create_payload(name, team=team, working_path=str(tmp_path))
    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 201, resp.text

    # Sanity: orchestration DID land (proves the negative assertion below isn't
    # vacuously true because the whole target_is_dir branch was skipped).
    assert (tmp_path / "CLAUDE.md").is_file()

    for rels in _EXPECTED_RELS_BY_TEAM.values():
        for rel in rels:
            assert not (
                tmp_path / rel
            ).exists(), f"unexpected {rel} for team={team!r}"


# =============================================================================
# 3. sample_sales.csv shape — the committed generated file (data-analytics only)
# =============================================================================

# File-relative (not AGENT_TEAMS_ROOT-based) so this test doesn't depend on the
# /repo container-mount convention — it only assumes the standard
# api/tests/../src/templates/ package layout, same as this test file's own location.
_CSV_PATH = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "templates"
    / "data-analytics"
    / "data"
    / "raw"
    / "sample_sales.csv"
)

_EXPECTED_HEADER = [
    "order_id",
    "customer_id",
    "sku",
    "category",
    "price",
    "qty",
    "order_date",
    "region",
]
_EXPECTED_CATEGORIES = {"electronics", "clothing", "books", "home", "food"}
_EXPECTED_REGIONS = {"bangkok", "chiang_mai", "phuket", "khon_kaen"}
_PII_LIKE_COLUMNS = {
    "name",
    "email",
    "phone",
    "address",
    "full_name",
    "first_name",
    "last_name",
    "ssn",
}


def test_sample_sales_csv_shape() -> None:
    with _CSV_PATH.open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        rows = list(reader)

    assert header == _EXPECTED_HEADER
    assert not ({h.lower() for h in header} & _PII_LIKE_COLUMNS)
    assert len(rows) == 1000
    assert all(len(r) == 8 for r in rows)

    categories = {r[3] for r in rows}
    prices = [int(r[4]) for r in rows]
    qtys = [int(r[5]) for r in rows]
    dates = [date.fromisoformat(r[6]) for r in rows]
    regions = {r[7] for r in rows}
    customer_ids = {r[1] for r in rows}
    skus = {r[2] for r in rows}

    assert categories == _EXPECTED_CATEGORIES
    assert regions == _EXPECTED_REGIONS
    assert all(10 <= p <= 15000 for p in prices)  # normal range + anomaly ceiling
    assert all(q >= 1 for q in qtys)
    # ~200 unique customers / ~50 unique SKUs — upper bound is exact by
    # construction (id pools are sized 200/50), lower bound guards against a
    # degenerate generator that collapses onto a handful of ids.
    assert 150 <= len(customer_ids) <= 200
    assert 30 <= len(skus) <= 50

    n_anomalies = sum(1 for p, q in zip(prices, qtys) if q > 50 or p > 5000)
    assert 10 <= n_anomalies <= 20

    span_days = (max(dates) - min(dates)).days
    assert span_days >= 150  # ~6-month window


# =============================================================================
# 4. sample-linkedin-post.md shape — the committed social template (new, #1319)
# =============================================================================

_LINKEDIN_POST_PATH = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "templates"
    / "social"
    / "posts"
    / "ready"
    / "sample-linkedin-post.md"
)


def test_sample_linkedin_post_shape() -> None:
    text = _LINKEDIN_POST_PATH.read_text(encoding="utf-8")

    # Front-matter block (platform / status / generated_by) delimited by
    # `---` lines. The body ALSO contains a `---` horizontal rule later on, so
    # split on the delimiter and rejoin everything past the front-matter back
    # together rather than assuming exactly 3 parts.
    parts = text.split("---\n")
    assert len(parts) >= 3, "expected a --- delimited front-matter block"
    front_matter = parts[1]
    body = "---\n".join(parts[2:])

    assert "platform: linkedin" in front_matter
    assert "status: sample" in front_matter
    assert "generated_by: scaffold" in front_matter

    assert "Three mistakes I made in my first year" in body  # hook (first line)
    assert "1. " in body and "2. " in body and "3. " in body  # numbered body
    assert "What would you cut?" in body  # single-line CTA

    # 3 hashtags (LinkedIn best-practice 3-5). `#\w+` (no space after #) so the
    # markdown H1 ("# Sample LinkedIn post") never counts as a hashtag.
    hashtags = re.findall(r"#\w+", body)
    assert hashtags == ["#leadership", "#career", "#firstyearlessons"]
