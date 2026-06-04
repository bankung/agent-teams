"""Kanban #1859 — email tier-escalation operator-proof gate (Phase 3 of #1852).

Covers the tiered operator-proof gate applied to the email tool endpoints, which
COMPOSES ON TOP OF the #1799 Layer-0 grant gate (Layer-0 answers WHICH role; this
gate answers OPERATOR-PRESENT) and reuses the #1857 Phase-1 operator-proof
primitive (`services/operator_auth.check_operator_proof` / `require_operator_proof`).

Tier model under test (per #1852 design §5 / #1859 AC):
  read           OPEN  — no operator-proof (status/usage GET succeed even ACTIVE).
  reply/send/delete   PROOF — operator-proof required; absent + ACTIVE -> 403.
  external_send  ESCALATE — operator-proof + out-of-band push/ntfy confirm + HALT.

The only mutating endpoint that exists on `/api/tools/email/*` today is `trash`
(Gmail + Outlook). Per the design, trash is the `delete` tier — so the endpoint
tests exercise the gate on trash. The reply/send_internal tiers (no endpoint yet)
and the external_send escalation are exercised at the pure-helper level so the
reusable primitive is locked for the future endpoints that will inherit it.

ACTIVATION semantics (mirror test_operator_auth — fail-open-when-unset):
  - gate INACTIVE (OPERATOR_ACTION_KEY unset) -> trash proceeds with NO token
    (the #1799 + cap gates still apply); the tier gate is DORMANT.
  - gate ACTIVE (key set via monkeypatch.setenv):
      trash, no token        -> 403 (operator_proof_required)  + NEGATIVE lock
      trash, valid token     -> 200 (the trash really happens)  + POSITIVE
      trash, wrong token     -> 403
      status/usage GET       -> 200 (read tier is OPEN regardless of token)

Runs against `agent_teams_test` per conftest.py. The endpoint tests do NOT touch
DB rows (creds are cache-seeded, upstream clients monkeypatched) so the live
`agent_teams` row-count invariant holds.
"""

from __future__ import annotations

import datetime
import json

import pytest

from src.routers import tools_email
from src.routers.tools_email import (
    EmailTier,
    _enforce_operator_tier_or_403,
    _escalate_external_send_or_202,
)
from src.services import operator_auth
from src.services.operator_auth import OperatorDecision

# A non-real project id — cache-seeded gate tests touch no FK (mirrors
# test_tools_email._PROJ).
_PROJ = 9998
_BASE = "/api/tools/email"
_HDR = {"X-Project-Id": str(_PROJ)}
_KEY_ENV = "OPERATOR_ACTION_KEY"
_TOKEN = "s3cret-operator-token"


# ---------------------------------------------------------------------------
# Fixtures — mirror test_tools_email's creds injection + store cleanup
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
    """Clear email in-memory stores + the operator-auth audit-path redirect."""
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
def _audit_to_tmp(monkeypatch, tmp_path):
    """Redirect the operator-auth audit JSONL to a tmp file so each ACTIVE/ INACTIVE
    decision is hermetically observable + never writes to the shared _scratch trail."""
    audit = tmp_path / "op-audit.jsonl"
    monkeypatch.setattr(operator_auth, "_AUDIT_PATH", audit)
    return audit


# ===========================================================================
# Pure tier-gate helper — _enforce_operator_tier_or_403
# ===========================================================================


def test_tier_helper_read_is_open_no_proof_needed():
    """`read` tier is OPEN: NOT_OPERATOR proof does not raise."""
    # Must NOT raise even with no operator proof.
    _enforce_operator_tier_or_403(EmailTier.READ, OperatorDecision.NOT_OPERATOR)


@pytest.mark.parametrize(
    "tier",
    [EmailTier.REPLY, EmailTier.SEND_INTERNAL, EmailTier.DELETE, EmailTier.EXTERNAL_SEND],
)
def test_tier_helper_above_read_requires_proof(tier):
    """Every tier above `read` raises 403 when the proof is NOT_OPERATOR.

    POSITIVE pair: the SAME tier with an OPERATOR proof does NOT raise.
    """
    from fastapi import HTTPException

    # NEGATIVE: missing proof -> 403 with the source-text-locked detail.
    with pytest.raises(HTTPException) as exc:
        _enforce_operator_tier_or_403(tier, OperatorDecision.NOT_OPERATOR)
    assert exc.value.status_code == 403
    assert "operator_proof_required" in exc.value.detail
    assert tier.value in exc.value.detail

    # POSITIVE: a valid operator proof passes the gate (no raise).
    _enforce_operator_tier_or_403(tier, OperatorDecision.OPERATOR)


