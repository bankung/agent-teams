"""http_get — GET request, tier=NETWORK, host-allowlist gated.

Policy (locked in Kanban #978 spec):
- Host allowlist comes from `InvokeContext.host_allowlist`; empty list = halt.
- Non-2xx → ToolResult(success=False, error_code='http_non_2xx', error_msg='<code>: <body excerpt>').
- 2xx → ToolResult(success=True, output=<body capped at 100KB>).
- Wildcard '*' in allowlist → success but retry_safe forced False + WARNING logged.
- timeout_s: default 30s, max 120s. Timeout → error_code='timeout' with duration_ms set.
- dry_run: skip the actual HTTP call, return a JSON envelope of what would be sent.

Implementation uses `httpx.AsyncClient` (already a direct dep per
langgraph/pyproject.toml). Idempotent verb → retry_safe defaults True for the
non-wildcard happy path.
"""

from __future__ import annotations

import time

import httpx
from pydantic import Field

from ..base import InvokeContext, Tier, Tool, ToolInput, ToolResult
from ._common import (
    build_envelope,
    check_host_allowed,
    host_not_allowed_result,
    non_2xx_error_msg,
    truncate_response_body,
    warn_wildcard,
)


class HttpGetInput(ToolInput):
    url: str = Field(
        ...,
        description=(
            "Fully qualified URL (http:// or https://). The hostname is "
            "matched against the per-project allowlist (projects.tools_config"
            ".http_hosts). Path + query are NOT validated by the tool."
        ),
    )
    headers: dict[str, str] | None = Field(
        default=None,
        description="Optional request headers. Auth headers must be set explicitly by the caller.",
    )
    timeout_s: int = Field(
        default=30,
        ge=1,
        le=120,
        description="Per-call timeout in seconds (1..120; default 30).",
    )
    dry_run: bool = Field(
        default=False,
        description=(
            "When True, return a JSON envelope describing the request that "
            "WOULD be sent, without making the HTTP call."
        ),
    )


class HttpGetTool(Tool):
    name = "http_get"
    description = (
        "Issue a GET request against an allowlisted host. Returns the response "
        "body (truncated to 100KB) on 2xx. Non-2xx responses surface the status "
        "code + first 1KB of body in error_msg. The host (parsed from the URL) "
        "must appear in the per-project http_hosts allowlist; an unlisted host "
        "halts with error_code='host_not_allowed'. Set dry_run=True to preview "
        "the request envelope without making the call."
    )
    tier = Tier.NETWORK
    input_schema = HttpGetInput

    async def _run(
        self, input_obj: ToolInput, context: InvokeContext
    ) -> ToolResult:
        assert isinstance(input_obj, HttpGetInput)

        # 1. Host gate.
        allowed, wildcard, offending_host = check_host_allowed(
            input_obj.url, context.host_allowlist
        )
        if not allowed:
            return host_not_allowed_result(offending_host)
        if wildcard:
            warn_wildcard(input_obj.url, method="GET")

        # 2. Dry-run short-circuit (no httpx call).
        if input_obj.dry_run:
            envelope = build_envelope(
                "GET", input_obj.url, input_obj.headers, body=None
            )
            return ToolResult(
                success=True,
                output=f"Dry-run: http_get\n{envelope}",
                retry_safe=not wildcard,
            )

        # 3. Issue the request. Catch network failures + timeout into typed
        #    ToolResult errors — never raise out of _run.
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=input_obj.timeout_s) as client:
                response = await client.get(
                    input_obj.url, headers=input_obj.headers
                )
        except httpx.TimeoutException:
            return ToolResult(
                success=False,
                error_code="timeout",
                error_msg=f"http_get exceeded {input_obj.timeout_s}s",
                retry_safe=True,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except httpx.HTTPError as exc:
            return ToolResult(
                success=False,
                error_code="network_error",
                error_msg=f"httpx error: {exc!r}",
                retry_safe=True,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        duration_ms = int((time.monotonic() - start) * 1000)
        body_text = response.text

        # 4. Non-2xx → halt with status + excerpt.
        if not (200 <= response.status_code < 300):
            return ToolResult(
                success=False,
                error_code="http_non_2xx",
                error_msg=non_2xx_error_msg(response.status_code, body_text),
                retry_safe=True,
                duration_ms=duration_ms,
            )

        # 5. 2xx happy path. Cap the body to 100KB before handing to the LLM.
        return ToolResult(
            success=True,
            output=truncate_response_body(body_text),
            retry_safe=not wildcard,
            duration_ms=duration_ms,
        )
