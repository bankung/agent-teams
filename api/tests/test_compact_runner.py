"""Tests for the compact runner (CTX-4, Kanban #719).

Covers:
1. POSITIVE — happy path: run_compact moves Recent Activity → archive,
   replaces Compacted History with the LLM summary, inserts session_compacts
   row with cost computed from SDK-reported usage tokens.
2. POSITIVE — archive ordinal increments across successive compacts
   (compact_001 → compact_002 → ...).
3. POSITIVE — sessions.status flips active → compacting → active.
4. POSITIVE — cost decimal value matches the locked snapshot for
   1234 input + 234 output haiku tokens.
5. NEGATIVE — concurrent run_compact: second call hits 409 with locked detail.
6. NEGATIVE — ANTHROPIC_API_KEY missing: POST returns 503 with locked detail.
7. NEGATIVE — provider 500: POST returns 502, status released, no archive,
   no session_compacts row.
8. NEGATIVE — closed session: POST returns 400 with locked detail.
9. NEGATIVE — missing session id: POST returns 404.
10. NEGATIVE — bad trigger_kind: POST returns 422 (Pydantic).
11. SCHEMA — SessionCompactRequest defaults / accepts each literal.

All HTTP tests use respx to stub the Anthropic /v1/messages endpoint — never
hit the real API. The key is set via monkeypatch.setenv unless the test
specifically asserts the missing-key path.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import ValidationError


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str, *, team: str = "dev") -> dict:
    return {
        "name": name,
        "description": f"test fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": team,
    }


# Stubbed Anthropic /v1/messages response. Locked numbers in `usage` so the
# cost-snapshot test below stays deterministic.
_STUB_SUMMARY_TEXT = "<<stubbed compacted summary>>"
_STUB_INPUT_TOKENS = 1234
_STUB_OUTPUT_TOKENS = 234
_STUB_RESPONSE_JSON = {
    "id": "msg_test",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": _STUB_SUMMARY_TEXT}],
    "model": "claude-haiku-4-5-20251001",
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {
        "input_tokens": _STUB_INPUT_TOKENS,
        "output_tokens": _STUB_OUTPUT_TOKENS,
    },
}

_ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"


@pytest.fixture
def session_fs_cleanup():
    """Remove `_sessions/<id>/` dirs created during a test."""
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


@pytest.fixture
def stub_anthropic():
    """respx mock for the Anthropic /v1/messages endpoint.

    Yields the route so individual tests can override the response (e.g. a
    500 error path). Default response = `_STUB_RESPONSE_JSON`. The fixture
    enforces `assert_all_called=False` so tests that don't actually call
    Anthropic (e.g. the closed-session 400 path) don't blow up at teardown.
    """
    with respx.mock(assert_all_called=False) as router:
        route = router.post(_ANTHROPIC_MESSAGES_URL).mock(
            return_value=httpx.Response(200, json=_STUB_RESPONSE_JSON)
        )
        yield router, route


async def _make_session_with_activity(
    client, *, scaffold_cleanup_register, session_fs_cleanup_register
) -> tuple[int, int]:
    """Create a project + session + append one Recent Activity entry.

    Returns `(project_id, session_id)`. Activity content gives the LLM
    something non-empty to summarize.
    """
    name = _unique_name("compact")
    scaffold_cleanup_register(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    s = await client.post("/api/sessions", json={"project_id": pid})
    sid = s.json()["id"]
    session_fs_cleanup_register(sid)

    # Seed Recent Activity so the runner has non-empty input.
    await client.post(
        f"/api/sessions/{sid}/activity",
        json={
            "summary": "ran task #1; pushed commit abc123; tests passed",
            "role": "dev-backend",
            "kind": "summary",
        },
    )
    return pid, sid


# =============================================================================
# Schema-level
# =============================================================================


def test_session_compact_request_default_trigger_is_manual() -> None:
    from src.schemas.session import SessionCompactRequest

    r = SessionCompactRequest()
    assert r.trigger_kind == "manual"


def test_session_compact_request_accepts_each_literal() -> None:
    from src.schemas.session import SessionCompactRequest

    for k in ("size", "manual", "run_count"):
        r = SessionCompactRequest(trigger_kind=k)
        assert r.trigger_kind == k


def test_session_compact_request_rejects_unknown_trigger() -> None:
    from src.schemas.session import SessionCompactRequest

    with pytest.raises(ValidationError):
        SessionCompactRequest(trigger_kind="size_v2")  # type: ignore[arg-type]


# =============================================================================
# Cost computation lock — snapshot at fixed token totals
# =============================================================================


def test_compact_cost_locked_snapshot_for_haiku_45() -> None:
    """1234 input + 234 output haiku tokens => $0.0019 (rounded HALF_UP @ 4dp).

    Math: 1234 * 0.8 / 1e6 + 234 * 4 / 1e6
        = 0.0009872 + 0.000936 = 0.0019232 → 0.0019.
    """
    from src.services.cost_tracker import compute_cost

    cost = compute_cost(
        "anthropic", "claude-haiku-4-5-20251001",
        _STUB_INPUT_TOKENS, _STUB_OUTPUT_TOKENS,
    )
    assert cost == Decimal("0.0019")


# =============================================================================
# HTTP — POSITIVE happy path
# =============================================================================


@pytest.mark.asyncio
async def test_compact_happy_path_replaces_history_and_inserts_audit_row(
    client, scaffold_cleanup, session_fs_cleanup, stub_anthropic, monkeypatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    pid, sid = await _make_session_with_activity(
        client,
        scaffold_cleanup_register=scaffold_cleanup,
        session_fs_cleanup_register=session_fs_cleanup,
    )

    try:
        r = await client.post(f"/api/sessions/{sid}/compact", json={})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["session_id"] == sid
        assert body["trigger_kind"] == "manual"
        assert body["compact_model"] == "claude-haiku-4-5-20251001"
        assert body["archive_path"] == f"_sessions/{sid}/archive/compact_001.md"
        assert body["before_tokens"] >= 1
        assert body["after_tokens"] >= 1
        # Cost from stub-locked usage tokens — see snapshot test.
        assert Decimal(body["compact_cost_usd"]) == Decimal("0.0019")

        # Filesystem: archive file exists with the stubbed summary embedded.
        from src.settings import get_settings

        repo_root = Path(get_settings().repo_root)
        archive_path = repo_root / "_sessions" / str(sid) / "archive" / "compact_001.md"
        assert archive_path.is_file()
        archive_content = archive_path.read_text(encoding="utf-8")
        assert _STUB_SUMMARY_TEXT in archive_content
        assert "trigger=manual" in archive_content
        # M1: archive must contain the prior Compacted History section so the
        # forensic record captures BOTH inputs the LLM saw + its output.
        # First compact has no real prior history — session.md's skeleton
        # placeholder ("_(empty — no compacts yet)_") is what the LLM saw,
        # so it must appear verbatim in the archive.
        assert (
            "## Prior Compacted History (verbatim — input context to this compact)"
            in archive_content
        )
        assert "_(empty — no compacts yet)_" in archive_content

        # session.md: Compacted History rebuilt = stubbed summary;
        # Recent Activity reset to empty body.
        from src.services.session_store import (
            SECTION_COMPACTED_HISTORY,
            SECTION_RECENT_ACTIVITY,
            get_section_text,
        )

        compacted = get_section_text(sid, SECTION_COMPACTED_HISTORY, repo_root)
        assert _STUB_SUMMARY_TEXT in compacted
        recent = get_section_text(sid, SECTION_RECENT_ACTIVITY, repo_root)
        # Empty-replace path: CTX-2 enforces trailing newline → body == "\n".
        assert recent.strip() == ""

        # Status: back to 'active' after release.
        det = await client.get(f"/api/sessions/{sid}")
        assert det.json()["status"] == "active"
        assert det.json()["compacts_count"] == 1
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_compact_archive_ordinal_increments_across_runs(
    client, scaffold_cleanup, session_fs_cleanup, stub_anthropic, monkeypatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    pid, sid = await _make_session_with_activity(
        client,
        scaffold_cleanup_register=scaffold_cleanup,
        session_fs_cleanup_register=session_fs_cleanup,
    )

    try:
        r1 = await client.post(f"/api/sessions/{sid}/compact", json={})
        assert r1.status_code == 201, r1.text
        assert r1.json()["archive_path"].endswith("compact_001.md")

        # Seed a fresh activity entry so the second compact has something
        # to operate on (not strictly required — the runner accepts an
        # empty Recent Activity — but mirrors the realistic flow).
        await client.post(
            f"/api/sessions/{sid}/activity",
            json={"summary": "second batch of activity"},
        )

        r2 = await client.post(f"/api/sessions/{sid}/compact", json={})
        assert r2.status_code == 201, r2.text
        assert r2.json()["archive_path"].endswith("compact_002.md")

        # Both files on disk.
        from src.settings import get_settings

        repo_root = Path(get_settings().repo_root)
        archive_dir = repo_root / "_sessions" / str(sid) / "archive"
        names = sorted(p.name for p in archive_dir.iterdir())
        assert "compact_001.md" in names
        assert "compact_002.md" in names

        # M1: compact_002's archive must embed compact_001's LLM summary as
        # its "Prior Compacted History" section (since run_compact replaced
        # ## Compacted History with that summary before the second run).
        c2 = (archive_dir / "compact_002.md").read_text(encoding="utf-8")
        assert (
            "## Prior Compacted History (verbatim — input context to this compact)"
            in c2
        )
        assert _STUB_SUMMARY_TEXT in c2

        det = await client.get(f"/api/sessions/{sid}")
        assert det.json()["compacts_count"] == 2
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_compact_status_lock_flips_active_to_compacting_to_active(
    client, scaffold_cleanup, session_fs_cleanup, stub_anthropic, monkeypatch
) -> None:
    """The status flip is observable through the audit (compacts_count==1)
    + final status='active'. Mid-flight 'compacting' is exercised by the
    concurrency test below."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    pid, sid = await _make_session_with_activity(
        client,
        scaffold_cleanup_register=scaffold_cleanup,
        session_fs_cleanup_register=session_fs_cleanup,
    )

    try:
        before = await client.get(f"/api/sessions/{sid}")
        assert before.json()["status"] == "active"

        r = await client.post(f"/api/sessions/{sid}/compact", json={})
        assert r.status_code == 201

        after = await client.get(f"/api/sessions/{sid}")
        assert after.json()["status"] == "active"
    finally:
        await client.delete(f"/api/projects/{pid}")


