"""PlatformIntegrationSetting ORM model (Kanban #1655).

Mirrors migration `0052_platform_integration_settings`. One row per OPTIONAL
integration that the operator has toggled. The row stores ONLY the enable flag
— secret presence + configured-ness is computed LIVE from os.environ in the
router (NEVER stored, NEVER returned as a value).

Absent row == disabled. The platform runs with zero keys by default; enabling
an integration is an explicit operator action that creates/updates this row.

`id` is a natural TEXT primary key matching an entry in
`services/integrations_registry.INTEGRATIONS_REGISTRY` — there is exactly one
toggle row per integration, so a surrogate key would add nothing.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class PlatformIntegrationSetting(Base):
    """Operator enable/disable toggle for one optional integration (Kanban #1655)."""

    __tablename__ = "platform_integration_settings"

    id: Mapped[str] = mapped_column(Text, primary_key=True)

    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
        default=False,
    )

    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<PlatformIntegrationSetting id={self.id!r} enabled={self.enabled}>"
        )
