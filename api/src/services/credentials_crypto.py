"""Fernet symmetric-encryption helper for the credentials vault (Kanban #1326 M3).

Reads the master key from env `CREDENTIALS_MASTER_KEY` (Fernet-format url-safe
base64-encoded 32-byte key). Generate one with:

    docker compose exec -T api python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

The Fernet instance is cached at module level after first construction so we
don't re-validate the env on every encrypt/decrypt call. The cache invalidates
naturally on process restart (which is the only supported key-rotation path
in v1 — write the new key to .env, restart api, then run a rotation script
that decrypts-with-old + re-encrypts-with-new per row).

Failure modes (loud):
  - Env var missing            → RuntimeError naming the var + the generate command
  - Env var malformed (Fernet) → RuntimeError wrapping the underlying ValueError

main.py calls get_fernet() once during app startup so the failure mode is
"app refuses to start" rather than "first credential request crashes".
"""

from __future__ import annotations

import os
from typing import Final

from cryptography.fernet import Fernet

CREDENTIALS_MASTER_KEY_ENV: Final[str] = "CREDENTIALS_MASTER_KEY"

# Module-level cache. Holds the Fernet instance once the env is validated.
_fernet: Fernet | None = None


def _missing_env_error() -> RuntimeError:
    return RuntimeError(
        f"{CREDENTIALS_MASTER_KEY_ENV} env var is required. Generate a key "
        "with: docker compose exec -T api python -c "
        "'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
    )


def get_fernet() -> Fernet:
    """Return the cached Fernet instance, constructing it on first call.

    Raises RuntimeError loudly if the env var is missing or malformed — call
    this from main.py lifespan so the failure surfaces at app startup, not at
    the first credential request.
    """
    global _fernet
    if _fernet is not None:
        return _fernet

    raw = os.environ.get(CREDENTIALS_MASTER_KEY_ENV)
    if not raw:
        raise _missing_env_error()

    try:
        # Fernet() validates url-safe-base64 + length on init. Pass bytes.
        _fernet = Fernet(raw.encode("ascii"))
    except (ValueError, TypeError) as exc:
        # Malformed key — surface the underlying error verbatim. Common cases:
        # wrong length (must decode to 32 bytes), non-url-safe-base64 chars,
        # whitespace contamination.
        raise RuntimeError(
            f"{CREDENTIALS_MASTER_KEY_ENV} is malformed: {exc!s}. "
            "Expected a Fernet url-safe base64 key (32 bytes after decode). "
            "Generate a fresh one with: docker compose exec -T api python -c "
            "'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
        ) from exc

    return _fernet


def encrypt(plaintext: str) -> bytes:
    """Encrypt a UTF-8 plaintext string to Fernet ciphertext bytes."""
    return get_fernet().encrypt(plaintext.encode("utf-8"))


def decrypt(ciphertext: bytes) -> str:
    """Decrypt Fernet ciphertext bytes to a UTF-8 plaintext string.

    Re-raises InvalidToken if the ciphertext is unreadable with the current
    master key — callers should treat this as either "wrong master key" (the
    operator rotated without running the rotation script) or "ciphertext
    corrupted in the DB".
    """
    return get_fernet().decrypt(ciphertext).decode("utf-8")
