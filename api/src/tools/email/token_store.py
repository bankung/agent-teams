"""Durable OAuth credentials store for email tools (Kanban #1604 / #1608).

Keyed by (provider, project_id) for multi-provider parallel safety.

DURABILITY (this phase): credentials now PERSIST in the Fernet-encrypted
`email_oauth_tokens` table, so they survive api restart/reload. This replaces
the prior process-local `_STORE` dict whose contents were LOST on every
restart. The DB row is the SOURCE OF TRUTH; an in-memory write-through cache
(`_CACHE`) is kept purely for read perf and to preserve live-object identity
within a single process. A fresh process (or a cleared cache) reloads from the
DB — decrypt + deserialize back to the SAME usable creds object the callers
expect.

Serialization per provider:
  - gmail   → google.oauth2.credentials.Credentials.to_json() (str) on write;
              Credentials.from_authorized_user_info(json.loads(...)) on read.
  - outlook → json.dumps(token_dict) on write; json.loads(...) on read.

Encryption: services/credentials_crypto.py (Fernet, env CREDENTIALS_MASTER_KEY).

Session: put/get/status are async and take an AsyncSession. The api engine is
async-only (no sync driver installed), so the callers — all FastAPI async route
handlers in routers/tools_email.py — `await` these and pass the request-scoped
session via Depends(get_session). This is the minimal caller change required to
add DB persistence; the (provider, project_id, creds) argument contract is
otherwise unchanged.

`status()` delegates the provider-specific projection (email / expires_at) to
the client module's `creds_summary(creds)` helper to avoid coupling token_store
to any one provider's credential shape. The outlook_client import stays lazy.
"""

from __future__ import annotations

import json

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import select

from src.models.email_oauth_token import EmailOAuthToken
from src.services.credentials_crypto import decrypt, encrypt

# Write-through cache: (provider, project_id) -> provider-specific creds object.
# NOT the source of truth — the DB row is. The cache is populated on put() and
# on the first get() after a cache miss; a fresh process starts empty and
# reloads from the DB. Value type is intentionally the live provider object
# (google Credentials / msal token dict) so callers get identical usable creds.
_CACHE: dict[tuple[str, int], object] = {}


def _serialize(provider: str, creds: object) -> str:
    """Serialize provider creds to a plaintext str for encryption.

    gmail: Credentials.to_json() already returns a JSON str.
    outlook: the stored value is a plain JSON-serializable token dict.
    """
    if provider == "gmail":
        # google Credentials.to_json() -> JSON str. Raises if `creds` is not a
        # real Credentials object (intentional — we do not silently persist an
        # unserializable value).
        return creds.to_json()  # type: ignore[attr-defined]
    # outlook (and any future dict-shaped provider).
    return json.dumps(creds)


def _deserialize(provider: str, plaintext: str) -> object:
    """Rebuild the provider creds object from decrypted plaintext.

    gmail: Credentials.from_authorized_user_info(json.loads(...)) -> the SAME
           usable Credentials object the gmail_client expects (token,
           refresh_token, scopes, expiry all preserved).
    outlook: json.loads(...) -> the original token dict.
    """
    data = json.loads(plaintext)
    if provider == "gmail":
        # Late import so token_store has no hard dependency on google-auth for
        # outlook-only usage paths.
        from google.oauth2.credentials import Credentials  # noqa: PLC0415

        return Credentials.from_authorized_user_info(data)
    return data


async def put(
    provider: str, project_id: int, creds: object, session: AsyncSession
) -> None:
    """Persist credentials for (provider, project_id). Overwrites any prior value.

    Serializes -> Fernet-encrypts -> UPSERTs the row, then write-through caches
    the live creds object. The DB write is the durability guarantee; the cache
    keeps the live object available without a decrypt round-trip this process.
    """
    encrypted = encrypt(_serialize(provider, creds))
    stmt = (
        pg_insert(EmailOAuthToken)
        .values(
            provider=provider,
            project_id=project_id,
            encrypted_creds=encrypted,
        )
        .on_conflict_do_update(
            index_elements=[EmailOAuthToken.provider, EmailOAuthToken.project_id],
            set_={"encrypted_creds": encrypted, "updated_at": _now_expr()},
        )
    )
    await session.execute(stmt)
    await session.commit()
    _CACHE[(provider, project_id)] = creds


async def get(
    provider: str, project_id: int, session: AsyncSession
) -> object | None:
    """Return stored credentials or None if absent.

    Cache-first for perf; on a miss, SELECT the row, decrypt + deserialize back
    to the live creds object, populate the cache, and return it. So a fresh
    process / cleared cache still returns usable creds from the DB.
    """
    cached = _CACHE.get((provider, project_id))
    if cached is not None:
        return cached

    row = (
        await session.execute(
            select(EmailOAuthToken.encrypted_creds).where(
                EmailOAuthToken.provider == provider,
                EmailOAuthToken.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None

    creds = _deserialize(provider, decrypt(row))
    _CACHE[(provider, project_id)] = creds
    return creds


async def status(
    provider: str, project_id: int, session: AsyncSession
) -> dict:
    """Return {authenticated, email?, expires_at?} for (provider, project_id).

    Provider-specific projection is delegated to the client module's
    `creds_summary(creds)` helper to avoid coupling token_store to any one
    provider's credential shape.
    """
    creds = await get(provider, project_id, session)
    if creds is None:
        return {"authenticated": False, "email": None, "expires_at": None}
    if provider == "gmail":
        from .gmail_client import creds_summary as _summary  # noqa: PLC0415
    elif provider == "outlook":
        from .outlook_client import creds_summary as _summary  # noqa: PLC0415
    else:
        return {"authenticated": True, "email": None, "expires_at": None}
    return {"authenticated": True, **_summary(creds)}


def _now_expr():
    """now() SQL expression for the UPSERT's updated_at bump.

    Wrapped in a function so the import stays local to where it's used.
    """
    from sqlalchemy.sql import func  # noqa: PLC0415

    return func.now()
