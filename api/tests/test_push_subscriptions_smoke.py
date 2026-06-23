"""Kanban #955.A — push subscriptions contract smoke tests.

First-pass smokes covering the happy path of:
  (1) CRUD round-trip: POST + GET + DELETE /api/push/subscribe[/{id}]
      lands a subscription, soft-deletes it, and re-list excludes it by default.
  (2) Idempotent re-subscribe: POSTing the same endpoint twice returns the
      same row (UPDATE-by-endpoint, D5) — 201 on first POST, 200 on second.
      Resurrection from a soft-deleted state via re-POST flips status back
      to 1.
  (3) Web Push adapter happy path: send_web_push(target, payload) calls into
      a stubbed webpush_fn (so no real network) and returns ok=True. Also
      verifies D6 auto-soft-delete on 410 Gone.

The rigorous suite — edge cases (FK violations, invalid keys shape, large
payload truncation, all 4 VAPID env missing combinations, project_id
filtering, pagination, concurrent ON CONFLICT races, project-scoped vs
global filtering correctness, etc.) — is dev-tester's domain. Slice 955.B
adds event-hook integration tests; 955.C adds FE integration.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Test fixtures (no shared helper — kept local; each test owns its data)
# ---------------------------------------------------------------------------


def _subscribe_payload(
    *,
    endpoint: str = "https://fcm.googleapis.com/fcm/send/SMOKE-CONTRACT-1",
    project_id: int | None = None,
    user_agent: str | None = "Smoke/1.0",
    kinds_enabled: dict | None = None,
) -> dict:
    body: dict = {
        "endpoint": endpoint,
        "keys": {
            "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM",
            "auth": "tBHItJI5svbpez7KI4CCXg",
        },
    }
    if project_id is not None:
        body["project_id"] = project_id
    if user_agent is not None:
        body["user_agent"] = user_agent
    if kinds_enabled is not None:
        body["kinds_enabled"] = kinds_enabled
    return body


# ---------------------------------------------------------------------------
# (1) CRUD round-trip — POST, GET list, DELETE, re-list (excluded by default),
#     re-list with include_deleted=true (visible).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_subscription_crud_happy_round_trip(client) -> None:
    """Full CRUD on an operator-scoped (project_id=None) subscription.

    POST creates → 201 + read shape.
    GET subscriptions includes the row.
    DELETE soft-deletes → 204; re-list excludes; include_deleted=true brings
    it back.
    """
    payload = _subscribe_payload(
        endpoint="https://example.test/push/crud-happy"
    )

    # POST
    resp = await client.post("/api/push/subscribe", json=payload)
    assert resp.status_code == 201, resp.text
    created = resp.json()
    sub_id = created["id"]
    assert created["endpoint"] == payload["endpoint"]
    assert created["p256dh"] == payload["keys"]["p256dh"]
    assert created["auth"] == payload["keys"]["auth"]
    assert created["project_id"] is None
    assert created["status"] == 1
    # Default kinds_enabled (D3 — four True + task_halted=False; 5-key default
    # since commit 56fc563 / Kanban #1841 added task_halted opt-in key).
    assert created["kinds_enabled"] == {
        "hitl_needed": True,
        "task_done": True,
        "task_failed": True,
        "budget_warn": True,
        "task_halted": False,
    }

    # GET list — row visible
    resp = await client.get("/api/push/subscriptions")
    assert resp.status_code == 200, resp.text
    ids = {row["id"] for row in resp.json()}
    assert sub_id in ids

    # DELETE soft-delete
    resp = await client.delete(f"/api/push/subscribe/{sub_id}")
    assert resp.status_code == 204, resp.text

    # GET list — row excluded by default
    resp = await client.get("/api/push/subscriptions")
    assert resp.status_code == 200
    ids = {row["id"] for row in resp.json()}
    assert sub_id not in ids

    # GET list — include_deleted=true makes it visible
    resp = await client.get("/api/push/subscriptions?include_deleted=true")
    assert resp.status_code == 200
    rows_by_id = {row["id"]: row for row in resp.json()}
    assert sub_id in rows_by_id
    assert rows_by_id[sub_id]["status"] == 0

    # Idempotent re-DELETE → 204
    resp = await client.delete(f"/api/push/subscribe/{sub_id}")
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# (2) Idempotent re-subscribe — D5 ON CONFLICT DO UPDATE semantics.
#     Re-POST same endpoint with refreshed keys → 200 (not 201), same row id,
#     status flips back to 1 if it was 0, keys/UA updated.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_subscription_resubscribe_idempotent_and_resurrects(
    client,
) -> None:
    endpoint = "https://example.test/push/resubscribe-idempotent"

    # First POST → 201
    resp1 = await client.post(
        "/api/push/subscribe",
        json=_subscribe_payload(endpoint=endpoint),
    )
    assert resp1.status_code == 201, resp1.text
    sub_id = resp1.json()["id"]

    # Second POST same endpoint with refreshed keys → 200, same id, keys updated
    refreshed_payload = {
        "endpoint": endpoint,
        "keys": {
            # Different keys to confirm the UPDATE path fires.
            "p256dh": "BREFRESHEDpubkeyREFRESHEDpubkeyREFRESHEDpubkeyREFRESHEDpubkeyREFRESHEDpubkeyREFRESHEDpubkey",
            "auth": "REFRESHEDauthsecret_xyz",
        },
        "user_agent": "Smoke/2.0 (updated)",
        "kinds_enabled": {
            "hitl_needed": True,
            "task_done": False,
            "task_failed": True,
            "budget_warn": False,
        },
    }
    resp2 = await client.post("/api/push/subscribe", json=refreshed_payload)
    assert resp2.status_code == 200, resp2.text
    refreshed = resp2.json()
    assert refreshed["id"] == sub_id  # Same row, no duplicate
    assert refreshed["p256dh"] == refreshed_payload["keys"]["p256dh"]
    assert refreshed["auth"] == refreshed_payload["keys"]["auth"]
    assert refreshed["user_agent"] == "Smoke/2.0 (updated)"
    assert refreshed["kinds_enabled"]["task_done"] is False
    assert refreshed["kinds_enabled"]["budget_warn"] is False
    assert refreshed["status"] == 1

    # DELETE → soft-delete
    resp = await client.delete(f"/api/push/subscribe/{sub_id}")
    assert resp.status_code == 204

    # Re-POST after soft-delete → 200, status flips back to 1 (resurrect)
    resp3 = await client.post(
        "/api/push/subscribe",
        json=_subscribe_payload(endpoint=endpoint),
    )
    assert resp3.status_code == 200, resp3.text
    resurrected = resp3.json()
    assert resurrected["id"] == sub_id
    assert resurrected["status"] == 1


# ---------------------------------------------------------------------------
# (3) Web Push adapter contract smoke — send_web_push happy path + D6
#     auto-soft-delete on 410 Gone.
#
# The adapter is the integration seam between the existing #1224 router and
# the Web Push protocol. We stub out the pywebpush.webpush call so no real
# network fires; the adapter contract under test is:
#   - Returns ok=True on a 200/201 response object.
#   - On 410 Gone (D6), soft-deletes the subscription row before returning
#     ok=False with the canonical "Subscription invalid; auto-removed" detail.
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Duck-type a pywebpush success Response."""

    def __init__(self, status_code: int = 201) -> None:
        self.status_code = status_code
        self.text = ""


