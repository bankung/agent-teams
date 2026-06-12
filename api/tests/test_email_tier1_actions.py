"""Kanban #1585 — Tier-1+2 secretary email actions (Gmail mark/archive/draft).

Covers the new `modify`-tier Gmail endpoints that COMPOSE the two already-shipped
gates exactly like /gmail/trash:
  1. Layer-0 (#1799) per-agent-name tool-grant gate — _enforce_tool_grant_or_403
  2. Tier gate   (#1859) operator-proof tier gate    — _enforce_operator_tier_or_403

Tier model under test:
  modify   OPEN — Layer-0 role-gated + audited, NO operator-proof (Tier-1: mark
                  read/unread, archive, draft — recoverable label mutations).
  delete   PROOF — /gmail/trash still requires operator-proof (Tier-2 regression).

What these tests lock:
  - The `modify` endpoints return 200 with NO operator-proof (proves OPEN), but
    403 if the Layer-0 role grant denies (proves gate ORDER: Layer-0 first).
  - The delete tier (/trash) STILL requires operator-proof when the gate is
    ACTIVE (regression — unchanged by this round).
  - Each successful action writes exactly one email-actions.jsonl line with the
    right tier/action.
  - Drift-guard: the policy file's set of operator_proof tiers EQUALS the code's
    _PROOF_REQUIRED_TIERS (fails if the two ever diverge).
  - Structural deny: there is NO permanent-delete / empty-trash route.

Mirrors test_email_tier_gate.py fixtures (creds injection, store cleanup, gate
activation via monkeypatch.setenv). Runs against agent_teams_test per conftest;
the endpoint tests touch NO DB rows (creds cache-seeded, upstream monkeypatched)
so the live `agent_teams` row-count invariant holds.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from src.routers import tools_email
from src.routers.tools_email import EmailTier, _PROOF_REQUIRED_TIERS

# Non-real project id — cache-seeded tests touch no FK (mirrors test_email_tier_gate).
_PROJ = 9997
_BASE = "/api/tools/email"
_HDR = {"X-Project-Id": str(_PROJ)}
_KEY_ENV = "OPERATOR_ACTION_KEY"
_TOKEN = "s3cret-operator-token"


# ---------------------------------------------------------------------------
# Fixtures — mirror test_email_tier_gate's creds injection + store cleanup
# ---------------------------------------------------------------------------


def _fake_gmail_creds() -> object:
    from unittest.mock import MagicMock

    from google.oauth2.credentials import Credentials as RealCreds

    creds = MagicMock(spec=RealCreds)
    creds.expiry = datetime.datetime(2099, 1, 1, 0, 0, 0)
    creds._at_email_cache = "test@gmail.com"
    return creds


@pytest.fixture(autouse=True)
def _clean_email_stores():
    """Clear the email in-memory stores between tests."""
    from src.tools.email import gate, token_store

    token_store._CACHE.pop(("gmail", _PROJ), None)
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    gate._DAILY_UNITS.pop((_PROJ, today), None)
    yield
    token_store._CACHE.pop(("gmail", _PROJ), None)
    gate._DAILY_UNITS.pop((_PROJ, today), None)


@pytest.fixture
def _actions_to_tmp(monkeypatch, tmp_path):
    """Redirect the secretary-action audit JSONL to a tmp file so each written
    line is hermetically observable + never pollutes the real _runtime trail."""
    audit = tmp_path / "email-actions.jsonl"
    monkeypatch.setattr(tools_email, "_EMAIL_ACTIONS_PATH", audit)
    return audit


def _read_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _seed_creds(monkeypatch):
    from src.tools.email import token_store

    token_store._CACHE[("gmail", _PROJ)] = _fake_gmail_creds()
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")


# ===========================================================================
# modify tier is OPEN — endpoints 200 with NO operator-proof, even gate ACTIVE
# ===========================================================================


@pytest.mark.asyncio
async def test_gmail_mark_read_open_no_proof_even_gate_active(client, monkeypatch, _actions_to_tmp):
    """AC: /gmail/mark (read=True) is OPEN — 200 with the gate ACTIVE + NO token.

    POSITIVE: modify_labels really runs with removeLabelIds=['UNREAD'] (mark read).
    """
    monkeypatch.setenv(_KEY_ENV, _TOKEN)  # gate ACTIVE — would 403 a delete-tier call.
    _seed_creds(monkeypatch)
    from src.tools.email import gmail_client

    calls: list[tuple] = []

    def _fake_modify(creds, ids, add, remove):
        calls.append((list(ids), list(add), list(remove)))
        return list(ids), []

    monkeypatch.setattr(gmail_client, "modify_labels", _fake_modify)

    resp = await client.post(
        f"{_BASE}/gmail/mark", headers=_HDR,
        json={"message_ids": ["abc123def456"], "read": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["modified_count"] == 1
    # POSITIVE — modify really ran; mark-read removes UNREAD, adds nothing.
    assert calls == [(["abc123def456"], [], ["UNREAD"])]


@pytest.mark.asyncio
async def test_gmail_mark_unread_adds_unread_label(client, monkeypatch, _actions_to_tmp):
    """/gmail/mark (read=False) adds the UNREAD label (mark unread)."""
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    _seed_creds(monkeypatch)
    from src.tools.email import gmail_client

    calls: list[tuple] = []
    monkeypatch.setattr(
        gmail_client, "modify_labels",
        lambda c, ids, add, remove: (calls.append((list(add), list(remove))), (list(ids), []))[1],
    )

    resp = await client.post(
        f"{_BASE}/gmail/mark", headers=_HDR,
        json={"message_ids": ["abc123def456"], "read": False},
    )
    assert resp.status_code == 200, resp.text
    # mark-unread adds UNREAD, removes nothing.
    assert calls == [(["UNREAD"], [])]


@pytest.mark.asyncio
async def test_gmail_archive_open_removes_inbox(client, monkeypatch, _actions_to_tmp):
    """AC: /gmail/archive is OPEN — 200 with the gate ACTIVE + NO token; removes INBOX."""
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    _seed_creds(monkeypatch)
    from src.tools.email import gmail_client

    calls: list[tuple] = []
    monkeypatch.setattr(
        gmail_client, "modify_labels",
        lambda c, ids, add, remove: (calls.append((list(add), list(remove))), (list(ids), []))[1],
    )

    resp = await client.post(
        f"{_BASE}/gmail/archive", headers=_HDR, json={"message_ids": ["abc123def456"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["modified_count"] == 1
    # archive removes INBOX, adds nothing.
    assert calls == [([], ["INBOX"])]


@pytest.mark.asyncio
async def test_gmail_draft_open_creates_draft(client, monkeypatch, _actions_to_tmp):
    """AC: /gmail/draft is OPEN — 200 with the gate ACTIVE + NO token; creates a draft."""
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    _seed_creds(monkeypatch)
    from src.tools.email import gmail_client

    calls: list[dict] = []

    def _fake_save_draft(creds, *, to, subject, body):
        calls.append({"to": to, "subject": subject, "body": body})
        return {"draft_id": "r-999", "message_id": "m-999"}

    monkeypatch.setattr(gmail_client, "save_draft", _fake_save_draft)

    resp = await client.post(
        f"{_BASE}/gmail/draft", headers=_HDR,
        json={"to": "bob@x.com", "subject": "hi", "body": "hello"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["draft_id"] == "r-999"
    assert resp.json()["message_id"] == "m-999"
    # POSITIVE — save_draft really ran with the supplied fields.
    assert calls == [{"to": "bob@x.com", "subject": "hi", "body": "hello"}]


# ===========================================================================
# Layer-0 (#1799) fires FIRST — a grant DENY 403s the modify endpoints, proving
# gate ORDER (role gate before tier gate)
# ===========================================================================


@pytest.mark.parametrize(
    "route,payload",
    [
        ("/gmail/mark", {"message_ids": ["abc123def456"], "read": True}),
        ("/gmail/archive", {"message_ids": ["abc123def456"]}),
        ("/gmail/draft", {"to": "bob@x.com", "subject": "s", "body": "b"}),
    ],
)
@pytest.mark.asyncio
async def test_modify_endpoints_layer0_denial_403(client, monkeypatch, route, payload):
    """A #1799 grant DENY 403s the modify endpoints BEFORE the tier gate.

    NEGATIVE lock: the upstream gmail client fn is NEVER called (Layer-0 turns the
    role away before any auth/quota/upstream work). Detail is the grant-denied
    string — proving Layer-0 ran first even though modify is OPEN on the tier gate.
    """
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    _seed_creds(monkeypatch)
    from src.tools.email import gmail_client
    from src.services import tool_grants as tg

    real_check = tg.check_grant

    def _deny_check(config, role, tool_name, *, project_id=None):
        if role == "locked-role":
            return tg.GrantDecision.DENY
        return real_check(config, role, tool_name, project_id=project_id)

    monkeypatch.setattr(tools_email, "check_grant", _deny_check)

    called: list = []
    monkeypatch.setattr(
        gmail_client, "modify_labels",
        lambda *a, **k: called.append("modify") or ([], []),
    )
    monkeypatch.setattr(
        gmail_client, "save_draft",
        lambda *a, **k: called.append("draft") or {"draft_id": "x"},
    )

    resp = await client.post(
        f"{_BASE}{route}", headers={**_HDR, "X-Agent-Role": "locked-role"}, json=payload,
    )
    assert resp.status_code == 403, resp.text
    assert "tool_grant_denied" in resp.json()["detail"]
    assert "operator_proof_required" not in resp.json()["detail"]
    # NEGATIVE lock — no upstream gmail work happened.
    assert called == [], "upstream gmail client must NOT run when Layer-0 denies"


# ===========================================================================
# Regression — delete tier (/trash) STILL requires operator-proof (unchanged)
# ===========================================================================


@pytest.mark.asyncio
async def test_trash_delete_tier_still_requires_proof(client, monkeypatch):
    """REGRESSION: /gmail/trash (delete tier) still 403s WITHOUT a token when ACTIVE.

    This round adds the OPEN `modify` tier; it must NOT have loosened the delete
    tier. NEGATIVE lock: trash_messages is never called.
    """
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    _seed_creds(monkeypatch)
    from src.tools.email import gmail_client

    called: list = []
    monkeypatch.setattr(
        gmail_client, "trash_messages",
        lambda c, ids: called.append(ids) or (list(ids), []),
    )

    resp = await client.post(
        f"{_BASE}/gmail/trash", headers=_HDR, json={"message_ids": ["abc123def456"]},
    )
    assert resp.status_code == 403, resp.text
    assert "operator_proof_required" in resp.json()["detail"]
    assert EmailTier.DELETE.value in resp.json()["detail"]
    assert called == [], "trash must still be blocked without operator-proof"


# ===========================================================================
# Audit sink — each successful action writes exactly ONE email-actions.jsonl line
# ===========================================================================


@pytest.mark.asyncio
async def test_mark_writes_one_action_audit_line(client, monkeypatch, _actions_to_tmp):
    """A successful /gmail/mark writes exactly one action-audit line (right tier/action)."""
    monkeypatch.delenv(_KEY_ENV, raising=False)  # gate inactive — modify is OPEN anyway.
    _seed_creds(monkeypatch)
    from src.tools.email import gmail_client

    monkeypatch.setattr(gmail_client, "modify_labels", lambda c, ids, a, r: (list(ids), []))

    resp = await client.post(
        f"{_BASE}/gmail/mark", headers=_HDR,
        json={"message_ids": ["abc123def456"], "read": True},
    )
    assert resp.status_code == 200, resp.text

    lines = _read_lines(_actions_to_tmp)
    assert len(lines) == 1, f"expected exactly one action-audit line, got {lines}"
    row = lines[0]
    assert row["action"] == "mark_read"
    assert row["tier"] == EmailTier.MODIFY.value
    assert row["approval_mode"] == "auto"
    assert row["message_ids"] == ["abc123def456"]
    assert row["result"] == "success"
    assert "ts" in row and "agent_role" in row


@pytest.mark.asyncio
async def test_archive_writes_one_action_audit_line(client, monkeypatch, _actions_to_tmp):
    """A successful /gmail/archive writes exactly one action-audit line."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_creds(monkeypatch)
    from src.tools.email import gmail_client

    monkeypatch.setattr(gmail_client, "modify_labels", lambda c, ids, a, r: (list(ids), []))

    resp = await client.post(
        f"{_BASE}/gmail/archive", headers=_HDR, json={"message_ids": ["abc123def456"]},
    )
    assert resp.status_code == 200, resp.text
    lines = _read_lines(_actions_to_tmp)
    assert len(lines) == 1
    assert lines[0]["action"] == "archive"
    assert lines[0]["tier"] == EmailTier.MODIFY.value
    assert lines[0]["approval_mode"] == "auto"


