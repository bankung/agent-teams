"""Kanban #2100 — Tier-3 email SEND routes (reply / forward / send-internal / external-send).

Covers the 8 new send routes for Gmail + Outlook that COMPOSE the same two gates
as every other mutation route:
  1. Layer-0 (#1799) per-agent-name tool-grant gate — _enforce_tool_grant_or_403
  2. Tier gate   (#1859) operator-proof tier gate    — _enforce_operator_tier_or_403
     EXCEPT external-send, which escalates via _escalate_external_send_or_202
     (202 HALT unless the operator-proof is present).

Tier model under test:
  reply / forward    -> EmailTier.REPLY          (PROOF — 403 if absent + ACTIVE)
  send-internal      -> EmailTier.SEND_INTERNAL  (PROOF)
  external-send      -> EmailTier.EXTERNAL_SEND  (202 HALT unless proven)

What these tests lock:
  - AC[0]/AC[3]: tier-gate enforcement — gate ACTIVE + no token -> 403 (NEGATIVE
    lock: upstream send NEVER called); gate ACTIVE + valid token -> 200 (POSITIVE:
    upstream send really fires); gate INACTIVE -> dormant passthrough.
  - AC[1]: external-send WITHOUT proof -> 202 HALT + NO send.
  - AC[2]: an EXECUTED send creates a Kanban [email-audit] chore task (real DB row)
    + one email-actions.jsonl line.
  - Schema validation: missing recipient / body -> 422.

Mirrors test_email_tier_gate.py fixtures (creds injection, store cleanup, gate
activation via monkeypatch.setenv). The gate/schema tests use a cache-seeded
non-real project id (touch NO DB rows); the D5 task-row test creates a REAL
project via POST /api/projects (mirrors test_task_close_auto_insert.py) and
reads the resulting Kanban row back through the public API. Runs against
agent_teams_test per conftest — the live agent_teams row-count invariant holds.
"""

from __future__ import annotations

import datetime
import json
import uuid
from pathlib import Path

import pytest

from src.routers import tools_email
from src.routers.tools_email import EmailTier

# Cache-seeded non-real project id (mirrors test_email_tier_gate._PROJ).
_PROJ = 9996
_BASE = "/api/tools/email"
_HDR = {"X-Project-Id": str(_PROJ)}
_KEY_ENV = "OPERATOR_ACTION_KEY"
_TOKEN = "s3cret-operator-token"


# ---------------------------------------------------------------------------
# Fixtures — mirror test_email_tier_gate
# ---------------------------------------------------------------------------


def _fake_gmail_creds() -> object:
    from unittest.mock import MagicMock

    from google.oauth2.credentials import Credentials as RealCreds

    creds = MagicMock(spec=RealCreds)
    creds.expiry = datetime.datetime(2099, 1, 1, 0, 0, 0)
    creds._at_email_cache = "test@gmail.com"
    return creds


def _fake_outlook_creds() -> dict:
    import time

    return {
        "access_token": "fake-access-token",
        "refresh_token": "fake-refresh-token",
        "expires_in": 3600,
        "_acquired_at": time.time(),
        "id_token_claims": {"preferred_username": "test@outlook.com"},
    }


@pytest.fixture(autouse=True)
def _clean_email_stores():
    from src.tools.email import gate, token_store

    for provider in ("gmail", "outlook"):
        token_store._CACHE.pop((provider, _PROJ), None)
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    gate._DAILY_UNITS.pop((_PROJ, today), None)
    yield
    for provider in ("gmail", "outlook"):
        token_store._CACHE.pop((provider, _PROJ), None)
    gate._DAILY_UNITS.pop((_PROJ, today), None)


@pytest.fixture
def _actions_to_tmp(monkeypatch, tmp_path):
    audit = tmp_path / "email-actions.jsonl"
    monkeypatch.setattr(tools_email, "_EMAIL_ACTIONS_PATH", audit)
    return audit


def _read_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _seed_gmail(monkeypatch):
    from src.tools.email import token_store

    token_store._CACHE[("gmail", _PROJ)] = _fake_gmail_creds()
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")


def _seed_outlook(monkeypatch):
    from src.tools.email import token_store

    token_store._CACHE[("outlook", _PROJ)] = _fake_outlook_creds()
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")


# Suppress the D5 in-process Kanban INSERT for the cache-seeded gate/schema tests
# (project 9996 is not a real FK; a flush would error — best-effort caught, but we
# avoid the noise + keep these tests touching NO DB rows). The dedicated D5 test
# below uses a REAL project and does NOT apply this fixture.
@pytest.fixture
def _no_db_audit(monkeypatch):
    async def _noop(session, **kwargs):
        return None

    monkeypatch.setattr(tools_email, "_write_send_audit_task", _noop)


