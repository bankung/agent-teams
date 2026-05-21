"""Pure webhook signature verification helpers (Kanban #1325 M2).

Two functions — one per provider — verify the inbound signature against the
raw payload bytes + a shared secret. Both raise `WebhookSignatureError` with
a `detail` string identifying which check failed; the router maps that to a
401 response. The secret itself is NEVER included in the detail string or any
log line touching this module.

Stripe path: full HMAC-SHA256 over `{t}.{payload}` against the `v1=` scheme
of the `Stripe-Signature` header. Replay protection via a tolerance window
(default 5 minutes — matches the Stripe SDK default).

PayPal path: shared-secret HEADER compare (NOT the full PayPal cert-chain
verification). This is the lean v1 path — the operator configures PayPal to
forward a private header value on every webhook delivery, and we compare it
against the vaulted secret with constant-time equality. Tradeoff: the full
PayPal cert chain is more secure (PayPal signs deliveries with their own
key + we verify via their public cert), but the integration surface is
significantly larger (cert fetch + cache + rotation). Documented as deferred
in the router. Kanban #1325 acceptance explicitly defers full cert-chain
verification.

No DB I/O. No logging of secrets / signatures / payload contents. Tests in
api/tests/test_webhook_verifiers.py cover the failure surface.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Final

logger = logging.getLogger(__name__)


# Stripe signature scheme — v1 is HMAC-SHA256 over "{t}.{payload}". Other
# schemes (v0 etc.) are present in the header but ignored by this verifier
# (Stripe rotates them rarely; v1 is the documented stable production scheme).
_STRIPE_V1_SCHEME: Final[str] = "v1"
_STRIPE_T_KEY: Final[str] = "t"

# Tolerance default — matches Stripe SDK behavior (300s). Configurable via
# the kwarg on `verify_stripe_signature` so tests can pass `tolerance_seconds=0`
# to assert the replay-window check fires.
_DEFAULT_STRIPE_TOLERANCE_SECONDS: Final[int] = 300


class WebhookSignatureError(Exception):
    """Raised when an inbound webhook signature is missing, malformed, or wrong.

    The `detail` attribute identifies WHICH check failed without leaking the
    secret. The router translates this to HTTP 401 + a static
    "invalid_signature" body so the client cannot use the detail as an oracle
    for brute-forcing.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _parse_stripe_signature_header(header: str) -> tuple[int, list[str]]:
    """Return (timestamp_int, list_of_v1_signatures) from a Stripe-Signature header.

    Header format: `t=<unix_ts>,v1=<hex_sha256>[,v0=<other>][,...]`. Multiple
    `v1=` entries may appear (Stripe rotates secrets via dual-signing during
    rollover); we return all of them.

    Raises WebhookSignatureError on:
      - missing/empty header
      - missing `t=` element
      - non-integer `t=` value
      - missing any `v1=` element
    """
    if not header:
        raise WebhookSignatureError("missing_signature_header")

    parts = [p.strip() for p in header.split(",") if p.strip()]
    if not parts:
        raise WebhookSignatureError("malformed_signature_header")

    timestamp: int | None = None
    v1_sigs: list[str] = []
    for part in parts:
        if "=" not in part:
            # Malformed element — skip silently per Stripe's permissive parser,
            # but require at least one t= and one v1= overall (checked below).
            continue
        key, _, value = part.partition("=")
        key = key.strip()
        value = value.strip()
        if key == _STRIPE_T_KEY:
            try:
                timestamp = int(value)
            except ValueError as exc:
                raise WebhookSignatureError("malformed_timestamp") from exc
        elif key == _STRIPE_V1_SCHEME:
            v1_sigs.append(value)

    if timestamp is None:
        raise WebhookSignatureError("missing_timestamp")
    if not v1_sigs:
        raise WebhookSignatureError("missing_v1_signature")

    return timestamp, v1_sigs


def verify_stripe_signature(
    payload_bytes: bytes,
    signature_header: str,
    secret: str,
    *,
    tolerance_seconds: int = _DEFAULT_STRIPE_TOLERANCE_SECONDS,
    now: datetime | None = None,
) -> None:
    """Verify a Stripe-Signature header against the raw payload + secret.

    Stripe signs `f"{t}.{payload}"` with HMAC-SHA256(secret). The header
    embeds `t=<ts>` and one-or-more `v1=<hex>` entries. We accept the request
    if ANY `v1=` matches (handles secret-rotation overlap).

    Replay guard: reject if `|now - t| > tolerance_seconds`. Default 300s
    matches the Stripe SDK. Tests pass `tolerance_seconds=0` to force the
    check to fire on aged-fixture timestamps.

    Args:
        payload_bytes: raw request body bytes (read BEFORE parsing JSON).
        signature_header: the `Stripe-Signature` header value as received.
        secret: the operator's webhook signing secret (Fernet-vault-decrypted).
        tolerance_seconds: replay tolerance window in seconds.
        now: injectable for tests; defaults to UTC now.

    Raises:
        WebhookSignatureError with one of:
            - "missing_signature_header"
            - "malformed_signature_header"
            - "missing_timestamp"
            - "malformed_timestamp"
            - "missing_v1_signature"
            - "timestamp_outside_tolerance"
            - "signature_mismatch"
    """
    timestamp, v1_sigs = _parse_stripe_signature_header(signature_header)

    if now is None:
        now = datetime.now(timezone.utc)
    age_seconds = abs(int(now.timestamp()) - timestamp)
    if age_seconds > tolerance_seconds:
        raise WebhookSignatureError("timestamp_outside_tolerance")

    # Compute the expected v1 signature once; compare against each header v1.
    signed_payload = f"{timestamp}.".encode("ascii") + payload_bytes
    expected = hmac.new(
        secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()

    # compare_digest on each — short-circuiting via `any()` is safe here because
    # the timing leak only reveals "this header's first v1 matched" which the
    # attacker already controls. The per-call hmac compare is constant-time.
    for sig in v1_sigs:
        if hmac.compare_digest(expected, sig):
            return
    raise WebhookSignatureError("signature_mismatch")


def verify_paypal_shared_secret(
    secret_header: str | None,
    expected_secret: str,
) -> None:
    """Verify the lean PayPal shared-secret header.

    PayPal sends webhook deliveries with an `X-PayPal-Shared-Secret` header
    that the operator configures in the PayPal developer dashboard. We
    compare it against the vaulted secret with constant-time equality. This
    is NOT the full PayPal cert-chain verification (deferred per #1325 scope).

    Args:
        secret_header: the `X-PayPal-Shared-Secret` header value (or None).
        expected_secret: the vaulted secret (Fernet-decrypted).

    Raises:
        WebhookSignatureError with one of:
            - "missing_shared_secret_header"
            - "shared_secret_mismatch"
    """
    if not secret_header:
        raise WebhookSignatureError("missing_shared_secret_header")
    if not hmac.compare_digest(secret_header, expected_secret):
        raise WebhookSignatureError("shared_secret_mismatch")