# ===========================================================================
# External-send escalation — _escalate_external_send_or_202
# ===========================================================================


def test_external_send_no_proof_fires_push_and_halts_202(monkeypatch):
    """AC#2: external_send WITHOUT operator-proof -> ntfy push fired + HTTP 202 HALT.

    POSITIVE: send_push is invoked (the out-of-band confirm really fires).
    NEGATIVE lock: a 202 (NOT 200) is raised, so the caller does NOT proceed
    with the send.
    """
    from fastapi import HTTPException

    calls: list[dict] = []

    def _fake_send_push(message, **kwargs):
        calls.append({"message": message, **kwargs})

        class _R:
            ok = True
            detail = "sent"

        return _R()

    monkeypatch.setattr(tools_email, "send_push", _fake_send_push)

    with pytest.raises(HTTPException) as exc:
        _escalate_external_send_or_202(
            OperatorDecision.NOT_OPERATOR, project_id=_PROJ, summary="to bob@x.com"
        )

    # NEGATIVE lock: HALT semantics — 202, not a pass-through 200.
    assert exc.value.status_code == 202
    assert exc.value.detail["halt_reason"] == "operator_confirm_required"
    assert "operator_confirm_pending" in exc.value.detail["message"]

    # POSITIVE: the out-of-band push really fired exactly once.
    assert len(calls) == 1
    assert "awaiting your confirmation" in calls[0]["message"]


def test_external_send_with_proof_passes_through_no_push(monkeypatch):
    """external_send WITH operator-proof -> pass through (operator approved OOB).

    POSITIVE: no exception (the caller proceeds with the send).
    NEGATIVE lock: send_push is NOT called (no redundant confirm when already proven).
    """
    calls: list = []
    monkeypatch.setattr(tools_email, "send_push", lambda *a, **k: calls.append(1))

    # Must NOT raise — operator already presented the token out-of-band.
    _escalate_external_send_or_202(
        OperatorDecision.OPERATOR, project_id=_PROJ, summary="to bob@x.com"
    )
    assert calls == [], "send_push must not fire when proof is already present"


def test_external_send_push_failure_still_halts_202(monkeypatch):
    """A push send failure (send_push raising) must NOT turn the HALT into a 500.

    The 202 HALT still fires even when the out-of-band channel errors — push is
    observability, the HALT is correctness.
    """
    from fastapi import HTTPException

    def _boom(*a, **k):
        raise RuntimeError("ntfy down")

    monkeypatch.setattr(tools_email, "send_push", _boom)

    with pytest.raises(HTTPException) as exc:
        _escalate_external_send_or_202(
            OperatorDecision.NOT_OPERATOR, project_id=_PROJ, summary="x"
        )
    assert exc.value.status_code == 202


# ===========================================================================
# Endpoint wire-up — delete tier (trash) on the LIVE endpoint surface
# ===========================================================================


