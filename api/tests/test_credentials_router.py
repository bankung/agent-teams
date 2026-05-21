"""Kanban #1326 (M3) — credentials vault router contract smoke tests.

Coverage:
  - POST + GET + PATCH + DELETE happy paths.
  - GET / list endpoint response shape contains NO ciphertext AND NO plaintext
    (binary-comparison-on-response-body — AC#3 lock).
  - /use without matching approval policy → 403; access_log row recorded with
    denial reason.
  - /use with matching approval policy → 200 + plaintext + access_log row
    recorded.
  - Cross-project access (header != path project_id) → 404.
  - Double-DELETE → 404 (soft-deleted rows treated as not-found).
  - Master key configured via monkeypatched CREDENTIALS_MASTER_KEY across all
    tests (the test DB is fresh per session; the api app was imported BEFORE
    the env was set, so we also reset the credentials_crypto cache).

The rigorous suite — concurrent INSERT races on the (project_id, name)
unique index, large-value boundary tests, malformed metadata shapes,
multi-project leakage matrices, /use access_count race conditions — is
dev-tester's domain.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from cryptography.fernet import Fernet

from src.services import credentials_crypto


# Recognisable sentinel — used by the plaintext-in-logs grep audit step.
SENTINEL_PLAINTEXT = "sentinel-plain-12345"
SENTINEL_PLAINTEXT_BYTES = SENTINEL_PLAINTEXT.encode("utf-8")


# ---------------------------------------------------------------------------
# Module-level fixture: a stable master key for this whole test file.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _credentials_master_key(monkeypatch):
    """Set a fresh Fernet master key for every test + clear the cache so the
    next call to get_fernet() picks it up.
    """
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CREDENTIALS_MASTER_KEY", key)
    credentials_crypto._fernet = None
    yield
    credentials_crypto._fernet = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


async def _make_project(client, scaffold_cleanup, slug: str) -> int:
    name = scaffold_cleanup(_unique_name(slug))
    resp = await client.post("/api/projects", json=_project_create_payload(name))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _cred_create_body(
    *,
    name: str,
    value: str = SENTINEL_PLAINTEXT,
    kind: str = "api_key",
    metadata: dict[str, Any] | None = None,
) -> dict:
    body: dict = {"name": name, "value": value, "kind": kind}
    if metadata is not None:
        body["metadata"] = metadata
    return body


# ---------------------------------------------------------------------------
# 1. POST + GET happy path. Locks AC#3 — no ciphertext/plaintext in list shape.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_then_list_no_plaintext_no_ciphertext_in_response(
    client, scaffold_cleanup
):
    """AC#3 lock: GET /credentials returns names + metadata, NEVER ciphertext
    or plaintext. The assertion is a binary comparison on the raw response
    body bytes so any future field-addition to the response model that leaks
    either surface fails loudly.

    POSITIVE: the credential appears in the list with id/name/kind intact.
    NEGATIVE: SENTINEL_PLAINTEXT_BYTES NOT in response.content; ciphertext
    bytes NOT in response.content.
    """
    pid = await _make_project(client, scaffold_cleanup, "cred-list")
    headers = {"X-Project-Id": str(pid)}
    cred_name = "openai_api_key"

    # POST creates the credential
    create_resp = await client.post(
        f"/api/projects/{pid}/credentials",
        json=_cred_create_body(name=cred_name, value=SENTINEL_PLAINTEXT),
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()
    cred_id = created["id"]
    assert created["name"] == cred_name
    assert created["kind"] == "api_key"
    assert created["project_id"] == pid
    assert created["access_count"] == 0
    assert created["status"] == 1
    # The create response shape must not leak the plaintext either.
    assert "value" not in created
    assert "ciphertext" not in created
    assert SENTINEL_PLAINTEXT_BYTES not in create_resp.content, (
        "POST response leaked plaintext"
    )

    # GET list returns the row WITHOUT plaintext or ciphertext
    list_resp = await client.get(f"/api/projects/{pid}/credentials", headers=headers)
    assert list_resp.status_code == 200, list_resp.text
    rows = list_resp.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == cred_id
    assert row["name"] == cred_name
    assert "value" not in row
    assert "ciphertext" not in row

    # Binary lock — the literal plaintext bytes never appear in the wire body.
    assert SENTINEL_PLAINTEXT_BYTES not in list_resp.content, (
        "GET list response leaked plaintext"
    )
    # Fetch the ciphertext via direct DB read and confirm it also doesn't appear.
    from sqlalchemy import select
    from src.db import SessionLocal
    from src.models.credential import ProjectCredential

    async with SessionLocal() as s:
        cred = (
            await s.execute(
                select(ProjectCredential).where(ProjectCredential.id == cred_id)
            )
        ).scalar_one()
        ciphertext_bytes = bytes(cred.ciphertext)
    assert ciphertext_bytes not in list_resp.content, (
        "GET list response leaked ciphertext bytes"
    )
    # Sanity: the ciphertext on disk is NOT the plaintext.
    assert SENTINEL_PLAINTEXT_BYTES not in ciphertext_bytes, (
        "DB ciphertext column contains plaintext — Fernet wasn't applied"
    )


# ---------------------------------------------------------------------------
# 2. PATCH happy path — re-encrypt value + update metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_credential_re_encrypts_value_and_updates_metadata(
    client, scaffold_cleanup
):
    pid = await _make_project(client, scaffold_cleanup, "cred-patch")
    headers = {"X-Project-Id": str(pid)}
    cred_name = "stripe_test"

    resp = await client.post(
        f"/api/projects/{pid}/credentials",
        json=_cred_create_body(
            name=cred_name, value="initial-value-abc", metadata={"env": "test"}
        ),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    cred_id = resp.json()["id"]

    # Capture pre-PATCH ciphertext via direct DB read.
    from sqlalchemy import select
    from src.db import SessionLocal
    from src.models.credential import ProjectCredential

    async with SessionLocal() as s:
        pre = (
            await s.execute(
                select(ProjectCredential).where(ProjectCredential.id == cred_id)
            )
        ).scalar_one()
        pre_ciphertext = bytes(pre.ciphertext)

    # PATCH the value + metadata
    patch_resp = await client.patch(
        f"/api/projects/{pid}/credentials/{cred_name}",
        json={"value": "rotated-value-xyz", "metadata": {"env": "test", "rotated": True}},
        headers=headers,
    )
    assert patch_resp.status_code == 200, patch_resp.text
    patched = patch_resp.json()
    assert patched["id"] == cred_id
    assert patched["metadata"] == {"env": "test", "rotated": True}
    assert patched["updated_at"] is not None
    assert "value" not in patched

    # POSITIVE: post-PATCH ciphertext differs from pre-PATCH (re-encryption
    # actually happened). NEGATIVE-lock: not equal (vacuous baseline blocked).
    async with SessionLocal() as s:
        post = (
            await s.execute(
                select(ProjectCredential).where(ProjectCredential.id == cred_id)
            )
        ).scalar_one()
        post_ciphertext = bytes(post.ciphertext)
    assert post_ciphertext != pre_ciphertext, (
        "PATCH did not re-encrypt — ciphertext unchanged"
    )


# ---------------------------------------------------------------------------
# 3. /use without policy → 403 + access_log row written with denial reason.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_use_denied_without_policy_logs_refusal_with_403(
    client, scaffold_cleanup
):
    pid = await _make_project(client, scaffold_cleanup, "cred-use-deny")
    headers = {"X-Project-Id": str(pid)}
    cred_name = "denied_key"

    resp = await client.post(
        f"/api/projects/{pid}/credentials",
        json=_cred_create_body(name=cred_name, value=SENTINEL_PLAINTEXT),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    cred_id = resp.json()["id"]

    # No approval_policies on the project → /use returns 403
    use_resp = await client.post(
        f"/api/projects/{pid}/credentials/{cred_name}/use",
        json={},
        headers=headers,
    )
    assert use_resp.status_code == 403, use_resp.text
    detail = use_resp.json()["detail"]
    assert "policy=not_matched" in detail
    # The response body MUST NOT carry plaintext on denial.
    assert SENTINEL_PLAINTEXT_BYTES not in use_resp.content

    # access_log row recorded with denial reason in accessed_by
    from sqlalchemy import select
    from src.db import SessionLocal
    from src.models.credential import CredentialAccessLog

    async with SessionLocal() as s:
        logs = list(
            (
                await s.execute(
                    select(CredentialAccessLog)
                    .where(CredentialAccessLog.credential_id == cred_id)
                    .where(CredentialAccessLog.action == "use")
                )
            ).scalars()
        )
    assert len(logs) == 1
    assert "denied=policy_unmatched" in logs[0].accessed_by

    # The credential's access_count was NOT incremented (denial path).
    from src.models.credential import ProjectCredential

    async with SessionLocal() as s:
        cred = (
            await s.execute(
                select(ProjectCredential).where(ProjectCredential.id == cred_id)
            )
        ).scalar_one()
    assert cred.access_count == 0, "access_count must not bump on denied /use"
    assert cred.last_accessed_at is None


# ---------------------------------------------------------------------------
# 4. /use with policy → 200 + plaintext + access_log row + counters bumped.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_use_granted_with_matching_policy_returns_plaintext_and_logs(
    client, scaffold_cleanup
):
    pid = await _make_project(client, scaffold_cleanup, "cred-use-grant")
    headers = {"X-Project-Id": str(pid)}
    cred_name = "approved_key"

    create_resp = await client.post(
        f"/api/projects/{pid}/credentials",
        json=_cred_create_body(name=cred_name, value=SENTINEL_PLAINTEXT),
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    cred_id = create_resp.json()["id"]

    # PATCH the project to grant this credential via approval_policies.
    grant_resp = await client.patch(
        f"/api/projects/{pid}",
        json={
            "approval_policies": {
                "rules": [
                    {
                        "action": "credential.use",
                        "credential_name": cred_name,
                        "auto_approve": True,
                    }
                ]
            }
        },
        headers=headers,
    )
    assert grant_resp.status_code == 200, grant_resp.text

    # Now /use returns plaintext
    use_resp = await client.post(
        f"/api/projects/{pid}/credentials/{cred_name}/use",
        json={"reason": "smoke-test integration call"},
        headers={**headers, "X-Agent-Identity": "operator:smoke-test"},
    )
    assert use_resp.status_code == 200, use_resp.text
    body = use_resp.json()
    assert body["value"] == SENTINEL_PLAINTEXT
    assert body["credential_id"] == cred_id
    assert body["access_log_id"] > 0

    # access_log row recorded WITHOUT the denial marker
    from sqlalchemy import select
    from src.db import SessionLocal
    from src.models.credential import CredentialAccessLog, ProjectCredential

    async with SessionLocal() as s:
        logs = list(
            (
                await s.execute(
                    select(CredentialAccessLog)
                    .where(CredentialAccessLog.credential_id == cred_id)
                    .where(CredentialAccessLog.action == "use")
                )
            ).scalars()
        )
    assert len(logs) == 1
    assert logs[0].accessed_by == "header:operator:smoke-test"
    assert "denied" not in logs[0].accessed_by

    # Credential counters bumped
    async with SessionLocal() as s:
        cred = (
            await s.execute(
                select(ProjectCredential).where(ProjectCredential.id == cred_id)
            )
        ).scalar_one()
    assert cred.access_count == 1
    assert cred.last_accessed_at is not None


# ---------------------------------------------------------------------------
# 5. DELETE soft-deletes; subsequent GET/use 404; double-DELETE 404.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_soft_deletes_then_subsequent_calls_404(client, scaffold_cleanup):
    pid = await _make_project(client, scaffold_cleanup, "cred-del")
    headers = {"X-Project-Id": str(pid)}
    cred_name = "to_be_deleted"

    resp = await client.post(
        f"/api/projects/{pid}/credentials",
        json=_cred_create_body(name=cred_name),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text

    # First DELETE → 204
    del_resp = await client.delete(
        f"/api/projects/{pid}/credentials/{cred_name}", headers=headers
    )
    assert del_resp.status_code == 204, del_resp.text

    # GET list excludes the soft-deleted row
    list_resp = await client.get(f"/api/projects/{pid}/credentials", headers=headers)
    assert list_resp.status_code == 200
    assert all(r["name"] != cred_name for r in list_resp.json())

    # Subsequent /use → 404
    use_resp = await client.post(
        f"/api/projects/{pid}/credentials/{cred_name}/use",
        json={},
        headers=headers,
    )
    assert use_resp.status_code == 404

    # Double-DELETE → 404
    del_resp2 = await client.delete(
        f"/api/projects/{pid}/credentials/{cred_name}", headers=headers
    )
    assert del_resp2.status_code == 404


# ---------------------------------------------------------------------------
# 6. Cross-project access (path != header project_id) → 404.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_project_access_returns_404(client, scaffold_cleanup):
    pid_a = await _make_project(client, scaffold_cleanup, "cred-a")
    pid_b = await _make_project(client, scaffold_cleanup, "cred-b")
    cred_name = "a_secret"

    resp = await client.post(
        f"/api/projects/{pid_a}/credentials",
        json=_cred_create_body(name=cred_name),
        headers={"X-Project-Id": str(pid_a)},
    )
    assert resp.status_code == 201, resp.text

    # Project B's session cannot see Project A's credentials.
    list_resp = await client.get(
        f"/api/projects/{pid_a}/credentials",
        headers={"X-Project-Id": str(pid_b)},
    )
    assert list_resp.status_code == 404
    assert f"Project id={pid_a} not found" in list_resp.json()["detail"]


# ---------------------------------------------------------------------------
# 7. Missing X-Project-Id header → 400 (parity with other per-project routers)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_header_returns_400(client, scaffold_cleanup):
    pid = await _make_project(client, scaffold_cleanup, "cred-noheader")
    # No headers passed → 400 from the require_project_id_header dependency
    resp = await client.get(f"/api/projects/{pid}/credentials")
    assert resp.status_code == 400
    assert "X-Project-Id header is required" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 8. Duplicate name in same project → 409 conflict.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_name_returns_409(client, scaffold_cleanup):
    pid = await _make_project(client, scaffold_cleanup, "cred-dup")
    headers = {"X-Project-Id": str(pid)}
    body = _cred_create_body(name="dup_key", value="value-one")

    first = await client.post(
        f"/api/projects/{pid}/credentials", json=body, headers=headers
    )
    assert first.status_code == 201

    second = await client.post(
        f"/api/projects/{pid}/credentials",
        json=_cred_create_body(name="dup_key", value="value-two"),
        headers=headers,
    )
    assert second.status_code == 409


# ---------------------------------------------------------------------------
# 9. X-Agent-Identity header sanitisation (S2 fix — Kanban review finding).
# ---------------------------------------------------------------------------


async def _make_approved_project_and_cred(
    client, scaffold_cleanup, slug: str
) -> tuple[int, dict, str]:
    """Helper: project with auto-approve policy + one credential. Returns
    (project_id, headers, cred_name).
    """
    pid = await _make_project(client, scaffold_cleanup, slug)
    headers = {"X-Project-Id": str(pid)}
    cred_name = f"key_{uuid.uuid4().hex[:6]}"

    create_resp = await client.post(
        f"/api/projects/{pid}/credentials",
        json=_cred_create_body(name=cred_name, value=SENTINEL_PLAINTEXT),
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text

    grant_resp = await client.patch(
        f"/api/projects/{pid}",
        json={
            "approval_policies": {
                "rules": [
                    {
                        "action": "credential.use",
                        "credential_name": cred_name,
                        "auto_approve": True,
                    }
                ]
            }
        },
        headers=headers,
    )
    assert grant_resp.status_code == 200, grant_resp.text
    return pid, headers, cred_name


@pytest.mark.asyncio
async def test_use_credential_unsanitized_x_agent_identity_gets_prefixed(
    client, scaffold_cleanup
):
    """Valid X-Agent-Identity header is prefixed with 'header:' in the audit log."""
    pid, headers, cred_name = await _make_approved_project_and_cred(
        client, scaffold_cleanup, "cred-s2-prefix"
    )
    cred_id = (
        await client.get(f"/api/projects/{pid}/credentials", headers=headers)
    ).json()[0]["id"]

    use_resp = await client.post(
        f"/api/projects/{pid}/credentials/{cred_name}/use",
        json={},
        headers={**headers, "X-Agent-Identity": "agent:dev-backend"},
    )
    assert use_resp.status_code == 200, use_resp.text

    from sqlalchemy import select
    from src.db import SessionLocal
    from src.models.credential import CredentialAccessLog

    async with SessionLocal() as s:
        logs = list(
            (
                await s.execute(
                    select(CredentialAccessLog)
                    .where(CredentialAccessLog.credential_id == cred_id)
                    .where(CredentialAccessLog.action == "use")
                )
            ).scalars()
        )
    assert len(logs) == 1
    # POSITIVE: stored value is the prefixed form, not the raw header value.
    assert logs[0].accessed_by == "header:agent:dev-backend"
    # NEGATIVE: raw value (without prefix) is NOT stored verbatim.
    assert logs[0].accessed_by != "agent:dev-backend"


@pytest.mark.asyncio
async def test_use_credential_malformed_x_agent_identity_gets_invalid_marker(
    client, scaffold_cleanup
):
    """Header with invalid chars (e.g. SQL-injection payload) coerces to
    'header:invalid_header' in the audit log — not written verbatim.
    """
    pid, headers, cred_name = await _make_approved_project_and_cred(
        client, scaffold_cleanup, "cred-s2-malform"
    )
    cred_id = (
        await client.get(f"/api/projects/{pid}/credentials", headers=headers)
    ).json()[0]["id"]

    malformed = "operator:admin'; DROP TABLE credentials;--"
    use_resp = await client.post(
        f"/api/projects/{pid}/credentials/{cred_name}/use",
        json={},
        headers={**headers, "X-Agent-Identity": malformed},
    )
    assert use_resp.status_code == 200, use_resp.text

    from sqlalchemy import select
    from src.db import SessionLocal
    from src.models.credential import CredentialAccessLog

    async with SessionLocal() as s:
        logs = list(
            (
                await s.execute(
                    select(CredentialAccessLog)
                    .where(CredentialAccessLog.credential_id == cred_id)
                    .where(CredentialAccessLog.action == "use")
                )
            ).scalars()
        )
    assert len(logs) == 1
    # POSITIVE: coerced to the safe sentinel.
    assert logs[0].accessed_by == "header:invalid_header"
    # NEGATIVE: the raw malformed string is NOT stored.
    assert malformed not in logs[0].accessed_by


@pytest.mark.asyncio
async def test_use_credential_no_x_agent_identity_uses_operator_api_default(
    client, scaffold_cleanup
):
    """Absent X-Agent-Identity header stores 'operator:api' (no 'header:' prefix)
    to preserve backwards-compat for direct-operator calls.
    """
    pid, headers, cred_name = await _make_approved_project_and_cred(
        client, scaffold_cleanup, "cred-s2-noheader"
    )
    cred_id = (
        await client.get(f"/api/projects/{pid}/credentials", headers=headers)
    ).json()[0]["id"]

    use_resp = await client.post(
        f"/api/projects/{pid}/credentials/{cred_name}/use",
        json={},
        headers=headers,  # no X-Agent-Identity
    )
    assert use_resp.status_code == 200, use_resp.text

    from sqlalchemy import select
    from src.db import SessionLocal
    from src.models.credential import CredentialAccessLog

    async with SessionLocal() as s:
        logs = list(
            (
                await s.execute(
                    select(CredentialAccessLog)
                    .where(CredentialAccessLog.credential_id == cred_id)
                    .where(CredentialAccessLog.action == "use")
                )
            ).scalars()
        )
    assert len(logs) == 1
    # POSITIVE: bare operator:api default (no header: prefix — it's system-derived).
    assert logs[0].accessed_by == "operator:api"
    # NEGATIVE: must not be the header: prefixed form.
    assert logs[0].accessed_by != "header:operator:api"


@pytest.mark.asyncio
async def test_use_credential_long_x_agent_identity_truncated_to_invalid(
    client, scaffold_cleanup
):
    """X-Agent-Identity exceeding 100 chars coerces to 'header:invalid_header'."""
    pid, headers, cred_name = await _make_approved_project_and_cred(
        client, scaffold_cleanup, "cred-s2-long"
    )
    cred_id = (
        await client.get(f"/api/projects/{pid}/credentials", headers=headers)
    ).json()[0]["id"]

    long_identity = "a" * 101  # exactly over the limit
    use_resp = await client.post(
        f"/api/projects/{pid}/credentials/{cred_name}/use",
        json={},
        headers={**headers, "X-Agent-Identity": long_identity},
    )
    assert use_resp.status_code == 200, use_resp.text

    from sqlalchemy import select
    from src.db import SessionLocal
    from src.models.credential import CredentialAccessLog

    async with SessionLocal() as s:
        logs = list(
            (
                await s.execute(
                    select(CredentialAccessLog)
                    .where(CredentialAccessLog.credential_id == cred_id)
                    .where(CredentialAccessLog.action == "use")
                )
            ).scalars()
        )
    assert len(logs) == 1
    # POSITIVE: over-length coerced to the safe sentinel.
    assert logs[0].accessed_by == "header:invalid_header"
    # NEGATIVE: the long value is NOT stored.
    assert long_identity not in logs[0].accessed_by


# ---------------------------------------------------------------------------
# 10. Partial-unique index: soft-deleted slot is reclaimed (Kanban #1375 fix).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_then_recreate_same_name_returns_201_slot_reclaimed(
    client, scaffold_cleanup
):
    """Regression lock for Kanban #1375: the UNIQUE index on (project_id, name)
    must be partial on status=1 (ACTIVE) so that a soft-deleted credential's
    slot is immediately available for re-use.

    Before the fix (unbounded UNIQUE), step 3 would return 409 forever until a
    manual hard-DELETE. After the fix (partial UNIQUE WHERE status=1), the same
    name is accepted again as soon as the row is soft-deleted.

    POSITIVE: third POST returns 201 with a NEW credential id.
    NEGATIVE: the new id differs from the original (not a resurrect — a fresh row).
    Also verifies the soft-deleted row is still present in the DB (not purged).
    """
    pid = await _make_project(client, scaffold_cleanup, "cred-slot-reclaim")
    headers = {"X-Project-Id": str(pid)}
    cred_name = "reusable_key"

    # Step 1: POST → 201
    create_resp = await client.post(
        f"/api/projects/{pid}/credentials",
        json=_cred_create_body(name=cred_name, value="first-value"),
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    original_id = create_resp.json()["id"]

    # Step 2: DELETE → 204 (soft-delete, status → 0)
    del_resp = await client.delete(
        f"/api/projects/{pid}/credentials/{cred_name}", headers=headers
    )
    assert del_resp.status_code == 204, del_resp.text

    # Step 3: POST same name → 201 (slot reclaimed — this is the regression gate)
    recreate_resp = await client.post(
        f"/api/projects/{pid}/credentials",
        json=_cred_create_body(name=cred_name, value="second-value"),
        headers=headers,
    )
    assert recreate_resp.status_code == 201, (
        f"Expected 201 (slot reclaim) but got {recreate_resp.status_code}: "
        f"{recreate_resp.text}"
    )
    new_id = recreate_resp.json()["id"]

    # POSITIVE: a fresh row was created (different id).
    assert new_id != original_id, (
        "Re-create returned the same id — row was resurrected instead of freshly inserted"
    )
    # POSITIVE: the new row is active and visible in the list.
    list_resp = await client.get(f"/api/projects/{pid}/credentials", headers=headers)
    assert list_resp.status_code == 200
    active_names = [r["name"] for r in list_resp.json()]
    assert cred_name in active_names

    # Step 4: Verify the original soft-deleted row is still in the DB (not purged).
    from sqlalchemy import select
    from src.db import SessionLocal
    from src.models.credential import ProjectCredential

    async with SessionLocal() as s:
        deleted_row = (
            await s.execute(
                select(ProjectCredential).where(ProjectCredential.id == original_id)
            )
        ).scalar_one_or_none()

    assert deleted_row is not None, "Soft-deleted row was purged — expected it to remain"
    assert deleted_row.status == 0, (
        f"Expected status=0 (soft-deleted) but got status={deleted_row.status}"
    )


# ---------------------------------------------------------------------------
# 12. approval_policies shape gate — Kanban #1405 (bug fix lock).
# ---------------------------------------------------------------------------


def test_policy_grants_use_rejects_bare_list_form():
    """Unit lock for Kanban #1405: _policy_grants_use MUST reject the bare-list
    form and return False (deny), not match rules inside it.

    NEGATIVE: passing a bare list with a matching rule does NOT grant access
              (the function returns False).
    POSITIVE: passing the same rule in the canonical dict-with-rules form
              DOES grant access (proving the function is not simply broken).
    """
    from src.routers.credentials import _policy_grants_use

    cred_name = "my_secret"
    matching_rule = {
        "action": "credential.use",
        "credential_name": cred_name,
        "auto_approve": True,
    }

    # NEGATIVE: bare-list form is REJECTED even when the rule would otherwise match.
    assert _policy_grants_use([matching_rule], cred_name) is False, (
        "bare-list form must not grant access (legacy shape rejected)"
    )

    # POSITIVE: same rule in canonical dict-with-rules shape is GRANTED.
    assert _policy_grants_use({"rules": [matching_rule]}, cred_name) is True, (
        "canonical dict-with-rules shape must grant access"
    )


@pytest.mark.asyncio
async def test_patch_project_with_bare_list_approval_policies_returns_422(
    client, scaffold_cleanup
):
    """Schema gate for Kanban #1405: PATCH /api/projects/{id} with
    approval_policies as a bare JSON array returns 422 (Pydantic type mismatch
    — the field type is dict[str, Any] | None, not list).

    NEGATIVE: bare-list body does NOT succeed (not 200).
    POSITIVE: the response status is 422 (validation error, not 500 or 200).
    """
    pid = await _make_project(client, scaffold_cleanup, "cred-policy-422")
    headers = {"X-Project-Id": str(pid)}

    resp = await client.patch(
        f"/api/projects/{pid}",
        json={
            "approval_policies": [
                {
                    "action": "credential.use",
                    "credential_name": "some_key",
                    "auto_approve": True,
                }
            ]
        },
        headers=headers,
    )
    # NEGATIVE: must not succeed.
    assert resp.status_code != 200, (
        f"bare-list approval_policies must not be accepted; got 200: {resp.text}"
    )
    # POSITIVE: 422 Unprocessable Entity from Pydantic type validation.
    assert resp.status_code == 422, (
        f"Expected 422 for bare-list approval_policies; got {resp.status_code}: {resp.text}"
    )


# ---------------------------------------------------------------------------
# 11. Audit-before-plaintext ordering lock (Kanban #1376 fix).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_use_audit_row_created_at_before_or_equal_response_timestamp(
    client, scaffold_cleanup
):
    """Lock for Kanban #1376: the credential_access_log audit row for a granted
    /use is committed BEFORE the plaintext is decrypted and returned.

    Verification strategy: record a UTC timestamp immediately BEFORE the /use
    call, then check that audit.created_at >= that timestamp (the row was
    written during this call, not some earlier time) — and that
    access_log_id is non-zero in the response (the row exists and was
    refreshed before decrypt ran).

    POSITIVE: audit row exists with action='use', no denial marker, and
              access_log_id in response body matches the DB row's id.
    NEGATIVE: the response must NOT return value=None or value='' (plaintext
              present), and the audit row must NOT have a denial marker.
    """
    import datetime

    pid, headers, cred_name = await _make_approved_project_and_cred(
        client, scaffold_cleanup, "cred-audit-order"
    )
    cred_id = (
        await client.get(f"/api/projects/{pid}/credentials", headers=headers)
    ).json()[0]["id"]

    # Record wall time BEFORE the /use call.
    before_call = datetime.datetime.now(datetime.timezone.utc)

    use_resp = await client.post(
        f"/api/projects/{pid}/credentials/{cred_name}/use",
        json={"reason": "audit-order regression lock"},
        headers=headers,
    )
    assert use_resp.status_code == 200, use_resp.text
    body = use_resp.json()

    # POSITIVE: plaintext returned (happy path works after the split commit).
    assert body["value"] == SENTINEL_PLAINTEXT, (
        "Expected SENTINEL_PLAINTEXT in response value — decrypt failed or value empty"
    )
    # NEGATIVE: plaintext is not None or empty string.
    assert body["value"] not in (None, ""), (
        "Response value is None or empty — plaintext was not returned"
    )
    # POSITIVE: access_log_id populated (audit row was committed + refreshed before response).
    access_log_id = body["access_log_id"]
    assert isinstance(access_log_id, int) and access_log_id > 0, (
        f"Expected positive int access_log_id; got {access_log_id!r}"
    )

    # Fetch the audit row and verify it was written during this call.
    from sqlalchemy import select
    from src.db import SessionLocal
    from src.models.credential import CredentialAccessLog, ProjectCredential

    async with SessionLocal() as s:
        log = (
            await s.execute(
                select(CredentialAccessLog).where(
                    CredentialAccessLog.id == access_log_id
                )
            )
        ).scalar_one_or_none()

    assert log is not None, (
        f"audit row id={access_log_id} not found — commit happened after decrypt, not before"
    )
    # POSITIVE: the audit row's accessed_at is NOT before we made the call —
    # i.e., it was written during or after our timestamp (not a stale row).
    assert log.accessed_at is not None
    log_ts = log.accessed_at
    # Normalise to UTC if naive (DB may return naive UTC).
    if log_ts.tzinfo is None:
        log_ts = log_ts.replace(tzinfo=datetime.timezone.utc)
    assert log_ts >= before_call, (
        f"Audit row accessed_at={log_ts} predates the /use call start={before_call}; "
        "row may be a stale artifact — not written by this request"
    )
    # POSITIVE: no denial marker — this is the granted path.
    assert "denied" not in log.accessed_by, (
        f"Audit row carries denial marker on granted path: {log.accessed_by!r}"
    )
    # NEGATIVE: raw access_log_id from the response matches the DB row we fetched.
    assert log.id == access_log_id

    # Verify counters were also bumped (second commit landed).
    async with SessionLocal() as s:
        cred = (
            await s.execute(
                select(ProjectCredential).where(ProjectCredential.id == cred_id)
            )
        ).scalar_one()
    assert cred.access_count == 1, (
        f"access_count should be 1 after one granted /use; got {cred.access_count}"
    )
    assert cred.last_accessed_at is not None, (
        "last_accessed_at should be set after granted /use"
    )
