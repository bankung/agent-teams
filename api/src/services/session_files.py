"""Back-compat shim — CTX-1 routers import skeleton helpers from here.

CTX-2 (Kanban #717) consolidated all session filesystem logic into
`services.session_store`. This module re-exports the two skeleton
creators so existing CTX-1 routers keep working without import churn.

Single source of truth lives in `session_store.py`. New code should
import from there directly; this shim stays for the existing CTX-1
imports and may be removed once those callsites migrate.
"""

from __future__ import annotations

from src.services.session_store import (
    create_card_log_skeleton,
    create_session_files as create_session_skeleton,
)

__all__ = ["create_card_log_skeleton", "create_session_skeleton"]