# ===========================================================================
# AC[0]/AC[3] — tier-gate enforcement on reply/forward/send-internal
# ===========================================================================


@pytest.mark.asyncio
async def test_gmail_reply_inactive_gate_no_token_proceeds(client, monkeypatch, _no_db_audit):
    """Gate INACTIVE (key unset): gmail reply proceeds with NO operator token (dormant)."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_gmail(monkeypatch)
    from src.tools.email import gmail_client

    monkeypatch.setattr(
        gmail_client, "send_reply",
        lambda c, **k: {"message_id": "m-1", "thread_id": "t-1"},
    )
    resp = await client.post(
        f"{_BASE}/gmail/reply", headers=_HDR,
        json={"message_id": "abc123", "body": "hello back"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["message_id"] == "m-1"


@pytest.mark.asyncio
async def test_gmail_reply_active_gate_no_token_403(client, monkeypatch, _no_db_audit):
    """AC[3]: gate ACTIVE + reply WITHOUT token -> 403 (REPLY tier).

    NEGATIVE lock: upstream send_reply is NEVER called.
    """
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    _seed_gmail(monkeypatch)
    from src.tools.email import gmail_client

    called: list = []
    monkeypatch.setattr(
        gmail_client, "send_reply",
        lambda c, **k: called.append(k) or {"message_id": "m-1", "thread_id": "t-1"},
    )
    resp = await client.post(
        f"{_BASE}/gmail/reply", headers=_HDR,
        json={"message_id": "abc123", "body": "hello back"},
    )
    assert resp.status_code == 403, resp.text
    assert "operator_proof_required" in resp.json()["detail"]
    assert EmailTier.REPLY.value in resp.json()["detail"]
    assert called == [], "send_reply must NOT fire when the gate rejects"


@pytest.mark.asyncio
async def test_gmail_reply_active_gate_valid_token_200(client, monkeypatch, _no_db_audit):
    """AC[3]: gate ACTIVE + reply WITH a valid token -> 200; POSITIVE: send really fires."""
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    _seed_gmail(monkeypatch)
    from src.tools.email import gmail_client

    called: list = []
    monkeypatch.setattr(
        gmail_client, "send_reply",
        lambda c, **k: called.append(k) or {"message_id": "m-9", "thread_id": "t-9"},
    )
    resp = await client.post(
        f"{_BASE}/gmail/reply",
        headers={**_HDR, "X-Operator-Token": _TOKEN},
        json={"message_id": "abc123", "body": "hello back"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["message_id"] == "m-9"
    assert len(called) == 1 and called[0]["message_id"] == "abc123"


@pytest.mark.asyncio
async def test_gmail_send_internal_active_gate_no_token_403(client, monkeypatch, _no_db_audit):
    """AC[3]: send-internal is SEND_INTERNAL tier — 403 without token + ACTIVE; NEGATIVE lock."""
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    _seed_gmail(monkeypatch)
    from src.tools.email import gmail_client

    called: list = []
    monkeypatch.setattr(
        gmail_client, "send_message",
        lambda c, **k: called.append(k) or {"message_id": "m", "thread_id": "t"},
    )
    resp = await client.post(
        f"{_BASE}/gmail/send-internal", headers=_HDR,
        json={"to": "alice@corp.com", "subject": "hi", "body": "hello"},
    )
    assert resp.status_code == 403, resp.text
    assert EmailTier.SEND_INTERNAL.value in resp.json()["detail"]
    assert called == []


@pytest.mark.asyncio
async def test_outlook_forward_active_gate_valid_token_200(client, monkeypatch, _no_db_audit):
    """AC[3]: outlook forward (REPLY tier) WITH valid token -> 200; POSITIVE: send fires."""
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    _seed_outlook(monkeypatch)
    from src.tools.email import outlook_client

    called: list = []
    monkeypatch.setattr(
        outlook_client, "send_forward",
        lambda c, **k: called.append(k) or {"message_id": None, "thread_id": None},
    )
    resp = await client.post(
        f"{_BASE}/outlook/forward",
        headers={**_HDR, "X-Operator-Token": _TOKEN},
        json={"message_id": "AAA111", "to": "bob@x.com", "body": "fyi"},
    )
    assert resp.status_code == 200, resp.text
    assert len(called) == 1 and called[0]["to"] == "bob@x.com"


# ===========================================================================
# AC[1] — external-send escalation: 202 HALT + NO send
# ===========================================================================


@pytest.mark.asyncio
async def test_gmail_external_send_no_proof_halts_202(client, monkeypatch, _no_db_audit):
    """AC[1]: external-send WITHOUT proof (gate ACTIVE) -> 202 HALT, NO send.

    NEGATIVE lock: send_message is NEVER called (the 202 fires before any upstream send).
    """
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    _seed_gmail(monkeypatch)
    from src.tools.email import gmail_client

    sends: list = []
    monkeypatch.setattr(
        gmail_client, "send_message",
        lambda c, **k: sends.append(k) or {"message_id": "m", "thread_id": "t"},
    )

    resp = await client.post(
        f"{_BASE}/gmail/external-send", headers=_HDR,
        json={"to": "stranger@external.com", "subject": "hi", "body": "hello"},
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["detail"]["halt_reason"] == "operator_confirm_required"
    # NEGATIVE lock: no mail was sent on the HALT path.
    assert sends == [], "send_message must NOT fire on the 202 HALT path"


@pytest.mark.asyncio
async def test_gmail_external_send_with_proof_fires_send(client, monkeypatch, _no_db_audit):
    """external-send WITH a valid token -> send fires (confirmed path)."""
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    _seed_gmail(monkeypatch)
    from src.tools.email import gmail_client

    sends: list = []
    monkeypatch.setattr(
        gmail_client, "send_message",
        lambda c, **k: sends.append(k) or {"message_id": "m-ext", "thread_id": "t-ext"},
    )

    resp = await client.post(
        f"{_BASE}/gmail/external-send",
        headers={**_HDR, "X-Operator-Token": _TOKEN},
        json={"to": "stranger@external.com", "subject": "hi", "body": "hello"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["message_id"] == "m-ext"
    assert len(sends) == 1


@pytest.mark.asyncio
async def test_outlook_external_send_no_proof_halts_202(client, monkeypatch, _no_db_audit):
    """AC[1]: outlook external-send WITHOUT proof -> 202 HALT + NO send."""
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    _seed_outlook(monkeypatch)
    from src.tools.email import outlook_client

    sends: list = []
    monkeypatch.setattr(
        outlook_client, "send_message",
        lambda c, **k: sends.append(k) or {"message_id": None, "thread_id": None},
    )
    resp = await client.post(
        f"{_BASE}/outlook/external-send", headers=_HDR,
        json={"to": "stranger@external.com", "subject": "hi", "body": "hello"},
    )
    assert resp.status_code == 202, resp.text
    assert sends == []


# ===========================================================================
# AC[2] — D5 audit: an executed send writes one JSONL line (cache-seeded)
# ===========================================================================


@pytest.mark.asyncio
async def test_executed_send_writes_one_action_audit_line(client, monkeypatch, _actions_to_tmp, _no_db_audit):
    """AC[2] (JSONL half): an executed reply writes exactly one email-actions.jsonl line."""
    monkeypatch.delenv(_KEY_ENV, raising=False)  # dormant — no token needed.
    _seed_gmail(monkeypatch)
    from src.tools.email import gmail_client

    monkeypatch.setattr(
        gmail_client, "send_reply",
        lambda c, **k: {"message_id": "m-1", "thread_id": "t-1"},
    )
    resp = await client.post(
        f"{_BASE}/gmail/reply", headers=_HDR,
        json={"message_id": "abc123", "body": "hello back"},
    )
    assert resp.status_code == 200, resp.text
    lines = _read_lines(_actions_to_tmp)
    assert len(lines) == 1
    assert lines[0]["action"] == "reply"
    assert lines[0]["tier"] == EmailTier.REPLY.value


# ===========================================================================
# AC[2] — D5 audit: an executed send creates a Kanban [email-audit] task (REAL DB)
# ===========================================================================


def _project_create_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"send-audit test {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


@pytest.mark.asyncio
async def test_executed_send_creates_kanban_audit_task(client, monkeypatch, scaffold_cleanup):
    """AC[2] (Kanban half): an executed send creates a [email-audit] chore task on the
    request's project, visible via the public tasks API.

    Uses a REAL project (the D5 INSERT needs a valid FK). The send itself is
    mocked (no real Gmail call). POSITIVE: a [email-audit] task appears with the
    recipient in its title + chore task_type. NEGATIVE lock: a request that 403s
    (no token, gate ACTIVE) creates NO audit task.
    """
    name = scaffold_cleanup(f"send-audit-{uuid.uuid4().hex[:8]}")
    resp = await client.post("/api/projects", json=_project_create_payload(name))
    assert resp.status_code == 201, resp.text
    pid = resp.json()["id"]
    hdr = {"X-Project-Id": str(pid)}

    from src.tools.email import gmail_client, token_store

    token_store._CACHE[("gmail", pid)] = _fake_gmail_creds()
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")
    monkeypatch.setattr(
        gmail_client, "send_message",
        lambda c, **k: {"message_id": "m-real", "thread_id": "t-real"},
    )

    async def _tasks_titles(project_id: int) -> list[str]:
        r = await client.get("/api/tasks", headers={"X-Project-Id": str(project_id)})
        assert r.status_code == 200, r.text
        return [t["title"] for t in r.json()]

    # NEGATIVE lock: gate ACTIVE + no token -> 403 -> NO audit task created.
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    blocked = await client.post(
        f"{_BASE}/gmail/send-internal", headers=hdr,
        json={"to": "alice@corp.com", "subject": "blocked", "body": "no"},
    )
    assert blocked.status_code == 403, blocked.text
    titles_after_block = await _tasks_titles(pid)
    assert not any(t.startswith("[email-audit]") for t in titles_after_block), (
        "a rejected send must NOT create an audit task"
    )

    # POSITIVE: valid token -> send fires -> exactly one [email-audit] task appears.
    ok = await client.post(
        f"{_BASE}/gmail/send-internal",
        headers={**hdr, "X-Operator-Token": _TOKEN},
        json={"to": "alice@corp.com", "subject": "hi", "body": "hello world"},
    )
    assert ok.status_code == 200, ok.text

    # Find the audit task + assert its shape.
    r = await client.get("/api/tasks", headers=hdr)
    assert r.status_code == 200, r.text
    audit = [t for t in r.json() if t["title"].startswith("[email-audit]")]
    assert len(audit) == 1, f"expected exactly one audit task, got {len(audit)}"
    task = audit[0]
    assert "alice@corp.com" in task["title"]
    assert task["task_type"] == "chore"
    assert "hello world" in (task["description"] or "")


# ===========================================================================
# Schema validation — missing recipient / body -> 422
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "route,payload",
    [
        # reply: missing body
        ("/gmail/reply", {"message_id": "abc123"}),
        # send-internal: missing recipient
        ("/gmail/send-internal", {"subject": "s", "body": "b"}),
        # send-internal: missing body
        ("/gmail/send-internal", {"to": "a@b.com", "subject": "s"}),
        # send-internal: recipient with no '@' (cheap shape guard)
        ("/gmail/send-internal", {"to": "not-an-address", "subject": "s", "body": "b"}),
        # forward: missing 'to'
        ("/gmail/forward", {"message_id": "abc123", "body": "b"}),
        # outlook reply: empty body
        ("/outlook/reply", {"message_id": "AAA111", "body": ""}),
        # outlook send-internal: missing recipient
        ("/outlook/send-internal", {"subject": "s", "body": "b"}),
    ],
)
async def test_send_schema_validation_422(client, monkeypatch, route, payload, _no_db_audit):
    """Missing/invalid recipient or body -> 422 (validation fires at the boundary).

    Gate INACTIVE so a 422 is unambiguously a SCHEMA rejection (not a gate 403).
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_gmail(monkeypatch)
    _seed_outlook(monkeypatch)
    resp = await client.post(f"{_BASE}{route}", headers=_HDR, json=payload)
    assert resp.status_code == 422, resp.text


