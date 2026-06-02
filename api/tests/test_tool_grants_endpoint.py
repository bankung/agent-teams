"""Endpoint-integration tests for the #1799 tool-governance gate wired into
`/api/tools/email/{gmail,outlook}/trash`.

Proves the 403/allow matrix end-to-end through the real router + dependency +
DB-config read:

  - restricted role lacking the tool   -> 403 (turned away before auth/quota).
  - unlisted role                       -> allowed (subject to existing gates).
  - no X-Agent-Role header              -> allowed (unrestricted).
  - granted role                        -> allowed (subject to existing gates).

A real seeded project row is needed because the gate reads `config.tool_grants`
from the DB. We PATCH the seeded project's `config.tool_grants` via the public
API (DB writes go through FastAPI only), exercise the endpoint, then restore
the original config in a fixture so other tests are unaffected.

"Allowed (subject to existing gates)" means: the #1799 gate passes, so the
request proceeds to the existing flow and lands on the NEXT gate. With no creds
injected, that next gate is the 401 auth check (message_ids mode: bulk-check
first, but 1 id is below threshold). So an ALLOWED call surfaces as 401, and we
assert the response is NOT 403 (the governance gate did not fire). The deny
path is the only one that yields 403.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

# A real seeded project id (seed always creates the agent-teams project at id=1).
_SEED_PROJ = 1
_HDR = {"X-Project-Id": str(_SEED_PROJ)}
_BASE = "/api/tools/email"


@pytest_asyncio.fixture
async def restore_config(client):
    """Snapshot the seeded project's config, yield, restore it after the test.

    Keeps the tool_grants mutation local to the test even though the test DB
    persists for the whole session.
    """
    resp = await client.get(f"/api/projects/{_SEED_PROJ}", headers=_HDR)
    assert resp.status_code == 200, resp.text
    original_config = resp.json()["config"]

    yield

    # Restore — PATCH the original config back verbatim.
    restore = await client.patch(
        f"/api/projects/{_SEED_PROJ}",
        headers=_HDR,
        json={"config": original_config},
    )
    assert restore.status_code == 200, restore.text


async def _set_tool_grants(client, grants: dict) -> None:
    """PATCH config.tool_grants onto the seeded project (preserving other keys)."""
    resp = await client.get(f"/api/projects/{_SEED_PROJ}", headers=_HDR)
    assert resp.status_code == 200, resp.text
    config = dict(resp.json()["config"])
    config["tool_grants"] = grants
    patch = await client.patch(
        f"/api/projects/{_SEED_PROJ}",
        headers=_HDR,
        json={"config": config},
    )
    assert patch.status_code == 200, patch.text


# ===========================================================================
# gmail/trash governance matrix
# ===========================================================================


@pytest.mark.asyncio
async def test_gmail_trash_restricted_role_missing_tool_403(client, restore_config) -> None:
    """A role listed in tool_grants WITHOUT gmail.trash -> 403."""
    await _set_tool_grants(client, {"secretary": ["outlook.trash"]})

    resp = await client.post(
        f"{_BASE}/gmail/trash",
        headers={**_HDR, "X-Agent-Role": "secretary"},
        json={"message_ids": ["abc123"]},
    )
    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert "tool_grant_denied" in detail
    assert "secretary" in detail
    assert "gmail.trash" in detail


@pytest.mark.asyncio
async def test_gmail_trash_empty_list_role_403(client, restore_config) -> None:
    """A role with an empty allow-list -> 403 for gmail.trash (deny-all lockout)."""
    await _set_tool_grants(client, {"locked-role": []})

    resp = await client.post(
        f"{_BASE}/gmail/trash",
        headers={**_HDR, "X-Agent-Role": "locked-role"},
        json={"message_ids": ["abc123"]},
    )
    assert resp.status_code == 403, resp.text
    assert "tool_grant_denied" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_gmail_trash_granted_role_passes_gate(client, restore_config) -> None:
    """A role granted gmail.trash passes the #1799 gate (then 401 — no creds).

    POSITIVE: the governance gate allows it. NEGATIVE locked: the response is
    NOT 403 (the gate did not fire).
    """
    await _set_tool_grants(client, {"secretary": ["gmail.trash"]})

    resp = await client.post(
        f"{_BASE}/gmail/trash",
        headers={**_HDR, "X-Agent-Role": "secretary"},
        json={"message_ids": ["abc123"]},
    )
    assert resp.status_code != 403, "granted role must pass the governance gate"
    assert resp.status_code == 401  # next gate: auth (no creds injected)


@pytest.mark.asyncio
async def test_gmail_trash_unlisted_role_passes_gate(client, restore_config) -> None:
    """A role NOT listed in tool_grants is unrestricted (opt-in) -> not 403."""
    await _set_tool_grants(client, {"secretary": ["outlook.trash"]})

    resp = await client.post(
        f"{_BASE}/gmail/trash",
        headers={**_HDR, "X-Agent-Role": "dev-backend"},  # unlisted
        json={"message_ids": ["abc123"]},
    )
    assert resp.status_code != 403, "unlisted role must be unrestricted"
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_gmail_trash_no_role_header_passes_gate(client, restore_config) -> None:
    """No X-Agent-Role header -> unrestricted even when tool_grants exist."""
    await _set_tool_grants(client, {"secretary": []})  # secretary locked, but...

    resp = await client.post(
        f"{_BASE}/gmail/trash",
        headers=_HDR,  # no X-Agent-Role
        json={"message_ids": ["abc123"]},
    )
    assert resp.status_code != 403, "no role header -> unrestricted"
    assert resp.status_code == 401


# ===========================================================================
# outlook/trash governance matrix (mirror — proves both endpoints wired)
# ===========================================================================


@pytest.mark.asyncio
async def test_outlook_trash_restricted_role_missing_tool_403(client, restore_config) -> None:
    """A role listed WITHOUT outlook.trash -> 403 on outlook/trash."""
    await _set_tool_grants(client, {"secretary": ["gmail.trash"]})

    resp = await client.post(
        f"{_BASE}/outlook/trash",
        headers={**_HDR, "X-Agent-Role": "secretary"},
        json={"message_ids": ["AAA111"]},
    )
    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert "tool_grant_denied" in detail
    assert "outlook.trash" in detail


@pytest.mark.asyncio
async def test_outlook_trash_granted_role_passes_gate(client, restore_config) -> None:
    """A role granted outlook.trash passes the gate (then 401 — no creds)."""
    await _set_tool_grants(client, {"secretary": ["outlook.trash"]})

    resp = await client.post(
        f"{_BASE}/outlook/trash",
        headers={**_HDR, "X-Agent-Role": "secretary"},
        json={"message_ids": ["AAA111"]},
    )
    assert resp.status_code != 403
    assert resp.status_code == 401