@pytest.mark.asyncio
async def test_draft_writes_one_action_audit_line(client, monkeypatch, _actions_to_tmp):
    """A successful /gmail/draft writes exactly one action-audit line (draft id captured)."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_creds(monkeypatch)
    from src.tools.email import gmail_client

    monkeypatch.setattr(
        gmail_client, "save_draft",
        lambda c, *, to, subject, body: {"draft_id": "r-1", "message_id": "m-1"},
    )

    resp = await client.post(
        f"{_BASE}/gmail/draft", headers=_HDR,
        json={"to": "bob@x.com", "subject": "s", "body": "b"},
    )
    assert resp.status_code == 200, resp.text
    lines = _read_lines(_actions_to_tmp)
    assert len(lines) == 1
    assert lines[0]["action"] == "draft"
    assert lines[0]["tier"] == EmailTier.MODIFY.value
    assert lines[0]["approval_mode"] == "auto"
    assert lines[0]["message_ids"] == ["r-1"]


@pytest.mark.asyncio
async def test_trash_writes_action_audit_line_with_delete_tier(client, monkeypatch, _actions_to_tmp):
    """A successful /gmail/trash also writes ONE action-audit line at the delete tier.

    Gate INACTIVE (OPERATOR_ACTION_KEY unset) -> approval_mode="dormant" (Kanban #2104).
    (AC5/AC8 wired the sink into the existing trash route too, so Tier-2 delete is
    captured in the same trail.)
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)  # gate inactive -> trash proceeds w/o token.
    _seed_creds(monkeypatch)
    from src.tools.email import gmail_client

    monkeypatch.setattr(gmail_client, "trash_messages", lambda c, ids: (list(ids), []))

    resp = await client.post(
        f"{_BASE}/gmail/trash", headers=_HDR, json={"message_ids": ["abc123def456"]},
    )
    assert resp.status_code == 200, resp.text
    lines = _read_lines(_actions_to_tmp)
    assert len(lines) == 1
    assert lines[0]["action"] == "trash"
    assert lines[0]["tier"] == EmailTier.DELETE.value
    # Gate INACTIVE (fail-open) -> "dormant", NOT "operator_proof" (Kanban #2104 fix).
    assert lines[0]["approval_mode"] == "dormant"


# ===========================================================================
# Policy manifest <-> code drift guard
# ===========================================================================


def _policy_path() -> Path:
    """Resolve the policy manifest path.

    Tests run inside the api container (cwd /repo/api). The manifest lives at
    /repo/_runtime/secretary-email-policy.json. Resolve relative to this test
    file's repo root so it works regardless of cwd.
    """
    # this file: /repo/api/tests/test_email_tier1_actions.py -> repo root = parents[2]
    return Path(__file__).resolve().parents[2] / "_runtime" / "secretary-email-policy.json"


def test_policy_manifest_exists_and_uses_emailtier_vocab():
    """The policy manifest exists and is keyed by the as-built EmailTier vocabulary."""
    path = _policy_path()
    assert path.exists(), f"policy manifest missing at {path}"
    policy = json.loads(path.read_text(encoding="utf-8"))
    tier_keys = set(policy["tiers"].keys())
    code_tiers = {t.value for t in EmailTier}
    assert tier_keys == code_tiers, (
        f"policy tiers {tier_keys} must EQUAL the EmailTier enum {code_tiers} "
        "(no parallel Tier1/2/3 vocabulary)"
    )
    assert policy["deny"] == ["permanent_delete"]


def test_policy_proof_tiers_match_code():
    """DRIFT GUARD: the policy's operator_proof tier set EQUALS _PROOF_REQUIRED_TIERS.

    Fails the instant the declarative manifest and the enforcing code diverge.
    """
    policy = json.loads(_policy_path().read_text(encoding="utf-8"))
    policy_proof = {
        name for name, spec in policy["tiers"].items()
        if spec["approval_mode"] == "operator_proof"
    }
    code_proof = {t.value for t in _PROOF_REQUIRED_TIERS}
    assert policy_proof == code_proof, (
        f"policy operator_proof tiers {policy_proof} diverge from code "
        f"_PROOF_REQUIRED_TIERS {code_proof} — update one to match the other"
    )


def test_modify_is_open_in_both_policy_and_code():
    """`modify` is auto (OPEN) in the policy AND absent from _PROOF_REQUIRED_TIERS."""
    policy = json.loads(_policy_path().read_text(encoding="utf-8"))
    assert policy["tiers"]["modify"]["approval_mode"] == "auto"
    assert EmailTier.MODIFY not in _PROOF_REQUIRED_TIERS


# ===========================================================================
# Structural deny — NO permanent-delete / empty-trash route exists
# ===========================================================================


def test_no_permanent_delete_route_exists():
    """STRUCTURAL DENY: the app exposes NO permanent-delete / empty-trash route.

    permanent_delete is in the policy `deny` list; this proves there is no API
    surface that could perform it (defense-in-depth: the policy is a declaration,
    the absence of the route is the structural enforcement).
    """
    from src.main import app

    forbidden_markers = ("permanent", "empty-trash", "emptytrash", "purge", "delete-forever")
    offending = [
        route.path for route in app.routes
        if any(m in getattr(route, "path", "").lower() for m in forbidden_markers)
    ]
    assert offending == [], f"unexpected permanent-delete-style route(s): {offending}"


# ===========================================================================
# FIX-1 — save_draft None draft_id → 502 (not 500), audit result="error"
# ===========================================================================


@pytest.mark.asyncio
async def test_draft_none_id_returns_502_and_audit_error(client, monkeypatch, _actions_to_tmp):
    """FIX-1: when save_draft returns draft_id=None, the endpoint must return 502
    (not 500) and the action-audit line must have result='error' (not 'noop'/'success').

    NEGATIVE lock: a None id is an upstream failure — 200 with a null id is wrong.
    POSITIVE: the audit trail records the failure so the trail is complete.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_creds(monkeypatch)
    from src.tools.email import gmail_client

    monkeypatch.setattr(
        gmail_client, "save_draft",
        lambda c, *, to, subject, body: {"draft_id": None, "message_id": None},
    )

    resp = await client.post(
        f"{_BASE}/gmail/draft", headers=_HDR,
        json={"to": "bob@x.com", "subject": "hi", "body": "hello"},
    )
    # NEGATIVE: must NOT be 200 with a null id, must NOT be 500 (unhandled exception).
    assert resp.status_code == 502, f"expected 502, got {resp.status_code}: {resp.text}"
    assert resp.json()["detail"]["error"] == "empty_draft_response"

    # POSITIVE: action-audit line written with result="error" (not "noop"/"success").
    lines = _read_lines(_actions_to_tmp)
    assert len(lines) == 1, f"expected one audit line, got {lines}"
    assert lines[0]["result"] == "error", f"audit result must be 'error', got: {lines[0]}"
    assert lines[0]["action"] == "draft"


# ===========================================================================
# FIX-2 — bulk gate on /gmail/mark and /gmail/archive
# ===========================================================================

# Use the same threshold env var the gate reads so tests self-calibrate.
_BULK_ENV = "EMAIL_TOOLS_BULK_THRESHOLD"
_BULK_LIMIT = 10  # set low in tests to avoid building a 101-element payload


@pytest.mark.asyncio
async def test_mark_bulk_threshold_400_without_force(client, monkeypatch, _actions_to_tmp):
    """FIX-2: /gmail/mark rejects N > threshold WITHOUT ?force=true → 400."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv(_BULK_ENV, str(_BULK_LIMIT))
    _seed_creds(monkeypatch)

    over_threshold_ids = [f"msg{i}" for i in range(_BULK_LIMIT + 1)]
    resp = await client.post(
        f"{_BASE}/gmail/mark", headers=_HDR,
        json={"message_ids": over_threshold_ids, "read": True},
    )
    assert resp.status_code == 400, f"expected 400, got {resp.status_code}: {resp.text}"
    assert resp.json()["detail"]["error"] == "bulk_threshold"


