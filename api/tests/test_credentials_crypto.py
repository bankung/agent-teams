"""Kanban #1326 (M3) — Fernet helper smoke tests.

Three contracts:
  1) Round-trip — encrypt then decrypt returns the original plaintext.
  2) Missing env — get_fernet() raises RuntimeError naming CREDENTIALS_MASTER_KEY.
  3) Malformed env — get_fernet() raises RuntimeError wrapping the underlying
     ValueError (Fernet rejects non-url-safe-base64 / wrong-length keys).

These are dev-sr-backend first-pass smokes. Rigorous edge coverage (boundary
sizes, concurrent encrypt safety, key-rotation drift) is dev-tester's domain.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from src.services import credentials_crypto


@pytest.fixture(autouse=True)
def _clear_fernet_cache_per_test(monkeypatch):
    """Reset the module-level cache before AND after each test so a prior
    test's monkey-patched env doesn't bleed into the next via the cached
    Fernet instance. Direct internal access — test-surface pollution helpers
    are an anti-pattern.
    """
    credentials_crypto._fernet = None
    yield
    credentials_crypto._fernet = None


def test_get_fernet_missing_env_raises_with_command_hint(monkeypatch) -> None:
    monkeypatch.delenv("CREDENTIALS_MASTER_KEY", raising=False)
    with pytest.raises(RuntimeError) as exc_info:
        credentials_crypto.get_fernet()
    msg = str(exc_info.value)
    # Loud + actionable — names the env var + how to generate a key.
    assert "CREDENTIALS_MASTER_KEY" in msg
    assert "Fernet.generate_key" in msg


def test_get_fernet_malformed_env_raises(monkeypatch) -> None:
    monkeypatch.setenv("CREDENTIALS_MASTER_KEY", "definitely-not-a-fernet-key")
    with pytest.raises(RuntimeError) as exc_info:
        credentials_crypto.get_fernet()
    msg = str(exc_info.value)
    assert "CREDENTIALS_MASTER_KEY" in msg
    assert "malformed" in msg


def test_get_fernet_cached_on_second_call(monkeypatch) -> None:
    monkeypatch.setenv("CREDENTIALS_MASTER_KEY", Fernet.generate_key().decode())
    first = credentials_crypto.get_fernet()
    second = credentials_crypto.get_fernet()
    # POSITIVE: same instance returned on second call (cache works).
    # NEGATIVE locked: not None, not a fresh Fernet per call.
    assert first is second
    assert first is not None


def test_encrypt_decrypt_round_trip_preserves_str(monkeypatch) -> None:
    monkeypatch.setenv("CREDENTIALS_MASTER_KEY", Fernet.generate_key().decode())
    plaintext = "sk-secret-api-key-with-unicode-ห้องครัว-and-emoji-🔒"
    ciphertext = credentials_crypto.encrypt(plaintext)
    # POSITIVE: ciphertext bytes are non-empty and do not contain the plaintext.
    assert isinstance(ciphertext, bytes)
    assert len(ciphertext) > 0
    assert plaintext.encode("utf-8") not in ciphertext, (
        "Fernet ciphertext must NOT contain the plaintext bytes"
    )
    # Round-trip back to the original str.
    recovered = credentials_crypto.decrypt(ciphertext)
    assert recovered == plaintext


def test_encrypt_with_different_runs_produces_different_ciphertext(monkeypatch) -> None:
    """Fernet IV randomisation: two encrypts of the same plaintext yield
    different ciphertexts. This is a Fernet invariant; we lock the project's
    dependency on it explicitly so a future swap to a non-randomised primitive
    is caught loudly.
    """
    monkeypatch.setenv("CREDENTIALS_MASTER_KEY", Fernet.generate_key().decode())
    plaintext = "constant-plaintext-value-1234567890"
    ct1 = credentials_crypto.encrypt(plaintext)
    ct2 = credentials_crypto.encrypt(plaintext)
    assert ct1 != ct2, "Fernet must randomise the IV per encrypt call"
    # Both decrypt back to the same value.
    assert credentials_crypto.decrypt(ct1) == plaintext
    assert credentials_crypto.decrypt(ct2) == plaintext
