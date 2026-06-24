"""Kanban #1857 / #1852 (Phase 1) — operator-vs-AI write-authorization gate.

Covers BOTH the pure primitive (`services/operator_auth.check_operator_proof`)
and the endpoint wire-up (`PATCH /api/tasks/{id}` 403-gates `verified_by` in
{'user','operator'} unless a valid operator-proof is present).

ACTIVATION semantics under test (design §4, fail-open-when-unset):
  - gate INACTIVE (OPERATOR_ACTION_KEY unset)  -> any token incl. None is
    treated as OPERATOR; a `verified_by='user'` PATCH succeeds (200) with a WARN.
  - gate ACTIVE (key set via monkeypatch.setenv):
      verified_by='user'      no token        -> 403
      verified_by='user'      valid token     -> 200
      verified_by='operator'  no token        -> 403
      verified_by='dev-backend' no token      -> 200 (NOT a reserved literal)
      verified_by='user'      empty/wrong tok -> 403

The pure-function tests monkeypatch `OPERATOR_ACTION_KEY` directly and assert an
audit row is written for BOTH allow and deny (redirecting the audit path to a
tmp file so the assertion is hermetic).

Runs against `agent_teams_test` per conftest.py. Live `agent_teams` row count
MUST NOT drift across the session (the conftest invariant asserts it); cleanup
is DELETE /api/projects/{id} on the way out (cascades child tasks).

NOTE on the gate's module-level INACTIVE-warn guard: `operator_auth` keeps a
process-wide `_inactive_warned` flag so the WARN fires once per process. Tests
that assert the INACTIVE branch reset it via the `_reset_inactive_warn` fixture
so the branch is exercised deterministically regardless of test order.
"""

from __future__ import annotations

import json
import uuid

import pytest

from src.services import operator_auth
from src.services.operator_auth import OperatorDecision, check_operator_proof


_KEY_ENV = "OPERATOR_ACTION_KEY"


# ---- helpers ---------------------------------------------------------------


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"k1857 operator-auth fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


