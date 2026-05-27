"""In-memory OAuth credentials store for email tools (Kanban #1604).

Keyed by (provider, project_id) for multi-provider parallel safety so #1608
(Outlook) can share this module without redefinition.

NO DB persistence this phase — credentials are LOST on container restart.
Intentional Karpathy cut; durability lands in a later phase (alembic table +
Fernet encryption using the existing credentials_crypto module).

Coordination note for #1608 (Outlook):
  The `status()` helper imports `outlook_client.creds_summary` LAZILY — only
  when provider="outlook" is queried. #1604 ships with no outlook_client.py;
  the import sits dormant until #1608 lands the file with a `creds_summary`
  function that returns {email, expires_at}.
"""

from __future__ import annotations

# Map (provider, project_id) -> provider-specific credentials object.
# Value type is intentionally `object` — each provider client owns its own
# credentials shape (google.oauth2.credentials.Credentials for gmail; msal
# token dict for outlook).
_STORE: dict[tuple[str, int], object] = {}


def put(provider: str, project_id: int, creds: object) -> None:
    """Store credentials for (provider, project_id). Overwrites any prior value."""
    _STORE[(provider, project_id)] = creds


def get(provider: str, project_id: int) -> object | None:
    """Return stored credentials or None if absent."""
    return _STORE.get((provider, project_id))


def status(provider: str, project_id: int) -> dict:
    """Return {authenticated, email?, expires_at?} for (provider, project_id).

    Provider-specific projection is delegated to the client module's
    `creds_summary(creds)` helper to avoid coupling token_store to any one
    provider's credential shape.
    """
    creds = _STORE.get((provider, project_id))
    if creds is None:
        return {"authenticated": False, "email": None, "expires_at": None}
    if provider == "gmail":
        from .gmail_client import creds_summary as _summary  # noqa: PLC0415
    elif provider == "outlook":
        # #1608 (Outlook mirror) lands `outlook_client.creds_summary`.
        # Import stays lazy — no hard dep on outlook_client.py existing
        # for #1604 to function.
        from .outlook_client import creds_summary as _summary  # noqa: PLC0415
    else:
        return {"authenticated": True, "email": None, "expires_at": None}
    return {"authenticated": True, **_summary(creds)}