# ===========================================================================
# Layer-0 (#1799) fires FIRST — a grant DENY 403s before the tier gate
# ===========================================================================


@pytest.mark.asyncio
async def test_layer0_grant_denial_precedes_tier_gate_on_send(client, monkeypatch, _no_db_audit):
    """A #1799 grant DENY 403s with the grant-denied detail (Layer-0 runs first)."""
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    _seed_gmail(monkeypatch)
    from src.services import tool_grants as tg

    real_check = tg.check_grant

    def _deny_check(config, role, tool_name, *, project_id=None):
        if role == "locked-role":
            return tg.GrantDecision.DENY
        return real_check(config, role, tool_name, project_id=project_id)

    monkeypatch.setattr(tools_email, "check_grant", _deny_check)

    resp = await client.post(
        f"{_BASE}/gmail/reply",
        headers={**_HDR, "X-Agent-Role": "locked-role"},
        json={"message_id": "abc123", "body": "hi"},
    )
    assert resp.status_code == 403, resp.text
    assert "tool_grant_denied" in resp.json()["detail"]
    assert "operator_proof_required" not in resp.json()["detail"]


# ===========================================================================
# WARN-1 (#2100 hardening) — send-internal recipient-domain enforcement
# ===========================================================================
#
# INTERNAL_EMAIL_DOMAIN SET   -> every to/cc/bcc address must be internal; a
#   mismatch -> 403 recipient_not_internal BEFORE any upstream send (NEGATIVE
#   lock) so an agent can't downgrade an external send through send-internal.
# INTERNAL_EMAIL_DOMAIN UNSET -> dormant fall-through (back-compat).
# The operator-proof gate is kept DORMANT (_KEY_ENV unset) in these tests so a
# 200/403 is unambiguously the recipient gate, not the tier gate.

