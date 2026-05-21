"""HTTP-level contract tests for POST /api/ingest/email (Kanban #1327 M4a).

Coverage:
  - 401 when the shared-secret credential is absent (no audit row to write).
  - 401 when the header mismatches the stored secret + denial audit logged.
  - Happy path: subject -> title (truncated to 200), task created in the
    default project, project_id returned.
  - Project tag routing: ``inbox+<projectname>@`` lands in that project.
  - Unknown project tag falls back to the default project.
  - 404 when both the tag AND the default-project lookup miss.
  - Body extraction: body_text wins over body_html; html-only payload gets
    tags stripped.
  - Attachments: write to disk + flag with 'pending #1309' in description.
  - Oversized attachment skipped with warning line in description.
  - Long subject truncated to 200 chars on title.
  - size_bytes payload field is NOT trusted (recomputed from decoded bytes).

The autouse fixture mints a fresh Fernet master key per test and clears the
crypto cache so the /credentials POST + email_ingest decrypt path share the
same key. The sentinel secret value is referenced by the secret-not-in-
response audit grep at the end of the spawn brief.
"""

from __future__ import annotations

import base64
import os
import shutil
import uuid
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from src.db import SessionLocal
from src.models.credential import CredentialAccessLog
from src.services import credentials_crypto


# Sentinel — the secret-not-in-response audit grep keys on this exact string.
# It must NEVER appear in any response body / log line printed by the test
# suite. Living in this file means a `grep -r "EMAIL-SENTINEL-SECRET-12345"`
# across the codebase shows only the test definitions + assertions.
EMAIL_SENTINEL_SECRET_12345 = "EMAIL-SENTINEL-SECRET-12345"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _email_ingest_credential_clean():
    """Ensure the ``email_ingest_shared_secret`` credential in project 1 is
    absent at the start of every test in this file.

    Sibling tests within this file (and across files — credentials_router
    tests, M4a smokes) create this credential and the session-scoped test
    DB retains it. Without this fixture, test_post_email_no_secret_...
    fails when run after any test that seeded the credential. See Q2
    finding in the 2026-05-21 review.

    Implementation note: we use SessionLocal directly (same pattern as
    _count_denial_audits_for_credential) rather than the API's soft-delete
    endpoint because the project_credentials unique index spans soft-deleted
    rows — a soft-delete via DELETE /api/.../credentials/{name} would block
    _seed_email_secret from re-creating the row in the same test session.
    Hard-deleting the row here keeps the DB clean across tests without
    poisoning the name slot.
    """
    from sqlalchemy import delete as sa_delete
    from src.models.credential import ProjectCredential

    async def _hard_delete_credential() -> None:
        async with SessionLocal() as s:
            await s.execute(
                sa_delete(ProjectCredential).where(
                    ProjectCredential.project_id == 1,
                    ProjectCredential.name == "email_ingest_shared_secret",
                )
            )
            await s.commit()

    await _hard_delete_credential()
    yield
    # Symmetric cleanup after the test so the next test file sees a clean
    # precondition (no dangling credential from this file's seeding tests).
    await _hard_delete_credential()


@pytest.fixture(autouse=True)
def _credentials_master_key(monkeypatch):
    """Mint a fresh Fernet master key per test + clear the crypto cache.

    Mirrors the test_webhooks_router pattern so /credentials POST + the
    email_ingest decrypt path share the same Fernet instance.
    """
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CREDENTIALS_MASTER_KEY", key)
    credentials_crypto._fernet = None
    yield
    credentials_crypto._fernet = None