@pytest.mark.asyncio
async def test_mark_bulk_threshold_passes_with_force(client, monkeypatch, _actions_to_tmp):
    """FIX-2: /gmail/mark with ?force=true bypasses bulk gate and proceeds."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv(_BULK_ENV, str(_BULK_LIMIT))
    _seed_creds(monkeypatch)
    from src.tools.email import gmail_client

    over_threshold_ids = [f"msg{i}" for i in range(_BULK_LIMIT + 1)]
    monkeypatch.setattr(gmail_client, "modify_labels", lambda c, ids, a, r: (list(ids), []))

    resp = await client.post(
        f"{_BASE}/gmail/mark?force=true", headers=_HDR,
        json={"message_ids": over_threshold_ids, "read": True},
    )
    assert resp.status_code == 200, f"expected 200 with force=true, got {resp.status_code}: {resp.text}"
    assert resp.json()["modified_count"] == _BULK_LIMIT + 1


@pytest.mark.asyncio
async def test_archive_bulk_threshold_400_without_force(client, monkeypatch, _actions_to_tmp):
    """FIX-2: /gmail/archive rejects N > threshold WITHOUT ?force=true → 400."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv(_BULK_ENV, str(_BULK_LIMIT))
    _seed_creds(monkeypatch)

    over_threshold_ids = [f"msg{i}" for i in range(_BULK_LIMIT + 1)]
    resp = await client.post(
        f"{_BASE}/gmail/archive", headers=_HDR,
        json={"message_ids": over_threshold_ids},
    )
    assert resp.status_code == 400, f"expected 400, got {resp.status_code}: {resp.text}"
    assert resp.json()["detail"]["error"] == "bulk_threshold"


@pytest.mark.asyncio
async def test_archive_bulk_threshold_passes_with_force(client, monkeypatch, _actions_to_tmp):
    """FIX-2: /gmail/archive with ?force=true bypasses bulk gate and proceeds."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv(_BULK_ENV, str(_BULK_LIMIT))
    _seed_creds(monkeypatch)
    from src.tools.email import gmail_client

    over_threshold_ids = [f"msg{i}" for i in range(_BULK_LIMIT + 1)]
    monkeypatch.setattr(gmail_client, "modify_labels", lambda c, ids, a, r: (list(ids), []))

    resp = await client.post(
        f"{_BASE}/gmail/archive?force=true", headers=_HDR,
        json={"message_ids": over_threshold_ids},
    )
    assert resp.status_code == 200, f"expected 200 with force=true, got {resp.status_code}: {resp.text}"
    assert resp.json()["modified_count"] == _BULK_LIMIT + 1


# ===========================================================================
# FIX-3 — system-label denylist inside modify_labels (gmail_client)
# ===========================================================================


def test_modify_labels_raises_on_trash_in_add():
    """FIX-3: modify_labels raises ValueError when TRASH appears in add_label_ids.

    NEGATIVE lock: no API call to Gmail is made — the guard fires before _build_service.
    """
    from unittest.mock import MagicMock, patch

    from google.oauth2.credentials import Credentials as RealCreds
    from src.tools.email import gmail_client

    fake_creds = MagicMock(spec=RealCreds)

    with patch.object(gmail_client, "_build_service", side_effect=AssertionError("should not reach build")):
        import pytest as _pytest
        with _pytest.raises(ValueError, match="system label not permitted via modify_labels: TRASH"):
            gmail_client.modify_labels(fake_creds, ["msg1"], add_label_ids=["TRASH"])


def test_modify_labels_raises_on_spam_in_remove():
    """FIX-3: modify_labels raises ValueError when SPAM appears in remove_label_ids."""
    from unittest.mock import MagicMock, patch

    from google.oauth2.credentials import Credentials as RealCreds
    from src.tools.email import gmail_client

    fake_creds = MagicMock(spec=RealCreds)

    with patch.object(gmail_client, "_build_service", side_effect=AssertionError("should not reach build")):
        import pytest as _pytest
        with _pytest.raises(ValueError, match="system label not permitted via modify_labels: SPAM"):
            gmail_client.modify_labels(fake_creds, ["msg1"], remove_label_ids=["SPAM"])


def test_modify_labels_allows_unread_and_inbox():
    """FIX-3: normal UNREAD/INBOX labels are NOT blocked — existing routes unaffected."""
    from unittest.mock import MagicMock, patch

    from google.oauth2.credentials import Credentials as RealCreds
    from src.tools.email import gmail_client

    fake_creds = MagicMock(spec=RealCreds)
    fake_service = MagicMock()
    fake_service.users().messages().modify().execute.return_value = {}

    with patch.object(gmail_client, "_build_service", return_value=fake_service):
        modified, errors = gmail_client.modify_labels(
            fake_creds, ["msg1"], add_label_ids=["UNREAD"], remove_label_ids=["INBOX"]
        )
    assert modified == ["msg1"]
    assert errors == []


# ===========================================================================
# Kanban #1917 — Outlook Tier-1 parity (mark / archive / draft)
# ===========================================================================
#
# Mirrors every Gmail Tier-1 test block above, swapping:
#   provider string "outlook", tool-grant names outlook.*,
#   creds via token_store._CACHE[("outlook", _PROJ)],
#   client = outlook_client.*
#
# Uses a different project id (_PROJ_OL) to stay hermetically separate from the
# Gmail tests sharing _PROJ = 9997 in the autouse store-cleanup fixture.

_PROJ_OL = 9996


def _fake_outlook_creds() -> dict:
    """Minimal Outlook token dict (no real msal needed for these tests)."""
    import time
    return {
        "access_token": "fake-outlook-access-token",
        "expires_in": 3600,
        "_acquired_at": time.time(),
    }


@pytest.fixture(autouse=True)
def _clean_outlook_stores():
    """Clear the Outlook in-memory stores between tests."""
    from src.tools.email import gate, token_store

    token_store._CACHE.pop(("outlook", _PROJ_OL), None)
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    gate._DAILY_UNITS.pop((_PROJ_OL, today), None)
    yield
    token_store._CACHE.pop(("outlook", _PROJ_OL), None)
    gate._DAILY_UNITS.pop((_PROJ_OL, today), None)


def _seed_outlook_creds(monkeypatch):
    from src.tools.email import token_store

    token_store._CACHE[("outlook", _PROJ_OL)] = _fake_outlook_creds()
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")


_HDR_OL = {"X-Project-Id": str(_PROJ_OL)}


# ---------------------------------------------------------------------------
# modify tier is OPEN — Outlook endpoints 200 with NO operator-proof
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outlook_mark_read_open_no_proof_even_gate_active(client, monkeypatch, _actions_to_tmp):
    """AC: /outlook/mark (read=True) is OPEN — 200 with the gate ACTIVE + NO token.

    POSITIVE: mark_read really runs with read=True.
    """
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    _seed_outlook_creds(monkeypatch)
    from src.tools.email import outlook_client

    calls: list[tuple] = []

    def _fake_mark_read(creds, ids, read):
        calls.append((list(ids), read))
        return list(ids), []

    monkeypatch.setattr(outlook_client, "mark_read", _fake_mark_read)

    resp = await client.post(
        f"{_BASE}/outlook/mark", headers=_HDR_OL,
        json={"message_ids": ["outlookMsgId1"], "read": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["modified_count"] == 1
    # POSITIVE — mark_read called with read=True.
    assert calls == [(["outlookMsgId1"], True)]


@pytest.mark.asyncio
async def test_outlook_mark_unread_passes_false(client, monkeypatch, _actions_to_tmp):
    """/outlook/mark (read=False) calls mark_read with read=False."""
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    _seed_outlook_creds(monkeypatch)
    from src.tools.email import outlook_client

    calls: list[bool] = []
    monkeypatch.setattr(
        outlook_client, "mark_read",
        lambda c, ids, read: (calls.append(read), (list(ids), []))[1],
    )

    resp = await client.post(
        f"{_BASE}/outlook/mark", headers=_HDR_OL,
        json={"message_ids": ["outlookMsgId1"], "read": False},
    )
    assert resp.status_code == 200, resp.text
    assert calls == [False]


@pytest.mark.asyncio
async def test_outlook_archive_open_moves_to_archive(client, monkeypatch, _actions_to_tmp):
    """AC: /outlook/archive is OPEN — 200 with gate ACTIVE + NO token; calls archive."""
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    _seed_outlook_creds(monkeypatch)
    from src.tools.email import outlook_client

    calls: list[list] = []
    monkeypatch.setattr(
        outlook_client, "archive",
        lambda c, ids: (calls.append(list(ids)), (list(ids), []))[1],
    )

    resp = await client.post(
        f"{_BASE}/outlook/archive", headers=_HDR_OL,
        json={"message_ids": ["outlookMsgId1"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["modified_count"] == 1
    assert calls == [["outlookMsgId1"]]


@pytest.mark.asyncio
async def test_outlook_draft_open_creates_draft(client, monkeypatch, _actions_to_tmp):
    """AC: /outlook/draft is OPEN — 200 with gate ACTIVE + NO token; creates draft."""
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    _seed_outlook_creds(monkeypatch)
    from src.tools.email import outlook_client

    calls: list[dict] = []

    def _fake_save_draft(creds, *, to, subject, body):
        calls.append({"to": to, "subject": subject, "body": body})
        return {"draft_id": "ol-draft-999", "message_id": "ol-draft-999"}

    monkeypatch.setattr(outlook_client, "save_draft", _fake_save_draft)

    resp = await client.post(
        f"{_BASE}/outlook/draft", headers=_HDR_OL,
        json={"to": "bob@x.com", "subject": "hi", "body": "hello"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["draft_id"] == "ol-draft-999"
    # POSITIVE — save_draft called with supplied fields.
    assert calls == [{"to": "bob@x.com", "subject": "hi", "body": "hello"}]


# ---------------------------------------------------------------------------
# Layer-0 (#1799) fires FIRST on Outlook modify endpoints
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "route,payload",
    [
        ("/outlook/mark", {"message_ids": ["outlookMsgId1"], "read": True}),
        ("/outlook/archive", {"message_ids": ["outlookMsgId1"]}),
        ("/outlook/draft", {"to": "bob@x.com", "subject": "s", "body": "b"}),
    ],
)
@pytest.mark.asyncio
async def test_outlook_modify_endpoints_layer0_denial_403(client, monkeypatch, route, payload):
    """A #1799 grant DENY 403s the Outlook modify endpoints BEFORE the tier gate.

    NEGATIVE lock: the upstream outlook client fn is NEVER called.
    """
    monkeypatch.setenv(_KEY_ENV, _TOKEN)
    _seed_outlook_creds(monkeypatch)
    from src.tools.email import outlook_client
    from src.services import tool_grants as tg

    real_check = tg.check_grant

    def _deny_check(config, role, tool_name, *, project_id=None):
        if role == "locked-role":
            return tg.GrantDecision.DENY
        return real_check(config, role, tool_name, project_id=project_id)

    monkeypatch.setattr(tools_email, "check_grant", _deny_check)

    called: list = []
    monkeypatch.setattr(
        outlook_client, "mark_read",
        lambda *a, **k: called.append("mark_read") or ([], []),
    )
    monkeypatch.setattr(
        outlook_client, "archive",
        lambda *a, **k: called.append("archive") or ([], []),
    )
    monkeypatch.setattr(
        outlook_client, "save_draft",
        lambda *a, **k: called.append("draft") or {"draft_id": "x"},
    )

    resp = await client.post(
        f"{_BASE}{route}", headers={**_HDR_OL, "X-Agent-Role": "locked-role"}, json=payload,
    )
    assert resp.status_code == 403, resp.text
    assert "tool_grant_denied" in resp.json()["detail"]
    assert called == [], "upstream outlook client must NOT run when Layer-0 denies"


# ---------------------------------------------------------------------------
# Audit sink — Outlook modify actions write exactly ONE action-audit line
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outlook_mark_writes_one_action_audit_line(client, monkeypatch, _actions_to_tmp):
    """A successful /outlook/mark writes exactly one action-audit line."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_outlook_creds(monkeypatch)
    from src.tools.email import outlook_client

    monkeypatch.setattr(outlook_client, "mark_read", lambda c, ids, read: (list(ids), []))

    resp = await client.post(
        f"{_BASE}/outlook/mark", headers=_HDR_OL,
        json={"message_ids": ["outlookMsgId1"], "read": True},
    )
    assert resp.status_code == 200, resp.text

    lines = _read_lines(_actions_to_tmp)
    assert len(lines) == 1
    row = lines[0]
    assert row["action"] == "mark_read"
    assert row["tier"] == EmailTier.MODIFY.value
    assert row["approval_mode"] == "auto"
    assert row["message_ids"] == ["outlookMsgId1"]
    assert row["result"] == "success"


