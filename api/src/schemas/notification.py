"""Pydantic schemas for the push-notification routing layer (Kanban #1224).

Borrows the SHAPE of Hermes' `gateway/delivery.py` DeliveryTarget DSL: a
priority-ordered list of explicit delivery targets with local-file fallback.
Stored as JSONB on `projects.notification_targets` (project-level default) and
`tasks.notification_targets` (per-task override; NULL = inherit project default).

Element-shape validation lives at the API boundary — NO DB CHECK on shape
(mirrors `acceptance_criteria` / `agent_overrides` / `sources` / `tools_config`
precedent). The DB column is plain JSONB; the validator below is the only
gate.

v1 adapter scope: Telegram only. Discord/Slack/WhatsApp adapters are deferred
per #1224 — `kind` Literal will widen as concrete adapters land.

Anti-pattern callout from #1220 deep research (AP1 mismatch): platform-kind is
metadata ON a notification target, NEVER part of a session key. agent-teams
sessions are bound to `project_id` only. A subagent that wants to send to
multiple chats files multiple targets — not multiple sessions.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# Vocabulary for NotificationTarget.kind. v1 = Telegram only; widen as
# adapters land. Keep in lockstep with the adapter dispatch table in
# `src/services/notification_router.py`.
NotificationKind = Literal["telegram"]


class NotificationTarget(BaseModel):
    """One element in a `notification_targets` JSONB array (Kanban #1224).

    Locked v1 shape:
      - `kind`     : Literal — discriminator for the adapter dispatch table.
                     'telegram' only in v1.
      - `chat_id`  : str — adapter-specific addressee. For Telegram, this is
                     the numeric chat id (kept as str for forward-compat with
                     adapters that use non-numeric ids, e.g. Slack channel
                     handles or Discord webhook fragments).
      - `priority` : int >= 1 — resolution order. Lower number = tried first.
                     Multiple targets MAY share a priority; resolution is
                     stable list-order within ties (list-as-stored ordering).
      - `label`    : str — free-form operator-facing tag ("operator-default",
                     "weekend-backup", etc.). Surfaces in audit rows so
                     reviewers can identify WHICH target attempt without
                     decoding chat ids.

    `extra='forbid'` matches the kill/grant-consent deliberate-action posture
    — a typo'd field (`labl` for `label`, `chatid` for `chat_id`) fails 422
    instead of silently persisting under a garbage key.
    """

    model_config = ConfigDict(extra="forbid")

    kind: NotificationKind
    chat_id: str = Field(min_length=1, max_length=200)
    priority: int = Field(ge=1)
    label: str = Field(min_length=1, max_length=200)


# Type alias for the JSONB column shape. Both `projects.notification_targets`
# and `tasks.notification_targets` use this. None at the column boundary =
# inherit (tasks) / no-default (projects); the router service handles
# fallback per AC3.
NotificationTargets = list[NotificationTarget]