@pytest.fixture(autouse=True)
def _scope_secret_project_to_active_project(monkeypatch):
    """Pin the secret-credential lookup to the seeded ``agent-teams`` project.

    The test DB is seeded with the canonical agent-teams project (id=1) by
    ``scripts/seed.py``. Default fallback in the router is the same id, but
    setting the env explicitly makes the test deterministic against any future
    seed renumbering.

    Also pin the default routing project so the routing tests don't rely on
    the env var matching the seeded project name by coincidence.
    """
    monkeypatch.setenv("EMAIL_INGEST_SECRET_PROJECT_ID", "1")
    monkeypatch.setenv("EMAIL_INGEST_DEFAULT_PROJECT", "agent-teams")
    yield


@pytest.fixture
def _attachment_disk_cleanup():
    """Best-effort cleanup of any attachment dirs the tests created.

    Tests that write attachments register the absolute path here; teardown
    removes the file. Idempotent on missing files.
    """
    paths: list[Path] = []

    def register(p: Path) -> Path:
        paths.append(p)
        return p

    yield register

    for p in paths:
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass
    # Also sweep the per-task attachment dirs (best-effort — leave the parent
    # _runtime/email_attachments intact since other tests may share it).


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"email_ingest fixture {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


async def _make_project(client, scaffold_cleanup, slug: str) -> tuple[int, str]:
    name = scaffold_cleanup(_unique_name(slug))
    resp = await client.post("/api/projects", json=_project_create_payload(name))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"], name


async def _seed_email_secret(client, value: str = EMAIL_SENTINEL_SECRET_12345) -> int:
    """Idempotently install the ``email_ingest_shared_secret`` credential in
    project 1.

    The session-scoped test DB persists across tests, so the credential row
    survives between test functions. POST first; on 409 (already exists) PATCH
    the value so the per-test Fernet key rotation (the autouse
    ``_credentials_master_key`` fixture mints a new key per test) re-encrypts
    the secret under the current master key — otherwise the verify path would
    fail with InvalidToken on the second test onward.
    """
    resp = await client.post(
        "/api/projects/1/credentials",
        json={
            "name": "email_ingest_shared_secret",
            "value": value,
            "kind": "webhook_secret",
        },
        headers={"X-Project-Id": "1"},
    )
    if resp.status_code == 201:
        return resp.json()["id"]
    if resp.status_code == 409:
        # Already exists from a prior test in this session. PATCH to re-encrypt
        # under the current per-test Fernet key.
        patch_resp = await client.patch(
            "/api/projects/1/credentials/email_ingest_shared_secret",
            json={"value": value},
            headers={"X-Project-Id": "1"},
        )
        assert patch_resp.status_code == 200, patch_resp.text
        return patch_resp.json()["id"]
    raise AssertionError(f"unexpected seed status {resp.status_code}: {resp.text}")


def _build_email_payload(
    *,
    from_address: str = "customer@example.com",
    to: str = "inbox@example.com",
    subject: str = "Test subject",
    body_text: str | None = "Hello, this is the body.",
    body_html: str | None = None,
    attachments: list[dict] | None = None,
    timestamp: int | None = 1747789200,  # 2026-05-21 some UTC time
    message_id: str | None = "<abc@example.com>",
) -> dict:
    """Build a Mailgun-shape body. Uses the wire field ``from`` (Python kw)."""
    body: dict = {
        "from": from_address,
        "to": to,
        "subject": subject,
    }
    if body_text is not None:
        body["body_text"] = body_text
    if body_html is not None:
        body["body_html"] = body_html
    if attachments is not None:
        body["attachments"] = attachments
    if timestamp is not None:
        body["timestamp"] = timestamp
    if message_id is not None:
        body["message_id"] = message_id
    return body


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
async def test_post_email_no_secret_returns_401_if_credential_missing(client):
    """No credential seeded → 401 with the configure-the-secret hint.

    POSITIVE: the response status is 401.
    NEGATIVE (locked invariants): the detail names the credential endpoint +
    the credential name + the kind — no oracle leak of secret content.
    """
    resp = await client.post(
        "/api/ingest/email",
        json=_build_email_payload(),
        headers={"X-Email-Ingest-Secret": "anything"},
    )
    assert resp.status_code == 401, resp.text
    detail = resp.json()["detail"]
    assert "email_ingest_shared_secret" in detail
    assert "/api/projects/" in detail
    assert "webhook_secret" in detail
    # Sentinel must NEVER appear in the response.
    assert EMAIL_SENTINEL_SECRET_12345 not in detail