@pytest.mark.asyncio
async def test_gmail_trash_inactive_gate_no_token_proceeds(client, monkeypatch):
    """Gate INACTIVE (key unset): gmail trash proceeds with NO operator token.

    Proves the tier gate is DORMANT on the live deployment (fail-open). The
    upstream trash still runs (200) — only the #1799 + cap gates apply.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    from src.tools.email import gmail_client, token_store

    token_store._CACHE[("gmail", _PROJ)] = _fake_gmail_creds()
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")
    monkeypatch.setattr(gmail_client, "trash_messages", lambda c, ids: (list(ids), []))

    resp = await client.post(
        f"{_BASE}/gmail/trash", headers=_HDR, json={"message_ids": ["abc123def456"]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["trashed_count"] == 1


@pytest.mark.asyncio
async def test_gmail_trash_active_gate_no_token_403(client, monkeypatch):
    """AC#1: Gate ACTIVE + gmail trash WITHOUT token -> 403 (delete tier).

    NEGATIVE lock: the upstream trash_messages is NEVER called (the 403 fires
    before any OAuth/quota/upstream work).
    """
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    from src.tools.email import gmail_client, token_store

    token_store._CACHE[("gmail", _PROJ)] = _fake_gmail_creds()
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")

    called = []
    monkeypatch.setattr(
        gmail_client, "trash_messages", lambda c, ids: called.append(ids) or (list(ids), [])
    )

    resp = await client.post(
        f"{_BASE}/gmail/trash", headers=_HDR, json={"message_ids": ["abc123def456"]}
    )
    assert resp.status_code == 403, resp.text
    assert "operator_proof_required" in resp.json()["detail"]
    assert EmailTier.DELETE.value in resp.json()["detail"]
    # NEGATIVE lock — no upstream trash happened.
    assert called == [], "trash_messages must NOT be called when the gate rejects"


@pytest.mark.asyncio
async def test_gmail_trash_active_gate_valid_token_200(client, monkeypatch):
    """AC#1: Gate ACTIVE + gmail trash WITH a valid X-Operator-Token -> 200.

    POSITIVE: the trash actually proceeds (upstream called, ids returned).
    """
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    from src.tools.email import gmail_client, token_store

    token_store._CACHE[("gmail", _PROJ)] = _fake_gmail_creds()
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")

    called = []
    monkeypatch.setattr(
        gmail_client, "trash_messages", lambda c, ids: called.append(ids) or (list(ids), [])
    )

    resp = await client.post(
        f"{_BASE}/gmail/trash",
        headers={**_HDR, "X-Operator-Token": _TOKEN},
        json={"message_ids": ["abc123def456"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["trashed_count"] == 1
    # POSITIVE — the trash really happened.
    assert called == [["abc123def456"]]


@pytest.mark.asyncio
async def test_gmail_trash_active_gate_wrong_token_403(client, monkeypatch):
    """Gate ACTIVE + gmail trash with a WRONG token -> 403."""
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    from src.tools.email import token_store

    token_store._CACHE[("gmail", _PROJ)] = _fake_gmail_creds()
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")

    resp = await client.post(
        f"{_BASE}/gmail/trash",
        headers={**_HDR, "X-Operator-Token": "not-the-key"},
        json={"message_ids": ["abc123def456"]},
    )
    assert resp.status_code == 403, resp.text
    assert "operator_proof_required" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_outlook_trash_active_gate_no_token_403(client, monkeypatch):
    """AC#1: Gate ACTIVE + outlook trash WITHOUT token -> 403 (delete tier).

    NEGATIVE lock: upstream trash_messages is NEVER called.
    """
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    from src.tools.email import outlook_client, token_store

    token_store._CACHE[("outlook", _PROJ)] = _fake_outlook_creds()
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")

    called = []
    monkeypatch.setattr(
        outlook_client, "trash_messages", lambda c, ids: called.append(ids) or (list(ids), [])
    )

    resp = await client.post(
        f"{_BASE}/outlook/trash", headers=_HDR, json={"message_ids": ["AAA111bbb222"]}
    )
    assert resp.status_code == 403, resp.text
    assert "operator_proof_required" in resp.json()["detail"]
    assert called == [], "trash_messages must NOT be called when the gate rejects"


@pytest.mark.asyncio
async def test_outlook_trash_active_gate_valid_token_200(client, monkeypatch):
    """AC#1: Gate ACTIVE + outlook trash WITH a valid token -> 200.

    POSITIVE: the move-to-Deleted-Items actually proceeds.
    """
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    from src.tools.email import outlook_client, token_store

    token_store._CACHE[("outlook", _PROJ)] = _fake_outlook_creds()
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")

    called = []
    monkeypatch.setattr(
        outlook_client, "trash_messages", lambda c, ids: called.append(ids) or (list(ids), [])
    )

    resp = await client.post(
        f"{_BASE}/outlook/trash",
        headers={**_HDR, "X-Operator-Token": _TOKEN},
        json={"message_ids": ["AAA111bbb222"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["trashed_count"] == 1
    assert called == [["AAA111bbb222"]]


# ===========================================================================
# Read tier is OPEN — status/usage GET succeed even when the gate is ACTIVE
# ===========================================================================


@pytest.mark.asyncio
async def test_read_tier_status_open_when_gate_active(client, monkeypatch):
    """`read` tier (auth status GET) is OPEN: 200 with the gate ACTIVE + no token."""
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    resp = await client.get(f"{_BASE}/auth/gmail/status", headers=_HDR)
    assert resp.status_code == 200, resp.text
    assert resp.json()["authenticated"] is False


@pytest.mark.asyncio
async def test_read_tier_usage_open_when_gate_active(client, monkeypatch):
    """`read` tier (usage GET) is OPEN: 200 with the gate ACTIVE + no token."""
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    resp = await client.get(f"{_BASE}/gmail/usage", headers=_HDR)
    assert resp.status_code == 200, resp.text
    assert "remaining" in resp.json()


# ===========================================================================
# Layer-0 (#1799) still fires BEFORE the tier gate — composition order lock
# ===========================================================================


@pytest.mark.asyncio
async def test_layer0_grant_denial_precedes_tier_gate(client, monkeypatch):
    """A #1799 grant DENY 403s before the tier gate — both 403 but the detail
    proves Layer-0 fired first (grant denial, not operator_proof_required).

    The project's config restricts a role to an empty allow-list; the request
    carries that role via X-Agent-Role -> Layer-0 DENY. Even though the gate is
    ACTIVE and no operator token is present (which would ALSO 403), the detail
    must be the grant-denied string (Layer-0 runs first per the handler order).
    """
    monkeypatch.setenv(_KEY_ENV, _TOKEN)

    # Patch the Project.config lookup so Layer-0 sees a restricted role without
    # touching the DB. _enforce_tool_grant_or_403 selects Project.config; we
    # instead patch check_grant's input by patching the helper's config read via
    # a project row is heavy — simpler: drive check_grant DENY through a role that
    # is explicitly listed with an empty allow-list in a patched config.
    from src.services import tool_grants as tg

    real_check = tg.check_grant

    def _deny_check(config, role, tool_name, *, project_id=None):
        # Force a DENY for our test role regardless of stored config.
        if role == "locked-role":
            return tg.GrantDecision.DENY
        return real_check(config, role, tool_name, project_id=project_id)

    monkeypatch.setattr(tools_email, "check_grant", _deny_check)

    resp = await client.post(
        f"{_BASE}/gmail/trash",
        headers={**_HDR, "X-Agent-Role": "locked-role"},
        json={"message_ids": ["abc123def456"]},
    )
    assert resp.status_code == 403, resp.text
    # Layer-0 fired FIRST — the detail is the grant-denied string, NOT the
    # operator_proof_required string.
    assert "tool_grant_denied" in resp.json()["detail"]
    assert "operator_proof_required" not in resp.json()["detail"]


# ===========================================================================
# NIT-1 (#1848): 403 role reflection is capped at 64 chars
# ===========================================================================


@pytest.mark.asyncio
async def test_grant_denied_detail_caps_role_at_64_chars(client, monkeypatch):
    """A long X-Agent-Role value is truncated to 64 chars in the 403 detail.

    The spoofable header must not be reflected verbatim — arbitrary-length
    strings in error bodies are a low-severity but real injection surface.
    The cap is applied in `_enforce_tool_grant_or_403` before interpolation.
    """
    from src.services import tool_grants as tg

    real_check = tg.check_grant

    def _deny_check(config, role, tool_name, *, project_id=None):
        # Always deny so the 403 body is always generated.
        return tg.GrantDecision.DENY

    monkeypatch.setattr(tools_email, "check_grant", _deny_check)

    long_role = "a" * 200  # 200-char role name — well above the 64-char cap.
    resp = await client.post(
        f"{_BASE}/gmail/trash",
        headers={**_HDR, "X-Agent-Role": long_role},
        json={"message_ids": ["abc123def456"]},
    )
    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert "tool_grant_denied" in detail
    # NEGATIVE: the full 200-char role string must NOT appear verbatim.
    assert long_role not in detail
    # POSITIVE: the 64-char prefix DOES appear (the role is in the detail, capped).
    assert "a" * 64 in detail
    assert "a" * 65 not in detail