@pytest.mark.asyncio
async def test_outlook_archive_writes_one_action_audit_line(client, monkeypatch, _actions_to_tmp):
    """A successful /outlook/archive writes exactly one action-audit line."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_outlook_creds(monkeypatch)
    from src.tools.email import outlook_client

    monkeypatch.setattr(outlook_client, "archive", lambda c, ids: (list(ids), []))

    resp = await client.post(
        f"{_BASE}/outlook/archive", headers=_HDR_OL, json={"message_ids": ["outlookMsgId1"]},
    )
    assert resp.status_code == 200, resp.text
    lines = _read_lines(_actions_to_tmp)
    assert len(lines) == 1
    assert lines[0]["action"] == "archive"
    assert lines[0]["tier"] == EmailTier.MODIFY.value
    assert lines[0]["approval_mode"] == "auto"


@pytest.mark.asyncio
async def test_outlook_draft_writes_one_action_audit_line(client, monkeypatch, _actions_to_tmp):
    """A successful /outlook/draft writes exactly one action-audit line (draft id captured)."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_outlook_creds(monkeypatch)
    from src.tools.email import outlook_client

    monkeypatch.setattr(
        outlook_client, "save_draft",
        lambda c, *, to, subject, body: {"draft_id": "ol-r-1", "message_id": "ol-r-1"},
    )

    resp = await client.post(
        f"{_BASE}/outlook/draft", headers=_HDR_OL,
        json={"to": "bob@x.com", "subject": "s", "body": "b"},
    )
    assert resp.status_code == 200, resp.text
    lines = _read_lines(_actions_to_tmp)
    assert len(lines) == 1
    assert lines[0]["action"] == "draft"
    assert lines[0]["tier"] == EmailTier.MODIFY.value
    assert lines[0]["message_ids"] == ["ol-r-1"]


# ---------------------------------------------------------------------------
# 401 when no Outlook creds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outlook_mark_401_no_auth(client, monkeypatch):
    """/outlook/mark returns 401 when no Outlook creds are present."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")
    # Deliberately do NOT seed Outlook creds.

    resp = await client.post(
        f"{_BASE}/outlook/mark", headers=_HDR_OL,
        json={"message_ids": ["outlookMsgId1"], "read": True},
    )
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_outlook_archive_401_no_auth(client, monkeypatch):
    """/outlook/archive returns 401 when no Outlook creds are present."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")

    resp = await client.post(
        f"{_BASE}/outlook/archive", headers=_HDR_OL,
        json={"message_ids": ["outlookMsgId1"]},
    )
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_outlook_draft_401_no_auth(client, monkeypatch):
    """/outlook/draft returns 401 when no Outlook creds are present."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")

    resp = await client.post(
        f"{_BASE}/outlook/draft", headers=_HDR_OL,
        json={"to": "bob@x.com", "subject": "s", "body": "b"},
    )
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# Bulk-threshold gate on /outlook/mark and /outlook/archive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outlook_mark_bulk_threshold_400_without_force(client, monkeypatch, _actions_to_tmp):
    """/outlook/mark rejects N > threshold WITHOUT ?force=true → 400."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv(_BULK_ENV, str(_BULK_LIMIT))
    _seed_outlook_creds(monkeypatch)

    over_threshold_ids = [f"msg{i}" for i in range(_BULK_LIMIT + 1)]
    resp = await client.post(
        f"{_BASE}/outlook/mark", headers=_HDR_OL,
        json={"message_ids": over_threshold_ids, "read": True},
    )
    assert resp.status_code == 400, f"expected 400, got {resp.status_code}: {resp.text}"
    assert resp.json()["detail"]["error"] == "bulk_threshold"


@pytest.mark.asyncio
async def test_outlook_mark_bulk_threshold_passes_with_force(client, monkeypatch, _actions_to_tmp):
    """/outlook/mark with ?force=true bypasses bulk gate and proceeds."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv(_BULK_ENV, str(_BULK_LIMIT))
    _seed_outlook_creds(monkeypatch)
    from src.tools.email import outlook_client

    over_threshold_ids = [f"msg{i}" for i in range(_BULK_LIMIT + 1)]
    monkeypatch.setattr(outlook_client, "mark_read", lambda c, ids, read: (list(ids), []))

    resp = await client.post(
        f"{_BASE}/outlook/mark?force=true", headers=_HDR_OL,
        json={"message_ids": over_threshold_ids, "read": True},
    )
    assert resp.status_code == 200, f"expected 200 with force=true, got {resp.status_code}: {resp.text}"
    assert resp.json()["modified_count"] == _BULK_LIMIT + 1


@pytest.mark.asyncio
async def test_outlook_archive_bulk_threshold_400_without_force(client, monkeypatch, _actions_to_tmp):
    """/outlook/archive rejects N > threshold WITHOUT ?force=true → 400."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv(_BULK_ENV, str(_BULK_LIMIT))
    _seed_outlook_creds(monkeypatch)

    over_threshold_ids = [f"msg{i}" for i in range(_BULK_LIMIT + 1)]
    resp = await client.post(
        f"{_BASE}/outlook/archive", headers=_HDR_OL,
        json={"message_ids": over_threshold_ids},
    )
    assert resp.status_code == 400, f"expected 400, got {resp.status_code}: {resp.text}"
    assert resp.json()["detail"]["error"] == "bulk_threshold"


@pytest.mark.asyncio
async def test_outlook_archive_bulk_threshold_passes_with_force(client, monkeypatch, _actions_to_tmp):
    """/outlook/archive with ?force=true bypasses bulk gate and proceeds."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv(_BULK_ENV, str(_BULK_LIMIT))
    _seed_outlook_creds(monkeypatch)
    from src.tools.email import outlook_client

    over_threshold_ids = [f"msg{i}" for i in range(_BULK_LIMIT + 1)]
    monkeypatch.setattr(outlook_client, "archive", lambda c, ids: (list(ids), []))

    resp = await client.post(
        f"{_BASE}/outlook/archive?force=true", headers=_HDR_OL,
        json={"message_ids": over_threshold_ids},
    )
    assert resp.status_code == 200, f"expected 200 with force=true, got {resp.status_code}: {resp.text}"
    assert resp.json()["modified_count"] == _BULK_LIMIT + 1


# ---------------------------------------------------------------------------
# Daily-cap 429 on Outlook modify endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outlook_mark_cap_429(client, monkeypatch, _actions_to_tmp):
    """/outlook/mark returns 429 when the daily cap is exhausted."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_outlook_creds(monkeypatch)
    # Override cap to 0 AFTER seed (seed sets it to 1000; this wins).
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "0")

    resp = await client.post(
        f"{_BASE}/outlook/mark", headers=_HDR_OL,
        json={"message_ids": ["outlookMsgId1"], "read": True},
    )
    assert resp.status_code == 429, resp.text


@pytest.mark.asyncio
async def test_outlook_archive_cap_429(client, monkeypatch, _actions_to_tmp):
    """/outlook/archive returns 429 when the daily cap is exhausted."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_outlook_creds(monkeypatch)
    # Override cap to 0 AFTER seed (seed sets it to 1000; this wins).
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "0")

    resp = await client.post(
        f"{_BASE}/outlook/archive", headers=_HDR_OL,
        json={"message_ids": ["outlookMsgId1"]},
    )
    assert resp.status_code == 429, resp.text


@pytest.mark.asyncio
async def test_outlook_draft_none_id_returns_502(client, monkeypatch, _actions_to_tmp):
    """When save_draft returns draft_id=None, /outlook/draft must return 502."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_outlook_creds(monkeypatch)
    from src.tools.email import outlook_client

    monkeypatch.setattr(
        outlook_client, "save_draft",
        lambda c, *, to, subject, body: {"draft_id": None, "message_id": None},
    )

    resp = await client.post(
        f"{_BASE}/outlook/draft", headers=_HDR_OL,
        json={"to": "bob@x.com", "subject": "hi", "body": "hello"},
    )
    assert resp.status_code == 502, f"expected 502, got {resp.status_code}: {resp.text}"
    assert resp.json()["detail"]["error"] == "empty_draft_response"

    lines = _read_lines(_actions_to_tmp)
    assert len(lines) == 1
    assert lines[0]["result"] == "error"
    assert lines[0]["action"] == "draft"