@pytest.mark.asyncio
async def test_post_email_bad_secret_returns_401_audit_logged(client):
    """Seed credential, send wrong header → 401 ``invalid signature`` + audit.

    POSITIVE: denial audit row appears for the credential.
    NEGATIVE: response detail is exactly the static ``"invalid signature"``
    (no oracle for attackers — same shape as the webhooks router).
    """
    cred_id = await _seed_email_secret(client)

    resp = await client.post(
        "/api/ingest/email",
        json=_build_email_payload(),
        headers={"X-Email-Ingest-Secret": "wrong-secret"},
    )
    assert resp.status_code == 401, resp.text
    assert resp.json() == {"detail": "invalid signature"}
    # Audit row written.
    denials = await _count_denial_audits_for_credential(cred_id)
    assert denials >= 1, "expected denial audit row for bad-secret attempt"


# ===========================================================================
# Happy path + routing
# ===========================================================================


@pytest.mark.asyncio
async def test_post_email_good_secret_creates_task_with_subject_as_title(client):
    """Happy path: signed payload -> 200 + new task visible via GET /api/tasks."""
    await _seed_email_secret(client)

    subject = f"Hello there {uuid.uuid4().hex[:8]}"
    payload = _build_email_payload(subject=subject)
    resp = await client.post(
        "/api/ingest/email",
        json=payload,
        headers={"X-Email-Ingest-Secret": EMAIL_SENTINEL_SECRET_12345},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["received"] is True
    assert body["attachment_count"] == 0
    assert body["project_id"] == 1  # default = agent-teams
    task_id = body["task_id"]

    # Read back via the existing /api/tasks/{id} surface (no title_contains
    # filter in list endpoint; fetch by id is the unambiguous probe).
    get_resp = await client.get(
        f"/api/tasks/{task_id}", headers={"X-Project-Id": "1"}
    )
    assert get_resp.status_code == 200, get_resp.text
    task = get_resp.json()
    assert task["title"] == subject
    # task_kind default for email ingest = 'human' (operator triage required).
    assert task["task_kind"] == "human"
    assert task["task_type"] == "feature"
    # Description carries the From: header.
    assert "From: customer@example.com" in task["description"]


@pytest.mark.asyncio
async def test_post_email_with_project_tag_routes_to_matching_project(
    client, scaffold_cleanup
):
    """``inbox+<projectname>@`` resolves to that project, not the default."""
    pid, pname = await _make_project(client, scaffold_cleanup, "ei-tag")
    await _seed_email_secret(client)

    payload = _build_email_payload(to=f"inbox+{pname}@example.com")
    resp = await client.post(
        "/api/ingest/email",
        json=payload,
        headers={"X-Email-Ingest-Secret": EMAIL_SENTINEL_SECRET_12345},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["project_id"] == pid


@pytest.mark.asyncio
async def test_post_email_unknown_project_tag_falls_back_to_default(client):
    """A tag that doesn't resolve falls through to the default project."""
    await _seed_email_secret(client)

    payload = _build_email_payload(
        to=f"inbox+nonexistent-{uuid.uuid4().hex[:8]}@example.com"
    )
    resp = await client.post(
        "/api/ingest/email",
        json=payload,
        headers={"X-Email-Ingest-Secret": EMAIL_SENTINEL_SECRET_12345},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["project_id"] == 1  # default


@pytest.mark.asyncio
async def test_post_email_default_project_missing_returns_404(
    client, monkeypatch
):
    """If the default project name doesn't exist → 404 with the fixed hint."""
    await _seed_email_secret(client)
    monkeypatch.setenv(
        "EMAIL_INGEST_DEFAULT_PROJECT",
        f"missing-{uuid.uuid4().hex[:8]}",
    )

    payload = _build_email_payload(to="inbox@example.com")
    resp = await client.post(
        "/api/ingest/email",
        json=payload,
        headers={"X-Email-Ingest-Secret": EMAIL_SENTINEL_SECRET_12345},
    )
    assert resp.status_code == 404, resp.text
    detail = resp.json()["detail"]
    assert "target project not found" in detail
    assert "EMAIL_INGEST_DEFAULT_PROJECT" in detail


# ===========================================================================
# Body extraction
# ===========================================================================


@pytest.mark.asyncio
async def test_post_email_body_text_prefers_over_body_html(client):
    """body_text + body_html both present → description uses body_text."""
    await _seed_email_secret(client)
    payload = _build_email_payload(
        body_text="PLAINTEXT-MARKER",
        body_html="<p>HTML-MARKER</p>",
    )
    resp = await client.post(
        "/api/ingest/email",
        json=payload,
        headers={"X-Email-Ingest-Secret": EMAIL_SENTINEL_SECRET_12345},
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["task_id"]
    get_resp = await client.get(
        f"/api/tasks/{task_id}", headers={"X-Project-Id": "1"}
    )
    desc = get_resp.json()["description"]
    assert "PLAINTEXT-MARKER" in desc
    assert "HTML-MARKER" not in desc


@pytest.mark.asyncio
async def test_post_email_strips_html_when_only_html_provided(client):
    """body_html only → tags stripped, text preserved."""
    await _seed_email_secret(client)
    payload = _build_email_payload(
        body_text=None,
        body_html="<html><body><p>STRIPPED-CONTENT</p><br></body></html>",
    )
    resp = await client.post(
        "/api/ingest/email",
        json=payload,
        headers={"X-Email-Ingest-Secret": EMAIL_SENTINEL_SECRET_12345},
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["task_id"]
    get_resp = await client.get(
        f"/api/tasks/{task_id}", headers={"X-Project-Id": "1"}
    )
    desc = get_resp.json()["description"]
    assert "STRIPPED-CONTENT" in desc
    assert "<p>" not in desc
    assert "<html>" not in desc


# ===========================================================================
# Attachments
# ===========================================================================


@pytest.mark.asyncio
async def test_post_email_with_attachment_writes_to_disk_and_flags_description(
    client, _attachment_disk_cleanup
):
    """Attachment decoded + written + description references the file + #1309."""
    await _seed_email_secret(client)

    raw_payload = b"NotArealAttachment"
    b64 = base64.b64encode(raw_payload).decode("ascii")
    payload = _build_email_payload(
        attachments=[
            {
                "filename": "notes.txt",
                "content_type": "text/plain",
                "size_bytes": len(raw_payload),
                "content_base64": b64,
            }
        ]
    )
    resp = await client.post(
        "/api/ingest/email",
        json=payload,
        headers={"X-Email-Ingest-Secret": EMAIL_SENTINEL_SECRET_12345},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["attachment_count"] == 1
    task_id = body["task_id"]

    # Read the task; description should reference the on-disk path + #1309.
    get_resp = await client.get(
        f"/api/tasks/{task_id}", headers={"X-Project-Id": "1"}
    )
    desc = get_resp.json()["description"]
    assert "pending #1309" in desc
    assert "notes.txt" in desc

    # Find the on-disk path by scanning the description for it.
    # Format: ``-> <abs-path> -- pending #1309 resource registration``
    line = next(line for line in desc.splitlines() if "notes.txt" in line and "->" in line)
    path_segment = line.split("->", 1)[1].split("--", 1)[0].strip()
    on_disk = Path(path_segment)
    _attachment_disk_cleanup(on_disk)
    assert on_disk.exists(), f"expected attachment file at {on_disk}"
    assert on_disk.read_bytes() == raw_payload


@pytest.mark.asyncio
async def test_post_email_with_oversized_attachment_skips_with_warning(
    client, monkeypatch
):
    """An attachment over the per-attachment byte cap is skipped + flagged in
    description; the task is still created.

    Implementation note: a real 25 MB+1 byte payload would be rejected at the
    GLOBAL request-size middleware (REQUEST_MAX_BYTES=2MB) before reaching
    this endpoint — that's a separate L18-prevention gate. Here we monkeypatch
    ``ATTACHMENT_MAX_BYTES`` down to a small value so the per-attachment cap
    inside ``ingest_email`` is reachable in test without bumping into the
    global cap. The shape of the behavior we're locking is the same: ATTACHMENT
    larger than the cap → skipped + flagged + task still created.
    """
    await _seed_email_secret(client)

    # Lower the per-attachment cap to 8 bytes for this test.
    from src.routers import ingest as ingest_module
    monkeypatch.setattr(ingest_module, "ATTACHMENT_MAX_BYTES", 8)

    over_cap = b"this-is-more-than-8-bytes"  # 25 bytes — well above the 8-byte cap
    b64 = base64.b64encode(over_cap).decode("ascii")
    payload = _build_email_payload(
        attachments=[
            {
                "filename": "toobig.bin",
                "content_type": "application/octet-stream",
                "size_bytes": len(over_cap),
                "content_base64": b64,
            }
        ]
    )
    resp = await client.post(
        "/api/ingest/email",
        json=payload,
        headers={"X-Email-Ingest-Secret": EMAIL_SENTINEL_SECRET_12345},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The oversized attachment was skipped — not counted as accepted.
    assert body["attachment_count"] == 0
    task_id = body["task_id"]

    get_resp = await client.get(
        f"/api/tasks/{task_id}", headers={"X-Project-Id": "1"}
    )
    desc = get_resp.json()["description"]
    # The 25MB hint string is locked by the brief; we keep the marker stable
    # regardless of the (test-overridden) cap value.
    assert "Attachment skipped (>25MB): toobig.bin" in desc


@pytest.mark.asyncio
async def test_post_email_truncates_long_subject_to_200_chars(client):
    """Subject longer than 200 chars is truncated to title (max len 200)."""
    await _seed_email_secret(client)

    long_subject = "X" * 500  # exceeds 200-char title cap
    payload = _build_email_payload(subject=long_subject)
    resp = await client.post(
        "/api/ingest/email",
        json=payload,
        headers={"X-Email-Ingest-Secret": EMAIL_SENTINEL_SECRET_12345},
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["task_id"]
    get_resp = await client.get(
        f"/api/tasks/{task_id}", headers={"X-Project-Id": "1"}
    )
    title = get_resp.json()["title"]
    assert len(title) == 200
    assert title == "X" * 200


@pytest.mark.asyncio
async def test_post_email_recomputes_attachment_size_doesnt_trust_payload_field(
    client, _attachment_disk_cleanup
):
    """size_bytes payload field is ignored; the router re-computes from bytes.

    POSITIVE: the file is written with the actual decoded length.
    NEGATIVE: a payload claiming size_bytes=99999999 with a 16-byte body still
    succeeds (size_bytes isn't trusted) — the description carries the actual
    (recomputed) size, NOT the bogus payload value.
    """
    await _seed_email_secret(client)

    raw_payload = b"NotArealAttachment"  # 18 bytes
    b64 = base64.b64encode(raw_payload).decode("ascii")
    payload = _build_email_payload(
        attachments=[
            {
                "filename": "lying.txt",
                "content_type": "text/plain",
                # Lying claim — actual is 18 bytes
                "size_bytes": 99999999,
                "content_base64": b64,
            }
        ]
    )
    resp = await client.post(
        "/api/ingest/email",
        json=payload,
        headers={"X-Email-Ingest-Secret": EMAIL_SENTINEL_SECRET_12345},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["attachment_count"] == 1  # accepted, not >25MB after decode
    task_id = body["task_id"]

    get_resp = await client.get(
        f"/api/tasks/{task_id}", headers={"X-Project-Id": "1"}
    )
    desc = get_resp.json()["description"]
    # The description should report the ACTUAL size (18), not the lie (99999999).
    assert "18 bytes" in desc
    assert "99999999 bytes" not in desc

    # Also confirm the disk write happened (and clean up).
    line = next(
        line for line in desc.splitlines() if "lying.txt" in line and "->" in line
    )
    path_segment = line.split("->", 1)[1].split("--", 1)[0].strip()
    on_disk = Path(path_segment)
    _attachment_disk_cleanup(on_disk)
    assert on_disk.exists()
    assert on_disk.read_bytes() == raw_payload


# ===========================================================================
# Encoding-error guard (Kanban #1378)
# ===========================================================================


@pytest.mark.asyncio
async def test_post_email_malformed_vault_secret_returns_401_not_500(
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
    cred_id = await _seed_email_secret(client)

    # Patch decrypt at the module the router uses so the credential is "found"
    # but returns a string that cannot be UTF-8 encoded.
    from src.routers import ingest as ingest_module

    monkeypatch.setattr(
        ingest_module.credentials_crypto,
        "decrypt",
        lambda _ciphertext: "\ud800\ud801",  # lone surrogates — not encodable
    )

    resp = await client.post(
        "/api/ingest/email",
        json=_build_email_payload(),
        headers={"X-Email-Ingest-Secret": "some-header-value"},
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


# ===========================================================================
# Attachment allowlist guard (Kanban #1379)
# ===========================================================================


def test_resolve_attachment_base_under_allowed_base_uses_working_path(
    tmp_path, monkeypatch
):
    """POSITIVE: working_path under an allowed base resolves to working_path/data/ingest.

    Arrange a tmpdir as both the working_path and the allowed base so the
    guard passes, then verify resolve_attachment_base returns the expected
    sub-path — NOT the fallback.
    """
    from src.services.email_ingest import resolve_attachment_base

    allowed_dir = tmp_path / "projects" / "myproject"
    allowed_dir.mkdir(parents=True)

    monkeypatch.setenv(
        "EMAIL_INGEST_ATTACHMENT_ALLOWED_BASES", str(allowed_dir.parent)
    )

    project = type("P", (), {"working_path": str(allowed_dir)})()
    repo_root = tmp_path / "repo"

    result = resolve_attachment_base(project, repo_root)

    assert result == allowed_dir / "data" / "ingest"


def test_resolve_attachment_base_outside_allowlist_falls_back_to_runtime(
    tmp_path, monkeypatch
):
    """NEGATIVE: working_path outside the allowlist falls back to repo_root/_runtime.

    Use /etc as the (malicious) working_path on Linux; on Windows use a
    temp dir that is NOT in the allowlist.  Either way the resolved base
    must NOT be under the injected path.
    """
    from src.services.email_ingest import resolve_attachment_base

    # Build a directory that exists but is NOT in the allowlist.
    hostile_dir = tmp_path / "hostile"
    hostile_dir.mkdir()

    # The allowlist contains only a different tmpdir — hostile_dir is excluded.
    safe_base = tmp_path / "safe"
    safe_base.mkdir()
    monkeypatch.setenv("EMAIL_INGEST_ATTACHMENT_ALLOWED_BASES", str(safe_base))

    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    project = type("P", (), {"working_path": str(hostile_dir)})()

    result = resolve_attachment_base(project, repo_root)

    # NEGATIVE: result must NOT be under hostile_dir
    assert not result.is_relative_to(hostile_dir), (
        f"guard failed: {result} is under hostile {hostile_dir}"
    )
    # POSITIVE: result is under the safe repo_root/_runtime fallback
    assert result == repo_root / "_runtime" / "email_attachments"
