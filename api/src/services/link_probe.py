"""SSRF-guarded outbound link probe for the resources API (Kanban #1906).

#1309 shipped the resources LINK create path with the outbound HEAD probe
DISABLED (head_status/title always None) because the original implementation
was an unguarded SSRF vector — a malicious `url` could target
169.254.169.254 (cloud metadata), 127.0.0.1 / RFC1918 (internal services), or
redirect there from an initially-innocuous public host. This module
re-implements the probe behind a guard. `resource_verify.py` stays
uninvolved — the router calls `verify_and_tag_link` for syntax validation
only, then `probe_link` here separately, and writes the (head_status, title)
result directly onto the returned tags dict itself (Kanban #1906).

Guard semantics (`is_url_allowed`)
-----------------------------------
  - `urlparse(url)` itself can raise a bare `ValueError` on a malformed URL
    (e.g. unbalanced IPv6 brackets, `http://[::1/`) — wrapped, caught,
    blocked. Then scheme must be http/https.
  - A bare IP-literal hostname (e.g. "127.0.0.1") is checked directly via
    `ipaddress.ip_address` — no DNS round-trip needed.
  - Otherwise the hostname is resolved via `socket.getaddrinfo` (off the
    event loop, via `anyio.to_thread.run_sync`). BOTH the IP-literal fast
    path and every resolved address run through ONE shared predicate,
    `_addr_blocked_reason`: `not ip.is_global` rejects private / loopback /
    link-local / reserved / unspecified ranges; `ip.is_multicast` is checked
    SEPARATELY because `is_global` does NOT reject multicast
    (224.0.0.0/4, ff00::/8 are `is_global=True`); and the IPv6 NAT64
    well-known prefix `64:ff9b::/96` (RFC 6052) is checked separately too —
    a NAT64 address ENCODES an arbitrary IPv4 address in its low 32 bits
    (e.g. `64:ff9b::7f00:1` == 127.0.0.1) and is ALSO `is_global=True`, so
    it can smuggle a private/loopback target past a bare `is_global` check.
    Exotic IPv4 literal forms (hex `0x7f000001`, octal `0177.0.0.1`) are NOT
    special-cased here — `ipaddress.ip_address` rejects them outright (falls
    through to getaddrinfo), and whatever the platform resolver decides they
    resolve to is what gets validated (through the same shared predicate).
    DNS failure -> blocked (probe skipped, not retried). `getaddrinfo` can
    fail two ways: a real resolution failure (`socket.gaierror`, an
    `OSError` subclass) or an IDNA codec failure (`UnicodeError` — NOT an
    `OSError` subclass — e.g. a hostname label >63 chars or a malformed
    IDN); both are caught and treated identically.

Probe semantics (`probe_link`)
-------------------------------
Best-effort, NEVER raises into the caller. A single bounded GET (not a real
HEAD — many servers 405 a HEAD request; the field name `head_status` is
historical from #1309's original design) with `follow_redirects=False` (a
3xx is recorded as-is; the redirect target is NEVER fetched — this is what
defeats the redirect-to-internal SSRF variant) and a bounded overall
timeout. Reads at most `_MAX_PROBE_BYTES` of the body (streamed, so a slow
or huge response can't hang/balloon the request) and extracts `<title>` via
a light regex only when the response is `text/html`.

# shortcut: resolve-then-fetch leaves a DNS-rebinding TOCTOU window (the
# guard resolves the hostname, then httpx independently re-resolves it to
# make the request — an attacker-controlled DNS server could answer
# differently the second time); acceptable for planning-hint metadata;
# upgrade: pin the resolved IP via a custom httpx transport.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from urllib.parse import urlparse

import anyio.to_thread
import httpx

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = ("http", "https")
_PROBE_TIMEOUT = httpx.Timeout(5.0)
# Bytes read from the response body before we stop streaming (bounds a slow
# or huge response; also caps how much we ever feed to the title regex).
_MAX_PROBE_BYTES = 64 * 1024
# Extracted <title> text is attacker-controlled (arbitrary response body) and
# lands in the link's tags (JSONB) — cap it well below _MAX_PROBE_BYTES.
_MAX_TITLE_CHARS = 300
_TITLE_RE = re.compile(rb"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
# NAT64 well-known prefix (RFC 6052) — see _addr_blocked_reason.
_NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")


def _addr_blocked_reason(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> str | None:
    """Single block-list predicate shared by the IP-literal fast path AND the
    resolved-address loop in `is_url_allowed` (an attacker can supply either
    a literal or a DNS answer — both must be checked identically). Returns a
    reason string when `ip` is blocked, else None.

    `is_global` alone misses two cases:
      - multicast (224.0.0.0/4, ff00::/8) — `is_global=True` for these.
      - the NAT64 well-known prefix `64:ff9b::/96` — also `is_global=True`,
        but a NAT64 address ENCODES an arbitrary IPv4 address in its low 32
        bits (e.g. `64:ff9b::7f00:1` == 127.0.0.1), so it can smuggle a
        private/loopback target past a bare `is_global` check.

    # shortcut: a NAT64 address is rejected OUTRIGHT rather than unwrapped to
    # re-check the embedded IPv4 address — fail-closed is the lean choice for
    # a planning-hint probe; upgrade: unwrap + recheck the embedded IPv4.
    """
    if not ip.is_global:
        return "not globally routable"
    if ip.is_multicast:
        return "multicast"
    if isinstance(ip, ipaddress.IPv6Address) and ip in _NAT64_PREFIX:
        return "NAT64 translation prefix"
    return None


async def is_url_allowed(url: str) -> tuple[bool, str]:
    """SSRF guard verdict for `url`. Returns (allowed, reason). NEVER raises
    — any parse/resolution error is treated as blocked, not propagated.
    """
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        # urlparse itself raises a bare ValueError on some malformed URLs
        # (e.g. unbalanced IPv6 brackets: "http://[::1/") — without this it
        # escapes here and 500s the create endpoint (Kanban #1906 fix).
        return False, f"unparseable url: {exc}"
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return False, f"scheme {parsed.scheme!r} not in {_ALLOWED_SCHEMES}"
    hostname = parsed.hostname
    if not hostname:
        return False, "no hostname in url"

    # Fast path: hostname is already a plain IP literal — no DNS needed.
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        blocked = _addr_blocked_reason(literal)
        if blocked is not None:
            return False, f"IP literal {hostname} is blocked: {blocked}"
        return True, "ip literal is globally routable"

    try:
        infos = await anyio.to_thread.run_sync(
            lambda: socket.getaddrinfo(hostname, None)
        )
    except (OSError, UnicodeError) as exc:
        # UnicodeError (NOT an OSError subclass) is raised by the IDNA codec
        # inside getaddrinfo for malformed IDN hostnames (e.g. a label >63
        # chars) — without this it escapes here and 500s the create endpoint
        # (Kanban #1906 defect fix).
        return False, f"dns resolution failed: {exc}"
    if not infos:
        return False, "dns resolution returned no addresses"

    for info in infos:
        addr = info[4][0]
        try:
            resolved = ipaddress.ip_address(addr)
        except ValueError:
            return False, f"unresolvable address form: {addr!r}"
        blocked = _addr_blocked_reason(resolved)
        if blocked is not None:
            # Fail-closed on a mixed [public, private] getaddrinfo answer —
            # ANY blocked address in the list blocks the whole url (an
            # attacker's DNS server can return both to try to slip past a
            # "first address only" check).
            return False, f"resolved address {addr} is blocked: {blocked}"

    return True, "all resolved addresses are globally routable"


async def probe_link(url: str) -> tuple[int | None, str | None, str | None]:
    """Best-effort probe of `url`. Returns (head_status, title, blocked_reason).

    `blocked_reason` is None whenever a probe attempt was actually made
    (whether it then succeeded or failed at the network layer) — it is only
    set when the SSRF guard rejected the url outright (no request sent). This
    lets the caller distinguish "blocked" from "attempted but failed" from
    "never probed at all" (the last case being simply not calling this
    function, e.g. a direct `verify_and_tag_link(url)` call in a unit test).
    """
    allowed, reason = await is_url_allowed(url)
    if not allowed:
        logger.info("link_probe: blocked (%s): %s", reason, url)
        return None, None, reason

    try:
        async with httpx.AsyncClient(
            follow_redirects=False, timeout=_PROBE_TIMEOUT
        ) as http_client:
            async with http_client.stream("GET", url) as response:
                status = response.status_code
                content_type = (response.headers.get("content-type") or "").lower()
                body = b""
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) >= _MAX_PROBE_BYTES:
                        break
                # The loop above checks the cap AFTER appending, so it can
                # overshoot by up to one chunk — slice so "reads at most
                # _MAX_PROBE_BYTES" (module docstring) is literally true.
                body = body[:_MAX_PROBE_BYTES]
                title = None
                if "text/html" in content_type:
                    match = _TITLE_RE.search(body)
                    if match:
                        title = (
                            match.group(1)
                            .decode("utf-8", errors="replace")
                            .strip()[:_MAX_TITLE_CHARS]
                        )
                return status, title, None
    except Exception as exc:  # best-effort — never raise into the create path
        logger.info("link_probe: probe failed for %s: %s", url, exc)
        return None, None, None
