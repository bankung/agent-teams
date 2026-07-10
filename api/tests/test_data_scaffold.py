"""Tests for services.data_scaffold + its POST /api/projects wiring (Kanban #1308).

Coverage:
  1. `scaffold_data_analytics` (tmp_path, DB-free) — fresh target gets all 5
     template files; idempotent re-call skips everything and never overwrites
     a mutated dest; path-traversal guard raises ValueError. Mirrors the
     conventions in test_zero_config_scaffold.py.
  2. Team gating via the real POST /api/projects wiring (DB-backed, mirrors
     the existing #1618 working_path-gate tests in test_routes_smoke.py) —
     team='data-analytics' gets the starter tree under working_path,
     team='dev' does NOT (no regression on the pre-existing orchestration copy).
  3. sample_sales.csv shape — the COMMITTED generated file: exactly 1000 data
     rows, 8 columns, header match, 10-20 anomaly rows, ~6-month date span,
     no PII columns.
"""

from __future__ import annotations

import csv
import tempfile
import uuid
from datetime import date
from pathlib import Path

import pytest

# Real source repo inside the container (FastAPI volume-mount) — same
# convention as test_zero_config_scaffold.py.
AGENT_TEAMS_ROOT = Path("/repo")

_EXPECTED_RELS = (
    "data/raw/sample_sales.csv",
    "data/raw/README.md",
    "data/cleaned/README.md",
    "analysis/outputs/README.md",
    "README.md",
)


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _has_file(target: Path, rel: str) -> bool:
    return (target / rel).is_file()


# =============================================================================
# 1. scaffold_data_analytics — unit, tmp_path, DB-free
# =============================================================================


def test_scaffold_fresh_target_copies_all_five_files() -> None:
    from src.services.data_scaffold import scaffold_data_analytics

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "proj-fresh"
        report = scaffold_data_analytics(
            target_path=target, agent_teams_root=AGENT_TEAMS_ROOT
        )

        for rel in _EXPECTED_RELS:
            assert _has_file(target, rel), f"missing {rel}"
            assert rel in report.copied
        assert report.skipped == []
        assert report.errors == []


def test_scaffold_idempotent_second_call_skips_all() -> None:
    """Call twice → second call's `copied` is empty and every file from the
    first call appears under `skipped` (core idempotent-add contract)."""
    from src.services.data_scaffold import scaffold_data_analytics

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "proj-idem"
        first = scaffold_data_analytics(
            target_path=target, agent_teams_root=AGENT_TEAMS_ROOT
        )
        second = scaffold_data_analytics(
            target_path=target, agent_teams_root=AGENT_TEAMS_ROOT
        )

        assert first.copied  # sanity — first run actually copied something
        assert second.copied == []
        assert set(_EXPECTED_RELS).issubset(set(second.skipped))
        assert second.errors == []


def test_scaffold_never_overwrites_mutated_dest() -> None:
    """Mutate a landed dest file, re-call → bytes stay as the mutation, proving
    the skip is a real no-overwrite (not a vacuous no-op on an untouched file)."""
    from src.services.data_scaffold import scaffold_data_analytics

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "proj-mutate"
        scaffold_data_analytics(target_path=target, agent_teams_root=AGENT_TEAMS_ROOT)

        sentinel = "MUTATED BY TEST — must survive re-scaffold"
        (target / "README.md").write_text(sentinel, encoding="utf-8")

        report = scaffold_data_analytics(
            target_path=target, agent_teams_root=AGENT_TEAMS_ROOT
        )

        assert (target / "README.md").read_text(encoding="utf-8") == sentinel
        assert "README.md" in report.skipped
        assert "README.md" not in report.copied


def test_scaffold_path_traversal_guard() -> None:
    """target_path == agent_teams_root → ValueError (refuses to write into the
    harness source repo). Mirrors test_zero_config_scaffold's guard test."""
    from src.services.data_scaffold import scaffold_data_analytics

    with pytest.raises(ValueError, match="resolves to or under"):
        scaffold_data_analytics(
            target_path=AGENT_TEAMS_ROOT, agent_teams_root=AGENT_TEAMS_ROOT
        )


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
async def test_create_project_data_analytics_team_gets_starter_tree(
    client, scaffold_cleanup, tmp_path
) -> None:
    """team='data-analytics' + working_path=<real dir> → the 5 starter files land."""
    name = scaffold_cleanup(_unique_name("proj-1308-da"))
    payload = _project_create_payload(
        name, team="data-analytics", working_path=str(tmp_path)
    )
    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 201, resp.text

    for rel in _EXPECTED_RELS:
        assert (tmp_path / rel).is_file(), f"missing {rel} for data-analytics team"


@pytest.mark.asyncio
@pytest.mark.parametrize("team", ["dev", "novel", "general", "seo", "sem"])
async def test_create_project_non_data_analytics_teams_do_not_get_starter_tree(
    client, scaffold_cleanup, tmp_path, team: str
) -> None:
    """Non-data-analytics teams + working_path=<real dir> → orchestration lands
    (sanity: the target_is_dir branch actually fired), the data starter tree
    does NOT (no regression on the pre-#1308 behavior for every other team)."""
    name = scaffold_cleanup(_unique_name(f"proj-1308-{team}"))
    payload = _project_create_payload(name, team=team, working_path=str(tmp_path))
    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 201, resp.text

    # Sanity: orchestration DID land (proves the negative assertion below isn't
    # vacuously true because the whole target_is_dir branch was skipped).
    assert (tmp_path / "CLAUDE.md").is_file()

    for rel in _EXPECTED_RELS:
        assert not (tmp_path / rel).exists(), f"unexpected {rel} for team={team!r}"


# =============================================================================
# 3. sample_sales.csv shape — the committed generated file
# =============================================================================

# File-relative (not AGENT_TEAMS_ROOT-based) so this test doesn't depend on the
# /repo container-mount convention — it only assumes the standard
# api/tests/../src/templates/ package layout, same as this test file's own location.
_CSV_PATH = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "templates"
    / "data_analytics"
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
