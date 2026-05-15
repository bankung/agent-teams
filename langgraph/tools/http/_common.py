"""Shared helpers for http_get + http_post.

Factored out so the host-allowlist + response-handling rules are defined ONCE.
A typo in either tool's halt logic is a real risk; sharing this module makes
the policy the source of truth for both verbs.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlparse

from ..base import ToolResult


# Per design doc §7 + spec #978. The cap is on the response body the LLM sees;
# the audit log (#980) may store the full body separately. 100KB matches the
# shell_run output cap so the LLM has a single mental model for "output size".
RESPONSE_OUTPUT_CAP_BYTES = 100_000

# Spec #978: POST request body cap. 256KB is generous for JSON request payloads
# while keeping a single tool call from monopolizing the upstream connection.
POST_BODY_CAP_BYTES = 256 * 1024

# When we surface a non-2xx response back to the LLM, only the first 1KB of
# the response body is included in error_msg. The remainder is dropped — if
# the LLM needs the full body for debugging, it should re-call the API with
# different params, not stare at multi-KB error noise.
NON_2XX_BODY_EXCERPT_BYTES = 1024


logger = logging.getLogger("tools.http")


def check_host_allowed(
    url: str, allowlist: list[str]
) -> tuple[bool, bool, str | None]:
    """Validate `url`'s host against `allowlist`.

    Returns (allowed, wildcard_used, error_msg).
    - allowed=True, wildcard_used=False → host explicitly listed.
    - allowed=True, wildcard_used=True → '*' present; caller must mark
      retry_safe=False and log a WARNING (this layer just signals, doesn't log
      so we keep the policy decision at the tool entrypoint).
    - allowed=False → error_msg carries the offending host string.

    Empty allowlist = fail-closed (allowed=False) by design (#978 spec).
    """
    parsed = urlparse(url)
    host = parsed.hostname  # lowercased, no port
    if host is None:
        return False, False, "<no-host-in-url>"
    if not allowlist:
        return False, False, host
    if "*" in allowlist:
        return True, True, None
    if host in allowlist:
        return True, False, None
    return False, False, host


def truncate_response_body(body: str, cap: int = RESPONSE_OUTPUT_CAP_BYTES) -> str:
    """Truncate `body` to `cap` UTF-8 bytes.

    On truncation, append a sentinel describing the full size so the LLM /
    audit log can tell "this was cut off" from "the response really was this short".
    The global sandbox post-flight (`apply_output_cap`) applies a second 100KB
    cap with a different sentinel; this http-side cap is the network-layer
    short-circuit so we don't buffer a multi-MB response in memory.
    """
    encoded = body.encode("utf-8")
    if len(encoded) <= cap:
        return body
    sliced = encoded[:cap].decode("utf-8", errors="replace")
    return sliced + f"\n... [response truncated; total {len(encoded)} bytes]"


def measure_body_bytes(body: Any) -> tuple[int, bytes | str | None]:
    """Compute the UTF-8 byte size of a POST body for the cap check.

    Returns (size_bytes, encoded_body). `encoded_body` is the bytes that will
    actually go on the wire (or the str if it's already a string — httpx
    accepts either). None if `body` is None / unset.
    """
    if body is None:
        return 0, None
    if isinstance(body, (dict, list)):
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        return len(encoded), encoded
    if isinstance(body, str):
        return len(body.encode("utf-8")), body
    # Bytes (or other) — fall back to bytes() coercion; let it raise if the
    # type genuinely doesn't fit. The pydantic input schema gates dict|str only,
    # so this branch is defensive.
    raw = bytes(body)
    return len(raw), raw


def build_envelope(
    method: str, url: str, headers: dict[str, str] | None, body: Any
) -> str:
    """JSON-serialize a description of the request that WOULD be sent.

    Used by dry_run mode. Keep the shape simple + grep-friendly so the Kanban
    UI / audit log can pretty-print it without parsing rules.
    """
    body_summary: Any
    if body is None:
        body_summary = None
    elif isinstance(body, (dict, list)):
        size, _ = measure_body_bytes(body)
        body_summary = {"kind": "json", "size_bytes": size}
    elif isinstance(body, str):
        body_summary = {"kind": "text", "size_bytes": len(body.encode("utf-8"))}
    else:
        body_summary = {"kind": type(body).__name__}
    return json.dumps(
        {
            "method": method,
            "url": url,
            "headers": headers or {},
            "body": body_summary,
        },
        ensure_ascii=False,
        indent=2,
    )


def non_2xx_error_msg(status_code: int, body: str) -> str:
    """Format a non-2xx error message: status + first 1KB of body."""
    excerpt = body
    encoded = body.encode("utf-8")
    if len(encoded) > NON_2XX_BODY_EXCERPT_BYTES:
        excerpt = encoded[:NON_2XX_BODY_EXCERPT_BYTES].decode(
            "utf-8", errors="replace"
        ) + f"... [body truncated; total {len(encoded)} bytes]"
    return f"{status_code}: {excerpt}"


def host_not_allowed_result(host: str | None) -> ToolResult:
    """Standard halt for an unlisted host. Same shape for both verbs."""
    return ToolResult(
        success=False,
        error_code="host_not_allowed",
        error_msg=host or "<unknown-host>",
        retry_safe=False,
    )


def warn_wildcard(url: str, method: str) -> None:
    """Structured WARNING when the wildcard '*' allowlist entry is used.

    Hooks into stdlib logging with `extra={...}` so #980's audit layer can
    surface it. The 'event' key is the convention used elsewhere in the
    langgraph stack for log-line grepability.
    """
    logger.warning(
        "host_allowlist wildcard used; retry_safe forced False",
        extra={
            "event": "tools.http.wildcard_host",
            "method": method,
            "url": url,
        },
    )
