"""Kanban #1906 — SSRF-guarded link probe unit tests (no real network).

Covers `is_url_allowed` (the guard, tested in isolation) and `probe_link`
(the guard wired to a best-effort GET via httpx/respx). DNS resolution is
ALWAYS monkeypatched where a test needs a specific outcome — never left to
the test host's real resolver — so results are deterministic across CI/dev
machines regardless of local network state (e.g. `0x7f000001` is NOT parsed
by `ipaddress.ip_address`, so whether it resolves at all depends on the
platform libc; monkeypatching removes that variable entirely).
"""

from __future__ import annotations

import socket

import httpx
import pytest
import respx

from src.services import link_probe


def _fake_getaddrinfo_public(*_args, **_kwargs):
    """Resolves ANY hostname to a single globally-routable address."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


def _fake_getaddrinfo_hex_literal(host, *_args, **_kwargs):
    """Resolves the hex-loopback test host to 127.0.0.1; passes through
    anything else unchanged (only the hex case in the parametrize below
    actually reaches getaddrinfo — see is_url_allowed's literal fast path)."""
    resolved = "127.0.0.1" if host == "0x7f000001" else host
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (resolved, 0))]


def _raise_gaierror(*_args, **_kwargs):
    raise socket.gaierror("Name or service not known")


def _raise_unicode_error(*_args, **_kwargs):
    raise UnicodeError("label too long")


# ---------------------------------------------------------------------------
# is_url_allowed — guard unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://127.0.0.1/admin",  # loopback
        "http://10.0.0.1/",  # RFC1918 private
        "http://0x7f000001/",  # hex-encoded loopback literal
    ],
)
async def test_is_url_allowed_blocks_malicious_ips(url, monkeypatch) -> None:
    # Only the hex case reaches getaddrinfo (the other three are plain IP
    # literals caught by the fast path before any DNS call) — harmless to
    # apply the patch uniformly across all four parametrize rows.
    monkeypatch.setattr(link_probe.socket, "getaddrinfo", _fake_getaddrinfo_hex_literal)
    allowed, reason = await link_probe.is_url_allowed(url)
    assert allowed is False, reason
    assert "not globally routable" in reason, reason


@pytest.mark.asyncio
async def test_is_url_allowed_ftp_scheme_blocked() -> None:
    allowed, reason = await link_probe.is_url_allowed("ftp://example.com/x")
    assert allowed is False
    assert "scheme" in reason, reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raiser",
    [
        _raise_gaierror,  # real DNS failure — an OSError subclass
        _raise_unicode_error,  # IDNA codec failure (e.g. label >63 chars /
        # malformed IDN) — NOT an OSError subclass; previously escaped
        # is_url_allowed's except clause and 500'd the create endpoint
        # (Kanban #1906 defect fix).
    ],
)
async def test_is_url_allowed_dns_failure_blocks(raiser, monkeypatch) -> None:
    monkeypatch.setattr(link_probe.socket, "getaddrinfo", raiser)
    allowed, reason = await link_probe.is_url_allowed("http://nonexistent.invalid/")
    assert allowed is False
    assert "dns" in reason.lower() or "resolution" in reason.lower(), reason


