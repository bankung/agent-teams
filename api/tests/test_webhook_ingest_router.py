"""HTTP-level contract tests for POST /api/ingest/webhook/{project_id}/{tag}
(Kanban #1328 M4b — generic webhook-to-task ingest).

Coverage:
  - 401 when the per-source credential is absent.
  - 401 with denial audit row when the header mismatches.
  - Happy path for the ``calendly`` template: title/description substituted.
  - Happy path for the ``github_issue`` template: ``task_type='bug'``.
  - Default-fallback path for an unregistered tag: full payload dumped.
  - 422 when a template field is missing from the payload (specific path).
  - 404 when project_id does not exist.
  - 429 after 60 hits in <60s for the SAME (project_id, tag) pair.
  - 429 boundary respects per-(project, tag) — a second tag in the same
    project starts fresh.
  - Unit test on ``substitute()`` for nested-dict dot-path extraction.

The autouse fixture pattern (Fernet key rotation + per-test rate-limit
reset) mirrors ``test_email_ingest.py`` — both routers share the M3 vault.
"""

from __future__ import annotations

import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from src.db import SessionLocal
from src.models.credential import CredentialAccessLog
from src.services import credentials_crypto


# Sentinel — secret-not-in-response audit grep keys on this exact string.
# It MUST NEVER appear in any response body / log line printed by the test
# suite.  `grep -r "WH-SENTINEL-SECRET-77889"` across the codebase should
# return only this file's definition + assertions.
WH_SENTINEL_SECRET_77889 = "WH-SENTINEL-SECRET-77889"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _credentials_master_key(monkeypatch):
    """Mint a fresh Fernet master key per test + clear the crypto cache.

    Mirrors the test_email_ingest pattern so /credentials POST + the
    webhook_ingest decrypt path share the same Fernet instance.
    """
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CREDENTIALS_MASTER_KEY", key)
    credentials_crypto._fernet = None
    yield
    credentials_crypto._fernet = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_webhook_secret(
    client, *, tag: str, value: str = WH_SENTINEL_SECRET_77889, project_id: int = 1
) -> int:
    """Idempotently install a ``webhook_<tag>`` credential in the given project.

    The session-scoped test DB persists across tests, so a previously-seeded
    row survives between test functions. POST first; on 409 (already exists)
    PATCH the value so the per-test Fernet key rotation re-encrypts the
    secret under the current master key.
    """
    name = f"webhook_{tag}"
    resp = await client.post(
        f"/api/projects/{project_id}/credentials",
        json={"name": name, "value": value, "kind": "webhook_secret"},
        headers={"X-Project-Id": str(project_id)},
    )
    if resp.status_code == 201:
        return resp.json()["id"]
    if resp.status_code == 409:
        patch_resp = await client.patch(
            f"/api/projects/{project_id}/credentials/{name}",
            json={"value": value},
            headers={"X-Project-Id": str(project_id)},
        )
        assert patch_resp.status_code == 200, patch_resp.text
        return patch_resp.json()["id"]
    raise AssertionError(f"unexpected seed status {resp.status_code}: {resp.text}")


async def _count_denial_audits_for_credential(cred_id: int) -> int:
    async with SessionLocal() as s:
        rows = (
            await s.execute(
                select(CredentialAccessLog).where(
                    CredentialAccessLog.credential_id == cred_id
                )
            )
        ).scalars().all()
    return sum(1 for r in rows if "denied=" in r.accessed_by)


# ===========================================================================
# Auth + secret-not-configured
# ===========================================================================


@pytest.mark.asyncio
async def test_webhook_missing_credential_returns_401(client):
    """No credential seeded → 401 with the configure-the-secret hint.

    POSITIVE: response status is 401.
    NEGATIVE: detail names the credential endpoint + name + kind so the
    operator's recovery step is in the response itself (no oracle leak of
    secret content).
    """
    tag = f"unseeded-{uuid.uuid4().hex[:8]}"
    resp = await client.post(
        f"/api/ingest/webhook/1/{tag}",
        json={"any": "payload"},
        headers={"X-Webhook-Secret": "anything"},
    )
    assert resp.status_code == 401, resp.text
    detail = resp.json()["detail"]
    assert f"webhook_{tag}" in detail
    assert "/api/projects/" in detail
    assert "webhook_secret" in detail
    assert WH_SENTINEL_SECRET_77889 not in detail