class _Fake410Exception(Exception):
    """Duck-type a pywebpush.WebPushException with a 410 response."""

    def __init__(self) -> None:
        super().__init__("subscription gone")
        self.response = _FakeResponse(status_code=410)


@pytest.mark.asyncio
async def test_send_web_push_happy_path_and_410_auto_soft_delete(
    client, monkeypatch
) -> None:
    """One test covers BOTH positive (ok=True) and the locked D6 negative
    (410 Gone auto-soft-delete). Paired POSITIVE + NEGATIVE per dev-sr-backend
    test contract — never bare equality, always assert the intended mutation.
    """
    from src.services import notify_web_push as nwp

    # Stub VAPID env so the env-gate passes (the adapter env-gates BEFORE the
    # DB fetch).
    monkeypatch.setenv(nwp.VAPID_ENV_PUBLIC, "BSTUB-public-key")
    monkeypatch.setenv(nwp.VAPID_ENV_PRIVATE, "STUB-private-key")
    monkeypatch.setenv(nwp.VAPID_ENV_SUBJECT, "mailto:smoke@example.test")

    # Create a subscription row via the public API.
    endpoint = "https://example.test/push/adapter-smoke"
    resp = await client.post(
        "/api/push/subscribe", json=_subscribe_payload(endpoint=endpoint)
    )
    assert resp.status_code == 201, resp.text
    sub_id = resp.json()["id"]

    # --- POSITIVE: stub webpush_fn returns 201; expect ok=True ---
    captured: dict = {}

    def webpush_fn_success(**kwargs):
        captured["subscription_info"] = kwargs["subscription_info"]
        captured["data"] = kwargs["data"]
        return _FakeResponse(status_code=201)

    target = {
        "kind": "web_push",
        "chat_id": str(sub_id),
        "priority": 1,
        "label": "smoke",
    }
    payload = {
        "title": "Smoke",
        "body": "hello from the adapter",
        "url": "/tasks/123",
    }
    result = await nwp.send_web_push(
        target, payload, webpush_fn=webpush_fn_success
    )
    assert result == {"ok": True, "detail": "sent"}
    # Subscription_info shape forwarded correctly
    assert captured["subscription_info"]["endpoint"] == endpoint
    assert captured["subscription_info"]["keys"]["p256dh"]
    assert captured["subscription_info"]["keys"]["auth"]
    # Payload serialized as JSON with title/body/url intact
    import json
    parsed = json.loads(captured["data"])
    assert parsed["title"] == "Smoke"
    assert parsed["url"] == "/tasks/123"

    # Verify the row is still status=1 BEFORE the 410 path (negative locks
    # the actual mutation: not a vacuous equal-to-pre-state assertion).
    resp = await client.get(f"/api/push/subscriptions?include_deleted=true")
    pre_410_row = next(r for r in resp.json() if r["id"] == sub_id)
    assert pre_410_row["status"] == 1, "row must be active BEFORE 410 path"

    # --- NEGATIVE / D6: stub webpush_fn raises 410; expect ok=False AND
    #     the row's status flips to 0 (auto-soft-delete). ---
    def webpush_fn_410(**kwargs):
        raise _Fake410Exception()

    result = await nwp.send_web_push(
        target, payload, webpush_fn=webpush_fn_410
    )
    assert result == {
        "ok": False,
        "detail": "Subscription invalid; auto-removed",
    }

    # Confirm the row is now status=0 (the locked D6 mutation).
    resp = await client.get(f"/api/push/subscriptions?include_deleted=true")
    post_410_row = next(r for r in resp.json() if r["id"] == sub_id)
    assert post_410_row["status"] == 0, "D6: 410 must soft-delete the row"
