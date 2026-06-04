"""Kanban #1115 (2026-05-17, L18 prevention) — request-body size cap.

Defensive belt-and-braces layer on top of Pydantic field constraints. A
malicious / misbehaving client that omits Content-Length still hits the
Pydantic 422 wall on each capped field, but Content-Length-honest clients
short-circuit here with a 413 before the body even gets parsed.

Cap default = 2 MB (~10x the safe sum of all string fields capped at the
Pydantic layer). Override via env `REQUEST_MAX_BYTES` for ops escape hatch.

Hammer-test FINDING #10 reference: a 10MB description + 10000 AC items was
accepted with zero size guard. This middleware fires at the ASGI boundary
before FastAPI starts parsing.
"""

from __future__ import annotations

import os

from fastapi import Request
from fastapi.responses import JSONResponse

# Default 2 MB — generous headroom over the worst-case legal payload
# (title 200 + description 20_000 + halt_reason 1_000 + status_change_reason
# 1_000 + 50 AC items @ 1_000 chars each = ~72 KB; 200 subagent_models entries
# of similar shape add ~30 KB). 2 MB leaves room for future field growth
# while still cutting the 10 MB hammer-test payload at the door.
# Kanban #1123 (L16, 2026-05-17) — halt_reason / status_change_reason caps
# tightened from 2_000 to 1_000 (no impact on the middleware threshold).
_DEFAULT_MAX_BYTES = 2 * 1024 * 1024


def _max_bytes() -> int:
    """Read the cap each call so tests can monkey-patch env without re-import."""
    raw = os.environ.get("REQUEST_MAX_BYTES")
    if raw is None:
        return _DEFAULT_MAX_BYTES
    try:
        parsed = int(raw)
    except ValueError:
        return _DEFAULT_MAX_BYTES
    return parsed if parsed > 0 else _DEFAULT_MAX_BYTES


async def request_size_middleware(request: Request, call_next):
    """Reject requests whose Content-Length header exceeds the cap with 413.

    Note: only checks the header — does NOT defend against chunked transfer
    or missing Content-Length. The Pydantic field caps still catch oversize
    bodies that slip past this check. Belt-and-braces, not single-point.

    SKIP for multipart/form-data: upload routes self-enforce their own cap
    (520 MB) via streaming. Applying the 2 MB middleware cap here would kill
    every upload >2 MB before it reached the route's real guard (#1309 fix #1).
    """
    content_type = (request.headers.get("content-type") or "").lower()
    if content_type.startswith("multipart/form-data"):
        # Let the upload route enforce its own cap via stream_to_disk.
        return await call_next(request)

    cl_header = request.headers.get("content-length")
    if cl_header is not None:
        try:
            content_length = int(cl_header)
        except ValueError:
            # Malformed Content-Length — let the downstream stack handle it.
            return await call_next(request)
        if content_length > _max_bytes():
            return JSONResponse(
                {"detail": f"Request body too large (max {_max_bytes()} bytes)"},
                status_code=413,
            )
    return await call_next(request)
