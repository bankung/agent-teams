"""Platform settings router — Integrations popup (Kanban #1655).

Mounted at `/api/settings`. GLOBAL (operator-level) surface — no X-Project-Id
header (parity with /api/teams, /api/dashboard). Integrations are a platform-wide
concept, not per-project.

Endpoints:

  - GET   /api/settings/integrations         — list every optional integration
                                                with its DB enable flag + LIVE
                                                env-presence / configured status.
  - PATCH /api/settings/integrations/{id}     — upsert the enable toggle. 404 if
                                                {id} is not a registered integration.

SECURITY MODEL (the load-bearing wall for #1655 Option A):
  - Keys STAY in .env. There is NO key entry/storage via this API.
  - `configured` and each env var's `present` are computed LIVE from os.environ
    at request time — NEVER stored, NEVER returned as a value (presence booleans
    only). The response model (IntegrationRead) physically cannot carry a value.
  - The DB row stores ONLY the operator's enable/disable toggle.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from src.db import get_session
from src.models.integration_setting import PlatformIntegrationSetting
from src.schemas.integration import (
    IntegrationListResponse,
    IntegrationRead,
    IntegrationToggleRequest,
    PlatformSecurity,
)
from src.services.integrations_registry import (
    INTEGRATIONS_REGISTRY,
    env_var_presence,
    get_integration,
    is_configured,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


# Source-text-locked detail string — pinned by test_settings_router.py.
_DETAIL_UNKNOWN_INTEGRATION_TEMPLATE = "Integration {integration_id!r} not found"


async def _enabled_map(session: AsyncSession) -> dict[str, bool]:
    """Return {integration_id: enabled} for every DB toggle row.

    Integrations with no row are simply absent from the map — the caller
    defaults them to disabled (the platform runs with zero keys by default).
    """
    rows = (
        await session.execute(select(PlatformIntegrationSetting))
    ).scalars().all()
    return {row.id: row.enabled for row in rows}


def _build_read(entry, enabled: bool) -> IntegrationRead:
    """Assemble one IntegrationRead from a registry entry + its DB enable flag.

    `configured` + env var `present` flags are computed LIVE here (os.environ),
    never persisted.
    """
    return IntegrationRead(
        id=entry["id"],
        label=entry["label"],
        category=entry["category"],
        enabled=enabled,
        configured=is_configured(entry),
        env_vars=env_var_presence(entry),
        setup=entry["setup"],
    )


# ---------------------------------------------------------------------------
# GET /api/settings/integrations
# ---------------------------------------------------------------------------


@router.get("/integrations", response_model=IntegrationListResponse)
async def list_integrations(
    session: AsyncSession = Depends(get_session),
) -> IntegrationListResponse:
    """List every optional integration with its enable + configured status.

    `enabled` reflects the DB toggle (False when no row exists). `configured`
    and each `env_vars[].present` are computed live from os.environ — secret
    values are never read out.
    """
    enabled_map = await _enabled_map(session)
    integrations = [
        _build_read(entry, enabled_map.get(entry["id"], False))
        for entry in INTEGRATIONS_REGISTRY
    ]
    platform_security = PlatformSecurity(
        vault_key_configured=bool(os.environ.get("CREDENTIALS_MASTER_KEY", "").strip()),
    )
    return IntegrationListResponse(
        integrations=integrations,
        platform_security=platform_security,
    )


# ---------------------------------------------------------------------------
# PATCH /api/settings/integrations/{integration_id}
# ---------------------------------------------------------------------------


@router.patch(
    "/integrations/{integration_id}",
    response_model=IntegrationRead,
)
async def toggle_integration(
    integration_id: str,
    payload: IntegrationToggleRequest,
    session: AsyncSession = Depends(get_session),
) -> IntegrationRead:
    """Upsert the enable toggle for `integration_id`.

    404 if `integration_id` is not a registered integration (checked against the
    static registry BEFORE any DB write — the DB never sees a non-registry id).
    Returns the updated integration with its freshly-computed configured status.
    """
    entry = get_integration(integration_id)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=_DETAIL_UNKNOWN_INTEGRATION_TEMPLATE.format(
                integration_id=integration_id
            ),
        )

    row = await session.get(PlatformIntegrationSetting, integration_id)
    if row is None:
        row = PlatformIntegrationSetting(id=integration_id, enabled=payload.enabled)
        row.updated_at = func.now()
        session.add(row)
    else:
        row.enabled = payload.enabled
        row.updated_at = func.now()

    await session.commit()
    await session.refresh(row)

    return _build_read(entry, row.enabled)