_DOMAIN_ENV = "INTERNAL_EMAIL_DOMAIN"


@pytest.mark.asyncio
async def test_send_internal_domain_set_all_internal_passes(client, monkeypatch, _no_db_audit):
    """WARN-1: domain SET + all recipients internal -> send fires (POSITIVE)."""
    monkeypatch.delenv(_KEY_ENV, raising=False)  # tier gate dormant.
    monkeypatch.setenv(_DOMAIN_ENV, "corp.com")
    _seed_gmail(monkeypatch)
    from src.tools.email import gmail_client

    sends: list = []
    monkeypatch.setattr(
        gmail_client, "send_message",
        lambda c, **k: sends.append(k) or {"message_id": "m-int", "thread_id": "t-int"},
    )
    resp = await client.post(
        f"{_BASE}/gmail/send-internal", headers=_HDR,
        json={"to": "alice@corp.com", "cc": "bob@corp.com", "subject": "hi", "body": "hello"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["message_id"] == "m-int"
    assert len(sends) == 1, "send must fire when every recipient is internal"


@pytest.mark.asyncio
async def test_send_internal_domain_set_external_to_403_no_send(client, monkeypatch, _no_db_audit):
    """WARN-1: domain SET + external `to` -> 403 recipient_not_internal, NO send (NEGATIVE)."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv(_DOMAIN_ENV, "corp.com")
    _seed_gmail(monkeypatch)
    from src.tools.email import gmail_client

    sends: list = []
    monkeypatch.setattr(
        gmail_client, "send_message",
        lambda c, **k: sends.append(k) or {"message_id": "m", "thread_id": "t"},
    )
    resp = await client.post(
        f"{_BASE}/gmail/send-internal", headers=_HDR,
        json={"to": "stranger@external.com", "subject": "hi", "body": "hello"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["error"] == "recipient_not_internal"
    assert sends == [], "send_message must NOT fire when a recipient is external"


@pytest.mark.asyncio
async def test_send_internal_domain_set_external_cc_403_no_send(client, monkeypatch, _no_db_audit):
    """WARN-1: domain SET + internal `to` but external `cc` -> 403, NO send.

    Locks that cc/bcc are checked too (not just `to`) — the downgrade can hide in cc.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv(_DOMAIN_ENV, "corp.com")
    _seed_gmail(monkeypatch)
    from src.tools.email import gmail_client

    sends: list = []
    monkeypatch.setattr(
        gmail_client, "send_message",
        lambda c, **k: sends.append(k) or {"message_id": "m", "thread_id": "t"},
    )
    resp = await client.post(
        f"{_BASE}/gmail/send-internal", headers=_HDR,
        json={"to": "alice@corp.com", "cc": "evil@external.com", "subject": "hi", "body": "x"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["error"] == "recipient_not_internal"
    assert sends == []


@pytest.mark.asyncio
async def test_send_internal_domain_set_case_insensitive_passes(client, monkeypatch, _no_db_audit):
    """WARN-1: domain match is case-insensitive (Alice@CORP.COM vs corp.com)."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv(_DOMAIN_ENV, "corp.com")
    _seed_gmail(monkeypatch)
    from src.tools.email import gmail_client

    sends: list = []
    monkeypatch.setattr(
        gmail_client, "send_message",
        lambda c, **k: sends.append(k) or {"message_id": "m-ci", "thread_id": "t"},
    )
    resp = await client.post(
        f"{_BASE}/gmail/send-internal", headers=_HDR,
        json={"to": "Alice@CORP.COM", "subject": "hi", "body": "hello"},
    )
    assert resp.status_code == 200, resp.text
    assert len(sends) == 1


@pytest.mark.asyncio
async def test_send_internal_domain_unset_external_passes(client, monkeypatch, _no_db_audit):
    """WARN-1: domain UNSET -> dormant; an external recipient still sends (back-compat)."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.delenv(_DOMAIN_ENV, raising=False)
    _seed_gmail(monkeypatch)
    from src.tools.email import gmail_client

    sends: list = []
    monkeypatch.setattr(
        gmail_client, "send_message",
        lambda c, **k: sends.append(k) or {"message_id": "m-bc", "thread_id": "t"},
    )
    resp = await client.post(
        f"{_BASE}/gmail/send-internal", headers=_HDR,
        json={"to": "stranger@external.com", "subject": "hi", "body": "hello"},
    )
    assert resp.status_code == 200, resp.text
    assert len(sends) == 1, "dormant gate must not block any recipient"


@pytest.mark.asyncio
async def test_outlook_send_internal_domain_set_external_403_no_send(client, monkeypatch, _no_db_audit):
    """WARN-1 (Outlook sibling): domain SET + external `to` -> 403, NO send."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv(_DOMAIN_ENV, "corp.com")
    _seed_outlook(monkeypatch)
    from src.tools.email import outlook_client

    sends: list = []
    monkeypatch.setattr(
        outlook_client, "send_message",
        lambda c, **k: sends.append(k) or {"message_id": None, "thread_id": None},
    )
    resp = await client.post(
        f"{_BASE}/outlook/send-internal", headers=_HDR,
        json={"to": "stranger@external.com", "subject": "hi", "body": "hello"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["error"] == "recipient_not_internal"
    assert sends == []


@pytest.mark.asyncio
async def test_outlook_send_internal_domain_set_internal_passes(client, monkeypatch, _no_db_audit):
    """WARN-1 (Outlook sibling): domain SET + internal recipient -> send fires."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv(_DOMAIN_ENV, "corp.com")
    _seed_outlook(monkeypatch)
    from src.tools.email import outlook_client

    sends: list = []
    monkeypatch.setattr(
        outlook_client, "send_message",
        lambda c, **k: sends.append(k) or {"message_id": None, "thread_id": None},
    )
    resp = await client.post(
        f"{_BASE}/outlook/send-internal", headers=_HDR,
        json={"to": "alice@corp.com", "subject": "hi", "body": "hello"},
    )
    assert resp.status_code == 200, resp.text
    assert len(sends) == 1


@pytest.mark.asyncio
async def test_external_send_unaffected_by_internal_domain(client, monkeypatch, _no_db_audit):
    """WARN-1: the internal-domain gate is NOT applied to external-send.

    external-send is the SANCTIONED external path — setting INTERNAL_EMAIL_DOMAIN
    must not 403 it; an external recipient flows through (with a valid token).
    """
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    monkeypatch.setenv(_DOMAIN_ENV, "corp.com")
    _seed_gmail(monkeypatch)
    from src.tools.email import gmail_client

    sends: list = []
    monkeypatch.setattr(
        gmail_client, "send_message",
        lambda c, **k: sends.append(k) or {"message_id": "m-ext", "thread_id": "t"},
    )
    resp = await client.post(
        f"{_BASE}/gmail/external-send",
        headers={**_HDR, "X-Operator-Token": _TOKEN},
        json={"to": "stranger@external.com", "subject": "hi", "body": "hello"},
    )
    assert resp.status_code == 200, resp.text
    assert len(sends) == 1, "external-send must not be blocked by the internal gate"


# ===========================================================================
# WARN-2 / NIT-1 / NIT-2 (#2100 hardening) — gmail_client header CRLF strip + cap
# ===========================================================================
#
# A crafted inbound email can carry CR/LF in its Subject / Message-Id /
# References. send_reply / send_forward re-inject those into outbound MIME
# headers. Before the fix, EmailMessage.__setitem__ raises on a CR/LF value,
# surfacing as a 502 on the crafted-inbound path. After: the values are stripped
# + capped so the send is normal operation. These exercise the client directly
# (no route) with a mocked _send_raw so no mail is sent.


def test_send_reply_strips_crlf_from_injected_headers(monkeypatch):
    """WARN-2/NIT-1: CR/LF in inbound subject/message-id/references is stripped.

    POSITIVE: send_reply completes (returns the sent dict) instead of raising.
    NEGATIVE lock: the assembled raw message contains NO injected header line
    (the smuggled 'X-Injected'/'Bcc' folded by CR/LF never lands as a header).
    """
    import base64

    from src.tools.email import gmail_client

    monkeypatch.setattr(gmail_client, "_build_service", lambda creds: object())
    monkeypatch.setattr(
        gmail_client, "_fetch_headers",
        lambda service, mid: {
            "from": "victim@corp.com",
            "subject": "Re: hi\r\nBcc: smuggled@evil.com",
            "message-id": "<orig@corp.com>\r\nX-Injected: yes",
            "references": "<a@corp.com>\r\nX-Ref-Injected: yes",
            "_thread_id": "thread-1",
        },
    )

    captured: dict = {}

    def _fake_send_raw(service, raw, thread_id=None):
        captured["raw"] = base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8", "replace")
        return {"message_id": "m-1", "thread_id": thread_id}

    monkeypatch.setattr(gmail_client, "_send_raw", _fake_send_raw)

    out = gmail_client.send_reply(object(), message_id="abc", body="reply text")
    assert out["message_id"] == "m-1"  # POSITIVE: no 502/raise.
    raw = captured["raw"]
    # NEGATIVE lock: smuggled header names never appear as standalone headers.
    assert "X-Injected:" not in raw
    assert "X-Ref-Injected:" not in raw
    assert "Bcc: smuggled@evil.com" not in raw


def test_send_reply_caps_oversized_references(monkeypatch):
    """NIT-1: an oversized References chain is capped at _REFS_MAX before assignment."""
    from src.tools.email import gmail_client

    monkeypatch.setattr(gmail_client, "_build_service", lambda creds: object())
    huge_refs = "<" + ("x" * 10000) + "@corp.com>"
    monkeypatch.setattr(
        gmail_client, "_fetch_headers",
        lambda service, mid: {
            "from": "victim@corp.com",
            "subject": "hi",
            "message-id": "<orig@corp.com>",
            "references": huge_refs,
            "_thread_id": "t",
        },
    )

    captured: dict = {}

    def _fake_send_raw(service, raw, thread_id=None):
        import base64
        from email import message_from_bytes, policy

        msg_bytes = base64.urlsafe_b64decode(raw.encode("ascii"))
        # Re-parse with the modern policy: it reports the LOGICAL header value
        # (folding whitespace unfolded). The legacy compat32 parser counts the
        # CRLF+space fold chars EmailMessage inserts when serializing a long line,
        # which inflates the measured length above the assigned (already-capped)
        # value and would make this assertion measure the wrong artifact.
        captured["msg"] = message_from_bytes(msg_bytes, policy=policy.default)
        return {"message_id": "m", "thread_id": thread_id}

    monkeypatch.setattr(gmail_client, "_send_raw", _fake_send_raw)

    gmail_client.send_reply(object(), message_id="abc", body="b")
    refs_header = str(captured["msg"]["References"] or "")
    assert len(refs_header) <= gmail_client._REFS_MAX, (
        f"References must be capped at {gmail_client._REFS_MAX}, got {len(refs_header)}"
    )


def test_send_forward_strips_crlf_from_subject(monkeypatch):
    """NIT-2: CR/LF in the fetched subject is stripped before the Fwd: header assignment.

    POSITIVE: send_forward completes. NEGATIVE lock: no smuggled header folds in.
    """
    import base64

    from src.tools.email import gmail_client

    monkeypatch.setattr(gmail_client, "_build_service", lambda creds: object())
    monkeypatch.setattr(
        gmail_client, "get_message",
        lambda creds, mid: {
            "subject": "quarterly\r\nBcc: smuggled@evil.com",
            "from": "victim@corp.com",
            "date": "2026-06-11",
            "body_text": "orig body",
        },
    )

    captured: dict = {}

    def _fake_send_raw(service, raw, thread_id=None):
        captured["raw"] = base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8", "replace")
        return {"message_id": "f-1", "thread_id": None}

    monkeypatch.setattr(gmail_client, "_send_raw", _fake_send_raw)

    out = gmail_client.send_forward(object(), message_id="abc", to="bob@corp.com", body="fyi")
    assert out["message_id"] == "f-1"  # POSITIVE: no raise.
    raw = captured["raw"]
    # The Subject header must be a single folded-free line; the smuggled Bcc must
    # not have become a real header.
    assert "Bcc: smuggled@evil.com" not in raw


def test_strip_header_value_unit():
    """WARN-2 helper: _strip_header_value removes CR/LF and caps length."""
    from src.tools.email import gmail_client

    assert gmail_client._strip_header_value("a\r\nb") == "ab"
    assert gmail_client._strip_header_value("") == ""
    long = "z" * 5000
    assert len(gmail_client._strip_header_value(long)) == gmail_client._HEADER_MAX
    assert len(gmail_client._strip_header_value(long, cap=gmail_client._REFS_MAX)) == gmail_client._REFS_MAX


# ===========================================================================
# WARN-3 (#2100 hardening) — D5 audit excerpt non-printable strip
# ===========================================================================


@pytest.mark.asyncio
async def test_write_send_audit_task_strips_nonprintable(monkeypatch):
    """WARN-3: the D5 audit task description strips control chars (keeps Thai), then caps.

    Captures the Task built by _write_send_audit_task via a stubbed Task class +
    a fake session, so no DB is touched.
    """
    captured: dict = {}

    class _FakeTask:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class _FakeSession:
        def add(self, obj):  # _write_send_audit_task calls session.add(audit_task).
            return None

        async def commit(self):
            return None

        async def rollback(self):
            return None

    monkeypatch.setattr(tools_email, "Task", _FakeTask)

    body = "hello\x00\x07\x1b[31mRED\x1b[0m world ทดสอบ"
    await tools_email._write_send_audit_task(
        _FakeSession(),
        session_project_id=1,
        provider="gmail",
        action="send_internal",
        recipient="a@corp.com",
        subject="s",
        body=body,
    )
    desc = captured["description"]
    # Control chars replaced with '?'; Thai preserved; ANSI bytes gone.
    assert "\x00" not in desc and "\x1b" not in desc and "\x07" not in desc
    assert "ทดสอบ" in desc
    assert "RED" in desc  # the printable ANSI payload text survives, escape gone.


@pytest.mark.asyncio
async def test_write_send_audit_task_caps_excerpt(monkeypatch):
    """WARN-3: the excerpt is capped at _AUDIT_BODY_EXCERPT_CHARS (post-sanitize)."""
    captured: dict = {}

    class _FakeTask:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class _FakeSession:
        def add(self, obj):  # _write_send_audit_task calls session.add(audit_task).
            return None

        async def commit(self):
            return None

        async def rollback(self):
            return None

    monkeypatch.setattr(tools_email, "Task", _FakeTask)

    body = "y" * 5000
    await tools_email._write_send_audit_task(
        _FakeSession(),
        session_project_id=1,
        provider="gmail",
        action="reply",
        recipient="a@corp.com",
        subject="s",
        body=body,
    )
    desc = captured["description"]
    # The body excerpt portion is capped; the elision marker is present.
    assert "…" in desc
    # Measure the EXCERPT region only (the description template legitimately
    # contains 'y' chars — "repl[y]", "Bod[y] excerpt" — so a whole-description
    # y-count would over-count). The body is all 'y', so the run of 'y' between
    # the "Body excerpt:\n" marker and the elision char IS the capped excerpt.
    marker = "Body excerpt:\n"
    excerpt_region = desc.split(marker, 1)[1]
    excerpt_body = excerpt_region.split("…", 1)[0]
    assert excerpt_body.count("y") == tools_email._AUDIT_BODY_EXCERPT_CHARS
    assert len(excerpt_body) == tools_email._AUDIT_BODY_EXCERPT_CHARS


# ===========================================================================
# Kanban #2104 — approval_mode reflects actual gate state (not hardcoded label)
# ===========================================================================


@pytest.mark.asyncio
async def test_approval_mode_dormant_when_gate_inactive(client, monkeypatch, _actions_to_tmp, _no_db_audit):
    """(i) Gate INACTIVE (OPERATOR_ACTION_KEY unset) -> approval_mode='dormant'.

    NEGATIVE: 'operator_proof' must NOT appear — that would be the old bug.
    POSITIVE: 'dormant' appears, proving the fix threads gate state to the audit.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)  # gate dormant / fail-open.
    _seed_gmail(monkeypatch)
    from src.tools.email import gmail_client

    monkeypatch.setattr(
        gmail_client, "send_reply",
        lambda c, **k: {"message_id": "m-dormant", "thread_id": "t-d"},
    )
    resp = await client.post(
        f"{_BASE}/gmail/reply", headers=_HDR,
        json={"message_id": "abc123", "body": "hello"},
    )
    assert resp.status_code == 200, resp.text
    lines = _read_lines(_actions_to_tmp)
    assert len(lines) == 1
    row = lines[0]
    # The fix: dormant gate -> "dormant", not the old hardcoded "operator_proof".
    assert row["approval_mode"] == "dormant", (
        f"expected 'dormant' (gate inactive); got {row['approval_mode']!r}"
    )
    assert row["approval_mode"] != "operator_proof"


@pytest.mark.asyncio
async def test_approval_mode_operator_proof_when_gate_active_and_token_valid(
    client, monkeypatch, _actions_to_tmp, _no_db_audit
):
    """(ii) Gate ACTIVE + valid X-Operator-Token -> approval_mode='operator_proof'.

    POSITIVE: the token was truly verified; the audit row must reflect that.
    """
    monkeypatch.setenv(_KEY_ENV, _TOKEN)  # gate ACTIVE.
    _seed_gmail(monkeypatch)
    from src.tools.email import gmail_client

    monkeypatch.setattr(
        gmail_client, "send_reply",
        lambda c, **k: {"message_id": "m-proven", "thread_id": "t-p"},
    )
    resp = await client.post(
        f"{_BASE}/gmail/reply",
        headers={**_HDR, "X-Operator-Token": _TOKEN},
        json={"message_id": "abc123", "body": "hello"},
    )
    assert resp.status_code == 200, resp.text
    lines = _read_lines(_actions_to_tmp)
    assert len(lines) == 1
    row = lines[0]
    # Token presented + verified -> "operator_proof".
    assert row["approval_mode"] == "operator_proof", (
        f"expected 'operator_proof' (token verified); got {row['approval_mode']!r}"
    )


@pytest.mark.asyncio
async def test_approval_mode_auto_for_tier1_modify_unchanged(client, monkeypatch, _actions_to_tmp):
    """(iii) Tier-1 MODIFY actions keep approval_mode='auto' — unchanged by #2104.

    Regression guard: the fix must not disturb auto-approve MODIFY rows.
    """
    monkeypatch.setenv(_KEY_ENV, _TOKEN)  # gate ACTIVE — modify is still OPEN.
    _seed_gmail(monkeypatch)
    from src.tools.email import gmail_client

    monkeypatch.setattr(gmail_client, "modify_labels", lambda c, ids, a, r: (list(ids), []))

    resp = await client.post(
        f"{_BASE}/gmail/mark", headers=_HDR,
        json={"message_ids": ["abc123def456"], "read": True},
    )
    assert resp.status_code == 200, resp.text
    lines = _read_lines(_actions_to_tmp)
    assert len(lines) == 1
    # MODIFY tier must remain "auto" — not touched by #2104.
    assert lines[0]["approval_mode"] == "auto"