# =============================================================================
# HTTP — NEGATIVE paths
# =============================================================================


@pytest.mark.asyncio
async def test_compact_concurrent_returns_409_with_locked_detail(
    client, scaffold_cleanup, session_fs_cleanup, monkeypatch
) -> None:
    """Two concurrent run_compact calls — the second hits 409.

    We delay the stubbed Anthropic call long enough that both POSTs are in
    flight at the same time; the DB-level UPDATE ... WHERE status='active'
    serializes them.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    pid, sid = await _make_session_with_activity(
        client,
        scaffold_cleanup_register=scaffold_cleanup,
        session_fs_cleanup_register=session_fs_cleanup,
    )

    async def _slow_anthropic(request):
        # Hold the response so the second POST has time to attempt the lock.
        await asyncio.sleep(0.4)
        return httpx.Response(200, json=_STUB_RESPONSE_JSON)

    try:
        with respx.mock(assert_all_called=False) as router:
            router.post(_ANTHROPIC_MESSAGES_URL).mock(side_effect=_slow_anthropic)

            results = await asyncio.gather(
                client.post(f"/api/sessions/{sid}/compact", json={}),
                client.post(f"/api/sessions/{sid}/compact", json={}),
                return_exceptions=False,
            )

        statuses = sorted(r.status_code for r in results)
        # One 201 (winner), one 409 (loser). Order of completion is
        # nondeterministic — sort to assert the multiset.
        assert statuses == [201, 409], [r.status_code for r in results]

        # Locked detail on the 409.
        loser = next(r for r in results if r.status_code == 409)
        assert loser.json() == {
            "detail": f"Session id={sid} is already compacting"
        }

        # Session ends in 'active' (winner released the lock).
        det = await client.get(f"/api/sessions/{sid}")
        assert det.json()["status"] == "active"
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_compact_missing_api_key_returns_503_with_locked_detail(
    client, scaffold_cleanup, session_fs_cleanup, monkeypatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    pid, sid = await _make_session_with_activity(
        client,
        scaffold_cleanup_register=scaffold_cleanup,
        session_fs_cleanup_register=session_fs_cleanup,
    )

    try:
        r = await client.post(f"/api/sessions/{sid}/compact", json={})
        assert r.status_code == 503
        assert r.json() == {
            "detail": "compact runner unavailable: ANTHROPIC_API_KEY not configured"
        }

        # Status released — back to 'active' (the lock was released by the
        # try/finally even though the runner failed).
        det = await client.get(f"/api/sessions/{sid}")
        assert det.json()["status"] == "active"
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_compact_provider_500_returns_502_releases_lock_no_audit(
    client, scaffold_cleanup, session_fs_cleanup, monkeypatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    pid, sid = await _make_session_with_activity(
        client,
        scaffold_cleanup_register=scaffold_cleanup,
        session_fs_cleanup_register=session_fs_cleanup,
    )

    try:
        with respx.mock(assert_all_called=False) as router:
            router.post(_ANTHROPIC_MESSAGES_URL).mock(
                return_value=httpx.Response(500, json={"error": "boom"})
            )
            r = await client.post(f"/api/sessions/{sid}/compact", json={})

        assert r.status_code == 502, r.text
        assert r.json() == {
            "detail": "compact runner: Anthropic API call failed"
        }

        # No archive file written.
        from src.settings import get_settings

        repo_root = Path(get_settings().repo_root)
        archive_dir = repo_root / "_sessions" / str(sid) / "archive"
        assert not any(p.name.startswith("compact_") for p in archive_dir.iterdir())

        # No session_compacts row inserted.
        det = await client.get(f"/api/sessions/{sid}")
        assert det.json()["compacts_count"] == 0
        # Lock released.
        assert det.json()["status"] == "active"
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_compact_on_closed_session_returns_400_with_locked_detail(
    client, scaffold_cleanup, session_fs_cleanup, monkeypatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    pid, sid = await _make_session_with_activity(
        client,
        scaffold_cleanup_register=scaffold_cleanup,
        session_fs_cleanup_register=session_fs_cleanup,
    )

    try:
        await client.patch(f"/api/sessions/{sid}", json={"status": "closed"})

        r = await client.post(f"/api/sessions/{sid}/compact", json={})
        assert r.status_code == 400
        assert r.json() == {
            "detail": f"Session id={sid} is closed; cannot compact"
        }
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_compact_on_missing_session_returns_404(
    client, monkeypatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r = await client.post("/api/sessions/999999999/compact", json={})
    assert r.status_code == 404
    assert r.json() == {"detail": "Session id=999999999 not found"}


@pytest.mark.asyncio
async def test_compact_rejects_bad_trigger_kind_with_422(
    client, scaffold_cleanup, session_fs_cleanup, monkeypatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    pid, sid = await _make_session_with_activity(
        client,
        scaffold_cleanup_register=scaffold_cleanup,
        session_fs_cleanup_register=session_fs_cleanup,
    )

    try:
        r = await client.post(
            f"/api/sessions/{sid}/compact",
            json={"trigger_kind": "size_v2"},
        )
        assert r.status_code == 422
    finally:
        await client.delete(f"/api/projects/{pid}")
