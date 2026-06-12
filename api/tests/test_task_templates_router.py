"""Contract-smoke tests for the task-templates router (Kanban #1303).

HTTP-level via the shared `client` fixture (httpx.AsyncClient + ASGITransport,
bound to the isolated `agent_teams_test` DB by conftest). First-pass coverage
proving the contract is wired:
  - migration<->ORM parity (the table + columns the ORM declares exist live),
  - GET filter (team + active-only; include_disabled opt-in),
  - POST + PATCH + DELETE(soft) happy paths,
  - unknown-team 422 (app-side enum validation, NO DB CHECK),
  - operator-gate 403 when activated (and token-success path),
  - the AC#5 Tier-1 smoke: POST -> GET filtered list shows it -> DELETE cleanup.

Rigorous edge/negative/regression coverage is dev-tester's domain.
"""

from __future__ import annotations

import uuid

import pytest

from src.constants import RecordStatus


def _template_payload(*, team: str = "dev", name: str | None = None) -> dict:
    """Minimal valid POST /api/task-templates body with a unique name."""
    return {
        "team": team,
        "name": name or f"tpl-{uuid.uuid4().hex[:8]}",
        "icon": "rocket",
        "description_template": "Analyze {{file}} for {{metric}}",
        "acceptance_criteria_template": [
            {"text": "verify {{file}} parses", "status": "pending"},
        ],
        "default_task_type": "feature",
        "default_priority": 2,
        "default_task_kind": "ai",
        "placeholders": ["file", "metric"],
    }


# ---------------------------------------------------------------------------
# Migration <-> ORM parity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_templates_table_matches_orm_columns(db_session) -> None:
    """The live test DB has `task_templates` with exactly the ORM's columns.

    Proves the hand-written migration 0060 and the ORM model agree on the
    schema (parity smoke — catches a column the model declares but the
    migration forgot, or vice versa).
    """
    from sqlalchemy import text

    from src.models.task_template import TaskTemplate

    rows = (
        await db_session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'task_templates'"
            )
        )
    ).fetchall()
    db_cols = {r[0] for r in rows}
    orm_cols = {c.name for c in TaskTemplate.__table__.columns}
    assert db_cols == orm_cols, f"db={db_cols} orm={orm_cols}"


