"""Tests for services.token_counter (CTX-3, Kanban #718).

Covers:
- count_tokens snapshot (chars/4 heuristic — locked).
- measure_session_prompt over a real session row + filesystem.
- check_budget NULL/over/under paths.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"test fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


@pytest.fixture
def session_fs_cleanup():
    from src.settings import get_settings

    repo_root = Path(get_settings().repo_root)
    ids: list[int] = []

    def register(session_id: int) -> int:
        ids.append(session_id)
        return session_id

    yield register
    for sid in ids:
        target = repo_root / "_sessions" / str(sid)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)


# =============================================================================
# count_tokens — locked chars/4 snapshot
# =============================================================================


def test_count_tokens_empty_string_returns_zero() -> None:
    from src.services.token_counter import count_tokens

    assert count_tokens("") == 0


def test_count_tokens_locked_snapshot_hello_world() -> None:
    """`"hello world"` is 11 chars → 11 // 4 = 2. If a future tokenizer swap
    changes this, the test fails loudly — intentional locked snapshot."""
    from src.services.token_counter import count_tokens

    assert count_tokens("hello world") == 2


def test_count_tokens_minimum_one_for_short_input() -> None:
    """Any non-empty input → >= 1. `"x"` is 1 char → max(1, 0) == 1."""
    from src.services.token_counter import count_tokens

    assert count_tokens("x") == 1


def test_count_tokens_4000_chars_returns_1000() -> None:
    """4000 chars / 4 = 1000 — sanity scaling check."""
    from src.services.token_counter import count_tokens

    assert count_tokens("a" * 4000) == 1000


def test_count_tokens_accepts_model_hint_kw() -> None:
    """`model_hint` is forward-compat — accepted, no behavior change in V1."""
    from src.services.token_counter import count_tokens

    assert count_tokens("hello world", model_hint="claude-haiku-4-5-20251001") == 2


# =============================================================================
# measure_session_prompt — exercises live DB + filesystem
# =============================================================================


@pytest.mark.asyncio
async def test_measure_session_prompt_returns_per_section_tokens(
    client, scaffold_cleanup, session_fs_cleanup, db_session
) -> None:
    from src.services.token_counter import measure_session_prompt
    from src.settings import get_settings

    name = _unique_name("measure-prompt")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)

        # Append an activity entry so Recent Activity has measurable content.
        await client.post(
            f"/api/sessions/{sid}/activity",
            json={"summary": "x" * 400},  # ~100 tokens via chars/4
        )

        result = await measure_session_prompt(
            sid,
            Path(get_settings().repo_root),
            include_card_id=None,
            db=db_session,
        )

        assert result["recent_activity_tokens"] > 0
        assert result["compacted_history_tokens"] >= 0
        assert result["card_detail_tokens"] == 0
        assert result["total_input_estimate"] == (
            result["compacted_history_tokens"]
            + result["recent_activity_tokens"]
            + result["card_detail_tokens"]
        )
        # Default ceilings from migration 0009 (#722).
        assert result["ceilings"]["compacted_history"] == 13000
        assert result["ceilings"]["recent_activity"] == 15000
        assert result["ceilings"]["card_detail"] == 6000
        assert result["ceilings"]["output_budget"] == 4000
    finally:
        await client.delete(f"/api/projects/{pid}")


# =============================================================================
# check_budget
# =============================================================================


@pytest.mark.asyncio
async def test_check_budget_null_budget_never_over(
    client, scaffold_cleanup, session_fs_cleanup, db_session
) -> None:
    from src.services.token_counter import check_budget

    name = _unique_name("budget-null")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        # Default token_budget_per_run=NULL.
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)

        result = await check_budget(sid, total_input_estimate=999_999_999, db=db_session)
        assert result["over_budget"] is False
        assert result["budget"] is None
        assert result["current"] == 999_999_999
        assert result["recommend_compact"] is False
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_check_budget_over_flips_recommend_compact(
    client, scaffold_cleanup, session_fs_cleanup, db_session
) -> None:
    from src.services.token_counter import check_budget

    name = _unique_name("budget-over")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post(
            "/api/sessions",
            json={"project_id": pid, "token_budget_per_run": 1000},
        )
        sid = s.json()["id"]
        session_fs_cleanup(sid)

        under = await check_budget(sid, total_input_estimate=500, db=db_session)
        assert under["over_budget"] is False
        assert under["budget"] == 1000
        assert under["recommend_compact"] is False

        over = await check_budget(sid, total_input_estimate=1500, db=db_session)
        assert over["over_budget"] is True
        assert over["budget"] == 1000
        assert over["current"] == 1500
        assert over["recommend_compact"] is True
    finally:
        await client.delete(f"/api/projects/{pid}")