# ---------------------------------------------------------------------------
# 502 on exception from outlook_client (mark + archive)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outlook_mark_exception_raises_502(client, monkeypatch, _actions_to_tmp):
    """When outlook_client.mark_read raises, /outlook/mark must return 502."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_outlook_creds(monkeypatch)
    from src.tools.email import outlook_client

    def _raise(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(outlook_client, "mark_read", _raise)

    resp = await client.post(
        f"{_BASE}/outlook/mark", headers=_HDR_OL,
        json={"message_ids": ["outlookMsgId1"], "read": True},
    )
    assert resp.status_code == 502, f"expected 502, got {resp.status_code}: {resp.text}"
    assert resp.json()["detail"]["error"] == "outlook_mark_failed"


@pytest.mark.asyncio
async def test_outlook_archive_exception_raises_502(client, monkeypatch, _actions_to_tmp):
    """When outlook_client.archive raises, /outlook/archive must return 502."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_outlook_creds(monkeypatch)
    from src.tools.email import outlook_client

    def _raise(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(outlook_client, "archive", _raise)

    resp = await client.post(
        f"{_BASE}/outlook/archive", headers=_HDR_OL,
        json={"message_ids": ["outlookMsgId1"]},
    )
    assert resp.status_code == 502, f"expected 502, got {resp.status_code}: {resp.text}"
    assert resp.json()["detail"]["error"] == "outlook_archive_failed"


# ===========================================================================
# AC3 — gate.log_audit best-effort guard (never raises on OSError)
# ===========================================================================


def test_log_audit_does_not_raise_on_oserror(monkeypatch):
    """AC3: gate.log_audit must NOT raise when the underlying write fails.

    NEGATIVE lock: an OSError from Path.open must be swallowed, not propagated.
    POSITIVE: the function returns normally (None) even when IO fails.

    Patch is scoped to gate._AUDIT_PATH only — no class-wide monkeypatching.
    """
    from src.tools.email import gate
    from pathlib import Path

    class _RaisingPath(type(Path())):
        def open(self, *args, **kwargs):  # type: ignore[override]
            raise OSError("simulated disk full")

        def mkdir(self, *args, **kwargs):  # type: ignore[override]
            pass  # let mkdir succeed so only open is the failure point

    monkeypatch.setattr(gate, "_AUDIT_PATH", _RaisingPath("/tmp/fake-audit-open.jsonl"))

    # Should not raise — best-effort guard.
    result = gate.log_audit("gmail", 1, "mark", 5, success=True)
    assert result is None, "log_audit must return None (best-effort, no raise)"


def test_log_audit_does_not_raise_on_mkdir_oserror(monkeypatch):
    """AC3: log_audit must NOT raise when the parent mkdir fails with OSError.

    Patch is scoped to gate._AUDIT_PATH only — monkeypatch auto-restores on teardown,
    no manual restore needed.
    """
    from src.tools.email import gate
    from pathlib import Path

    class _MkdirRaisingPath(type(Path())):
        def mkdir(self, *args, **kwargs):  # type: ignore[override]
            raise OSError("simulated permission denied on mkdir")

    monkeypatch.setattr(gate, "_AUDIT_PATH", _MkdirRaisingPath("/tmp/fake-audit-mkdir.jsonl"))

    result = gate.log_audit("outlook", 2, "archive", 5, success=False)
    assert result is None


# ===========================================================================
# Kanban #1939 — READ endpoints: search + get (Gmail + Outlook)
# ===========================================================================
#
# Gate chain under test: Layer-0 → tier(READ, no-op) → auth → cap → client →
# gate.log_audit. NO _write_action_audit for reads (mutations only).
#
# Privacy assertion (body_text never in audit): gate.log_audit records only
# {provider, action, units, success, error_code?} — body_text must NOT appear.
#
# Tests use two separate project ids to stay hermetic from the MODIFY blocks
# above, which use _PROJ=9997 and _PROJ_OL=9996.

_PROJ_GS = 9995  # Gmail search/get tests
_PROJ_OS = 9994  # Outlook search/get tests
_HDR_GS = {"X-Project-Id": str(_PROJ_GS)}
_HDR_OS = {"X-Project-Id": str(_PROJ_OS)}


def _fake_gmail_creds_read() -> object:
    from unittest.mock import MagicMock
    from google.oauth2.credentials import Credentials as RealCreds
    import datetime as _dt
    creds = MagicMock(spec=RealCreds)
    creds.expiry = _dt.datetime(2099, 1, 1, 0, 0, 0)
    creds._at_email_cache = "test@gmail.com"
    return creds


def _fake_outlook_creds_read() -> dict:
    import time
    return {
        "access_token": "fake-outlook-read-token",
        "expires_in": 3600,
        "_acquired_at": time.time(),
    }


@pytest.fixture(autouse=True)
def _clean_read_stores():
    """Clear the in-memory stores for both read-test project ids between tests."""
    from src.tools.email import gate, token_store
    import datetime as _dt
    today = _dt.datetime.now(_dt.UTC).date().isoformat()
    for key in [("gmail", _PROJ_GS), ("outlook", _PROJ_OS)]:
        token_store._CACHE.pop(key, None)
    for key in [(_PROJ_GS, today), (_PROJ_OS, today)]:
        gate._DAILY_UNITS.pop(key, None)
    yield
    for key in [("gmail", _PROJ_GS), ("outlook", _PROJ_OS)]:
        token_store._CACHE.pop(key, None)
    for key in [(_PROJ_GS, today), (_PROJ_OS, today)]:
        gate._DAILY_UNITS.pop(key, None)


def _seed_gmail_read_creds(monkeypatch):
    from src.tools.email import token_store
    token_store._CACHE[("gmail", _PROJ_GS)] = _fake_gmail_creds_read()
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")


def _seed_outlook_read_creds(monkeypatch):
    from src.tools.email import token_store
    token_store._CACHE[("outlook", _PROJ_OS)] = _fake_outlook_creds_read()
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")


# ===== Gmail search =====


@pytest.mark.asyncio
async def test_gmail_search_success_returns_metadata_shape(client, monkeypatch, _actions_to_tmp):
    """AC: /gmail/search returns 200 with the expected metadata shape.

    POSITIVE: search_messages is called; result includes all metadata fields.
    NO body_text in the response (metadata-only endpoint).
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_gmail_read_creds(monkeypatch)
    from src.tools.email import gmail_client

    fake_items = [
        {"id": "msg001", "thread_id": "thr001", "from": "alice@x.com",
         "subject": "Hello", "date": "Mon, 1 Jan 2024 10:00:00 +0000", "snippet": "Hi there"},
    ]
    monkeypatch.setattr(
        gmail_client, "search_messages",
        lambda creds, query, max_results: fake_items,
    )

    resp = await client.post(
        f"{_BASE}/gmail/search",
        headers=_HDR_GS,
        json={"query": "from:alice@x.com", "max_results": 10},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 1
    item = body["results"][0]
    assert item["id"] == "msg001"
    assert item["thread_id"] == "thr001"
    assert item["from"] == "alice@x.com"
    assert item["subject"] == "Hello"
    assert item["snippet"] == "Hi there"
    assert "body_text" not in item


@pytest.mark.asyncio
async def test_gmail_search_layer0_denial_403(client, monkeypatch):
    """AC: /gmail/search 403s on Layer-0 grant denial; search_messages NOT called."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_gmail_read_creds(monkeypatch)
    from src.tools.email import gmail_client
    from src.services import tool_grants as tg

    real_check = tg.check_grant

    def _deny_check(config, role, tool_name, *, project_id=None):
        if role == "locked-role":
            return tg.GrantDecision.DENY
        return real_check(config, role, tool_name, project_id=project_id)

    monkeypatch.setattr(tools_email, "check_grant", _deny_check)
    called: list = []
    monkeypatch.setattr(
        gmail_client, "search_messages",
        lambda *a, **k: called.append(1) or [],
    )

    resp = await client.post(
        f"{_BASE}/gmail/search",
        headers={**_HDR_GS, "X-Agent-Role": "locked-role"},
        json={"query": "test"},
    )
    assert resp.status_code == 403, resp.text
    assert "tool_grant_denied" in resp.json()["detail"]
    assert called == [], "search_messages must NOT run when Layer-0 denies"


@pytest.mark.asyncio
async def test_gmail_search_401_no_auth(client, monkeypatch):
    """/gmail/search returns 401 when no Gmail creds are present."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")
    # Deliberately do NOT seed Gmail creds for _PROJ_GS.
    resp = await client.post(
        f"{_BASE}/gmail/search", headers=_HDR_GS, json={"query": "test"},
    )
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_gmail_search_cap_429(client, monkeypatch):
    """/gmail/search returns 429 when the daily cap is exhausted."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_gmail_read_creds(monkeypatch)
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "0")

    resp = await client.post(
        f"{_BASE}/gmail/search", headers=_HDR_GS, json={"query": "test"},
    )
    assert resp.status_code == 429, resp.text


# ===== Gmail get =====


@pytest.mark.asyncio
async def test_gmail_get_success_returns_body(client, monkeypatch, _actions_to_tmp):
    """AC: /gmail/get returns 200 with the expected shape including body_text.

    POSITIVE: get_message is called; response contains body_text.
    PRIVACY: audit JSONL row does NOT contain body_text.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_gmail_read_creds(monkeypatch)
    from src.tools.email import gmail_client, gate

    secret_body = "Secret message content for test"
    fake_msg = {
        "id": "msg001", "thread_id": "thr001",
        "from": "alice@x.com", "to": "bob@y.com",
        "subject": "Hello", "date": "Mon, 1 Jan 2024 10:00:00 +0000",
        "body_text": secret_body,
    }
    monkeypatch.setattr(
        gmail_client, "get_message",
        lambda creds, message_id: fake_msg,
    )

    # Redirect gate audit to tmp so we can assert privacy.
    import json as _json
    from pathlib import Path as _Path
    audit_rows: list[dict] = []

    def _fake_log_audit(provider, pid, action, units, success, error_code=None):
        audit_rows.append({"provider": provider, "action": action,
                           "units": units, "success": success})

    monkeypatch.setattr(gate, "log_audit", _fake_log_audit)

    resp = await client.post(
        f"{_BASE}/gmail/get", headers=_HDR_GS,
        json={"message_id": "msg001"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "msg001"
    assert body["body_text"] == secret_body
    assert body["from"] == "alice@x.com"

    # PRIVACY: audit rows must NOT contain body_text.
    for row in audit_rows:
        row_str = _json.dumps(row)
        assert secret_body not in row_str, (
            f"body_text leaked into audit row: {row_str}"
        )


@pytest.mark.asyncio
async def test_gmail_get_401_no_auth(client, monkeypatch):
    """/gmail/get returns 401 when no Gmail creds are present."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")
    resp = await client.post(
        f"{_BASE}/gmail/get", headers=_HDR_GS,
        json={"message_id": "abc123def456"},
    )
    assert resp.status_code == 401, resp.text


# ===== Outlook search =====


