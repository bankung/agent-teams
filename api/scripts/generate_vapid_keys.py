"""One-shot script: generate a fresh VAPID keypair (Kanban #955.A).

Run ONCE per deployment, then paste the output into `.env`:

    docker compose exec -T api python -m scripts.generate_vapid_keys

Output shape (stdout — pipe-safe):

    VAPID_PUBLIC_KEY=<base64url ECDH public key>
    VAPID_PRIVATE_KEY=<base64url ECDH private key>
    VAPID_SUBJECT=mailto:you@example.com    # placeholder, edit before saving

NEVER commit the PRIVATE key. Keep it in `.env` (gitignored) only. The
PUBLIC key is also handed to the FE via its own env var in slice 955.C;
both halves must come from the SAME generated pair.

Uses py_vapid (a transitive dep of pywebpush) for the key generation. If
py_vapid is unavailable, falls back to the `cryptography` library directly
(also a transitive dep). Either path produces RFC 8292-compatible keys.
"""

from __future__ import annotations

import base64
import sys


def _b64url_encode(raw: bytes) -> str:
    """RFC 7515 base64url, no padding (`=` stripped)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _generate_via_cryptography() -> tuple[str, str]:
    """Generate a P-256 ECDH keypair using `cryptography` — same curve and
    encoding the Web Push spec requires (RFC 8292 §3.2)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    private_key = ec.generate_private_key(ec.SECP256R1())

    # Private key: 32-byte big-endian scalar (RFC 8292 §3.2 — the "ecPrivateKey
    # OCTET STRING" form). The `private_numbers().private_value` is the
    # bare integer; convert to 32 bytes big-endian.
    private_int = private_key.private_numbers().private_value
    private_bytes = private_int.to_bytes(32, byteorder="big")
    private_b64 = _b64url_encode(private_bytes)

    # Public key: uncompressed point (65 bytes: 0x04 || X || Y). Web Push
    # standard form.
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    public_b64 = _b64url_encode(public_bytes)

    return public_b64, private_b64


def main() -> int:
    try:
        public_b64, private_b64 = _generate_via_cryptography()
    except ImportError as exc:
        print(
            f"ERROR: cryptography import failed: {exc}. "
            "pywebpush is a hard dep of this project; the rebuild script "
            "should have installed cryptography as a transitive dep.",
            file=sys.stderr,
        )
        return 1

    print("# Generated VAPID keypair — paste into .env, NEVER commit the private key.")
    print(f"VAPID_PUBLIC_KEY={public_b64}")
    print(f"VAPID_PRIVATE_KEY={private_b64}")
    print("VAPID_SUBJECT=mailto:you@example.com  # edit to a real mailto: or https:// URL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
