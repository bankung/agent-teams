"""Pydantic schemas for the bits of Stripe / PayPal payloads we care about
(Kanban #1325 M2).

Both providers add new fields over time; `extra='allow'` keeps us forward-
compatible. We validate ONLY the fields we extract — id, type, amount,
currency, timestamps. Anything else is passed through untouched (and
ignored).

These schemas are NOT the wire surface — the webhook endpoints accept raw
bytes (so the signature verifier can hash exactly what the provider signed)
and only parse JSON after signature checks pass.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# Stripe
# ---------------------------------------------------------------------------


class StripeEvent(BaseModel):
    """Top-level Stripe Event envelope. Only the fields we use are required."""

    model_config = ConfigDict(extra="allow")

    id: str
    type: str
    data: dict[str, Any]
    # `created` is Stripe's UNIX timestamp at the EVENT level. Optional because
    # very old fixtures sometimes omit it; we fall back to the inner object's
    # `created` field when extracting occurred_at.
    created: int | None = None


class StripePaymentIntentObject(BaseModel):
    """Subset of `data.object` for payment_intent.succeeded events."""

    model_config = ConfigDict(extra="allow")

    id: str
    amount: int  # minor units (cents)
    currency: str
    status: str | None = None  # 'succeeded' for payment_intent.succeeded
    created: int | None = None  # UNIX timestamp


class StripeChargeRefundedObject(BaseModel):
    """Subset of `data.object` for charge.refunded events."""

    model_config = ConfigDict(extra="allow")

    id: str
    amount_refunded: int  # minor units (cents)
    currency: str
    payment_intent: str | None = None
    created: int | None = None


# ---------------------------------------------------------------------------
# PayPal
# ---------------------------------------------------------------------------


class PayPalAmount(BaseModel):
    """PayPal amount object — value is a decimal STRING, NOT a number."""

    model_config = ConfigDict(extra="allow")

    # PayPal serializes amount.value as a string like "12.34". We parse it to
    # Decimal at the router layer (Decimal(value) * minor_divisor).
    value: str
    currency: str | None = None
    # Some events use `currency_code` instead of `currency`. The router tries
    # both keys; this field captures whichever PayPal sent.
    currency_code: str | None = None


class PayPalSaleResource(BaseModel):
    """Subset of `resource` for PAYMENT.SALE.COMPLETED + PAYMENT.SALE.REFUNDED."""

    model_config = ConfigDict(extra="allow")

    id: str
    amount: PayPalAmount
    create_time: str | None = None  # ISO-8601


class PayPalEvent(BaseModel):
    """Top-level PayPal Event envelope."""

    model_config = ConfigDict(extra="allow")

    id: str
    event_type: str
    resource: dict[str, Any]
    create_time: str | None = None
