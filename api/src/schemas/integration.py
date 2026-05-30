"""Pydantic schemas for the Integrations settings popup (Kanban #1655).

Response shapes for GET /api/settings/integrations and the PATCH toggle.

SECURITY: no schema here ever carries a secret VALUE — only presence booleans.
`EnvVarStatus.present` is a bool computed live from os.environ in the router;
the env var's value never enters any of these models.

`extra='forbid'` on the PATCH body (parity with other routers) so a typo'd key
422s instead of being silently ignored.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SetupLink(BaseModel):
    """A labelled help link for the setup panel."""

    label: str
    url: str


class IntegrationSetup(BaseModel):
    """Operator-facing setup guidance: ordered steps + reference links."""

    steps: list[str]
    links: list[SetupLink]


class EnvVarStatus(BaseModel):
    """One env var's name + required flag + LIVE presence (never the value)."""

    name: str
    required: bool
    present: bool


class IntegrationRead(BaseModel):
    """One integration row in the GET /api/settings/integrations response.

    `enabled` comes from the DB toggle (defaults False when no row exists).
    `configured` + each `env_vars[].present` are computed LIVE from os.environ.
    No secret value is ever serialized.
    """

    id: str
    label: str
    category: str
    enabled: bool
    configured: bool
    env_vars: list[EnvVarStatus]
    setup: IntegrationSetup  # always present — every registry entry carries setup guidance


class PlatformSecurity(BaseModel):
    """Read-only platform-security summary (Kanban #1658).

    Reflects core crypto state that cannot be toggled — only reported.
    SECURITY: `vault_key_configured` is a PRESENCE BOOLEAN only; the key value
    is never included here (same pattern as EnvVarStatus.present).
    """

    vault_key_configured: bool


class IntegrationListResponse(BaseModel):
    """Top-level GET response — `{ "integrations": [...], "platform_security": {...} }`."""

    integrations: list[IntegrationRead]
    platform_security: PlatformSecurity


class IntegrationToggleRequest(BaseModel):
    """Body for PATCH /api/settings/integrations/{id} — the enable toggle."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