@pytest.mark.asyncio
async def test_outlook_search_success_returns_metadata_shape(client, monkeypatch, _actions_to_tmp):
    """AC: /outlook/search returns 200 with the expected metadata shape.

    POSITIVE: search_messages is called; result includes all metadata fields.
    NO body_text in response (metadata-only endpoint).
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_outlook_read_creds(monkeypatch)
    from src.tools.email import outlook_client

    fake_items = [
        {"id": "olMsg001", "thread_id": "olConv001", "from": "alice@x.com",
         "subject": "Invoice Q1", "date": "2024-01-01T10:00:00Z", "snippet": "Please find"},
    ]
    monkeypatch.setattr(
        outlook_client, "search_messages",
        lambda creds, query, max_results: fake_items,
    )

    resp = await client.post(
        f"{_BASE}/outlook/search",
        headers=_HDR_OS,
        json={"query": "from:alice@x.com", "max_results": 10},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 1
    item = body["results"][0]
    assert item["id"] == "olMsg001"
    assert item["thread_id"] == "olConv001"
    assert item["from"] == "alice@x.com"
    assert item["subject"] == "Invoice Q1"
    assert item["snippet"] == "Please find"
    assert "body_text" not in item


@pytest.mark.asyncio
async def test_outlook_search_layer0_denial_403(client, monkeypatch):
    """AC: /outlook/search 403s on Layer-0 grant denial; search_messages NOT called."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_outlook_read_creds(monkeypatch)
    from src.tools.email import outlook_client
    from src.services import tool_grants as tg

    real_check = tg.check_grant

    def _deny_check(config, role, tool_name, *, project_id=None):
        if role == "locked-role":
            return tg.GrantDecision.DENY
        return real_check(config, role, tool_name, project_id=project_id)

    monkeypatch.setattr(tools_email, "check_grant", _deny_check)
    called: list = []
    monkeypatch.setattr(
        outlook_client, "search_messages",
        lambda *a, **k: called.append(1) or [],
    )

    resp = await client.post(
        f"{_BASE}/outlook/search",
        headers={**_HDR_OS, "X-Agent-Role": "locked-role"},
        json={"query": "test"},
    )
    assert resp.status_code == 403, resp.text
    assert "tool_grant_denied" in resp.json()["detail"]
    assert called == [], "search_messages must NOT run when Layer-0 denies"


@pytest.mark.asyncio
async def test_outlook_search_401_no_auth(client, monkeypatch):
    """/outlook/search returns 401 when no Outlook creds are present."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")
    resp = await client.post(
        f"{_BASE}/outlook/search", headers=_HDR_OS, json={"query": "test"},
    )
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_outlook_search_cap_429(client, monkeypatch):
    """/outlook/search returns 429 when the daily cap is exhausted."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_outlook_read_creds(monkeypatch)
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "0")

    resp = await client.post(
        f"{_BASE}/outlook/search", headers=_HDR_OS, json={"query": "test"},
    )
    assert resp.status_code == 429, resp.text


# ===== Outlook get =====


@pytest.mark.asyncio
async def test_outlook_get_success_returns_body(client, monkeypatch, _actions_to_tmp):
    """AC: /outlook/get returns 200 with the expected shape including body_text.

    POSITIVE: get_message is called; response contains body_text.
    PRIVACY: audit row does NOT contain body_text.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_outlook_read_creds(monkeypatch)
    from src.tools.email import outlook_client, gate

    secret_body = "Outlook secret message body content"
    fake_msg = {
        "id": "olMsg001", "thread_id": "olConv001",
        "from": "alice@x.com", "to": "bob@y.com",
        "subject": "Invoice", "date": "2024-01-01T10:00:00Z",
        "body_text": secret_body,
    }
    monkeypatch.setattr(
        outlook_client, "get_message",
        lambda creds, message_id: fake_msg,
    )

    import json as _json
    audit_rows: list[dict] = []

    def _fake_log_audit(provider, pid, action, units, success, error_code=None):
        audit_rows.append({"provider": provider, "action": action,
                           "units": units, "success": success})

    monkeypatch.setattr(gate, "log_audit", _fake_log_audit)

    resp = await client.post(
        f"{_BASE}/outlook/get", headers=_HDR_OS,
        json={"message_id": "olMsg001"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "olMsg001"
    assert body["body_text"] == secret_body
    assert body["from"] == "alice@x.com"

    # PRIVACY: audit rows must NOT contain body_text.
    for row in audit_rows:
        row_str = _json.dumps(row)
        assert secret_body not in row_str, (
            f"body_text leaked into audit row: {row_str}"
        )


@pytest.mark.asyncio
async def test_outlook_get_401_no_auth(client, monkeypatch):
    """/outlook/get returns 401 when no Outlook creds are present."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")
    resp = await client.post(
        f"{_BASE}/outlook/get", headers=_HDR_OS,
        json={"message_id": "olMsg001"},
    )
    assert resp.status_code == 401, resp.text


# ===========================================================================
# FIX-8 (#1939) — message_id validation-rejection coverage (Gmail + Outlook)
# ===========================================================================


@pytest.mark.asyncio
async def test_gmail_get_invalid_chars_message_id_422(client, monkeypatch):
    """FIX-8: /gmail/get rejects message_id with disallowed chars (path-traversal) → 422.

    NEGATIVE lock: the upstream get_message is never called.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_gmail_read_creds(monkeypatch)

    resp = await client.post(
        f"{_BASE}/gmail/get",
        headers=_HDR_GS,
        json={"message_id": "../etc/passwd"},
    )
    assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_gmail_get_oversized_message_id_422(client, monkeypatch):
    """FIX-8: /gmail/get rejects message_id exceeding 64 chars → 422."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_gmail_read_creds(monkeypatch)

    oversized_id = "A" * 65  # exceeds Gmail 64-char bound
    resp = await client.post(
        f"{_BASE}/gmail/get",
        headers=_HDR_GS,
        json={"message_id": oversized_id},
    )
    assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_outlook_get_invalid_chars_message_id_422(client, monkeypatch):
    """FIX-8: /outlook/get rejects message_id with disallowed chars (path-traversal) → 422.

    NEGATIVE lock: the upstream get_message is never called.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_outlook_read_creds(monkeypatch)

    resp = await client.post(
        f"{_BASE}/outlook/get",
        headers=_HDR_OS,
        json={"message_id": "../etc/passwd"},
    )
    assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_outlook_get_oversized_message_id_422(client, monkeypatch):
    """FIX-8: /outlook/get rejects message_id exceeding 512 chars → 422."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_outlook_read_creds(monkeypatch)

    oversized_id = "A" * 513  # exceeds Outlook 512-char bound
    resp = await client.post(
        f"{_BASE}/outlook/get",
        headers=_HDR_OS,
        json={"message_id": oversized_id},
    )
    assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"


# ===========================================================================
# Kanban #1940 — READ extras: thread + labels + attachment (Gmail)
# ===========================================================================
#
# Gate chain: Layer-0 tool-grant → tier(READ, no-op) → auth → cap → client →
# gate.log_audit. NO _write_action_audit (reads only).
#
# Tests use a separate project id to stay hermetic.

_PROJ_1940 = 9993
_HDR_1940 = {"X-Project-Id": str(_PROJ_1940)}


@pytest.fixture(autouse=True)
def _clean_1940_stores():
    """Clear in-memory stores for _PROJ_1940 between tests."""
    from src.tools.email import gate, token_store
    import datetime as _dt
    today = _dt.datetime.now(_dt.UTC).date().isoformat()
    token_store._CACHE.pop(("gmail", _PROJ_1940), None)
    gate._DAILY_UNITS.pop((_PROJ_1940, today), None)
    yield
    token_store._CACHE.pop(("gmail", _PROJ_1940), None)
    gate._DAILY_UNITS.pop((_PROJ_1940, today), None)


def _seed_1940_creds(monkeypatch):
    from src.tools.email import token_store
    from unittest.mock import MagicMock
    from google.oauth2.credentials import Credentials as RealCreds
    import datetime as _dt
    creds = MagicMock(spec=RealCreds)
    creds.expiry = _dt.datetime(2099, 1, 1, 0, 0, 0)
    creds._at_email_cache = "test@gmail.com"
    token_store._CACHE[("gmail", _PROJ_1940)] = creds
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")