async def _create_project(client, scaffold_cleanup) -> dict:
    name = scaffold_cleanup(_unique_name("k1857"))
    resp = await client.post("/api/projects", json=_project_create_payload(name))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_task(client, project_id: int) -> dict:
    resp = await client.post(
        "/api/tasks",
        headers={"X-Project-Id": str(project_id)},
        json={
            "project_id": project_id,
            "title": "k1857 fixture task",
            "description": "operator-auth gate test task",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _ac(verified_by: str | None) -> list[dict]:
    """One acceptance criterion with the given verified_by attribution."""
    crit: dict = {"text": "AC under test", "status": "passed"}
    if verified_by is not None:
        crit["verified_by"] = verified_by
    return [crit]


@pytest.fixture(autouse=True)
def _reset_inactive_warn():
    """Reset the module's one-time INACTIVE-warn guard before each test so the
    fail-open branch is deterministically exercisable regardless of order."""
    operator_auth._inactive_warned = False
    yield
    operator_auth._inactive_warned = False


# ===========================================================================
# Pure primitive — check_operator_proof
# ===========================================================================


def test_check_proof_inactive_allows_any_token(monkeypatch, tmp_path):
    """Key UNSET -> OPERATOR for any token (fail-open), NO audit row written.

    AC1: when the gate is INACTIVE the audit file must NOT grow — inactive
    passes carry no signal and every task PATCH would append noise.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setattr(operator_auth, "_AUDIT_PATH", audit)

    assert check_operator_proof(None) is OperatorDecision.OPERATOR
    assert check_operator_proof("anything") is OperatorDecision.OPERATOR

    # AC1 negative assertion: the file must not have been created at all.
    assert not audit.exists(), "inactive gate must not write any audit rows"


def test_check_proof_inactive_no_audit_row(monkeypatch, tmp_path):
    """AC1 explicit spy: _write_audit is NOT called when the gate is INACTIVE.

    Complements the file-existence check above with a direct call-count assertion
    so any future refactor that moves the guard inside _write_audit is also caught.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setattr(operator_auth, "_AUDIT_PATH", audit)

    calls: list = []
    original = operator_auth._write_audit
    monkeypatch.setattr(
        operator_auth, "_write_audit", lambda *a, **kw: calls.append((a, kw))
    )
    try:
        check_operator_proof(None)
        check_operator_proof("any-token")
    finally:
        monkeypatch.setattr(operator_auth, "_write_audit", original)

    assert calls == [], f"_write_audit called {len(calls)} time(s) when gate inactive"


def test_check_proof_active_audit_written(monkeypatch, tmp_path):
    """AC2: when the gate is ACTIVE, operator-proof decisions ARE audited.

    Both an allow (valid token) and a deny (wrong token) must produce a row.
    """
    monkeypatch.setenv(_KEY_ENV, "s3cret-token")
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setattr(operator_auth, "_AUDIT_PATH", audit)

    check_operator_proof("s3cret-token")   # allow
    check_operator_proof("wrong-token")    # deny

    rows = [json.loads(line) for line in audit.read_text().splitlines()]
    assert len(rows) == 2, f"expected 2 audit rows for active gate, got {len(rows)}"
    assert rows[0]["decision"] == "operator" and rows[0]["gate_active"] is True
    assert rows[1]["decision"] == "not_operator" and rows[1]["gate_active"] is True


def test_check_proof_active_valid_token(monkeypatch, tmp_path):
    """Key SET + matching token -> OPERATOR; audit row (allow) written."""
    monkeypatch.setenv(_KEY_ENV, "s3cret-token")
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setattr(operator_auth, "_AUDIT_PATH", audit)

    assert check_operator_proof("s3cret-token") is OperatorDecision.OPERATOR

    rows = [json.loads(line) for line in audit.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["decision"] == "operator"
    assert rows[0]["gate_active"] is True
    # The secret is NEVER logged.
    assert "s3cret-token" not in audit.read_text()


def test_check_proof_active_invalid_token(monkeypatch, tmp_path):
    """Key SET + wrong token -> NOT_OPERATOR; audit row (deny) written."""
    monkeypatch.setenv(_KEY_ENV, "s3cret-token")
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setattr(operator_auth, "_AUDIT_PATH", audit)

    assert check_operator_proof("wrong") is OperatorDecision.NOT_OPERATOR

    rows = [json.loads(line) for line in audit.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["decision"] == "not_operator" and rows[0]["gate_active"] is True


def test_check_proof_active_missing_token(monkeypatch, tmp_path):
    """Key SET + no token (None) -> NOT_OPERATOR (deny)."""
    monkeypatch.setenv(_KEY_ENV, "s3cret-token")
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setattr(operator_auth, "_AUDIT_PATH", audit)

    assert check_operator_proof(None) is OperatorDecision.NOT_OPERATOR
    assert check_operator_proof("") is OperatorDecision.NOT_OPERATOR


def test_check_proof_active_emits_warn_only_when_inactive(monkeypatch, tmp_path, caplog):
    """The INACTIVE WARN fires when unset; does NOT fire when the key is set."""
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setattr(operator_auth, "_AUDIT_PATH", audit)

    monkeypatch.setenv(_KEY_ENV, "s3cret-token")
    with caplog.at_level("WARNING"):
        check_operator_proof("s3cret-token")
    assert "operator-proof gate inactive" not in caplog.text

    operator_auth._inactive_warned = False
    monkeypatch.delenv(_KEY_ENV, raising=False)
    with caplog.at_level("WARNING"):
        check_operator_proof(None)
    assert "operator-proof gate inactive: OPERATOR_ACTION_KEY unset" in caplog.text


# ===========================================================================
# Endpoint wire-up — PATCH /api/tasks/{id}
# ===========================================================================


@pytest.mark.asyncio
async def test_patch_inactive_allows_verified_by_user(
    client, scaffold_cleanup, monkeypatch
):
    """Gate INACTIVE (key unset): verified_by='user' no token -> 200 (fail-open)."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    project = await _create_project(client, scaffold_cleanup)
    pid = project["id"]
    try:
        task = await _create_task(client, pid)
        resp = await client.patch(
            f"/api/tasks/{task['id']}",
            headers={"X-Project-Id": str(pid)},
            json={"acceptance_criteria": _ac("user")},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["acceptance_criteria"][0]["verified_by"] == "user"
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_patch_active_verified_by_user_no_token_403(
    client, scaffold_cleanup, monkeypatch
):
    """Gate ACTIVE: verified_by='user' WITHOUT token -> 403 + verbatim detail.

    NEGATIVE lock paired below: the criterion must NOT have landed.
    """
    monkeypatch.setenv(_KEY_ENV, "s3cret-token")
    project = await _create_project(client, scaffold_cleanup)
    pid = project["id"]
    try:
        task = await _create_task(client, pid)
        resp = await client.patch(
            f"/api/tasks/{task['id']}",
            headers={"X-Project-Id": str(pid)},
            json={"acceptance_criteria": _ac("user")},
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"] == (
            "operator_proof_required: verified_by in {'user','operator'} "
            "is operator-only"
        )

        # NEGATIVE lock — the rejected PATCH did NOT persist the criterion.
        get_resp = await client.get(
            f"/api/tasks/{task['id']}", headers={"X-Project-Id": str(pid)}
        )
        assert get_resp.json()["acceptance_criteria"] is None
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_patch_active_verified_by_user_valid_token_200(
    client, scaffold_cleanup, monkeypatch
):
    """Gate ACTIVE: verified_by='user' WITH a valid X-Operator-Token -> 200.

    POSITIVE: the operator-attributed criterion really lands.
    """
    monkeypatch.setenv(_KEY_ENV, "s3cret-token")
    project = await _create_project(client, scaffold_cleanup)
    pid = project["id"]
    try:
        task = await _create_task(client, pid)
        resp = await client.patch(
            f"/api/tasks/{task['id']}",
            headers={
                "X-Project-Id": str(pid),
                "X-Operator-Token": "s3cret-token",
            },
            json={"acceptance_criteria": _ac("user")},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["acceptance_criteria"][0]["verified_by"] == "user"
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_patch_active_verified_by_operator_no_token_403(
    client, scaffold_cleanup, monkeypatch
):
    """Gate ACTIVE: the second reserved literal 'operator' is gated too -> 403."""
    monkeypatch.setenv(_KEY_ENV, "s3cret-token")
    project = await _create_project(client, scaffold_cleanup)
    pid = project["id"]
    try:
        task = await _create_task(client, pid)
        resp = await client.patch(
            f"/api/tasks/{task['id']}",
            headers={"X-Project-Id": str(pid)},
            json={"acceptance_criteria": _ac("operator")},
        )
        assert resp.status_code == 403, resp.text
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_patch_active_role_attribution_no_token_200(
    client, scaffold_cleanup, monkeypatch
):
    """Gate ACTIVE: a NON-reserved verified_by ('dev-backend') needs NO token.

    POSITIVE: the AI-issued role-attributed criterion lands at 200, proving the
    gate is on the EXACT reserved literals only — not on all of verified_by.
    """
    monkeypatch.setenv(_KEY_ENV, "s3cret-token")
    project = await _create_project(client, scaffold_cleanup)
    pid = project["id"]
    try:
        task = await _create_task(client, pid)
        resp = await client.patch(
            f"/api/tasks/{task['id']}",
            headers={"X-Project-Id": str(pid)},
            json={"acceptance_criteria": _ac("dev-backend")},
        )
        assert resp.status_code == 200, resp.text
        assert (
            resp.json()["acceptance_criteria"][0]["verified_by"] == "dev-backend"
        )
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_patch_active_wrong_token_403(client, scaffold_cleanup, monkeypatch):
    """Gate ACTIVE: verified_by='user' with a WRONG token -> 403."""
    monkeypatch.setenv(_KEY_ENV, "s3cret-token")
    project = await _create_project(client, scaffold_cleanup)
    pid = project["id"]
    try:
        task = await _create_task(client, pid)
        resp = await client.patch(
            f"/api/tasks/{task['id']}",
            headers={
                "X-Project-Id": str(pid),
                "X-Operator-Token": "not-the-key",
            },
            json={"acceptance_criteria": _ac("user")},
        )
        assert resp.status_code == 403, resp.text
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_patch_active_non_ac_field_no_token_200(
    client, scaffold_cleanup, monkeypatch
):
    """Gate ACTIVE: a PATCH touching NO acceptance_criteria succeeds without a
    token — the gate only fires when a reserved verified_by is actually set."""
    monkeypatch.setenv(_KEY_ENV, "s3cret-token")
    project = await _create_project(client, scaffold_cleanup)
    pid = project["id"]
    try:
        task = await _create_task(client, pid)
        resp = await client.patch(
            f"/api/tasks/{task['id']}",
            headers={"X-Project-Id": str(pid)},
            json={"title": "renamed, no AC touched"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["title"] == "renamed, no AC touched"
    finally:
        await client.delete(f"/api/projects/{pid}")
