"""Pydantic schemas for the Integrations settings popup.

Response shapes for GET /api/settings/integrations (read-only; no toggle).

SECURITY: no schema here ever carries a secret VALUE — only presence booleans.
`EnvVarStatus.present` is a bool computed live from os.environ in the router;
the env var's value never enters any of these models.
"""

from __future__ import annotations

from pydantic import BaseModel


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

    `configured` + each `env_vars[].present` are computed LIVE from os.environ.
    No secret value is ever serialized. There is no `enabled` field — the popup
    is read-only; runtime enable/disable is controlled via .env only.
    """

    id: str
    label: str
    category: str
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