# ---------------------------------------------------------------------------
# POST + GET + filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_then_get_by_id_roundtrip(client) -> None:
    """POST returns 201 + the row; GET /{id} returns the same row incl. raw templates."""
    payload = _template_payload()
    resp = await client.post("/api/task-templates", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["team"] == "dev"
    assert body["description_template"] == "Analyze {{file}} for {{metric}}"
    assert body["acceptance_criteria_template"][0]["text"] == "verify {{file}} parses"
    assert body["status"] == RecordStatus.ACTIVE
    assert body["updated_at"] is None  # NULL until first edit

    tid = body["id"]
    detail = await client.get(f"/api/task-templates/{tid}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["id"] == tid

    # Cleanup (soft-delete) so the disabled-only filter test isn't polluted.
    await client.delete(f"/api/task-templates/{tid}")


@pytest.mark.asyncio
async def test_get_missing_returns_404(client) -> None:
    resp = await client.get("/api/task-templates/99999999")
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Task template id=99999999 not found"


@pytest.mark.asyncio
async def test_list_filters_by_team_and_active_only(client) -> None:
    """GET ?team= returns only that team's ACTIVE templates; disabled excluded.

    POSITIVE: the freshly-created active row appears in the team-filtered list.
    NEGATIVE: after soft-delete it disappears from the default (active-only)
    list but reappears with ?include_disabled=true.
    """
    payload = _template_payload(team="dev")
    created = (await client.post("/api/task-templates", json=payload)).json()
    tid = created["id"]

    listing = await client.get("/api/task-templates?team=dev&limit=500")
    assert listing.status_code == 200, listing.text
    ids = {t["id"] for t in listing.json()}
    assert tid in ids
    assert all(t["team"] == "dev" for t in listing.json())

    # Soft-delete then confirm active-only filter hides it, include_disabled shows it.
    assert (await client.delete(f"/api/task-templates/{tid}")).status_code == 204
    after = await client.get("/api/task-templates?team=dev&limit=500")
    assert tid not in {t["id"] for t in after.json()}
    incl = await client.get(
        "/api/task-templates?team=dev&include_disabled=true&limit=500"
    )
    assert tid in {t["id"] for t in incl.json()}


@pytest.mark.asyncio
async def test_list_unknown_team_422(client) -> None:
    """A bogus ?team= 422s (app-side enum validation, NO DB CHECK)."""
    resp = await client.get("/api/task-templates?team=bogusteam")
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Unknown-team on POST — app-side validation (the #1620 correction)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_unknown_team_422(client) -> None:
    payload = _template_payload(team="not-a-team")
    resp = await client.post("/api/task-templates", json=payload)
    assert resp.status_code == 422, resp.text
    assert "Unknown team" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_post_netops_team_accepted(client) -> None:
    """`netops` (added post-#1620 with NO migration) is a valid team here too.

    Proves there is no per-team CHECK regression: a team that exists only in
    the constants registry — never in any DB CHECK — is accepted.
    """
    payload = _template_payload(team="netops")
    resp = await client.post("/api/task-templates", json=payload)
    assert resp.status_code == 201, resp.text
    await client.delete(f"/api/task-templates/{resp.json()['id']}")


# ---------------------------------------------------------------------------
# PATCH — toggle status + edit text, updated_at bump
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_edits_text_and_bumps_updated_at(client) -> None:
    created = (await client.post("/api/task-templates", json=_template_payload())).json()
    tid = created["id"]
    assert created["updated_at"] is None

    resp = await client.patch(
        f"/api/task-templates/{tid}",
        json={"name": "renamed", "description_template": "New {{file}} body"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "renamed"
    assert body["description_template"] == "New {{file}} body"
    # updated_at is set explicitly on PATCH (no DB trigger).
    assert body["updated_at"] is not None

    await client.delete(f"/api/task-templates/{tid}")


@pytest.mark.asyncio
async def test_patch_status_toggle(client) -> None:
    """PATCH can disable (status=0) and re-enable (status=1) — the spec's toggle."""
    created = (await client.post("/api/task-templates", json=_template_payload())).json()
    tid = created["id"]

    disabled = await client.patch(f"/api/task-templates/{tid}", json={"status": 0})
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["status"] == 0

    enabled = await client.patch(f"/api/task-templates/{tid}", json={"status": 1})
    assert enabled.json()["status"] == 1

    await client.delete(f"/api/task-templates/{tid}")


@pytest.mark.asyncio
async def test_patch_extra_field_forbidden_422(client) -> None:
    """TaskTemplateUpdate is extra='forbid' — an unknown field 422s."""
    created = (await client.post("/api/task-templates", json=_template_payload())).json()
    tid = created["id"]
    resp = await client.patch(f"/api/task-templates/{tid}", json={"bogus": "x"})
    assert resp.status_code == 422, resp.text
    await client.delete(f"/api/task-templates/{tid}")


# ---------------------------------------------------------------------------
# DELETE — soft-delete idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_soft_then_idempotent(client) -> None:
    created = (await client.post("/api/task-templates", json=_template_payload())).json()
    tid = created["id"]

    first = await client.delete(f"/api/task-templates/{tid}")
    assert first.status_code == 204, first.text
    # Detail still returns the row (status flipped to 0).
    detail = await client.get(f"/api/task-templates/{tid}")
    assert detail.json()["status"] == RecordStatus.DELETED
    # Idempotent second delete.
    second = await client.delete(f"/api/task-templates/{tid}")
    assert second.status_code == 204, second.text


# ---------------------------------------------------------------------------
# AC#5 Tier-1 smoke: POST -> GET filtered list shows it -> DELETE cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac5_tier1_lifecycle_smoke(client) -> None:
    """Full create -> list-filtered -> delete lifecycle in one flow."""
    payload = _template_payload(team="dev", name=f"ac5-{uuid.uuid4().hex[:8]}")
    created = await client.post("/api/task-templates", json=payload)
    assert created.status_code == 201, created.text
    tid = created.json()["id"]

    listed = await client.get("/api/task-templates?team=dev&limit=500")
    assert tid in {t["id"] for t in listed.json()}

    deleted = await client.delete(f"/api/task-templates/{tid}")
    assert deleted.status_code == 204, deleted.text
    after = await client.get("/api/task-templates?team=dev&limit=500")
    assert tid not in {t["id"] for t in after.json()}


# ---------------------------------------------------------------------------
# Operator gate (#1857) — activate via monkeypatch, verify 403 + token success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_gated_403_when_active_without_token(client, monkeypatch) -> None:
    """With OPERATOR_ACTION_KEY set, POST without a valid token is 403.

    Resets the operator_auth one-time-warn flag so the gate re-evaluates the
    env live (it reads os.environ per request).
    """
    import src.services.operator_auth as oa
    from src.routers.task_templates import _DETAIL_OPERATOR_PROOF_REQUIRED

    monkeypatch.setenv("OPERATOR_ACTION_KEY", "secret-token-123")
    oa._inactive_warned = False  # noqa: SLF001 — reset module guard for test

    resp = await client.post("/api/task-templates", json=_template_payload())
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == _DETAIL_OPERATOR_PROOF_REQUIRED


@pytest.mark.asyncio
async def test_post_gated_succeeds_with_valid_token(client, monkeypatch) -> None:
    """With OPERATOR_ACTION_KEY set, POST WITH the matching token succeeds."""
    monkeypatch.setenv("OPERATOR_ACTION_KEY", "secret-token-123")

    resp = await client.post(
        "/api/task-templates",
        json=_template_payload(),
        headers={"X-Operator-Token": "secret-token-123"},
    )
    assert resp.status_code == 201, resp.text
    # Cleanup needs the token too (DELETE is gated).
    await client.delete(
        f"/api/task-templates/{resp.json()['id']}",
        headers={"X-Operator-Token": "secret-token-123"},
    )


# ---------------------------------------------------------------------------
# Kanban #1909-N1: placeholder key pattern (^[\w.-]+$)
# ---------------------------------------------------------------------------


def _payload_with_placeholders(keys: list[str]) -> dict:
    """Minimal POST body overriding the placeholders field."""
    p = _template_payload()
    p["placeholders"] = keys
    return p


@pytest.mark.asyncio
async def test_placeholder_invalid_space_422(client) -> None:
    """'my key' (space) must be rejected with 422 naming the value.

    NEGATIVE: space is not in [\\w.-]; the FE substitution regex would never
    match it → the placeholder creates an orphaned input that silently never subs.
    """
    resp = await client.post(
        "/api/task-templates", json=_payload_with_placeholders(["my key"])
    )
    assert resp.status_code == 422, resp.text
    body = resp.text
    assert "my key" in body, f"422 detail must name the offending value; got: {body!r}"


@pytest.mark.asyncio
async def test_placeholder_invalid_colon_422(client) -> None:
    """'a:b' (colon) must be rejected with 422 naming the index/value."""
    resp = await client.post(
        "/api/task-templates", json=_payload_with_placeholders(["good_key", "a:b"])
    )
    assert resp.status_code == 422, resp.text
    body = resp.text
    assert "a:b" in body, f"422 detail must name the offending value; got: {body!r}"


@pytest.mark.asyncio
async def test_placeholder_invalid_non_ascii_422(client) -> None:
    """Non-ASCII key ('ไทย') must be rejected with 422 (\\w matches only ASCII word chars)."""
    resp = await client.post(
        "/api/task-templates", json=_payload_with_placeholders(["ไทย"])
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_placeholder_valid_patterns_accepted(client) -> None:
    """Valid keys — 'branch_name', 'my-key', 'a.b_c' — must be accepted (201).

    POSITIVE: all three patterns that the FE regex matches are allowed.
    """
    resp = await client.post(
        "/api/task-templates",
        json=_payload_with_placeholders(["branch_name", "my-key", "a.b_c"]),
    )
    assert resp.status_code == 201, resp.text
    del_resp = await client.delete(f"/api/task-templates/{resp.json()['id']}")
    assert del_resp.status_code == 204, del_resp.text


@pytest.mark.asyncio
async def test_placeholder_patch_invalid_key_422(client) -> None:
    """PATCH with an invalid placeholder key ('bad value') must return 422.

    Covers the Update path — _check_placeholders on TaskTemplateUpdate.
    NEGATIVE: invalid key on PATCH must not silently persist.
    POSITIVE: same 422 contract as Create.
    """
    created = (
        await client.post("/api/task-templates", json=_template_payload())
    ).json()
    tid = created["id"]
    resp = await client.patch(
        f"/api/task-templates/{tid}", json={"placeholders": ["bad value"]}
    )
    assert resp.status_code == 422, resp.text
    body = resp.text
    assert "bad value" in body, f"422 detail must name the offending value; got: {body!r}"
    # Cleanup — template still exists (PATCH failed, nothing was mutated).
    await client.delete(f"/api/task-templates/{tid}")


@pytest.mark.asyncio
async def test_placeholder_patch_valid_key_accepted(client) -> None:
    """PATCH with valid placeholder keys must be accepted (200).

    POSITIVE: Update path passes the same regex constraint without over-rejecting.
    """
    created = (
        await client.post("/api/task-templates", json=_template_payload())
    ).json()
    tid = created["id"]
    resp = await client.patch(
        f"/api/task-templates/{tid}", json={"placeholders": ["new_key", "other.key"]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["placeholders"] == ["new_key", "other.key"]
    await client.delete(f"/api/task-templates/{tid}")
