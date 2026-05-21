"""Unit tests for the Stripe / PayPal signature verifiers (Kanban #1325 M2).

Pure functions — no DB, no fastapi client. Each test computes the signature
in-process so the round-trip is hermetic.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone

import pytest

from src.services.webhook_verifiers import (
    WebhookSignatureError,
    verify_paypal_shared_secret,
    verify_stripe_signature,
)


# Sentinel — also referenced by the secret-not-in-response audit grep in the
# final-reply spec. Keep it stable across the test file.
WEBHOOK_SENTINEL_SECRET_99887 = "WEBHOOK-SENTINEL-SECRET-99887"


def _stripe_sign(payload_bytes: bytes, secret: str, timestamp: int) -> str:
    """Recompute the Stripe v1 signature used in test fixtures."""
    signed = f"{timestamp}.".encode("ascii") + payload_bytes
    return hmac.new(
        secret.encode("utf-8"), signed, hashlib.sha256
    ).hexdigest()


# ---------------------------------------------------------------------------
# Stripe
# ---------------------------------------------------------------------------


def test_verify_stripe_signature_happy():
    """Valid t=<recent>,v1=<hex> matches → returns None (no raise)."""
    payload = b'{"id":"evt_1","type":"payment_intent.succeeded","data":{"object":{"id":"pi_1","amount":1000,"currency":"usd"}}}'
    secret = WEBHOOK_SENTINEL_SECRET_99887
    now = datetime.now(timezone.utc)
    ts = int(now.timestamp())
    sig = _stripe_sign(payload, secret, ts)
    header = f"t={ts},v1={sig}"

    # POSITIVE: no exception raised.
    verify_stripe_signature(payload, header, secret, now=now)


def test_verify_stripe_signature_missing_header_raises():
    """Empty header → missing_signature_header (NEGATIVE — distinct from
    other failure modes; the router maps every WebhookSignatureError to a
    static 401 invalid_signature but the audit log retains the reason)."""
    with pytest.raises(WebhookSignatureError) as exc_info:
        verify_stripe_signature(b"{}", "", "secret")
    assert exc_info.value.detail == "missing_signature_header"


def test_verify_stripe_signature_malformed_header_raises():
    """Header with no t= → missing_timestamp; non-int t → malformed_timestamp."""
    # No `t=` element.
    with pytest.raises(WebhookSignatureError) as exc_info:
        verify_stripe_signature(b"{}", "v1=abcdef", "secret")
    assert exc_info.value.detail == "missing_timestamp"

    # Non-integer t.
    with pytest.raises(WebhookSignatureError) as exc_info:
        verify_stripe_signature(b"{}", "t=notanint,v1=abc", "secret")
    assert exc_info.value.detail == "malformed_timestamp"

    # Has t but no v1.
    with pytest.raises(WebhookSignatureError) as exc_info:
        verify_stripe_signature(b"{}", "t=1700000000", "secret")
    assert exc_info.value.detail == "missing_v1_signature"


def test_verify_stripe_signature_expired_timestamp_raises():
    """Timestamp older than tolerance → timestamp_outside_tolerance.

    POSITIVE control: the same signature passes when `tolerance_seconds` is
    widened. The NEGATIVE is the locked invariant: a 10-minute-old delivery
    with the default 300s tolerance MUST be rejected.
    """
    payload = b'{"id":"evt_old"}'
    secret = WEBHOOK_SENTINEL_SECRET_99887
    now = datetime.now(timezone.utc)
    # Stamp the signature 10 minutes in the past — beyond the 300s default.
    ts_old = int(now.timestamp()) - 600
    sig = _stripe_sign(payload, secret, ts_old)
    header = f"t={ts_old},v1={sig}"

    # NEGATIVE: default tolerance rejects.
    with pytest.raises(WebhookSignatureError) as exc_info:
        verify_stripe_signature(payload, header, secret, now=now)
    assert exc_info.value.detail == "timestamp_outside_tolerance"

    # POSITIVE control: widening tolerance accepts the same payload.
    verify_stripe_signature(
        payload, header, secret, tolerance_seconds=10_000, now=now
    )


def test_verify_stripe_signature_wrong_signature_raises():
    """Right t=, wrong v1= → signature_mismatch."""
    payload = b'{"id":"evt_bad"}'
    now = datetime.now(timezone.utc)
    ts = int(now.timestamp())
    # Sign with the wrong secret, verify with the right one — guarantees mismatch.
    wrong_sig = _stripe_sign(payload, "wrong_secret", ts)
    header = f"t={ts},v1={wrong_sig}"

    with pytest.raises(WebhookSignatureError) as exc_info:
        verify_stripe_signature(
            payload, header, WEBHOOK_SENTINEL_SECRET_99887, now=now
        )
    assert exc_info.value.detail == "signature_mismatch"


def test_verify_stripe_signature_multiple_v1_accepts_any_match():
    """Stripe rotates secrets via dual-signing — two v1= entries, one matches
    → accept (no raise).
    """
    payload = b'{"id":"evt_rot"}'
    now = datetime.now(timezone.utc)
    ts = int(now.timestamp())
    good = _stripe_sign(payload, WEBHOOK_SENTINEL_SECRET_99887, ts)
    bad = _stripe_sign(payload, "different_secret", ts)
    # Bad one first, good one second — confirms we don't short-circuit on the
    # first v1=.
    header = f"t={ts},v1={bad},v1={good}"
    verify_stripe_signature(
        payload, header, WEBHOOK_SENTINEL_SECRET_99887, now=now
    )


# ---------------------------------------------------------------------------
# PayPal
# ---------------------------------------------------------------------------


def test_verify_paypal_shared_secret_happy():
    """Matching header → returns None (no raise)."""
    verify_paypal_shared_secret(
        WEBHOOK_SENTINEL_SECRET_99887, WEBHOOK_SENTINEL_SECRET_99887
    )


def test_verify_paypal_shared_secret_mismatch_raises():
    """Header present but mismatched → shared_secret_mismatch; absent header →
    missing_shared_secret_header. Both NEGATIVES are locked here paired with
    the happy-path POSITIVE above.
    """
    with pytest.raises(WebhookSignatureError) as exc_info:
        verify_paypal_shared_secret("wrong", WEBHOOK_SENTINEL_SECRET_99887)
    assert exc_info.value.detail == "shared_secret_mismatch"

    with pytest.raises(WebhookSignatureError) as exc_info:
        verify_paypal_shared_secret(None, WEBHOOK_SENTINEL_SECRET_99887)
    assert exc_info.value.detail == "missing_shared_secret_header"

    with pytest.raises(WebhookSignatureError) as exc_info:
        verify_paypal_shared_secret("", WEBHOOK_SENTINEL_SECRET_99887)
    assert exc_info.value.detail == "missing_shared_secret_header"
