"""Pydantic schemas for `push_subscriptions` rows (Kanban #955.A).

Element shapes for the 3 CRUD endpoints under `/api/push/*`:
  - `PushSubscribeRequest` — POST body. Validates the PushSubscription JSON
    handed back by the browser's `PushManager.subscribe()` call.
  - `PushSubscriptionRead` — full row shape returned by POST/GET endpoints.
  - `KindsEnabled` / `PushKeys` — nested shapes referenced by the above.

`extra='forbid'` on every body-shaped class (parity with NotificationTarget
+ HandoffTemplate). Typo'd keys 422.

`kinds_enabled` shape (D3 locked): dict with 4 boolean keys
{hitl_needed, task_done, task_failed, budget_warn}, all defaulting to True.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PushKeys(BaseModel):
    """Public + auth keys from PushSubscription.keys.

    `p256dh` is the URL-safe-base64-encoded ECDH public key (~87 chars after
    padding-strip on most browsers). `auth` is the URL-safe-base64-encoded
    16-byte auth secret. Web Push spec (RFC 8030 + RFC 8291).

    Field caps are generous (256/128) — the spec does not pin an exact length,
    but real-world Chrome/Firefox/Safari values fit well under these.
    """

    model_config = ConfigDict(extra="forbid")

    p256dh: str = Field(min_length=1, max_length=256)
    auth: str = Field(min_length=1, max_length=128)


class KindsEnabled(BaseModel):
    """Per-subscription notification-kind toggles (D3 locked).

    Four booleans, all default True so a freshly-subscribed browser receives
    every event-type until the operator narrows it. `extra='forbid'` so a
    typo from the FE (`task_dones`, `budgetWarn`) fails 422 instead of
    silently persisting under a garbage key.

    Kept in lockstep with the JSONB column default in
    `models/push_subscription.py::_KINDS_ENABLED_DEFAULT` and the
    `server_default` in migration `0046_push_subscriptions`.
    """

    model_config = ConfigDict(extra="forbid")

    hitl_needed: bool = True
    task_done: bool = True
    task_failed: bool = True
    budget_warn: bool = True


class PushSubscribeRequest(BaseModel):
    """Request body for POST /api/push/subscribe.

    The FE collects this shape from the browser's `PushSubscription.toJSON()`
    + optional operator UX hints (`project_id` for scoping, `user_agent` for
    identification in the settings UI).

    Idempotent re-subscribe: when the same `endpoint` is re-POSTed the
    handler does UPDATE-by-endpoint semantics (D5 — ON CONFLICT DO UPDATE).
    """

    model_config = ConfigDict(extra="forbid")

    # `endpoint` is the browser-supplied Push Service URL. We accept it as a
    # plain (long) string rather than HttpUrl — Pydantic's HttpUrl coerces
    # certain inputs (e.g. trailing slash normalization) which would break
    # the byte-equal endpoint dedup on the unique index. The contract is
    # opaque-pass-through, not URL-parsing.
    endpoint: str = Field(min_length=1, max_length=2_000)
    keys: PushKeys
    project_id: int | None = Field(default=None, ge=1)
    user_agent: str | None = Field(default=None, max_length=512)
    kinds_enabled: KindsEnabled | None = None


class PushSubscriptionRead(BaseModel):
    """Full push_subscriptions row as returned by the API.

    `kinds_enabled` typed as `KindsEnabled` (not free dict) — every row in
    the DB has the locked 4-key shape because we always write through the
    Pydantic class. If a hand-edited row somehow drops a key, the response
    boundary will 500 — that's the correct failure mode for a column shape
    we own end-to-end.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int | None
    endpoint: str
    p256dh: str
    auth: str
    kinds_enabled: KindsEnabled
    user_agent: str | None
    status: int
    created_at: datetime
    updated_at: datetime


class PushSubscriptionUpdate(BaseModel):
    """Request body for PATCH /api/push/subscribe/{id} (Kanban #955.B).

    Allows the FE settings UI to toggle individual `kinds_enabled` flags
    without resupplying the full subscription. `exclude_unset=True` PATCH
    semantics: only the supplied fields are written; omitted fields are
    unchanged.

    `extra='forbid'` — typo'd keys 422 (parity with PushSubscribeRequest).

    Updatable fields:
      - `kinds_enabled`: full replacement of the 4-flag dict. The FE sends
        the complete KindsEnabled object (not a merge-patch) to avoid partial
        shape mismatches — the API boundary re-validates the shape via the
        `KindsEnabled` nested model.
      - `project_id`: allows reassigning or clearing (None) the subscription's
        project scope. Optional update path; FE settings UI may expose it.
      - `user_agent`: free-text label update.
    """

    model_config = ConfigDict(extra="forbid")

    kinds_enabled: KindsEnabled | None = None
    project_id: int | None = None
    user_agent: str | None = Field(default=None, max_length=512)