@pytest.mark.asyncio
async def test_webhook_bad_secret_returns_401_audit_logged(client):
    """Seed credential, send wrong header → 401 ``invalid signature`` + audit.

    POSITIVE: denial audit row appears for the credential.
    NEGATIVE: response detail is exactly the static ``"invalid signature"``.
    """
    tag = f"bs-{uuid.uuid4().hex[:8]}"
    cred_id = await _seed_webhook_secret(client, tag=tag)

    resp = await client.post(
        f"/api/ingest/webhook/1/{tag}",
        json={"any": "payload"},
        headers={"X-Webhook-Secret": "wrong-secret"},
    )
    assert resp.status_code == 401, resp.text
    assert resp.json() == {"detail": "invalid signature"}
    denials = await _count_denial_audits_for_credential(cred_id)
    assert denials >= 1, "expected denial audit row for bad-secret attempt"


# ===========================================================================
# Happy path + template substitution
# ===========================================================================


@pytest.mark.asyncio
async def test_webhook_good_secret_with_calendly_template_creates_task_with_substituted_title(
    client,
):
    """Calendly template: title + description carry substituted fields."""
    await _seed_webhook_secret(client, tag="calendly")

    # Real Calendly v2 webhook shape: invitee data under payload.invitee,
    # event type label under payload.event_type.name, timing under payload.event.
    payload = {
        "event": "invitee.created",
        "payload": {
            "event_type": {
                "uuid": "ET-abc123",
                "name": "Intro chat (30m)",
            },
            "invitee": {
                "name": "Alex Customer",
                "email": "alex@example.com",
                "uuid": "INV-xyz789",
            },
            "event": {
                "start_time": "2026-06-01T10:00:00Z",
                "end_time": "2026-06-01T10:30:00Z",
                "uuid": "EVT-def456",
            },
        },
    }
    resp = await client.post(
        "/api/ingest/webhook/1/calendly",
        json=payload,
        headers={"X-Webhook-Secret": WH_SENTINEL_SECRET_77889},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["received"] is True
    assert body["project_id"] == 1
    assert body["template_used"] == "calendly"
    assert body["tag"] == "calendly"
    task_id = body["task_id"]

    # Round-trip via /api/tasks/{id} — the substituted title should land.
    get_resp = await client.get(
        f"/api/tasks/{task_id}", headers={"X-Project-Id": "1"}
    )
    assert get_resp.status_code == 200, get_resp.text
    task = get_resp.json()
    assert task["title"] == "Booking: Alex Customer — Intro chat (30m)"
    assert "alex@example.com" in task["description"]
    assert "Intro chat (30m)" in task["description"]
    assert task["task_kind"] == "human"
    assert task["task_type"] == "feature"


@pytest.mark.asyncio
async def test_webhook_good_secret_with_github_template_creates_bug_typed_task(client):
    """GitHub issue template: ``task_type='bug'`` on the created task."""
    await _seed_webhook_secret(client, tag="github_issue")

    payload = {
        "issue": {
            "title": "Add dark mode",
            "number": 42,
            "user": {"login": "octocat"},
            "html_url": "https://github.com/foo/bar/issues/42",
            "body": "Please add dark mode support.",
        }
    }
    resp = await client.post(
        "/api/ingest/webhook/1/github_issue",
        json=payload,
        headers={"X-Webhook-Secret": WH_SENTINEL_SECRET_77889},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["template_used"] == "github_issue"
    task_id = body["task_id"]

    get_resp = await client.get(
        f"/api/tasks/{task_id}", headers={"X-Project-Id": "1"}
    )
    task = get_resp.json()
    assert task["title"] == "GitHub issue: Add dark mode (#42)"
    assert task["task_type"] == "bug"
    assert "octocat" in task["description"]
    assert "https://github.com/foo/bar/issues/42" in task["description"]


@pytest.mark.asyncio
async def test_webhook_good_secret_unknown_tag_uses_default_fallback_dumps_payload(
    client,
):
    """Unregistered tag (no template entry) → DEFAULT_FALLBACK_TEMPLATE.

    Description carries the pretty-printed JSON payload so the operator can
    triage even before a template is registered.
    """
    tag = "typeform"  # not in TEMPLATE_REGISTRY
    await _seed_webhook_secret(client, tag=tag)

    payload = {
        "form_response": {
            "form_id": "abcde",
            "answers": [{"field": "email", "value": "test@example.com"}],
        }
    }
    resp = await client.post(
        f"/api/ingest/webhook/1/{tag}",
        json=payload,
        headers={"X-Webhook-Secret": WH_SENTINEL_SECRET_77889},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["template_used"] == "default-fallback"
    assert body["tag"] == tag
    task_id = body["task_id"]

    get_resp = await client.get(
        f"/api/tasks/{task_id}", headers={"X-Project-Id": "1"}
    )
    task = get_resp.json()
    assert task["title"] == f"Webhook: {tag}"
    # Payload should be dumped into the description as JSON.
    assert "form_response" in task["description"]
    assert "abcde" in task["description"]
    assert "test@example.com" in task["description"]


# ===========================================================================
# Errors: missing field, unknown project
# ===========================================================================


@pytest.mark.asyncio
async def test_webhook_template_missing_field_returns_422_with_field_path(client):
    """Template references {{payload.invitee.name}} but invitee block absent → 422 with path."""
    await _seed_webhook_secret(client, tag="calendly")

    # Omit the ``invitee`` sub-object entirely — the calendly title template
    # requires ``payload.invitee.name`` (Calendly v2 nested shape).
    payload = {
        "event": "invitee.created",
        "payload": {
            "event_type": {"uuid": "ET-abc", "name": "chat"},
            "event": {"start_time": "2026-06-01T10:00:00Z", "end_time": "2026-06-01T10:30:00Z"},
            # "invitee" key intentionally absent
        },
    }
    resp = await client.post(
        "/api/ingest/webhook/1/calendly",
        json=payload,
        headers={"X-Webhook-Secret": WH_SENTINEL_SECRET_77889},
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "missing required template field" in detail
    assert "payload.invitee.name" in detail


@pytest.mark.asyncio
async def test_webhook_unknown_project_id_returns_404(client):
    """A project_id that doesn't exist → 404, with no credential/audit work done."""
    # Seed credential in project 1 (irrelevant — the 404 should fire first).
    await _seed_webhook_secret(client, tag="calendly")

    resp = await client.post(
        "/api/ingest/webhook/999999/calendly",
        json={
            "event": "invitee.created",
            "payload": {
                "event_type": {"uuid": "ET-x", "name": "z"},
                "invitee": {"name": "x", "email": "y", "uuid": "INV-x"},
                "event": {"start_time": "t", "end_time": "t", "uuid": "EVT-x"},
            },
        },
        headers={"X-Webhook-Secret": WH_SENTINEL_SECRET_77889},
    )
    assert resp.status_code == 404, resp.text
    assert "999999" in resp.json()["detail"]


# ===========================================================================
# Rate limiting
# ===========================================================================


@pytest.mark.asyncio
async def test_webhook_rate_limit_60_per_minute_returns_429(client):
    """Fire 60 requests, all OK. The 61st returns 429.

    The per-test fixture resets the in-memory bucket so this test starts
    from zero.
    """
    tag = "calendly"
    await _seed_webhook_secret(client, tag=tag)

    payload = {
        "event": "invitee.created",
        "payload": {
            "event_type": {"uuid": "ET-rate", "name": "rate-test"},
            "invitee": {"name": "Burst Customer", "email": "burst@example.com", "uuid": "INV-rate"},
            "event": {"start_time": "2026-06-01T10:00:00Z", "end_time": "2026-06-01T10:30:00Z", "uuid": "EVT-rate"},
        },
    }
    headers = {"X-Webhook-Secret": WH_SENTINEL_SECRET_77889}

    # Fire 60 — every one should be 200.
    for i in range(60):
        r = await client.post(f"/api/ingest/webhook/1/{tag}", json=payload, headers=headers)
        assert r.status_code == 200, f"hit {i} returned {r.status_code}: {r.text}"

    # 61st must be 429.
    r = await client.post(f"/api/ingest/webhook/1/{tag}", json=payload, headers=headers)
    assert r.status_code == 429, r.text
    assert "rate limit exceeded" in r.json()["detail"]


@pytest.mark.asyncio
async def test_webhook_rate_limit_resets_between_project_tag_pairs(client):
    """(project=1, tag=A) hits limit — (project=1, tag=B) still OK.

    Locks the key-shape: the bucket is per-(project_id, tag) pair, not
    per-project nor per-tag. Confirms tag scope isolation.

    We lower the limit via env override to avoid 60 round-trips here.
    """
    import os
    os.environ["WEBHOOK_RATE_LIMIT_PER_MIN"] = "3"
    try:
        tag_a = "calendly"
        tag_b = "github_issue"
        await _seed_webhook_secret(client, tag=tag_a)
        await _seed_webhook_secret(client, tag=tag_b)

        payload_a = {
            "event": "invitee.created",
            "payload": {
                "event_type": {"uuid": "ET-a", "name": "e"},
                "invitee": {"name": "A", "email": "a@x.com", "uuid": "INV-a"},
                "event": {"start_time": "t", "end_time": "t", "uuid": "EVT-a"},
            },
        }
        payload_b = {
            "issue": {
                "title": "T", "number": 1,
                "user": {"login": "x"}, "html_url": "u", "body": "b",
            }
        }
        headers = {"X-Webhook-Secret": WH_SENTINEL_SECRET_77889}

        # Saturate tag_a.
        for _ in range(3):
            r = await client.post(f"/api/ingest/webhook/1/{tag_a}", json=payload_a, headers=headers)
            assert r.status_code == 200, r.text
        r = await client.post(f"/api/ingest/webhook/1/{tag_a}", json=payload_a, headers=headers)
        assert r.status_code == 429, r.text

        # tag_b in the SAME project must still be admitted.
        r = await client.post(f"/api/ingest/webhook/1/{tag_b}", json=payload_b, headers=headers)
        assert r.status_code == 200, r.text
    finally:
        del os.environ["WEBHOOK_RATE_LIMIT_PER_MIN"]


# ===========================================================================
# Pure unit test on substitute()
# ===========================================================================


def test_substitute_dot_path_extraction_handles_nested_dicts():
    """``{{a.b.c}}`` walks a 3-deep dict; missing path raises MissingTemplateField."""
    from src.services.webhook_templates import MissingTemplateField, substitute

    out = substitute(
        "User: {{a.b.c}} / count: {{count}}",
        {"a": {"b": {"c": "alice"}}, "count": 7},
    )
    assert out == "User: alice / count: 7"

    # Missing path (intermediate key absent) → raise with the offending path.
    with pytest.raises(MissingTemplateField) as exc:
        substitute("hello {{a.missing.c}}", {"a": {"b": {"c": "alice"}}})
    assert exc.value.field_path == "a.missing.c"

    # Non-dict intermediate (a string value where a dict was expected).
    with pytest.raises(MissingTemplateField) as exc:
        substitute("hello {{a.b.c.d}}", {"a": {"b": {"c": "alice"}}})
    assert exc.value.field_path == "a.b.c.d"


# ===========================================================================
# Calendly v2 nested-shape contract (Kanban #1404)
# ===========================================================================


@pytest.mark.asyncio
async def test_calendly_v2_nested_payload_extracts_correct_fields(client):
    """Calendly v2 shape (nested invitee/event_type/event) → correct task fields.

    POSITIVE: title uses payload.invitee.name + payload.event_type.name;
              description uses payload.invitee.email + payload.event.start_time.
    NEGATIVE: the old flat field names (payload.name / payload.email) are NOT
              present as keys in the request body — confirming the template
              works from the nested form, not a flat shim.
    """
    await _seed_webhook_secret(client, tag="calendly")

    nested_payload = {
        "event": "invitee.created",
        "payload": {
            "event_type": {"uuid": "ET-nested1", "name": "Deep Dive (60m)"},
            "invitee": {
                "name": "Nested User",
                "email": "nested@example.com",
                "uuid": "INV-nested1",
            },
            "event": {
                "start_time": "2026-07-01T14:00:00Z",
                "end_time": "2026-07-01T15:00:00Z",
                "uuid": "EVT-nested1",
            },
        },
    }

    # NEGATIVE guard: flat keys must NOT exist at payload root.
    assert "name" not in nested_payload["payload"]
    assert "email" not in nested_payload["payload"]
    assert "start_time" not in nested_payload["payload"]

    resp = await client.post(
        "/api/ingest/webhook/1/calendly",
        json=nested_payload,
        headers={"X-Webhook-Secret": WH_SENTINEL_SECRET_77889},
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["task_id"]

    get_resp = await client.get(f"/api/tasks/{task_id}", headers={"X-Project-Id": "1"})
    assert get_resp.status_code == 200, get_resp.text
    task = get_resp.json()

    # POSITIVE: nested fields land in title + description.
    assert task["title"] == "Booking: Nested User — Deep Dive (60m)"
    assert "nested@example.com" in task["description"]
    assert "2026-07-01T14:00:00Z" in task["description"]


@pytest.mark.asyncio
async def test_calendly_missing_invitee_block_returns_422(client):
    """Calendly payload without invitee sub-object → 422, field path in detail.

    POSITIVE: HTTP 422, detail names the missing nested path.
    NEGATIVE: HTTP 200 must NOT be returned (old flat shape would silently pass).
    """
    await _seed_webhook_secret(client, tag="calendly")

    payload_no_invitee = {
        "event": "invitee.created",
        "payload": {
            "event_type": {"uuid": "ET-ni", "name": "Quick Call"},
            # invitee block absent — simulates a real Calendly delivery that
            # unexpectedly omits the invitee object (e.g. a cancellation variant).
            "event": {
                "start_time": "2026-07-02T09:00:00Z",
                "end_time": "2026-07-02T09:30:00Z",
                "uuid": "EVT-ni",
            },
        },
    }

    resp = await client.post(
        "/api/ingest/webhook/1/calendly",
        json=payload_no_invitee,
        headers={"X-Webhook-Secret": WH_SENTINEL_SECRET_77889},
    )

    # NEGATIVE: must not succeed.
    assert resp.status_code != 200, (
        f"expected 422 for missing invitee, got 200: {resp.text}"
    )
    # POSITIVE: 422 with the failing nested path.
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "missing required template field" in detail
    assert "payload.invitee" in detail


# ===========================================================================
# Encoding-error guard (Kanban #1378)
# ===========================================================================


@pytest.mark.asyncio
async def test_webhook_malformed_vault_secret_returns_401_not_500(
    client, monkeypatch
):
    """A vault secret containing lone-surrogate chars triggers UnicodeEncodeError
    on .encode('utf-8').  The router must return 401 + a denial audit row — NOT
    a 500 that leaks internal exception text.

    POSITIVE: response status is 401 with the static "invalid signature" body.
    NEGATIVE:
      - response status is NOT 500 (no internal exception leak).
      - response body does NOT contain any Python exception text (UnicodeEncodeError).
      - A denial audit row IS written for the credential so the corrupt entry is
        detectable in the audit trail.

    Strategy: monkeypatch ``credentials_crypto.decrypt`` (at the module level
    the router imports from) to return a string containing a lone surrogate
    (\\ud800).  Python's str can hold surrogates; .encode('utf-8') raises
    UnicodeEncodeError on them in the default 'strict' mode.  This is the
    exact failure mode a mis-stored binary vault entry would trigger.
    """
    tag = f"enc-err-{uuid.uuid4().hex[:8]}"
    cred_id = await _seed_webhook_secret(client, tag=tag)

    # Patch decrypt at the module the router uses so the credential is "found"
    # but returns a string that cannot be UTF-8 encoded.
    from src.routers import ingest as ingest_module

    monkeypatch.setattr(
        ingest_module.credentials_crypto,
        "decrypt",
        lambda _ciphertext: "\ud800\ud801",  # lone surrogates — not encodable
    )

    resp = await client.post(
        f"/api/ingest/webhook/1/{tag}",
        json={"any": "payload"},
        headers={"X-Webhook-Secret": "some-header-value"},
    )

    # NEGATIVE: must not be 500.
    assert resp.status_code != 500, (
        f"router leaked internal exception: {resp.text[:200]}"
    )
    # POSITIVE: 401 with the static detail (no oracle for caller).
    assert resp.status_code == 401, resp.text
    assert resp.json() == {"detail": "invalid signature"}
    # NEGATIVE: no exception class name in the response body.
    assert "UnicodeEncodeError" not in resp.text
    assert "UnicodeDecodeError" not in resp.text

    # Denial audit row MUST be written (with reason "secret_encoding_error").
    async with SessionLocal() as s:
        rows = (
            await s.execute(
                select(CredentialAccessLog).where(
                    CredentialAccessLog.credential_id == cred_id
                )
            )
        ).scalars().all()
    encoding_error_rows = [
        r for r in rows if "secret_encoding_error" in r.accessed_by
    ]
    assert len(encoding_error_rows) >= 1, (
        "expected a denial audit row with reason 'secret_encoding_error'"
    )
