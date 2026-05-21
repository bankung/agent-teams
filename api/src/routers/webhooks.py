"""External payment-webhook ingest router (Kanban #1325 M2).

Two endpoints — one per provider — both scoped to a project via the path:

  POST /api/webhooks/stripe/{project_id}
  POST /api/webhooks/paypal/{project_id}

Both share a common shape:

  1. Resolve project (404 if missing / soft-deleted).
  2. Load the per-project webhook secret credential from the vault. If absent,
     401 + a hint pointing the operator at the credentials POST endpoint.
  3. Decrypt the secret directly via `credentials_crypto.decrypt`. We do NOT
     route through the /use HTTP endpoint — /use is for agent-driven flows
     and gated by approval_policies. Webhook verification is a SYSTEM flow
     (the operator configures the secret once; every inbound delivery uses
     it), and we audit the access via the access_log directly.
  4. Verify the signature. On failure, write a denial audit row, return 401.
  5. Parse JSON, route on event type, translate to a Transaction row.
  6. Insert via `session.add` + `session.commit`. On IntegrityError matching
     the partial unique index, look up the existing row and return its id
     with `deduplicated: true`. Otherwise return the new id with
     `deduplicated: false`.
  7. Write a successful-use audit row.

Idempotency: the `ux_transactions_project_source_ref` partial unique index
(project_id, source, source_ref WHERE source_ref IS NOT NULL) gates dedup
at the DB layer. Stripe / PayPal both retry deliveries on non-2xx, so
duplicate event.id ingestions are the rule, not the exception.

Security:
  - 401 detail is always the static "invalid_signature" or "secret_not_configured"
    — the verifier's internal reason (which check failed) is logged but NEVER
    surfaces on the wire (no oracle for attackers).
  - Secret plaintext is never logged.
  - Cross-project access is implicit: the path's project_id is the only
    project scope; no X-Project-Id header is read (the path IS the scope).
  - Each provider's secret lives at a fixed name in the vault — operator
    POSTs it once via `/api/projects/{id}/credentials` with name='stripe_webhook_secret'
    or 'paypal_webhook_secret'.

Out of scope (deferred):
  - Full PayPal cert-chain verification (we accept the lean shared-secret
    header path for v1).
  - Subscription event handling (only one-time payment + refund this slice).
  - Multi-currency FX (out of scope across M2 entirely).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import RecordStatus
from src.db import get_session
from src.models.credential import CredentialAccessLog, ProjectCredential
from src.models.project import Project
from src.models.transaction import Transaction
from src.schemas.transaction import TransactionRead
from src.services import credentials_crypto
from src.services.pl_calculator import MINOR_DIVISOR_BY_CURRENCY
from src.services.webhook_verifiers import (
    WebhookSignatureError,
    verify_paypal_shared_secret,
    verify_stripe_signature,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# Fixed credential names per provider — the operator writes the webhook secret
# to the vault under this name once, and every inbound delivery looks it up by
# this fixed key. Avoids hardcoding the string in two places (verifier router
# + tests).
WEBHOOK_SECRET_NAMES: Final[dict[str, str]] = {
    "stripe": "stripe_webhook_secret",
    "paypal": "paypal_webhook_secret",
}

# Provider-source identifier persisted as transactions.source.
_SOURCE_STRIPE: Final[str] = "stripe"
_SOURCE_PAYPAL: Final[str] = "paypal"

# Wire detail constants — pinned by tests.
_DETAIL_INVALID_SIGNATURE: Final[str] = "invalid_signature"
_DETAIL_PROJECT_NOT_FOUND_TEMPLATE: Final[str] = "Project id={project_id} not found"
_DETAIL_SECRET_NOT_CONFIGURED_TEMPLATE: Final[str] = (
    "webhook secret not configured for this project — store via POST "
    "/api/projects/{project_id}/credentials with name={secret_name!r}"
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _resolve_project_or_404(
    session: AsyncSession, project_id: int
) -> Project:
    """Look up the project. 404 if missing OR soft-deleted (status != ACTIVE).

    Mirrors ingest.py::_resolve_webhook_project — soft-deleted projects must
    not silently accept webhook deliveries.
    """
    stmt = (
        select(Project)
        .where(Project.id == project_id)
        .where(Project.status == RecordStatus.ACTIVE)
    )
    project = (await session.execute(stmt)).scalar_one_or_none()
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=_DETAIL_PROJECT_NOT_FOUND_TEMPLATE.format(project_id=project_id),
        )
    return project


async def _load_active_secret_credential(
    session: AsyncSession,
    project_id: int,
    secret_name: str,
) -> ProjectCredential:
    """Look up the per-project webhook secret credential by name.

    Returns the active (status=1) row or raises 401 with a hint pointing the
    operator at the credentials POST endpoint. We use 401 (NOT 404) because
    the request itself is unauthenticated when the secret is absent — the
    operator must configure the secret before any delivery can be accepted.
    """
    stmt = (
        select(ProjectCredential)
        .where(ProjectCredential.project_id == project_id)
        .where(ProjectCredential.name == secret_name)
        .where(ProjectCredential.status == RecordStatus.ACTIVE)
    )
    cred = (await session.execute(stmt)).scalar_one_or_none()
    if cred is None:
        raise HTTPException(
            status_code=401,
            detail=_DETAIL_SECRET_NOT_CONFIGURED_TEMPLATE.format(
                project_id=project_id, secret_name=secret_name
            ),
        )
    return cred


async def _write_denial_audit(
    session: AsyncSession,
    credential_id: int,
    reason: str,
) -> None:
    """Append an audit row recording a denied webhook verification.

    Reason is the verifier's internal detail (e.g. "signature_mismatch") so
    the audit trail can distinguish real attacks from misconfiguration. It is
    NOT returned on the wire.
    """
    session.add(
        CredentialAccessLog(
            credential_id=credential_id,
            accessed_by=f"system:webhook (denied={reason})",
            action="use",
        )
    )
    await session.commit()


async def _write_use_audit(
    session: AsyncSession,
    credential_id: int,
) -> None:
    """Stage an audit row for a successful webhook verification.

    IMPORTANT — no session.commit() here. This function only stages rows into
    the current transaction. The caller is responsible for the single final
    commit AFTER both this and _insert_or_dedupe have staged their writes.
    This guarantees that the audit row and the transaction row are either both
    committed or both rolled back (atomic write — Kanban #1377).
    """
    session.add(
        CredentialAccessLog(
            credential_id=credential_id,
            accessed_by="system:webhook",
            action="use",
        )
    )
    # Bump usage counters too — the vault router does this on /use; we mirror
    # the behavior so the access_count metric stays accurate across both
    # invocation surfaces.
    cred = await session.get(ProjectCredential, credential_id)
    if cred is not None:
        cred.access_count = cred.access_count + 1
        # last_accessed_at is server-side now() at commit time. SQLAlchemy
        # accepts a datetime.now(timezone.utc) here (TIMESTAMPTZ column).
        cred.last_accessed_at = datetime.now(timezone.utc)


async def _insert_or_dedupe(
    session: AsyncSession,
    project_id: int,
    source: str,
    source_ref: str,
    *,
    amount_minor: int,
    currency: str,
    kind: str,
    category: str,
    occurred_at: datetime,
    notes: str | None,
) -> tuple[Transaction, bool]:
    """Try to insert the transaction; on the partial-unique-index conflict
    return the existing row + `True` (deduplicated).

    Returns: (transaction_row, deduplicated_flag).
    """
    txn = Transaction(
        project_id=project_id,
        amount_minor=amount_minor,
        currency=currency,
        kind=kind,
        category=category,
        occurred_at=occurred_at,
        source=source,
        source_ref=source_ref,
        notes=notes,
    )
    session.add(txn)
    try:
        # flush() sends the INSERT to the DB within the current transaction,
        # which is sufficient to surface the partial-unique-index violation.
        # We do NOT commit here — the caller issues the single final commit
        # after _write_use_audit has also staged its rows (Kanban #1377 atomic
        # write: audit row + transaction row commit together or roll back together).
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        orig_text = str(exc.orig)
        if "ux_transactions_project_source_ref" in orig_text:
            # Dedup path — fetch the existing row and return its id.
            # The session is clean after rollback; the caller will still stage
            # and commit the audit row for this successful (deduplicated) delivery.
            stmt = (
                select(Transaction)
                .where(Transaction.project_id == project_id)
                .where(Transaction.source == source)
                .where(Transaction.source_ref == source_ref)
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing is None:
                # Defensive — the unique index fired but the row isn't visible.
                # Should never happen in practice. Fall through to 500-ish via
                # the generic re-raise below.
                raise
            return existing, True
        # Some other constraint — surface as 400 so the operator sees the real
        # cause. This shouldn't fire for well-formed event payloads but covers
        # FK violations / kind-CHECK breaches that slip past upstream guards.
        raise HTTPException(
            status_code=400,
            detail=f"Transaction write violates a database constraint: {orig_text[:200]}",
        ) from exc
    # flush() populates txn.id via PostgreSQL RETURNING — no refresh needed.
    return txn, False


def _stripe_timestamp_to_dt(ts: int | None) -> datetime:
    """Convert Stripe's UNIX-second timestamp into a TZ-aware UTC datetime.

    Falls back to UTC-now when the event omits a usable timestamp (rare —
    only seen on very old fixtures).
    """
    if ts is None:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _paypal_amount_to_minor(value_str: str, currency: str) -> int:
    """Convert a PayPal decimal-string amount to BIGINT minor units.

    PayPal serializes amount.value as a decimal STRING (e.g. "12.34"). We use
    Decimal (NEVER float — half-even rounding bugs in ledgers are silent and
    catastrophic) and multiply by the currency's minor divisor.
    """
    divisor = MINOR_DIVISOR_BY_CURRENCY.get(currency.upper(), 100)
    if divisor == 1:
        # Zero-decimal currency — Decimal("12") * 1 = 12 (exact int).
        return int(Decimal(value_str))
    return int((Decimal(value_str) * Decimal(divisor)).quantize(Decimal("1")))


def _paypal_iso_to_dt(s: str | None) -> datetime:
    """Parse PayPal's ISO-8601 create_time to TZ-aware UTC. Falls back to now."""
    if not s:
        return datetime.now(timezone.utc)
    try:
        # PayPal uses "2026-01-01T00:00:00Z" — Python 3.11+ fromisoformat
        # accepts the 'Z' suffix.
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def _ignored_event_response(event_type: str) -> dict[str, Any]:
    """Standard body for unhandled event types — 200 + flag.

    Stripe and PayPal both expect a 2xx for unconfigured event types so they
    stop retrying; we return the event_type back so the operator can grep
    server logs / response body for "ignored_event_type=<x>" and decide whether
    to add a handler.
    """
    return {"received": True, "ignored_event_type": event_type}


# ---------------------------------------------------------------------------
# Stripe
# ---------------------------------------------------------------------------


@router.post("/stripe/{project_id}")
async def stripe_webhook(
    project_id: int,
    request: Request,
    stripe_signature: Annotated[
        str | None, Header(alias="Stripe-Signature")
    ] = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Ingest a Stripe webhook event for the named project.

    Translates `payment_intent.succeeded` → revenue and `charge.refunded` →
    refund. Other event types respond 2xx + an `ignored_event_type` flag.

    Idempotency: the `(project_id, source='stripe', source_ref=event.id)`
    tuple is unique. Replays return 200 + the existing transaction_id +
    `deduplicated: true`.
    """
    project = await _resolve_project_or_404(session, project_id)

    cred = await _load_active_secret_credential(
        session, project_id, WEBHOOK_SECRET_NAMES["stripe"]
    )
    # Capture scalar id before any session operation that might expire `cred`
    # (e.g. rollback inside _insert_or_dedupe on the dedup path). Accessing
    # `cred.id` on an expired mapped object inside an async context triggers
    # SQLAlchemy's MissingGreenlet error (Kanban #1377).
    cred_id = cred.id

    # Raw bytes BEFORE JSON parse — the signature is over the literal payload
    # the provider sent, NOT a re-serialized form.
    payload_bytes = await request.body()

    # Decrypt the secret directly via the crypto helper. We do NOT route through
    # the /use HTTP endpoint because /use is for agent-driven flows gated by
    # approval_policies; webhook verification is a SYSTEM operation.
    secret = credentials_crypto.decrypt(cred.ciphertext)

    try:
        verify_stripe_signature(payload_bytes, stripe_signature or "", secret)
    except WebhookSignatureError as exc:
        # Audit denial with the internal reason (NOT returned on the wire).
        await _write_denial_audit(session, cred_id, exc.detail)
        raise HTTPException(
            status_code=401, detail=_DETAIL_INVALID_SIGNATURE
        ) from exc

    # Signature good — parse JSON.
    try:
        body = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=400, detail="invalid_json_payload"
        ) from exc

    event_type = body.get("type")
    event_id = body.get("id")
    data = body.get("data") or {}
    obj = data.get("object") or {}

    if event_type == "payment_intent.succeeded":
        amount = int(obj.get("amount", 0))
        currency = str(obj.get("currency", "usd")).upper()
        occurred_at = _stripe_timestamp_to_dt(
            obj.get("created") or body.get("created")
        )
        txn, dedup = await _insert_or_dedupe(
            session,
            project_id=project_id,
            source=_SOURCE_STRIPE,
            source_ref=str(event_id),
            amount_minor=amount,
            currency=currency,
            kind="revenue",
            category="stripe_payment",
            occurred_at=occurred_at,
            notes=f"Stripe event {event_id} type=payment_intent.succeeded pi={obj.get('id')}",
        )
        # Audit + transaction insert committed atomically (Kanban #1377).
        # _insert_or_dedupe staged the Transaction (or rolled back on dedup and
        # returned the existing row); _write_use_audit stages the audit row and
        # counter bumps. The single commit here is the only commit in this path.
        await _write_use_audit(session, cred_id)
        await session.commit()
        return {
            "received": True,
            "transaction_id": txn.id,
            "deduplicated": dedup,
        }

    if event_type == "charge.refunded":
        amount = int(obj.get("amount_refunded", 0))
        currency = str(obj.get("currency", "usd")).upper()
        occurred_at = _stripe_timestamp_to_dt(
            body.get("created") or obj.get("created")
        )
        txn, dedup = await _insert_or_dedupe(
            session,
            project_id=project_id,
            source=_SOURCE_STRIPE,
            source_ref=str(event_id),
            amount_minor=amount,
            currency=currency,
            kind="refund",
            category="stripe_refund",
            occurred_at=occurred_at,
            notes=f"Stripe event {event_id} type=charge.refunded charge={obj.get('id')}",
        )
        # Audit + transaction insert committed atomically (Kanban #1377).
        await _write_use_audit(session, cred_id)
        await session.commit()
        return {
            "received": True,
            "transaction_id": txn.id,
            "deduplicated": dedup,
        }

    # Unhandled event type — 200 + ignored flag (Stripe stops retrying).
    # No transaction insert; no audit needed (signature was valid but event is
    # out of scope for this project).
    return _ignored_event_response(str(event_type))


# ---------------------------------------------------------------------------
# PayPal
# ---------------------------------------------------------------------------


@router.post("/paypal/{project_id}")
async def paypal_webhook(
    project_id: int,
    request: Request,
    paypal_shared_secret: Annotated[
        str | None, Header(alias="X-PayPal-Shared-Secret")
    ] = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Ingest a PayPal webhook event for the named project.

    Translates `PAYMENT.SALE.COMPLETED` → revenue and `PAYMENT.SALE.REFUNDED`
    → refund. Other event types respond 2xx + an `ignored_event_type` flag.

    Verification is the lean shared-secret-header path (NOT full PayPal cert-
    chain) — see webhook_verifiers.py module docstring for the tradeoff.

    Idempotency: `(project_id, source='paypal', source_ref=event.id)` is
    unique. Replays return 200 + the existing transaction_id +
    `deduplicated: true`.
    """
    project = await _resolve_project_or_404(session, project_id)

    cred = await _load_active_secret_credential(
        session, project_id, WEBHOOK_SECRET_NAMES["paypal"]
    )
    # Capture scalar id before any session operation that might expire `cred`
    # (e.g. rollback inside _insert_or_dedupe on the dedup path). Mirrors the
    # same guard in the Stripe handler (Kanban #1377).
    cred_id = cred.id

    payload_bytes = await request.body()
    secret = credentials_crypto.decrypt(cred.ciphertext)

    try:
        verify_paypal_shared_secret(paypal_shared_secret, secret)
    except WebhookSignatureError as exc:
        await _write_denial_audit(session, cred_id, exc.detail)
        raise HTTPException(
            status_code=401, detail=_DETAIL_INVALID_SIGNATURE
        ) from exc

    try:
        body = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=400, detail="invalid_json_payload"
        ) from exc

    event_type = body.get("event_type")
    event_id = body.get("id")
    resource = body.get("resource") or {}
    amount_obj = resource.get("amount") or {}

    if event_type in ("PAYMENT.SALE.COMPLETED", "PAYMENT.SALE.REFUNDED"):
        # PayPal uses both `currency` and `currency_code` in different event
        # versions; try the more specific one first.
        currency = str(
            amount_obj.get("currency_code") or amount_obj.get("currency") or "USD"
        ).upper()
        value_str = str(amount_obj.get("value", "0"))
        try:
            amount_minor = _paypal_amount_to_minor(value_str, currency)
        except (ValueError, ArithmeticError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"invalid_amount_value: {value_str!r}",
            ) from exc

        kind = "revenue" if event_type == "PAYMENT.SALE.COMPLETED" else "refund"
        category = (
            "paypal_payment" if kind == "revenue" else "paypal_refund"
        )

        occurred_at = _paypal_iso_to_dt(
            resource.get("create_time") or body.get("create_time")
        )

        txn, dedup = await _insert_or_dedupe(
            session,
            project_id=project_id,
            source=_SOURCE_PAYPAL,
            source_ref=str(event_id),
            amount_minor=amount_minor,
            currency=currency,
            kind=kind,
            category=category,
            occurred_at=occurred_at,
            notes=f"PayPal event {event_id} type={event_type} sale={resource.get('id')}",
        )
        # Audit + transaction insert committed atomically (Kanban #1377).
        await _write_use_audit(session, cred_id)
        await session.commit()
        return {
            "received": True,
            "transaction_id": txn.id,
            "deduplicated": dedup,
        }

    # Unhandled event type — 200 + ignored flag (PayPal stops retrying).
    # No transaction insert; no audit needed (signature was valid but event is
    # out of scope for this project).
    return _ignored_event_response(str(event_type))
