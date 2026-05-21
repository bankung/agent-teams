"""HTTP-level contract tests for /api/webhooks/* (Kanban #1325 M2).

Coverage:
  - Stripe: missing credential 401, bad signature 401 + audit denial,
    payment_intent.succeeded inserts revenue, charge.refunded inserts refund,
    ignored event 200, duplicate event id dedupes.
  - PayPal: bad secret 401, sale.completed inserts revenue, dup dedupes.
  - P&L smoke: insert via webhook then GET /pl reflects the revenue.

Master key is provided via the same autouse fixture as the credentials
router tests so /credentials POST + the webhook decrypt path share the same
Fernet instance.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from src.db import SessionLocal
from src.models.credential import CredentialAccessLog
from src.services import credentials_crypto


# Sentinel — also referenced by the secret-not-in-response audit grep. Stays
# in lockstep with the verifier-tests constant.
WEBHOOK_SENTINEL_SECRET_99887 = "WEBHOOK-SENTINEL-SECRET-99887"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _credentials_master_key(monkeypatch):
    """Set a fresh Fernet master key per test + clear the crypto cache."""
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


def _project_create_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"webhook fixture {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


async def _make_project(client, scaffold_cleanup, slug: str) -> int:
    name = scaffold_cleanup(_unique_name(slug))
    resp = await client.post("/api/projects", json=_project_create_payload(name))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _seed_secret(client, pid: int, *, name: str, value: str) -> int:
    """POST a webhook_secret credential via the existing vault router."""
    resp = await client.post(
        f"/api/projects/{pid}/credentials",
        json={"name": name, "value": value, "kind": "webhook_secret"},
        headers={"X-Project-Id": str(pid)},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _stripe_sign(payload_bytes: bytes, secret: str, timestamp: int) -> str:
    signed = f"{timestamp}.".encode("ascii") + payload_bytes
    return hmac.new(
        secret.encode("utf-8"), signed, hashlib.sha256
    ).hexdigest()


def _build_stripe_payment_intent_event(
    event_id: str = "evt_test_pi_1",
    amount: int = 12345,  # $123.45
    currency: str = "usd",
    pi_id: str = "pi_test_1",
    created: int | None = None,
) -> bytes:
    now_ts = created if created is not None else int(datetime.now(timezone.utc).timestamp())
    return json.dumps(
        {
            "id": event_id,
            "type": "payment_intent.succeeded",
            "created": now_ts,
            "data": {
                "object": {
                    "id": pi_id,
                    "amount": amount,
                    "currency": currency,
                    "status": "succeeded",
                    "created": now_ts,
                }
            },
        }
    ).encode("utf-8")


def _build_stripe_charge_refunded_event(
    event_id: str = "evt_test_refund_1",
    amount_refunded: int = 5000,  # $50.00
    currency: str = "usd",
    charge_id: str = "ch_test_1",
    created: int | None = None,
) -> bytes:
    now_ts = created if created is not None else int(datetime.now(timezone.utc).timestamp())
    return json.dumps(
        {
            "id": event_id,
            "type": "charge.refunded",
            "created": now_ts,
            "data": {
                "object": {
                    "id": charge_id,
                    "amount_refunded": amount_refunded,
                    "currency": currency,
                    "payment_intent": "pi_test_1",
                    "created": now_ts,
                }
            },
        }
    ).encode("utf-8")


def _build_paypal_sale_completed_event(
    event_id: str = "WH-PP-EVT-1",
    value: str = "23.45",
    currency: str = "USD",
    sale_id: str = "PAY-SALE-1",
) -> bytes:
    return json.dumps(
        {
            "id": event_id,
            "event_type": "PAYMENT.SALE.COMPLETED",
            "create_time": "2026-05-21T03:00:00Z",
            "resource": {
                "id": sale_id,
                "amount": {"value": value, "currency_code": currency},
                "create_time": "2026-05-21T03:00:00Z",
            },
        }
    ).encode("utf-8")


async def _count_denial_audits_for_credential(cred_id: int) -> int:
    """Return the count of CredentialAccessLog rows whose accessed_by starts
    with 'system:webhook (denied=' — i.e. denial audit rows for this credential.
    """
    async with SessionLocal() as s:
        rows = (
            await s.execute(
                select(CredentialAccessLog).where(
                    CredentialAccessLog.credential_id == cred_id
                )
            )
        ).scalars().all()
    return sum(
        1 for r in rows if r.accessed_by.startswith("system:webhook (denied=")
    )


# ===========================================================================
# Stripe
# ===========================================================================


@pytest.mark.asyncio
async def test_stripe_webhook_missing_credential_returns_401(
    client, scaffold_cleanup
):
    """No credential seeded → 401 with the secret-not-configured hint."""
    pid = await _make_project(client, scaffold_cleanup, "wh-stripe-nocred")
    payload = _build_stripe_payment_intent_event()
    resp = await client.post(
        f"/api/webhooks/stripe/{pid}",
        content=payload,
        headers={"Stripe-Signature": "t=0,v1=abc"},
    )
    assert resp.status_code == 401, resp.text
    detail = resp.json()["detail"]
    assert "stripe_webhook_secret" in detail
    assert "not configured" in detail


@pytest.mark.asyncio
async def test_stripe_webhook_bad_signature_returns_401_and_audits_denial(
    client, scaffold_cleanup
):
    """Seed credential, send junk signature → 401 + denial audit row written.

    POSITIVE: a denial audit row appears for the credential.
    NEGATIVE: the locked status code is 401 AND the wire detail is the static
    'invalid_signature' string (no oracle leak of which check failed).
    """
    pid = await _make_project(client, scaffold_cleanup, "wh-stripe-bad")
    cred_id = await _seed_secret(
        client, pid,
        name="stripe_webhook_secret",
        value=WEBHOOK_SENTINEL_SECRET_99887,
    )

    payload = _build_stripe_payment_intent_event()
    # Wrong signature — header well-formed but the hex doesn't match.
    now_ts = int(datetime.now(timezone.utc).timestamp())
    bad_sig = _stripe_sign(payload, "wrong-secret", now_ts)
    resp = await client.post(
        f"/api/webhooks/stripe/{pid}",
        content=payload,
        headers={"Stripe-Signature": f"t={now_ts},v1={bad_sig}"},
    )
    assert resp.status_code == 401, resp.text
    assert resp.json() == {"detail": "invalid_signature"}

    # Audit row written.
    denials = await _count_denial_audits_for_credential(cred_id)
    assert denials >= 1, "expected denial audit row for the bad-signature attempt"


@pytest.mark.asyncio
async def test_stripe_webhook_payment_intent_succeeded_inserts_revenue_row(
    client, scaffold_cleanup
):
    """Happy path: signed event → 200 + new revenue txn visible via /transactions."""
    pid = await _make_project(client, scaffold_cleanup, "wh-stripe-pi")
    await _seed_secret(
        client, pid,
        name="stripe_webhook_secret",
        value=WEBHOOK_SENTINEL_SECRET_99887,
    )

    payload = _build_stripe_payment_intent_event(
        event_id="evt_pi_happy_1", amount=99900, currency="usd"
    )
    now_ts = int(datetime.now(timezone.utc).timestamp())
    sig = _stripe_sign(payload, WEBHOOK_SENTINEL_SECRET_99887, now_ts)

    resp = await client.post(
        f"/api/webhooks/stripe/{pid}",
        content=payload,
        headers={"Stripe-Signature": f"t={now_ts},v1={sig}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["received"] is True
    assert body["deduplicated"] is False
    txn_id = body["transaction_id"]
    assert isinstance(txn_id, int) and txn_id > 0

    # Read the row back via the existing /transactions surface to confirm
    # wiring end-to-end (not just an in-memory mock).
    list_resp = await client.get(
        "/api/transactions",
        headers={"X-Project-Id": str(pid)},
    )
    assert list_resp.status_code == 200, list_resp.text
    rows = list_resp.json()
    matches = [r for r in rows if r["id"] == txn_id]
    assert len(matches) == 1
    row = matches[0]
    assert row["kind"] == "revenue"
    assert row["amount_minor"] == 99900
    assert row["currency"] == "USD"
    assert row["source"] == "stripe"
    assert row["source_ref"] == "evt_pi_happy_1"
    assert row["category"] == "stripe_payment"


@pytest.mark.asyncio
async def test_stripe_webhook_charge_refunded_inserts_refund_row(
    client, scaffold_cleanup
):
    """charge.refunded → kind='refund' row with amount = amount_refunded."""
    pid = await _make_project(client, scaffold_cleanup, "wh-stripe-refund")
    await _seed_secret(
        client, pid,
        name="stripe_webhook_secret",
        value=WEBHOOK_SENTINEL_SECRET_99887,
    )

    payload = _build_stripe_charge_refunded_event(
        event_id="evt_refund_happy_1", amount_refunded=2500, currency="usd"
    )
    now_ts = int(datetime.now(timezone.utc).timestamp())
    sig = _stripe_sign(payload, WEBHOOK_SENTINEL_SECRET_99887, now_ts)

    resp = await client.post(
        f"/api/webhooks/stripe/{pid}",
        content=payload,
        headers={"Stripe-Signature": f"t={now_ts},v1={sig}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deduplicated"] is False
    txn_id = body["transaction_id"]

    list_resp = await client.get(
        "/api/transactions",
        headers={"X-Project-Id": str(pid), "Cache-Control": "no-cache"},
    )
    rows = list_resp.json()
    matches = [r for r in rows if r["id"] == txn_id]
    assert len(matches) == 1
    assert matches[0]["kind"] == "refund"
    assert matches[0]["amount_minor"] == 2500
    assert matches[0]["category"] == "stripe_refund"


@pytest.mark.asyncio
async def test_stripe_webhook_ignored_event_type_returns_200_no_row(
    client, scaffold_cleanup
):
    """Unhandled event types → 200 + ignored_event_type flag, no DB row.

    POSITIVE: the response carries received=True and ignored_event_type.
    NEGATIVE (locked invariant): the transaction count for the project is
    UNCHANGED after the call.
    """
    pid = await _make_project(client, scaffold_cleanup, "wh-stripe-ignore")
    await _seed_secret(
        client, pid,
        name="stripe_webhook_secret",
        value=WEBHOOK_SENTINEL_SECRET_99887,
    )

    pre_list = await client.get(
        "/api/transactions", headers={"X-Project-Id": str(pid)}
    )
    pre_count = len(pre_list.json())

    payload = json.dumps(
        {
            "id": "evt_unknown_1",
            "type": "customer.subscription.created",  # not handled
            "created": int(datetime.now(timezone.utc).timestamp()),
            "data": {"object": {"id": "sub_1"}},
        }
    ).encode("utf-8")
    now_ts = int(datetime.now(timezone.utc).timestamp())
    sig = _stripe_sign(payload, WEBHOOK_SENTINEL_SECRET_99887, now_ts)

    resp = await client.post(
        f"/api/webhooks/stripe/{pid}",
        content=payload,
        headers={"Stripe-Signature": f"t={now_ts},v1={sig}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["received"] is True
    assert body["ignored_event_type"] == "customer.subscription.created"
    assert "transaction_id" not in body

    post_list = await client.get(
        "/api/transactions", headers={"X-Project-Id": str(pid)}
    )
    post_count = len(post_list.json())
    # NEGATIVE: ignored events never write a row.
    assert post_count == pre_count, (
        f"ignored event leaked a transaction row: {pre_count} -> {post_count}"
    )


@pytest.mark.asyncio
async def test_stripe_webhook_duplicate_event_id_dedupes(
    client, scaffold_cleanup
):
    """Same event id posted twice → second call returns 200 + deduplicated:true
    + the SAME transaction_id as the first.

    POSITIVE: the second response carries deduplicated=true.
    NEGATIVE (locked invariant): the project's transaction count goes up by
    EXACTLY 1 across two webhook deliveries (not 2 — the dedup is real).
    """
    pid = await _make_project(client, scaffold_cleanup, "wh-stripe-dedup")
    await _seed_secret(
        client, pid,
        name="stripe_webhook_secret",
        value=WEBHOOK_SENTINEL_SECRET_99887,
    )

    pre_list = await client.get(
        "/api/transactions", headers={"X-Project-Id": str(pid)}
    )
    pre_count = len(pre_list.json())

    payload = _build_stripe_payment_intent_event(event_id="evt_dedup_1", amount=4242)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    sig = _stripe_sign(payload, WEBHOOK_SENTINEL_SECRET_99887, now_ts)
    headers = {"Stripe-Signature": f"t={now_ts},v1={sig}"}

    # First delivery — creates the row.
    r1 = await client.post(f"/api/webhooks/stripe/{pid}", content=payload, headers=headers)
    assert r1.status_code == 200, r1.text
    b1 = r1.json()
    assert b1["deduplicated"] is False
    txn_id_1 = b1["transaction_id"]

    # Second delivery — same payload, same signature → dedup path.
    r2 = await client.post(f"/api/webhooks/stripe/{pid}", content=payload, headers=headers)
    assert r2.status_code == 200, r2.text
    b2 = r2.json()
    # POSITIVE
    assert b2["deduplicated"] is True
    assert b2["transaction_id"] == txn_id_1

    # NEGATIVE — total row count goes up by exactly 1 across both calls.
    post_list = await client.get(
        "/api/transactions", headers={"X-Project-Id": str(pid)}
    )
    post_count = len(post_list.json())
    assert post_count == pre_count + 1, (
        f"dedup invariant violated: {pre_count} -> {post_count} (expected +1)"
    )


# ===========================================================================
# PayPal
# ===========================================================================


@pytest.mark.asyncio
async def test_paypal_webhook_sale_completed_inserts_revenue_row(
    client, scaffold_cleanup
):
    """Happy PayPal path — PAYMENT.SALE.COMPLETED → revenue row."""
    pid = await _make_project(client, scaffold_cleanup, "wh-paypal-sale")
    await _seed_secret(
        client, pid,
        name="paypal_webhook_secret",
        value=WEBHOOK_SENTINEL_SECRET_99887,
    )

    payload = _build_paypal_sale_completed_event(
        event_id="WH-PP-HAPPY-1", value="42.75", currency="USD"
    )
    resp = await client.post(
        f"/api/webhooks/paypal/{pid}",
        content=payload,
        headers={"X-PayPal-Shared-Secret": WEBHOOK_SENTINEL_SECRET_99887},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["received"] is True
    assert body["deduplicated"] is False
    txn_id = body["transaction_id"]

    list_resp = await client.get(
        "/api/transactions", headers={"X-Project-Id": str(pid)}
    )
    rows = list_resp.json()
    matches = [r for r in rows if r["id"] == txn_id]
    assert len(matches) == 1
    row = matches[0]
    assert row["kind"] == "revenue"
    # $42.75 USD → 4275 cents.
    assert row["amount_minor"] == 4275
    assert row["currency"] == "USD"
    assert row["source"] == "paypal"
    assert row["source_ref"] == "WH-PP-HAPPY-1"
    assert row["category"] == "paypal_payment"


@pytest.mark.asyncio
async def test_paypal_webhook_bad_secret_returns_401(client, scaffold_cleanup):
    """Wrong shared secret → 401 invalid_signature, no row written."""
    pid = await _make_project(client, scaffold_cleanup, "wh-paypal-bad")
    cred_id = await _seed_secret(
        client, pid,
        name="paypal_webhook_secret",
        value=WEBHOOK_SENTINEL_SECRET_99887,
    )

    payload = _build_paypal_sale_completed_event(event_id="WH-PP-BAD-1")
    resp = await client.post(
        f"/api/webhooks/paypal/{pid}",
        content=payload,
        headers={"X-PayPal-Shared-Secret": "this-is-not-the-secret"},
    )
    assert resp.status_code == 401, resp.text
    assert resp.json() == {"detail": "invalid_signature"}

    denials = await _count_denial_audits_for_credential(cred_id)
    assert denials >= 1


@pytest.mark.asyncio
async def test_paypal_webhook_duplicate_event_id_dedupes(
    client, scaffold_cleanup
):
    """Same PayPal event.id replayed → second call deduplicated:true."""
    pid = await _make_project(client, scaffold_cleanup, "wh-paypal-dedup")
    await _seed_secret(
        client, pid,
        name="paypal_webhook_secret",
        value=WEBHOOK_SENTINEL_SECRET_99887,
    )

    pre_list = await client.get(
        "/api/transactions", headers={"X-Project-Id": str(pid)}
    )
    pre_count = len(pre_list.json())

    payload = _build_paypal_sale_completed_event(
        event_id="WH-PP-DEDUP-1", value="10.00"
    )
    headers = {"X-PayPal-Shared-Secret": WEBHOOK_SENTINEL_SECRET_99887}

    r1 = await client.post(f"/api/webhooks/paypal/{pid}", content=payload, headers=headers)
    assert r1.status_code == 200, r1.text
    txn_id_1 = r1.json()["transaction_id"]
    assert r1.json()["deduplicated"] is False

    r2 = await client.post(f"/api/webhooks/paypal/{pid}", content=payload, headers=headers)
    assert r2.status_code == 200, r2.text
    assert r2.json()["deduplicated"] is True
    assert r2.json()["transaction_id"] == txn_id_1

    post_list = await client.get(
        "/api/transactions", headers={"X-Project-Id": str(pid)}
    )
    assert len(post_list.json()) == pre_count + 1


# ===========================================================================
# P&L smoke
# ===========================================================================


# ===========================================================================
# Soft-deleted project → 404 (S1 fix)
# ===========================================================================


@pytest.mark.asyncio
async def test_stripe_webhook_to_soft_deleted_project_returns_404(
    client, scaffold_cleanup
):
    """Stripe webhook to a soft-deleted project must return 404.

    POSITIVE: after DELETE the project no longer accepts webhook deliveries.
    NEGATIVE (locked invariant): status code is 404, not 200 or 401.
    """
    pid = await _make_project(client, scaffold_cleanup, "wh-stripe-softdel")
    await _seed_secret(
        client, pid,
        name="stripe_webhook_secret",
        value=WEBHOOK_SENTINEL_SECRET_99887,
    )

    # Soft-delete the project.
    del_resp = await client.delete(f"/api/projects/{pid}")
    assert del_resp.status_code == 204, del_resp.text

    # Attempt a webhook delivery to the now-deleted project.
    payload = _build_stripe_payment_intent_event(event_id="evt_sd_1", amount=1000)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    sig = _stripe_sign(payload, WEBHOOK_SENTINEL_SECRET_99887, now_ts)
    resp = await client.post(
        f"/api/webhooks/stripe/{pid}",
        content=payload,
        headers={"Stripe-Signature": f"t={now_ts},v1={sig}"},
    )
    # NEGATIVE: must be 404, not 200/401/any-other.
    assert resp.status_code == 404, (
        f"expected 404 for soft-deleted project but got {resp.status_code}: {resp.text}"
    )


@pytest.mark.asyncio
async def test_paypal_webhook_to_soft_deleted_project_returns_404(
    client, scaffold_cleanup
):
    """PayPal webhook to a soft-deleted project must return 404.

    POSITIVE: after DELETE the project no longer accepts webhook deliveries.
    NEGATIVE (locked invariant): status code is 404, not 200 or 401.
    """
    pid = await _make_project(client, scaffold_cleanup, "wh-paypal-softdel")
    await _seed_secret(
        client, pid,
        name="paypal_webhook_secret",
        value=WEBHOOK_SENTINEL_SECRET_99887,
    )

    # Soft-delete the project.
    del_resp = await client.delete(f"/api/projects/{pid}")
    assert del_resp.status_code == 204, del_resp.text

    # Attempt a webhook delivery to the now-deleted project.
    payload = _build_paypal_sale_completed_event(event_id="WH-PP-SD-1", value="5.00")
    resp = await client.post(
        f"/api/webhooks/paypal/{pid}",
        content=payload,
        headers={"X-PayPal-Shared-Secret": WEBHOOK_SENTINEL_SECRET_99887},
    )
    # NEGATIVE: must be 404, not 200/401/any-other.
    assert resp.status_code == 404, (
        f"expected 404 for soft-deleted project but got {resp.status_code}: {resp.text}"
    )


# ===========================================================================
# P&L smoke
# ===========================================================================


@pytest.mark.asyncio
async def test_pl_endpoint_reflects_webhook_inserted_revenue(
    client, scaffold_cleanup
):
    """End-to-end: insert via webhook then GET /pl shows the revenue.

    Wires the entire surface together (router → vault → verifier → insert →
    P&L calculator). Locks AC#5 — P&L reflects webhook-sourced revenue.
    """
    pid = await _make_project(client, scaffold_cleanup, "wh-pl-smoke")
    await _seed_secret(
        client, pid,
        name="stripe_webhook_secret",
        value=WEBHOOK_SENTINEL_SECRET_99887,
    )

    # Two events — one revenue ($100.00) and one refund ($25.00).
    payload_rev = _build_stripe_payment_intent_event(
        event_id="evt_pl_rev_1", amount=10000, currency="usd"
    )
    payload_ref = _build_stripe_charge_refunded_event(
        event_id="evt_pl_ref_1", amount_refunded=2500, currency="usd"
    )
    now_ts = int(datetime.now(timezone.utc).timestamp())
    sig_rev = _stripe_sign(payload_rev, WEBHOOK_SENTINEL_SECRET_99887, now_ts)
    sig_ref = _stripe_sign(payload_ref, WEBHOOK_SENTINEL_SECRET_99887, now_ts)

    r_rev = await client.post(
        f"/api/webhooks/stripe/{pid}",
        content=payload_rev,
        headers={"Stripe-Signature": f"t={now_ts},v1={sig_rev}"},
    )
    assert r_rev.status_code == 200, r_rev.text
    r_ref = await client.post(
        f"/api/webhooks/stripe/{pid}",
        content=payload_ref,
        headers={"Stripe-Signature": f"t={now_ts},v1={sig_ref}"},
    )
    assert r_ref.status_code == 200, r_ref.text

    # Fetch the monthly P&L.
    pl_resp = await client.get(
        f"/api/projects/{pid}/pl?period=monthly",
        headers={"X-Project-Id": str(pid)},
    )
    assert pl_resp.status_code == 200, pl_resp.text
    pl = pl_resp.json()
    # $100 revenue, $25 refund → net $75.
    assert float(pl["revenue"]) == 100.0, pl
    assert float(pl["refund"]) == 25.0, pl
    assert float(pl["net"]) == 75.0, pl
    assert pl["currency"] == "USD"
    # Two transactions counted into the top-level summary (same currency bucket).
    assert pl["transaction_count"] == 2, pl
