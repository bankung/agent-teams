"""http_post — POST request, tier=NETWORK, host-allowlist gated + 256KB body cap.

Same host/response policies as http_get; additional rules:
- Body size cap: 256KB measured on UTF-8 bytes (json.dumps for dicts/lists).
- retry_safe defaults False (POST is non-idempotent).
- Body accepts dict (auto-JSON-serialized) OR string (raw body verbatim).
"""

from __future__ import annotations

import time

import httpx
from pydantic import Field

from ..base import InvokeContext, Tier, Tool, ToolInput, ToolResult
from ._common import (
    POST_BODY_CAP_BYTES,
    build_envelope,
    check_host_allowed,
    host_not_allowed_result,
    measure_body_bytes,
    non_2xx_error_msg,
    truncate_response_body,
    warn_wildcard,
)


class HttpPostInput(ToolInput):
    url: str = Field(
        ...,
        description=(
            "Fully qualified URL (http:// or https://). Hostname matched "
            "against projects.tools_config.http_hosts."
        ),
    )
    body: dict | list | str = Field(
        ...,
        description=(
            "Request body. dict/list → JSON-serialized + Content-Type defaults "
            "to application/json if the caller didn't set it. str → sent verbatim. "
            "Hard cap: 256KB of UTF-8 bytes on the wire — oversize bodies halt "
            "with error_code='body_too_large'."
        ),
    )
    headers: dict[str, str] | None = Field(
        default=None,
        description="Optional request headers; auth headers must be set explicitly.",
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


class HttpPostTool(Tool):
    name = "http_post"
    description = (
        "Issue a POST request against an allowlisted host. body=dict|list → "
        "JSON-serialized; body=str → sent verbatim. Body capped at 256KB on "
        "the wire (UTF-8 bytes). Returns the response body (truncated to "
        "100KB) on 2xx. Non-2xx responses surface the status code + first "
        "1KB of body in error_msg. retry_safe is False by default (POST is "
        "non-idempotent). Set dry_run=True to preview without making the call."
    )
    tier = Tier.NETWORK
    input_schema = HttpPostInput

    async def _run(
        self, input_obj: ToolInput, context: InvokeContext
    ) -> ToolResult:
        assert isinstance(input_obj, HttpPostInput)

        # 1. Host gate.
        allowed, wildcard, offending_host = check_host_allowed(
            input_obj.url, context.host_allowlist
        )
        if not allowed:
            return host_not_allowed_result(offending_host)
        if wildcard:
            warn_wildcard(input_obj.url, method="POST")

        # 2. Body size cap. We measure ONCE here and reuse `encoded` for both
        #    the dry-run envelope and the actual request so the wire bytes
        #    can't drift from the size we checked.
        try:
            size_bytes, encoded = measure_body_bytes(input_obj.body)
        except Exception as exc:
            return ToolResult(
                success=False,
                error_code="invalid_input",
                error_msg=f"failed to serialize body: {exc!r}",
                retry_safe=False,
            )
        if size_bytes > POST_BODY_CAP_BYTES:
            return ToolResult(
                success=False,
                error_code="body_too_large",
                error_msg=(
                    f"256KB cap exceeded ({size_bytes} bytes > "
                    f"{POST_BODY_CAP_BYTES})"
                ),
                retry_safe=False,
            )

        # 3. Dry-run short-circuit.
        if input_obj.dry_run:
            envelope = build_envelope(
                "POST", input_obj.url, input_obj.headers, body=input_obj.body
            )
            return ToolResult(
                success=True,
                output=f"Dry-run: http_post\n{envelope}",
                retry_safe=False,  # POST stays non-retry-safe even in dry-run
            )

        # 4. Resolve content type for JSON bodies if the caller didn't pass one.
        request_headers = dict(input_obj.headers or {})
        if isinstance(input_obj.body, (dict, list)):
            # Case-insensitive lookup — httpx accepts either casing.
            if not any(k.lower() == "content-type" for k in request_headers):
                request_headers["Content-Type"] = "application/json"

        # 5. Issue the request.
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=input_obj.timeout_s) as client:
                response = await client.post(
                    input_obj.url, content=encoded, headers=request_headers
                )
        except httpx.TimeoutException:
            return ToolResult(
                success=False,
                error_code="timeout",
                error_msg=f"http_post exceeded {input_obj.timeout_s}s",
                retry_safe=False,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except httpx.HTTPError as exc:
            return ToolResult(
                success=False,
                error_code="network_error",
                error_msg=f"httpx error: {exc!r}",
                retry_safe=False,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        duration_ms = int((time.monotonic() - start) * 1000)
        body_text = response.text

        # 6. Non-2xx → halt.
        if not (200 <= response.status_code < 300):
            return ToolResult(
                success=False,
                error_code="http_non_2xx",
                error_msg=non_2xx_error_msg(response.status_code, body_text),
                retry_safe=False,
                duration_ms=duration_ms,
            )

        # 7. 2xx happy path.
        return ToolResult(
            success=True,
            output=truncate_response_body(body_text),
            retry_safe=False,  # POST always non-idempotent
            duration_ms=duration_ms,
        )