@pytest.mark.asyncio
async def test_is_url_allowed_public_host_allowed(monkeypatch) -> None:
    """POSITIVE guard-level anchor: a resolvable public host passes. Kept as
    its own isolated (respx-free) test so a guard regression fails here with
    a direct message instead of surfacing only as a confusing probe_link
    symptom in the tests below."""
    monkeypatch.setattr(link_probe.socket, "getaddrinfo", _fake_getaddrinfo_public)
    allowed, reason = await link_probe.is_url_allowed("https://public.example.test/page")
    assert allowed is True, reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url,expect_reason_substring",
    [
        # Unbalanced IPv6 bracket — urlparse() itself raises a bare
        # ValueError ("Invalid IPv6 URL") here; must be caught, never
        # propagate (Kanban #1906 defect fix).
        ("http://[::1/", "unparseable"),
        # Bracketed-but-invalid IPv6 literal — urlparse() ALSO raises here
        # (confirmed live: "'gh' does not appear to be an IPv4 or IPv6
        # address") — same fix covers both malformed-bracket shapes.
        ("http://[gh]/", "unparseable"),
        # IPv4 multicast — is_global=True, so `is_multicast` must be checked
        # separately (Kanban #1906 hardening).
        ("http://224.0.0.1/", "multicast"),
        # IPv6 multicast (link-local scope) — same is_global=True gap.
        ("http://[ff02::1]/", "multicast"),
        # NAT64 well-known prefix encoding 127.0.0.1 in its low 32 bits —
        # is_global=True for the NAT64 address itself; must be rejected
        # outright (Kanban #1906 hardening).
        ("http://[64:ff9b::7f00:1]/", "NAT64"),
    ],
)
async def test_is_url_allowed_blocks_edge_cases(url, expect_reason_substring) -> None:
    allowed, reason = await link_probe.is_url_allowed(url)
    assert allowed is False, reason
    assert expect_reason_substring.lower() in reason.lower(), reason


@pytest.mark.asyncio
async def test_is_url_allowed_mixed_dns_answer_fails_closed(monkeypatch) -> None:
    """A getaddrinfo answer carrying BOTH a public and a private address
    must block the whole url — fail-closed on the first blocked address
    found, regardless of its position in the list (an attacker's DNS server
    could return a mixed list to try to slip a private target past a naive
    first-address-only check)."""
    def _mixed(*_args, **_kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0)),
        ]

    monkeypatch.setattr(link_probe.socket, "getaddrinfo", _mixed)
    allowed, reason = await link_probe.is_url_allowed("http://multi.example.test/")
    assert allowed is False
    assert "not globally routable" in reason, reason


# ---------------------------------------------------------------------------
# probe_link — guard blocks -> no network call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url,use_dns_failure",
    [
        ("http://127.0.0.1/admin", False),
        ("http://nonexistent.invalid/", True),
    ],
)
async def test_probe_link_blocked_makes_no_request(url, use_dns_failure, monkeypatch) -> None:
    if use_dns_failure:
        monkeypatch.setattr(link_probe.socket, "getaddrinfo", _raise_gaierror)

    with respx.mock(assert_all_called=False):
        # Zero routes registered — ANY httpx request inside this block raises,
        # proving the guard short-circuits before probe_link ever touches the
        # network (Kanban #1906 AC1's "no request" requirement).
        status, title, reason = await link_probe.probe_link(url)

    assert status is None
    assert title is None
    assert reason is not None


# ---------------------------------------------------------------------------
# probe_link — redirect-to-internal is recorded, never followed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_link_redirect_to_internal_not_followed(monkeypatch) -> None:
    """The guard passes the OUTER public host; follow_redirects=False means
    the 169.254.x Location header is recorded as head_status verbatim but the
    redirect target is NEVER fetched — this is what defeats the
    redirect-to-internal SSRF variant."""
    monkeypatch.setattr(link_probe.socket, "getaddrinfo", _fake_getaddrinfo_public)
    url = "https://public.example.test/redirect"

    with respx.mock(assert_all_called=True) as router:
        route = router.get(url).mock(
            return_value=httpx.Response(
                301,
                headers={"Location": "http://169.254.169.254/latest/meta-data/"},
            )
        )
        status, title, reason = await link_probe.probe_link(url)

    assert status == 301
    assert title is None
    assert reason is None
    # NEGATIVE (the lock): exactly one request — the redirect was NOT followed.
    assert route.call_count == 1


# ---------------------------------------------------------------------------
# probe_link — public URL happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_link_public_happy_path(monkeypatch) -> None:
    monkeypatch.setattr(link_probe.socket, "getaddrinfo", _fake_getaddrinfo_public)
    url = "https://public.example.test/page"
    html = b"<html><head><title>the title</title></head><body>hi</body></html>"

    with respx.mock(assert_all_called=True) as router:
        router.get(url).mock(
            return_value=httpx.Response(
                200, content=html, headers={"content-type": "text/html; charset=utf-8"}
            )
        )
        status, title, reason = await link_probe.probe_link(url)

    assert status == 200
    assert title == "the title"
    assert reason is None
