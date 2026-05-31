"""Platform settings router — Integrations popup.

Mounted at `/api/settings`. GLOBAL (operator-level) surface — no X-Project-Id
header (parity with /api/teams, /api/dashboard). Integrations are a platform-wide
concept, not per-project.

Endpoints:

  - GET /api/settings/integrations — list every optional integration with its
                                     LIVE env-presence / configured status.
                                     Read-only; there is no toggle endpoint.

SECURITY MODEL:
  - Keys STAY in .env. There is NO key entry/storage via this API.
  - `configured` and each env var's `present` are computed LIVE from os.environ
    at request time — NEVER stored, NEVER returned as a value (presence booleans
    only). The response model (IntegrationRead) physically cannot carry a value.
  - There is no DB-backed enable/disable toggle; runtime control is via .env.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Request

from src.schemas.integration import (
    IntegrationListResponse,
    IntegrationRead,
    PlatformSecurity,
)
from src.middleware.rate_limit import limiter
from src.services.integrations_registry import (
    INTEGRATIONS_REGISTRY,
    env_var_presence,
    is_configured,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


def _build_read(entry) -> IntegrationRead:
    """Assemble one IntegrationRead from a registry entry.

    `configured` + env var `present` flags are computed LIVE (os.environ),
    never persisted.
    """
    return IntegrationRead(
        id=entry["id"],
        label=entry["label"],
        category=entry["category"],
        configured=is_configured(entry),
        env_vars=env_var_presence(entry),
        setup=entry["setup"],
    )


# ---------------------------------------------------------------------------
# GET /api/settings/integrations
# ---------------------------------------------------------------------------


@router.get("/integrations", response_model=IntegrationListResponse)
@limiter.limit("30/minute")
async def list_integrations(
    request: Request,  # required by slowapi key_func
) -> IntegrationListResponse:
    """List every optional integration with its configured status.

    `configured` and each `env_vars[].present` are computed live from
    os.environ — secret values are never read out. No DB query is made.
    """
    integrations = [_build_read(entry) for entry in INTEGRATIONS_REGISTRY]
    platform_security = PlatformSecurity(
        vault_key_configured=bool(os.environ.get("CREDENTIALS_MASTER_KEY", "").strip()),
    )
    return IntegrationListResponse(
        integrations=integrations,
        platform_security=platform_security,
    )
