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
from unittest.mock import patch

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
    extra='allow' so the GOV3 bookkeeping keys pass through POST validation.
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

        from src import db as _db
        await _db.engine.dispose()

        flags = await fetch_open_audit_flags(db_session)

        flag_ids = [f["id"] for f in flags]
        assert flag_id in flag_ids, (
            f"Expected flag id={flag_id} in fetch_open_audit_flags, got ids={flag_ids}"
        )

        our_flag = next(f for f in flags if f["id"] == flag_id)
        assert our_flag["project"] == project["name"]
        assert our_flag["title"] == "k1217 active-project flag"
        assert our_flag["streak"] == 2
        assert our_flag["severity"] == "high"
        assert our_flag["verdict"] == "review"

    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# Area 2b + 2c — flags from killed/paused projects are excluded (parametrized)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("state,patch_endpoint,patch_body,state_key", [
    (
        "killed",
        "kill",
        {"reason": "k1217 digest integration test — killed project"},
        "is_killed",
    ),
    (
        "paused",
        "pause",
        {"reason": "k1217 digest integration test — paused project"},
        "is_paused",
    ),
])
async def test_fetch_open_audit_flags_excludes_inactive_project_flags(
    client, scaffold_cleanup, db_session, state, patch_endpoint, patch_body, state_key
) -> None:
    """Flags from a killed or paused project must NOT appear in the results.

    Paired positive+negative assertion per anti-hackable-test discipline.
    """
    # Project A (active) — flag SHOULD appear.
    project_active = await _create_project(client, scaffold_cleanup)
    active_id = project_active["id"]

    # Project B (will be killed/paused) — flag should NOT appear.
    project_inactive = await _create_project(client, scaffold_cleanup)
    inactive_id = project_inactive["id"]

    try:
        flag_active = await _create_audit_flag_task(
            client, active_id, title=f"k1217 active flag ({state} test)"
        )
        flag_inactive = await _create_audit_flag_task(
            client, inactive_id, title=f"k1217 {state}-project flag"
        )

        # Apply killed/paused state to project B.
        state_resp = await client.post(
            f"/api/projects/{inactive_id}/{patch_endpoint}",
            json=patch_body,
        )
        assert state_resp.status_code == 200, state_resp.text
        assert state_resp.json()[state_key] is True

        from src import db as _db
        await _db.engine.dispose()

        flags = await fetch_open_audit_flags(db_session)
        flag_ids = [f["id"] for f in flags]

        # Positive assertion: active-project flag is present.
        assert flag_active["id"] in flag_ids, (
            f"Active-project flag {flag_active['id']} should be in results"
        )

        # Negative assertion: inactive-project flag is absent.
        assert flag_inactive["id"] not in flag_ids, (
            f"{state}-project flag {flag_inactive['id']} should NOT be in results "
            f"(got ids={flag_ids})"
        )

    finally:
        await client.delete(f"/api/projects/{active_id}")
        await client.delete(f"/api/projects/{inactive_id}")


# ---------------------------------------------------------------------------
# Area 3 — End-to-end digest fire: 1 flag in active project, mock SMTP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_digest_fire_e2e_sends_correct_email_for_flag(
    client, scaffold_cleanup, smtp_env, smtp_success_mock, monkeypatch
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
    monkeypatch.setenv("GMAIL_SMTP_USER", "sender@gmail.com")
    monkeypatch.setenv("DIGEST_EMAIL_RECIPIENT", "operator@example.com")
    monkeypatch.setenv("WEB_BASE_URL", "http://localhost:5431")

    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]

    try:
        flag_task = await _create_audit_flag_task(
            client, project_id, title="k1217 e2e flag task"
        )
        flag_id = flag_task["id"]

        with patch("smtplib.SMTP", return_value=smtp_success_mock):
            resp = await client.post("/api/digest/fire")

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True, f"Expected ok=True, got detail={body['detail']!r}"
        assert body["detail"] == "sent"
        assert body["recipient"] == "operator@example.com"

        assert body["flag_count"] >= 1, (
            f"Expected flag_count >= 1 (our flag id={flag_id}), got {body['flag_count']}"
        )

        subject = body["subject"]
        flag_count = body["flag_count"]
        if flag_count == 1:
            assert "1 open flag" in subject, f"Subject should mention '1 open flag': {subject!r}"
        else:
            assert str(flag_count) in subject, f"Subject should mention count {flag_count}: {subject!r}"

        assert smtp_success_mock.sendmail.call_count == 1, (
            f"Expected sendmail called once, got {smtp_success_mock.sendmail.call_count}"
        )

        sendmail_args = smtp_success_mock.sendmail.call_args
        to_arg = sendmail_args[0][1]
        assert "operator@example.com" in to_arg, (
            f"Expected 'operator@example.com' in sendmail to={to_arg!r}"
        )

        import email as _email_lib

        msg_string = sendmail_args[0][2]
        parsed_msg = _email_lib.message_from_string(msg_string)

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

        assert "k1217 e2e flag task" in all_body_text, (
            f"Email body should contain the flag title; "
            f"decoded body excerpt: {all_body_text[:300]!r}"
        )

    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# Area 3 (negative) — digest fire with zero flags: SMTP called once, ok=True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_digest_fire_e2e_no_flags_sends_empty_digest(
    client, scaffold_cleanup, smtp_env, smtp_success_mock, monkeypatch
) -> None:
    """Positive control: digest fires successfully even when flag count is zero.

    Uses a freshly-created project with NO flag tasks so we can assert
    flag_count is predictable relative to that project. The response-level
    flag_count may be > 0 if other test-seeded flags exist; we only assert
    that the subject and SMTP call are consistent with the count.
    """
    monkeypatch.setenv("GMAIL_SMTP_USER", "sender@gmail.com")
    monkeypatch.setenv("DIGEST_EMAIL_RECIPIENT", "operator@example.com")

    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]

    try:
        with patch("smtplib.SMTP", return_value=smtp_success_mock):
            resp = await client.post("/api/digest/fire")

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["detail"] == "sent"

        assert smtp_success_mock.sendmail.call_count == 1

        flag_count = body["flag_count"]
        subject = body["subject"]
        if flag_count == 0:
            assert "no open flags" in subject
        else:
            assert str(flag_count) in subject

    finally:
        await client.delete(f"/api/projects/{project_id}")
