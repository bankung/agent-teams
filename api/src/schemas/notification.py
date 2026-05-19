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

    `extra='forbid'` matches the kill/grant-consent deliberate-action posture
    — a typo'd field fails 422 instead of silently persisting under a garbage key.
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
