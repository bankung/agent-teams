"""http_get / http_post — Kanban #978.

Covers:
- Happy-path 2xx for GET + POST.
- Host allowlist: unlisted host halts; empty allowlist halts everything;
  wildcard '*' allows but forces retry_safe=False + emits a WARNING log.
- Provider feature-flag: register_http_tools(..., provider='ollama') is a no-op.
- Body cap: POST > 256KB halts with error_code='body_too_large'.
- Non-2xx response surfaces status + body excerpt in error_msg.
- Timeout maps httpx.TimeoutException → error_code='timeout' with duration_ms.
- Dry-run: returns the request envelope WITHOUT calling httpx.

httpx is mocked with `respx` so no real network hits happen. The respx
`mock(assert_all_called=False)` decorator scopes mock state per test.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest
import respx

from tools import GLOBAL_REGISTRY
from tools.base import InvokeContext
from tools.http import (
    HttpGetTool,
    HttpPostTool,
    register_http_tools,
)
from tools.http._common import POST_BODY_CAP_BYTES
from tools.registry import ToolRegistry


# ----------------------------------------------------------------------
# Happy paths
# ----------------------------------------------------------------------


@respx.mock
async def test_http_get_happy_path():
    """GET against an allowlisted host → success=True + response body in output."""
    route = respx.get("https://api.allowed.com/v1/status").mock(
        return_value=httpx.Response(200, text="OK-BODY")
    )
    tool = GLOBAL_REGISTRY.get("http_get")
    ctx = InvokeContext(host_allowlist=["api.allowed.com"])
    result = await tool.invoke(
        {"url": "https://api.allowed.com/v1/status"}, context=ctx
    )
    assert result.success is True, result.error_msg
    assert result.output == "OK-BODY"
    assert result.error_code is None
    # idempotent verb + no wildcard → retry_safe stays True
    assert result.retry_safe is True
    assert route.called


@respx.mock
async def test_http_post_happy_path():
    """POST against an allowlisted host → success=True + response body in output."""
    route = respx.post("https://api.allowed.com/v1/echo").mock(
        return_value=httpx.Response(201, text='{"id":42}')
    )
    tool = GLOBAL_REGISTRY.get("http_post")
    ctx = InvokeContext(host_allowlist=["api.allowed.com"])
    result = await tool.invoke(
        {"url": "https://api.allowed.com/v1/echo", "body": {"x": 1}},
        context=ctx,
    )
    assert result.success is True, result.error_msg
    assert result.output == '{"id":42}'
    # POST is non-idempotent regardless of wildcard
    assert result.retry_safe is False
    assert route.called
    # Content-Type defaulted to application/json for dict bodies
    request = route.calls[0].request
    assert request.headers.get("Content-Type") == "application/json"
    assert json.loads(request.content.decode("utf-8")) == {"x": 1}


# ----------------------------------------------------------------------
# Host allowlist
# ----------------------------------------------------------------------


@respx.mock
async def test_host_not_in_allowlist_halts():
    """GET against an unlisted host → success=False, error_code='host_not_allowed'.

    The httpx mock would match anything, but the tool must SHORT-CIRCUIT
    before issuing the call — we assert the route was NOT called.
    """
    route = respx.get("https://api.evil.com/v1/x").mock(
        return_value=httpx.Response(200, text="should-never-see-this")
    )
    tool = GLOBAL_REGISTRY.get("http_get")
    ctx = InvokeContext(host_allowlist=["api.allowed.com"])
    result = await tool.invoke({"url": "https://api.evil.com/v1/x"}, context=ctx)
    assert result.success is False
    assert result.error_code == "host_not_allowed"
    assert "api.evil.com" in (result.error_msg or "")
    assert not route.called, "Tool issued a real call against an unlisted host."


async def test_empty_allowlist_halts():
    """Empty allowlist = fail-closed; both verbs halt."""
    get_tool = GLOBAL_REGISTRY.get("http_get")
    post_tool = GLOBAL_REGISTRY.get("http_post")
    ctx = InvokeContext(host_allowlist=[])
    get_res = await get_tool.invoke(
        {"url": "https://anywhere.example.com/"}, context=ctx
    )
    post_res = await post_tool.invoke(
        {"url": "https://anywhere.example.com/", "body": {}}, context=ctx
    )
    assert get_res.success is False and get_res.error_code == "host_not_allowed"
    assert post_res.success is False and post_res.error_code == "host_not_allowed"


@respx.mock
async def test_wildcard_allowlist_with_warning(caplog: pytest.LogCaptureFixture):
    """allowlist=['*'] → success=True, retry_safe=False, WARNING log captured."""
    respx.get("https://anywhere.example.com/x").mock(
        return_value=httpx.Response(200, text="W")
    )
    tool = GLOBAL_REGISTRY.get("http_get")
    ctx = InvokeContext(host_allowlist=["*"])
    with caplog.at_level(logging.WARNING, logger="tools.http"):
        result = await tool.invoke(
            {"url": "https://anywhere.example.com/x"}, context=ctx
        )
    assert result.success is True
    assert result.retry_safe is False, (
        "Wildcard '*' MUST force retry_safe=False (caller didn't pin a host)."
    )
    # Structured warning captured
    wildcard_records = [
        r for r in caplog.records
        if r.levelno == logging.WARNING
        and getattr(r, "event", "") == "tools.http.wildcard_host"
    ]
    assert wildcard_records, (
        f"Expected a WARNING with event=tools.http.wildcard_host; "
        f"got {[(r.levelno, r.getMessage()) for r in caplog.records]}"
    )


# ----------------------------------------------------------------------
# Provider feature-flag
# ----------------------------------------------------------------------


def test_ollama_provider_skips_registration():
    """register_http_tools(provider='ollama') must NOT register either http tool.

    We use a fresh ToolRegistry (NOT GLOBAL_REGISTRY) so the test doesn't
    interfere with the module-level singleton state used by other tests.
    """
    fresh = ToolRegistry()
    did_register = register_http_tools(fresh, provider="ollama")
    assert did_register is False
    assert "http_get" not in fresh.list()
    assert "http_post" not in fresh.list()


def test_anthropic_provider_does_register():
    """Sanity counterpart: provider='anthropic' DOES register on a fresh registry."""
    fresh = ToolRegistry()
    did_register = register_http_tools(fresh, provider="anthropic")
    assert did_register is True
    assert "http_get" in fresh.list()
    assert "http_post" in fresh.list()


def test_unset_provider_does_register():
    """Empty/unset provider also registers (default is feature-on)."""
    fresh = ToolRegistry()
    did_register = register_http_tools(fresh, provider="")
    assert did_register is True
    assert {"http_get", "http_post"}.issubset(set(fresh.list()))


def test_tool_classes_remain_importable_under_ollama():
    """Even when ollama is the active provider, the classes themselves must
    import — unit tests instantiate them directly without going through the
    registry."""
    assert HttpGetTool.name == "http_get"
    assert HttpPostTool.name == "http_post"


# ----------------------------------------------------------------------
# Body cap (POST)
# ----------------------------------------------------------------------


@respx.mock
async def test_oversize_body_rejected():
    """POST body > 256KB → error_code='body_too_large'; httpx never called."""
    route = respx.post("https://api.allowed.com/x").mock(
        return_value=httpx.Response(200, text="should-never-see-this")
    )
    tool = GLOBAL_REGISTRY.get("http_post")
    ctx = InvokeContext(host_allowlist=["api.allowed.com"])
    # 257KB of ASCII payload → 257 * 1024 bytes UTF-8
    payload = "x" * (POST_BODY_CAP_BYTES + 1)
    result = await tool.invoke(
        {"url": "https://api.allowed.com/x", "body": payload}, context=ctx
    )
    assert result.success is False
    assert result.error_code == "body_too_large"
    assert "256KB" in (result.error_msg or "") or "262144" in (result.error_msg or "")
    assert not route.called


@respx.mock
async def test_body_exactly_at_cap_is_allowed():
    """Exactly 256KB (cap) should still go through — bound is `>` not `>=`."""
    respx.post("https://api.allowed.com/x").mock(
        return_value=httpx.Response(200, text="OK")
    )
    tool = GLOBAL_REGISTRY.get("http_post")
    ctx = InvokeContext(host_allowlist=["api.allowed.com"])
    payload = "y" * POST_BODY_CAP_BYTES
    result = await tool.invoke(
        {"url": "https://api.allowed.com/x", "body": payload}, context=ctx
    )
    assert result.success is True, result.error_msg


# ----------------------------------------------------------------------
# Non-2xx handling
# ----------------------------------------------------------------------


@respx.mock
async def test_non_2xx_returns_http_error():
    """500 response → error_code='http_non_2xx' with status code + body excerpt."""
    respx.get("https://api.allowed.com/fail").mock(
        return_value=httpx.Response(500, text="internal server explosion")
    )
    tool = GLOBAL_REGISTRY.get("http_get")
    ctx = InvokeContext(host_allowlist=["api.allowed.com"])
    result = await tool.invoke(
        {"url": "https://api.allowed.com/fail"}, context=ctx
    )
    assert result.success is False
    assert result.error_code == "http_non_2xx"
    assert "500" in (result.error_msg or "")
    assert "internal server explosion" in (result.error_msg or "")


@respx.mock
async def test_non_2xx_post_returns_http_error():
    respx.post("https://api.allowed.com/fail").mock(
        return_value=httpx.Response(422, text='{"detail":"bad"}')
    )
    tool = GLOBAL_REGISTRY.get("http_post")
    ctx = InvokeContext(host_allowlist=["api.allowed.com"])
    result = await tool.invoke(
        {"url": "https://api.allowed.com/fail", "body": {}}, context=ctx
    )
    assert result.success is False
    assert result.error_code == "http_non_2xx"
    assert "422" in (result.error_msg or "")
    assert "bad" in (result.error_msg or "")


# ----------------------------------------------------------------------
# Timeout
# ----------------------------------------------------------------------


@respx.mock
async def test_timeout_enforced():
    """Mock raises httpx.TimeoutException → error_code='timeout', duration_ms set."""
    respx.get("https://api.allowed.com/slow").mock(
        side_effect=httpx.TimeoutException("read timeout")
    )
    tool = GLOBAL_REGISTRY.get("http_get")
    ctx = InvokeContext(host_allowlist=["api.allowed.com"])
    result = await tool.invoke(
        {"url": "https://api.allowed.com/slow", "timeout_s": 1}, context=ctx
    )
    assert result.success is False
    assert result.error_code == "timeout"
    assert "1" in (result.error_msg or "")
    # duration_ms is populated even on timeout (caller may want to know how
    # close the call came to completing)
    assert result.duration_ms >= 0


@respx.mock
async def test_post_timeout_enforced():
    respx.post("https://api.allowed.com/slow").mock(
        side_effect=httpx.TimeoutException("read timeout")
    )
    tool = GLOBAL_REGISTRY.get("http_post")
    ctx = InvokeContext(host_allowlist=["api.allowed.com"])
    result = await tool.invoke(
        {"url": "https://api.allowed.com/slow", "body": {}, "timeout_s": 1},
        context=ctx,
    )
    assert result.success is False
    assert result.error_code == "timeout"


# ----------------------------------------------------------------------
# Dry-run
# ----------------------------------------------------------------------


@respx.mock
async def test_dry_run_returns_envelope_without_sending():
    """dry_run=True → success=True, envelope in output, httpx NOT called."""
    get_route = respx.get("https://api.allowed.com/dry").mock(
        return_value=httpx.Response(200, text="should-not-be-seen")
    )
    post_route = respx.post("https://api.allowed.com/dry").mock(
        return_value=httpx.Response(200, text="should-not-be-seen")
    )

    get_tool = GLOBAL_REGISTRY.get("http_get")
    post_tool = GLOBAL_REGISTRY.get("http_post")
    ctx = InvokeContext(host_allowlist=["api.allowed.com"])

    get_res = await get_tool.invoke(
        {"url": "https://api.allowed.com/dry", "dry_run": True}, context=ctx
    )
    post_res = await post_tool.invoke(
        {
            "url": "https://api.allowed.com/dry",
            "body": {"hello": "world"},
            "dry_run": True,
        },
        context=ctx,
    )

    assert get_res.success is True
    assert "Dry-run: http_get" in (get_res.output or "")
    assert "https://api.allowed.com/dry" in (get_res.output or "")

    assert post_res.success is True
    assert "Dry-run: http_post" in (post_res.output or "")
    # Envelope is JSON-y enough to parse the body line
    assert "json" in (post_res.output or "")

    assert not get_route.called, "Dry-run GET must NOT issue the HTTP call."
    assert not post_route.called, "Dry-run POST must NOT issue the HTTP call."


# ----------------------------------------------------------------------
# Tier + schema invariants
# ----------------------------------------------------------------------


def test_tier_is_network():
    assert GLOBAL_REGISTRY.get("http_get").tier.value == "network"
    assert GLOBAL_REGISTRY.get("http_post").tier.value == "network"


def test_input_schema_has_dry_run():
    """Both tools accept dry_run on their input_schema (parallel to fs tools)."""
    assert "dry_run" in HttpGetTool.input_schema.model_fields
    assert "dry_run" in HttpPostTool.input_schema.model_fields


def test_timeout_s_bounded():
    """timeout_s capped at 120s; rejecting > 120 prevents a single tool call
    monopolizing the worker. Lower bound 1s prevents 0/negative timeouts."""
    fields = HttpGetTool.input_schema.model_fields
    timeout_field = fields["timeout_s"]
    metadata = timeout_field.metadata
    # pydantic stores ge/le as constraints in metadata; check le=120, ge=1
    constraints = {type(c).__name__: c for c in metadata}
    # le constraint
    has_le = any(getattr(c, "le", None) == 120 for c in metadata)
    has_ge = any(getattr(c, "ge", None) == 1 for c in metadata)
    assert has_le, f"timeout_s missing le=120; metadata={metadata}"
    assert has_ge, f"timeout_s missing ge=1; metadata={metadata}"


async def test_invalid_url_no_host_halts():
    """A URL string with no parseable host → host_not_allowed (fail-closed)."""
    tool = GLOBAL_REGISTRY.get("http_get")
    ctx = InvokeContext(host_allowlist=["api.allowed.com"])
    result = await tool.invoke({"url": "not-a-real-url"}, context=ctx)
    assert result.success is False
    assert result.error_code == "host_not_allowed"
