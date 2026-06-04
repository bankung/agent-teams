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
    assert lines[0]["approval_mode"] == "operator_proof"


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
