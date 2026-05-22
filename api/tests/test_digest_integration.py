"""Kanban #1217 — digest integration tests: fetch_open_audit_flags + e2e pipeline.

Area 2: fetch_open_audit_flags DB integration — verifies the helper returns
  only tasks from active (non-killed, non-paused) projects with the correct
  question_payload shape.

Area 3: End-to-end digest fire with a real audit-flag task in the test DB —
  verifies that SMTP.sendmail is called with correct subject/body content
  including the flag's deep-link URL.

All tests run against `agent_teams_test` per conftest.py isolation contract.
Live `agent_teams` row count is guarded by the session-scope
`_live_db_row_count_invariant` fixture.

Cleanup strategy: every project created here is soft-deleted via
`DELETE /api/projects/{id}` in a `finally` block (the API soft-deletes
cascade tasks). scaffold_cleanup handles on-disk folder teardown.

No raw SQL DML — project/task creation goes through the API per CLAUDE.md
golden rules. The `db_session` fixture is READ-ONLY here (assertions only).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, call, patch

import pytest

from src.services.digest_template import fetch_open_audit_flags


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"k1217 digest integration fixture — {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


def _audit_flag_task_payload(project_id: int, title: str = "k1217 open flag") -> dict:
    """Task payload that matches the fetch_open_audit_flags criteria.

    interaction_kind='question' + process_status=1 (TODO) +
    question_payload.is_audit_flag='true'. The QuestionPayload schema uses
    extra='allow' so the AA3 bookkeeping keys pass through POST validation.
    """
    return {
        "project_id": project_id,
        "title": title,
        "description": "k1217 digest integration test flag task",
        "process_status": 1,  # TODO
        "interaction_kind": "question",
        "question_payload": {
            "question": "k1217 digest test: should we review this project?",
            "options": ["review", "continue"],
            "is_audit_flag": True,
            "breach_streak_days": 2,
            "latest_audit_summary": {
                "severity": "high",
                "verdict": "review",
            },
        },
    }


async def _create_project(client, scaffold_cleanup) -> dict:
    name = scaffold_cleanup(_unique_name("k1217"))
    resp = await client.post("/api/projects", json=_project_payload(name))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_audit_flag_task(client, project_id: int, title: str = "k1217 open flag") -> dict:
    resp = await client.post(
        "/api/tasks",
        headers={"X-Project-Id": str(project_id)},
        json=_audit_flag_task_payload(project_id, title),
    )
    assert resp.status_code == 201, f"create_task failed: {resp.text}"
    return resp.json()


def _make_smtp_success_mock() -> MagicMock:
    smtp = MagicMock()
    smtp.__enter__ = MagicMock(return_value=smtp)
    smtp.__exit__ = MagicMock(return_value=False)
    return smtp


# ---------------------------------------------------------------------------
# Area 2a — fetch_open_audit_flags returns flag from active project
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_open_audit_flags_returns_flag_from_active_project(
    client, scaffold_cleanup, db_session
) -> None:
    """A flag task in an active project appears in fetch_open_audit_flags results."""
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]

    try:
        flag_task = await _create_audit_flag_task(
            client, project_id, title="k1217 active-project flag"
        )
        flag_id = flag_task["id"]

        # Dispose engine pool so db_session opens a fresh connection after API writes.
        from src import db as _db
        await _db.engine.dispose()

        flags = await fetch_open_audit_flags(db_session)

        # The flag we created must be present — even if other test-seeded flags exist.
        flag_ids = [f["id"] for f in flags]
        assert flag_id in flag_ids, (
            f"Expected flag id={flag_id} in fetch_open_audit_flags, got ids={flag_ids}"
        )

        # Verify shape of our flag entry.
        our_flag = next(f for f in flags if f["id"] == flag_id)
        assert our_flag["project"] == project["name"]
        assert our_flag["title"] == "k1217 active-project flag"
        assert our_flag["streak"] == 2  # breach_streak_days from payload
        assert our_flag["severity"] == "high"
        assert our_flag["verdict"] == "review"

    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# Area 2b — flags from killed projects are excluded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_open_audit_flags_excludes_killed_project_flags(
    client, scaffold_cleanup, db_session
) -> None:
    """Flags from a is_killed=True project must NOT appear in the results.

    This is the negative assertion required by anti-hackable-test discipline —
    we verify the filter gate actually works by pairing with a positive-path
    project that IS returned.
    """
    # Project A (active) — flag SHOULD appear.
    project_active = await _create_project(client, scaffold_cleanup)
    active_id = project_active["id"]

    # Project B (will be killed) — flag should NOT appear.
    project_to_kill = await _create_project(client, scaffold_cleanup)
    kill_id = project_to_kill["id"]

    try:
        flag_active = await _create_audit_flag_task(
            client, active_id, title="k1217 active flag"
        )
        flag_killed = await _create_audit_flag_task(
            client, kill_id, title="k1217 killed-project flag"
        )

        # Kill project B.
        kill_resp = await client.post(
            f"/api/projects/{kill_id}/kill",
            json={"reason": "k1217 digest integration test — killed project"},
        )
        assert kill_resp.status_code == 200, kill_resp.text
        assert kill_resp.json()["is_killed"] is True

        # Refresh engine pool so db_session sees the latest state.
        from src import db as _db
        await _db.engine.dispose()

        flags = await fetch_open_audit_flags(db_session)
        flag_ids = [f["id"] for f in flags]

        # Positive assertion: active-project flag is present.
        assert flag_active["id"] in flag_ids, (
            f"Active-project flag {flag_active['id']} should be in results"
        )

        # Negative assertion: killed-project flag is absent.
        assert flag_killed["id"] not in flag_ids, (
            f"Killed-project flag {flag_killed['id']} should NOT be in results "
            f"(got ids={flag_ids})"
        )

    finally:
        # Delete both projects (cascade tasks).
        await client.delete(f"/api/projects/{active_id}")
        await client.delete(f"/api/projects/{kill_id}")


# ---------------------------------------------------------------------------
# Area 2c — flags from paused projects are excluded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_open_audit_flags_excludes_paused_project_flags(
    client, scaffold_cleanup, db_session
) -> None:
    """Flags from a is_paused=True project must NOT appear in the results."""
    # Project A (active) — flag SHOULD appear.
    project_active = await _create_project(client, scaffold_cleanup)
    active_id = project_active["id"]

    # Project B (will be paused) — flag should NOT appear.
    project_to_pause = await _create_project(client, scaffold_cleanup)
    pause_id = project_to_pause["id"]

    try:
        flag_active = await _create_audit_flag_task(
            client, active_id, title="k1217 active flag (pause test)"
        )
        flag_paused = await _create_audit_flag_task(
            client, pause_id, title="k1217 paused-project flag"
        )

        # Pause project B.
        pause_resp = await client.post(
            f"/api/projects/{pause_id}/pause",
            json={"reason": "k1217 digest integration test — paused project"},
        )
        assert pause_resp.status_code == 200, pause_resp.text
        assert pause_resp.json()["is_paused"] is True

        # Refresh engine pool so db_session sees the latest state.
        from src import db as _db
        await _db.engine.dispose()

        flags = await fetch_open_audit_flags(db_session)
        flag_ids = [f["id"] for f in flags]

        # Positive assertion: active-project flag is present.
        assert flag_active["id"] in flag_ids, (
            f"Active-project flag {flag_active['id']} should be in results"
        )

        # Negative assertion: paused-project flag is absent.
        assert flag_paused["id"] not in flag_ids, (
            f"Paused-project flag {flag_paused['id']} should NOT be in results "
            f"(got ids={flag_ids})"
        )

    finally:
        await client.delete(f"/api/projects/{active_id}")
        await client.delete(f"/api/projects/{pause_id}")


# ---------------------------------------------------------------------------
# Area 3 — End-to-end digest fire: 1 flag in active project, mock SMTP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_digest_fire_e2e_sends_correct_email_for_flag(
    client, scaffold_cleanup, monkeypatch
) -> None:
    """Full pipeline: active project + flag task → POST /api/digest/fire.

    Asserts:
    - Response 200 + ok=True.
    - SMTP sendmail called exactly once.
    - Recipient matches DIGEST_EMAIL_RECIPIENT env.
    - Subject contains the flag count (1).
    - Email body (text) contains the flag's deep-link URL (/review?flag=<id>).
    - Email body (text) contains the flag title.

    Negative check: SMTP called exactly once (not zero, not twice) — catches
    the "mock bypassed" class of spurious PASS.
    """
    # Set SMTP env vars.
    monkeypatch.setenv("DIGEST_EMAIL_ENABLED", "true")
    monkeypatch.setenv("GMAIL_SMTP_USER", "sender@gmail.com")
    monkeypatch.setenv("GMAIL_SMTP_APP_PASSWORD", "app-pw-16-chars-x")
    monkeypatch.setenv("DIGEST_EMAIL_RECIPIENT", "operator@example.com")
    monkeypatch.setenv("WEB_BASE_URL", "http://localhost:5431")

    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]

    try:
        flag_task = await _create_audit_flag_task(
            client, project_id, title="k1217 e2e flag task"
        )
        flag_id = flag_task["id"]

        smtp_mock = _make_smtp_success_mock()
        with patch("smtplib.SMTP", return_value=smtp_mock) as mock_smtp_cls:
            resp = await client.post("/api/digest/fire")

        # --- wire-level assertions ---
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True, f"Expected ok=True, got detail={body['detail']!r}"
        assert body["detail"] == "sent"
        assert body["recipient"] == "operator@example.com"

        # --- flag count in response (positive: must be >= 1 because we created one) ---
        assert body["flag_count"] >= 1, (
            f"Expected flag_count >= 1 (our flag id={flag_id}), got {body['flag_count']}"
        )

        # --- subject contains the count ---
        subject = body["subject"]
        flag_count = body["flag_count"]
        if flag_count == 1:
            assert "1 open flag" in subject, f"Subject should mention '1 open flag': {subject!r}"
        else:
            assert str(flag_count) in subject, f"Subject should mention count {flag_count}: {subject!r}"

        # --- SMTP sendmail called exactly once ---
        assert smtp_mock.sendmail.call_count == 1, (
            f"Expected sendmail called once, got {smtp_mock.sendmail.call_count}"
        )

        # --- recipient in sendmail args ---
        sendmail_args = smtp_mock.sendmail.call_args
        # sendmail(from_addr, [to], msg_string) — positional args
        to_arg = sendmail_args[0][1]  # second positional arg
        assert "operator@example.com" in to_arg, (
            f"Expected 'operator@example.com' in sendmail to={to_arg!r}"
        )

        # --- email body contains deep-link for our flag ---
        # sendmail third arg is the full MIME string (bodies are base64-encoded).
        # Parse the MIME message to decode the text/plain part for assertion.
        import email as _email_lib
        import quopri as _quopri
        import base64 as _base64

        msg_string = sendmail_args[0][2]
        parsed_msg = _email_lib.message_from_string(msg_string)

        # Collect decoded body text from all MIME parts.
        decoded_bodies: list[str] = []
        if parsed_msg.is_multipart():
            for part in parsed_msg.walk():
                if part.get_content_type() in ("text/plain", "text/html"):
                    charset = part.get_content_charset("utf-8") or "utf-8"
                    payload = part.get_payload(decode=True)
                    if payload:
                        decoded_bodies.append(payload.decode(charset, errors="replace"))
        else:
            payload = parsed_msg.get_payload(decode=True)
            if payload:
                charset = parsed_msg.get_content_charset("utf-8") or "utf-8"
                decoded_bodies.append(payload.decode(charset, errors="replace"))

        all_body_text = "\n".join(decoded_bodies)

        deep_link = f"/review?flag={flag_id}"
        assert deep_link in all_body_text, (
            f"Email body should contain deep link {deep_link!r}; "
            f"decoded body excerpt: {all_body_text[:300]!r}"
        )

        # --- email body contains the flag title ---
        assert "k1217 e2e flag task" in all_body_text, (
            f"Email body should contain the flag title; "
            f"decoded body excerpt: {all_body_text[:300]!r}"
        )

    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# Area 3 (negative) — digest fire with zero flags: SMTP called once, ok=True,
# subject says "no open flags"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_digest_fire_e2e_no_flags_sends_empty_digest(
    client, scaffold_cleanup, monkeypatch
) -> None:
    """Positive control: digest fires successfully even when flag count is zero.

    Uses a freshly-created project with NO flag tasks so we can assert
    flag_count is predictable relative to that project. The response-level
    flag_count may be > 0 if other test-seeded flags exist; we only assert
    that the subject and SMTP call are consistent with the count.
    """
    monkeypatch.setenv("DIGEST_EMAIL_ENABLED", "true")
    monkeypatch.setenv("GMAIL_SMTP_USER", "sender@gmail.com")
    monkeypatch.setenv("GMAIL_SMTP_APP_PASSWORD", "app-pw-16-chars-x")
    monkeypatch.setenv("DIGEST_EMAIL_RECIPIENT", "operator@example.com")

    # Create a project but add NO flag tasks — so this project contributes 0 flags.
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]

    try:
        smtp_mock = _make_smtp_success_mock()
        with patch("smtplib.SMTP", return_value=smtp_mock):
            resp = await client.post("/api/digest/fire")

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["detail"] == "sent"

        # SMTP called exactly once regardless of flag count.
        assert smtp_mock.sendmail.call_count == 1

        # Subject is consistent with the flag count from the response.
        flag_count = body["flag_count"]
        subject = body["subject"]
        if flag_count == 0:
            assert "no open flags" in subject
        else:
            assert str(flag_count) in subject

    finally:
        await client.delete(f"/api/projects/{project_id}")