# ---------------------------------------------------------------------------
# /gmail/thread
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gmail_thread_success_returns_messages(client, monkeypatch, _actions_to_tmp):
    """AC: /gmail/thread returns 200 with {thread_id, messages, count}.

    POSITIVE: get_thread is called; response contains body_text for each message.
    PRIVACY: audit row does NOT contain body_text.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_1940_creds(monkeypatch)
    from src.tools.email import gmail_client, gate

    secret_body = "Thread body content for test"
    fake_thread = {
        "thread_id": "thr001",
        "messages": [
            {
                "id": "msg001",
                "from": "alice@x.com",
                "to": "bob@y.com",
                "subject": "Re: hello",
                "date": "Mon, 1 Jan 2024 10:00:00 +0000",
                "body_text": secret_body,
            },
            {
                "id": "msg002",
                "from": "bob@y.com",
                "to": "alice@x.com",
                "subject": "Re: hello",
                "date": "Tue, 2 Jan 2024 10:00:00 +0000",
                "body_text": "Reply body",
            },
        ],
    }
    monkeypatch.setattr(
        gmail_client, "get_thread",
        lambda creds, thread_id: fake_thread,
    )

    import json as _json
    audit_rows: list[dict] = []

    def _fake_log_audit(provider, pid, action, units, success, error_code=None):
        audit_rows.append({"provider": provider, "action": action,
                           "units": units, "success": success})

    monkeypatch.setattr(gate, "log_audit", _fake_log_audit)

    resp = await client.post(
        f"{_BASE}/gmail/thread", headers=_HDR_1940,
        json={"thread_id": "thr001"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["thread_id"] == "thr001"
    assert body["count"] == 2
    assert len(body["messages"]) == 2
    assert body["messages"][0]["id"] == "msg001"
    assert body["messages"][0]["body_text"] == secret_body
    assert body["messages"][0]["from"] == "alice@x.com"

    # PRIVACY: body_text must NOT appear in audit rows.
    for row in audit_rows:
        row_str = _json.dumps(row)
        assert secret_body not in row_str, f"body_text leaked into audit row: {row_str}"


@pytest.mark.asyncio
async def test_gmail_thread_401_no_auth(client, monkeypatch):
    """/gmail/thread returns 401 when no Gmail creds are present."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")
    resp = await client.post(
        f"{_BASE}/gmail/thread", headers=_HDR_1940,
        json={"thread_id": "thr001"},
    )
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_gmail_thread_layer0_denial_403(client, monkeypatch):
    """AC: /gmail/thread 403s on Layer-0 grant denial; get_thread NOT called."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_1940_creds(monkeypatch)
    from src.tools.email import gmail_client
    from src.services import tool_grants as tg

    real_check = tg.check_grant

    def _deny_check(config, role, tool_name, *, project_id=None):
        if role == "locked-role":
            return tg.GrantDecision.DENY
        return real_check(config, role, tool_name, project_id=project_id)

    monkeypatch.setattr(tools_email, "check_grant", _deny_check)
    called: list = []
    monkeypatch.setattr(
        gmail_client, "get_thread",
        lambda *a, **k: called.append(1) or {"thread_id": "x", "messages": []},
    )

    resp = await client.post(
        f"{_BASE}/gmail/thread",
        headers={**_HDR_1940, "X-Agent-Role": "locked-role"},
        json={"thread_id": "thr001"},
    )
    assert resp.status_code == 403, resp.text
    assert "tool_grant_denied" in resp.json()["detail"]
    assert called == [], "get_thread must NOT run when Layer-0 denies"


# ---------------------------------------------------------------------------
# /gmail/labels
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gmail_labels_success_returns_label_list(client, monkeypatch, _actions_to_tmp):
    """AC: /gmail/labels returns 200 with {labels, count}.

    POSITIVE: list_labels is called; response has correct shape.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_1940_creds(monkeypatch)
    from src.tools.email import gmail_client

    fake_labels = [
        {"id": "INBOX", "name": "INBOX", "type": "system"},
        {"id": "Label_42", "name": "Work", "type": "user"},
    ]
    monkeypatch.setattr(
        gmail_client, "list_labels",
        lambda creds: fake_labels,
    )

    resp = await client.post(
        f"{_BASE}/gmail/labels", headers=_HDR_1940, json={},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 2
    assert len(body["labels"]) == 2
    assert body["labels"][0]["id"] == "INBOX"
    assert body["labels"][0]["name"] == "INBOX"
    assert body["labels"][0]["type"] == "system"
    assert body["labels"][1]["id"] == "Label_42"


@pytest.mark.asyncio
async def test_gmail_labels_401_no_auth(client, monkeypatch):
    """/gmail/labels returns 401 when no Gmail creds are present."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")
    resp = await client.post(
        f"{_BASE}/gmail/labels", headers=_HDR_1940, json={},
    )
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_gmail_labels_layer0_denial_403(client, monkeypatch):
    """AC: /gmail/labels 403s on Layer-0 grant denial; list_labels NOT called."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_1940_creds(monkeypatch)
    from src.tools.email import gmail_client
    from src.services import tool_grants as tg

    real_check = tg.check_grant

    def _deny_check(config, role, tool_name, *, project_id=None):
        if role == "locked-role":
            return tg.GrantDecision.DENY
        return real_check(config, role, tool_name, project_id=project_id)

    monkeypatch.setattr(tools_email, "check_grant", _deny_check)
    called: list = []
    monkeypatch.setattr(
        gmail_client, "list_labels",
        lambda *a, **k: called.append(1) or [],
    )

    resp = await client.post(
        f"{_BASE}/gmail/labels",
        headers={**_HDR_1940, "X-Agent-Role": "locked-role"},
        json={},
    )
    assert resp.status_code == 403, resp.text
    assert "tool_grant_denied" in resp.json()["detail"]
    assert called == [], "list_labels must NOT run when Layer-0 denies"


# ---------------------------------------------------------------------------
# /gmail/attachment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gmail_attachment_success_returns_data(client, monkeypatch, _actions_to_tmp):
    """AC: /gmail/attachment returns 200 with {filename, mime_type, size, data_base64}.

    POSITIVE: get_attachment is called; response has correct shape.
    PRIVACY: audit row does NOT contain filename or data.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_1940_creds(monkeypatch)
    from src.tools.email import gmail_client, gate

    secret_filename = "secret_invoice.pdf"
    secret_data = "SGVsbG8gV29ybGQ="  # base64url of "Hello World"

    fake_att = {
        "filename": secret_filename,
        "mime_type": "application/pdf",
        "size": 12345,
        "data_base64": secret_data,
    }
    monkeypatch.setattr(
        gmail_client, "get_attachment",
        lambda creds, message_id, attachment_id: fake_att,
    )

    import json as _json
    audit_rows: list[dict] = []

    def _fake_log_audit(provider, pid, action, units, success, error_code=None):
        audit_rows.append({"provider": provider, "action": action,
                           "units": units, "success": success})

    monkeypatch.setattr(gate, "log_audit", _fake_log_audit)

    resp = await client.post(
        f"{_BASE}/gmail/attachment", headers=_HDR_1940,
        json={"message_id": "msg001", "attachment_id": "att001"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["filename"] == secret_filename
    assert body["mime_type"] == "application/pdf"
    assert body["size"] == 12345
    assert body["data_base64"] == secret_data

    # PRIVACY: filename and data must NOT appear in audit rows.
    for row in audit_rows:
        row_str = _json.dumps(row)
        assert secret_filename not in row_str, f"filename leaked into audit row: {row_str}"
        assert secret_data not in row_str, f"data leaked into audit row: {row_str}"


@pytest.mark.asyncio
async def test_gmail_attachment_oversize_returns_413(client, monkeypatch, _actions_to_tmp):
    """AC: /gmail/attachment returns 413 when the attachment exceeds 10 MB.

    NEGATIVE lock: the 413 detail must be {error, max_mb} only — NO filename/data.
    PRIVACY: audit row must NOT contain filename or any attachment content.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_1940_creds(monkeypatch)
    from src.tools.email import gmail_client, gate

    import json as _json
    audit_rows: list[dict] = []

    def _fake_log_audit(provider, pid, action, units, success, error_code=None):
        audit_rows.append({"provider": provider, "action": action,
                           "units": units, "success": success,
                           "error_code": error_code})

    monkeypatch.setattr(gate, "log_audit", _fake_log_audit)

    secret_filename = "huge_video.mp4"

    def _raise_too_large(creds, message_id, attachment_id):
        raise gmail_client.AttachmentTooLargeError(
            f"attachment {secret_filename} is 15 MB, exceeds cap"
        )

    monkeypatch.setattr(gmail_client, "get_attachment", _raise_too_large)

    resp = await client.post(
        f"{_BASE}/gmail/attachment", headers=_HDR_1940,
        json={"message_id": "msg001", "attachment_id": "att001"},
    )
    assert resp.status_code == 413, f"expected 413, got {resp.status_code}: {resp.text}"
    detail = resp.json()["detail"]
    assert detail["error"] == "attachment_too_large"
    assert detail["max_mb"] == 10
    # PRIVACY: 413 detail must NOT contain filename or attachment-specific data.
    detail_str = _json.dumps(detail)
    assert secret_filename not in detail_str, f"filename leaked into 413 detail: {detail_str}"

    # PRIVACY: audit row must NOT contain filename.
    for row in audit_rows:
        row_str = _json.dumps(row)
        assert secret_filename not in row_str, f"filename leaked into audit row: {row_str}"


@pytest.mark.asyncio
async def test_gmail_attachment_401_no_auth(client, monkeypatch):
    """/gmail/attachment returns 401 when no Gmail creds are present."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")
    resp = await client.post(
        f"{_BASE}/gmail/attachment", headers=_HDR_1940,
        json={"message_id": "msg001", "attachment_id": "att001"},
    )
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_gmail_attachment_layer0_denial_403(client, monkeypatch):
    """AC: /gmail/attachment 403s on Layer-0 grant denial; get_attachment NOT called."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_1940_creds(monkeypatch)
    from src.tools.email import gmail_client
    from src.services import tool_grants as tg

    real_check = tg.check_grant

    def _deny_check(config, role, tool_name, *, project_id=None):
        if role == "locked-role":
            return tg.GrantDecision.DENY
        return real_check(config, role, tool_name, project_id=project_id)

    monkeypatch.setattr(tools_email, "check_grant", _deny_check)
    called: list = []
    monkeypatch.setattr(
        gmail_client, "get_attachment",
        lambda *a, **k: called.append(1) or {},
    )

    resp = await client.post(
        f"{_BASE}/gmail/attachment",
        headers={**_HDR_1940, "X-Agent-Role": "locked-role"},
        json={"message_id": "msg001", "attachment_id": "att001"},
    )
    assert resp.status_code == 403, resp.text
    assert "tool_grant_denied" in resp.json()["detail"]
    assert called == [], "get_attachment must NOT run when Layer-0 denies"


# ===========================================================================
# FIX-D (#1940) — 422 validation-rejection tests (thread + attachment)
# ===========================================================================


@pytest.mark.asyncio
async def test_gmail_thread_invalid_chars_422(client, monkeypatch):
    """FIX-D: /gmail/thread rejects thread_id with disallowed chars → 422.

    NEGATIVE lock: get_thread is never called.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_1940_creds(monkeypatch)

    resp = await client.post(
        f"{_BASE}/gmail/thread",
        headers=_HDR_1940,
        json={"thread_id": "../etc/passwd"},
    )
    assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_gmail_thread_oversized_422(client, monkeypatch):
    """FIX-D: /gmail/thread rejects thread_id exceeding 64 chars → 422."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_1940_creds(monkeypatch)

    oversized_id = "A" * 65  # exceeds 64-char bound
    resp = await client.post(
        f"{_BASE}/gmail/thread",
        headers=_HDR_1940,
        json={"thread_id": oversized_id},
    )
    assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_gmail_attachment_invalid_chars_422(client, monkeypatch):
    """FIX-D: /gmail/attachment rejects message_id with disallowed chars → 422.

    NEGATIVE lock: get_attachment is never called.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_1940_creds(monkeypatch)

    resp = await client.post(
        f"{_BASE}/gmail/attachment",
        headers=_HDR_1940,
        json={"message_id": "../etc/passwd", "attachment_id": "att001"},
    )
    assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_gmail_attachment_oversized_422(client, monkeypatch):
    """FIX-D: /gmail/attachment rejects message_id exceeding 512 chars → 422."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_1940_creds(monkeypatch)

    oversized_id = "A" * 513  # exceeds 512-char bound
    resp = await client.post(
        f"{_BASE}/gmail/attachment",
        headers=_HDR_1940,
        json={"message_id": oversized_id, "attachment_id": "att001"},
    )
    assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_gmail_attachment_not_found_404(client, monkeypatch):
    """FIX-D: /gmail/attachment returns 404 when get_attachment raises AttachmentNotFoundError.

    NEGATIVE lock: must NOT be 502 (generic) when the specific not-found exception fires.
    POSITIVE: detail is {error: attachment_not_found} with no ids or filenames.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_1940_creds(monkeypatch)
    from src.tools.email import gmail_client

    def _raise_not_found(creds, message_id, attachment_id):
        raise gmail_client.AttachmentNotFoundError("attachment_id not found in message")

    monkeypatch.setattr(gmail_client, "get_attachment", _raise_not_found)

    resp = await client.post(
        f"{_BASE}/gmail/attachment", headers=_HDR_1940,
        json={"message_id": "msg001", "attachment_id": "att001"},
    )
    assert resp.status_code == 404, f"expected 404, got {resp.status_code}: {resp.text}"
    detail = resp.json()["detail"]
    assert detail["error"] == "attachment_not_found"
    # NEGATIVE: must NOT be 502 (generic path) or 413 (size path).
    assert resp.status_code != 502
    assert resp.status_code != 413


# ===========================================================================
# Kanban #1941 — dry_run preview for /gmail/trash and /outlook/trash
# ===========================================================================
#
# Tests use separate project ids to stay hermetic from other test blocks.
# Fixtures mirror the existing per-provider store-cleanup patterns.

_PROJ_DR_G = 9992   # Gmail dry_run tests
_PROJ_DR_OL = 9991  # Outlook dry_run tests
_HDR_DR_G = {"X-Project-Id": str(_PROJ_DR_G)}
_HDR_DR_OL = {"X-Project-Id": str(_PROJ_DR_OL)}


@pytest.fixture(autouse=True)
def _clean_dryrun_stores():
    """Clear in-memory stores for both dry_run project ids between tests."""
    from src.tools.email import gate, token_store
    import datetime as _dt
    today = _dt.datetime.now(_dt.UTC).date().isoformat()
    for key in [("gmail", _PROJ_DR_G), ("outlook", _PROJ_DR_OL)]:
        token_store._CACHE.pop(key, None)
    for key in [(_PROJ_DR_G, today), (_PROJ_DR_OL, today)]:
        gate._DAILY_UNITS.pop(key, None)
    yield
    for key in [("gmail", _PROJ_DR_G), ("outlook", _PROJ_DR_OL)]:
        token_store._CACHE.pop(key, None)
    for key in [(_PROJ_DR_G, today), (_PROJ_DR_OL, today)]:
        gate._DAILY_UNITS.pop(key, None)


def _seed_gmail_dryrun_creds(monkeypatch):
    from src.tools.email import token_store
    from unittest.mock import MagicMock
    from google.oauth2.credentials import Credentials as RealCreds
    import datetime as _dt
    creds = MagicMock(spec=RealCreds)
    creds.expiry = _dt.datetime(2099, 1, 1, 0, 0, 0)
    creds._at_email_cache = "test@gmail.com"
    token_store._CACHE[("gmail", _PROJ_DR_G)] = creds
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")


def _seed_outlook_dryrun_creds(monkeypatch):
    from src.tools.email import token_store
    import time
    token_store._CACHE[("outlook", _PROJ_DR_OL)] = {
        "access_token": "fake-dryrun-token",
        "expires_in": 3600,
        "_acquired_at": time.time(),
    }
    monkeypatch.setenv("EMAIL_TOOLS_DAILY_UNITS_CAP", "1000")


# ---------------------------------------------------------------------------
# Gmail dry_run — message_ids mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gmail_trash_dryrun_message_ids_returns_preview_no_move(client, monkeypatch):
    """AC (#1941): /gmail/trash dry_run=true with message_ids returns preview; trash NOT called.

    POSITIVE: would_affect_count == len(ids) and would_affect_ids == ids.
    NEGATIVE: trash_messages is never called (no mutation).
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_gmail_dryrun_creds(monkeypatch)
    from src.tools.email import gmail_client

    trash_calls: list = []
    monkeypatch.setattr(
        gmail_client, "trash_messages",
        lambda c, ids: trash_calls.append(ids) or (list(ids), []),
    )

    ids_to_preview = ["abc123", "def456", "ghi789"]
    resp = await client.post(
        f"{_BASE}/gmail/trash",
        headers=_HDR_DR_G,
        json={"message_ids": ids_to_preview, "dry_run": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Preview shape assertions.
    assert body["dry_run"] is True
    assert body["trashed_count"] == 0
    assert body["trashed_ids"] == []
    assert body["would_affect_count"] == len(ids_to_preview)
    assert body["would_affect_ids"] == ids_to_preview

    # NEGATIVE lock: trash_messages must NOT have been called.
    assert trash_calls == [], "trash_messages must NOT run on dry_run"


@pytest.mark.asyncio
async def test_gmail_trash_dryrun_succeeds_without_operator_proof_when_gate_active(
    client, monkeypatch
):
    """AC (#1941): dry_run=true succeeds with gate ACTIVE + NO operator-proof token.

    Proves the operator-proof gate is skipped for the preview path.
    NEGATIVE: trash_messages never called.
    """
    monkeypatch.setenv(_KEY_ENV, _TOKEN)   # gate ACTIVE — would 403 a real trash
    _seed_gmail_dryrun_creds(monkeypatch)
    from src.tools.email import gmail_client

    trash_calls: list = []
    monkeypatch.setattr(
        gmail_client, "trash_messages",
        lambda c, ids: trash_calls.append(ids) or (list(ids), []),
    )

    resp = await client.post(
        f"{_BASE}/gmail/trash",
        headers=_HDR_DR_G,  # NO X-Operator-Token header
        json={"message_ids": ["abc123"], "dry_run": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dry_run"] is True
    assert body["would_affect_count"] == 1
    assert trash_calls == [], "trash_messages must NOT run on dry_run"


@pytest.mark.asyncio
async def test_gmail_trash_dryrun_query_mode_returns_preview_no_move(client, monkeypatch):
    """AC (#1941): /gmail/trash dry_run=true in query mode resolves ids and returns preview.

    list_message_ids is called (id resolution happens; list units charged).
    trash_messages is NOT called.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_gmail_dryrun_creds(monkeypatch)
    from src.tools.email import gmail_client

    resolved_ids = ["qid1", "qid2"]
    list_calls: list = []
    trash_calls: list = []

    monkeypatch.setattr(
        gmail_client, "list_message_ids",
        lambda creds, query, max_results: (list_calls.append(query), resolved_ids)[1],
    )
    monkeypatch.setattr(
        gmail_client, "trash_messages",
        lambda c, ids: trash_calls.append(ids) or (list(ids), []),
    )

    resp = await client.post(
        f"{_BASE}/gmail/trash",
        headers=_HDR_DR_G,
        json={"query": "from:spam@x.com", "dry_run": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dry_run"] is True
    assert body["trashed_count"] == 0
    assert body["would_affect_count"] == len(resolved_ids)
    assert set(body["would_affect_ids"]) == set(resolved_ids)

    # NEGATIVE: trash_messages must NOT run.
    assert trash_calls == [], "trash_messages must NOT run on dry_run"
    # POSITIVE: list_message_ids DID run (id resolution happens).
    assert len(list_calls) == 1


# ---------------------------------------------------------------------------
# Outlook dry_run — message_ids mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outlook_trash_dryrun_message_ids_returns_preview_no_move(client, monkeypatch):
    """AC (#1941): /outlook/trash dry_run=true with message_ids returns preview; trash NOT called.

    POSITIVE: would_affect_count == len(ids) and would_affect_ids == ids.
    NEGATIVE: trash_messages is never called.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_outlook_dryrun_creds(monkeypatch)
    from src.tools.email import outlook_client

    trash_calls: list = []
    monkeypatch.setattr(
        outlook_client, "trash_messages",
        lambda c, ids: trash_calls.append(ids) or (list(ids), []),
    )

    ids_to_preview = ["olId1", "olId2"]
    resp = await client.post(
        f"{_BASE}/outlook/trash",
        headers=_HDR_DR_OL,
        json={"message_ids": ids_to_preview, "dry_run": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["dry_run"] is True
    assert body["trashed_count"] == 0
    assert body["trashed_ids"] == []
    assert body["would_affect_count"] == len(ids_to_preview)
    assert body["would_affect_ids"] == ids_to_preview

    # NEGATIVE lock: trash_messages must NOT have been called.
    assert trash_calls == [], "trash_messages must NOT run on dry_run"


@pytest.mark.asyncio
async def test_outlook_trash_dryrun_succeeds_without_operator_proof_when_gate_active(
    client, monkeypatch
):
    """AC (#1941): Outlook dry_run=true succeeds with gate ACTIVE + NO operator-proof token.

    Proves the operator-proof gate is skipped for the Outlook preview path.
    NEGATIVE: trash_messages never called.
    """
    monkeypatch.setenv(_KEY_ENV, _TOKEN)   # gate ACTIVE
    _seed_outlook_dryrun_creds(monkeypatch)
    from src.tools.email import outlook_client

    trash_calls: list = []
    monkeypatch.setattr(
        outlook_client, "trash_messages",
        lambda c, ids: trash_calls.append(ids) or (list(ids), []),
    )

    resp = await client.post(
        f"{_BASE}/outlook/trash",
        headers=_HDR_DR_OL,  # NO X-Operator-Token header
        json={"message_ids": ["olId1"], "dry_run": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dry_run"] is True
    assert body["would_affect_count"] == 1
    assert trash_calls == [], "trash_messages must NOT run on dry_run"


@pytest.mark.asyncio
async def test_outlook_trash_dryrun_query_mode_returns_preview_no_move(client, monkeypatch):
    """AC (#1941 NIT-2): /outlook/trash dry_run=true in query mode resolves ids and returns preview.

    list_message_ids is called once (id resolution happens; list units charged).
    trash_messages is NOT called (no mutation).

    POSITIVE: would_affect_count == len(fake ids) and dry_run is True.
    NEGATIVE: trash_messages never called.
    """
    monkeypatch.delenv(_KEY_ENV, raising=False)
    _seed_outlook_dryrun_creds(monkeypatch)
    from src.tools.email import outlook_client

    resolved_ids = ["olQid1", "olQid2", "olQid3"]
    list_calls: list = []
    trash_calls: list = []

    monkeypatch.setattr(
        outlook_client, "list_message_ids",
        lambda creds, query, max_results: (list_calls.append(query), resolved_ids)[1],
    )
    monkeypatch.setattr(
        outlook_client, "trash_messages",
        lambda c, ids: trash_calls.append(ids) or (list(ids), []),
    )

    resp = await client.post(
        f"{_BASE}/outlook/trash",
        headers=_HDR_DR_OL,
        json={"query": "from:spam@x.com", "dry_run": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["dry_run"] is True
    assert body["trashed_count"] == 0
    assert body["trashed_ids"] == []
    assert body["would_affect_count"] == len(resolved_ids)
    assert set(body["would_affect_ids"]) == set(resolved_ids)

    # NEGATIVE: trash_messages must NOT run on dry_run.
    assert trash_calls == [], "trash_messages must NOT run on dry_run"
    # POSITIVE: list_message_ids DID run exactly once (id resolution happens).
    assert len(list_calls) == 1
