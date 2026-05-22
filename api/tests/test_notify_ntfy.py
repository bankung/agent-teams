"""Kanban #1192 — send_push unit tests.

Mocked httpx.Client (via test-seam httpx_client arg) — no real HTTP.

Covers:
1. Happy path: ntfy returns 2xx → ok=True, detail='sent'.
2. PUSH_ENABLED not 'true' → ok=False, detail='push_disabled'.
3. PUSH_ENABLED not set → ok=False, detail='push_disabled'.
4. Missing NTFY_TOPIC → ok=False, detail='missing_env_NTFY_TOPIC'.
5. NTFY_ACCESS_TOKEN whitespace-only (treated as unset) → no Auth header sent.
6. HTTP 4xx → ok=False, detail='http_403'.
7. HTTP 5xx → ok=False, detail='http_503'.
8. Network error (httpx.RequestError) → ok=False, detail contains 'request_error'.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.services.notify_ntfy import (
    NTFY_ENV_BASE_URL,
    NTFY_ENV_ENABLED,
    NTFY_ENV_TOPIC,
    NTFY_ENV_ACCESS_TOKEN,
    send_push,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_client(status_code: int = 200, text: str = "") -> MagicMock:
    """Return a mock httpx.Client whose post() returns a response with given status."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    client = MagicMock(spec=httpx.Client)
    client.post.return_value = resp
    return client


def _call(
    message: str = "hello",
    *,
    httpx_client=None,
    **kwargs,
) -> object:
    return send_push(message, httpx_client=httpx_client, **kwargs)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_send_push_happy_path(monkeypatch) -> None:
    """ntfy returns 200 → ok=True, detail='sent'."""
    monkeypatch.setenv(NTFY_ENV_ENABLED, "true")
    monkeypatch.setenv(NTFY_ENV_TOPIC, "test-topic-abc")
    monkeypatch.setenv(NTFY_ENV_BASE_URL, "https://ntfy.sh")

    client = _mock_client(200)
    result = _call(httpx_client=client)

    assert result.ok is True
    assert result.detail == "sent"
    assert result.error is None
    client.post.assert_called_once()
    call_url = client.post.call_args[0][0]
    assert call_url == "https://ntfy.sh/test-topic-abc"


# ---------------------------------------------------------------------------
# PUSH_ENABLED gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env_val,use_delenv", [
    ("false", False),
    ("FALSE", False),
    (None, True),
])
def test_send_push_disabled(monkeypatch, env_val, use_delenv) -> None:
    """When PUSH_ENABLED is not 'true', no HTTP call, ok=False."""
    if use_delenv:
        monkeypatch.delenv(NTFY_ENV_ENABLED, raising=False)
    else:
        monkeypatch.setenv(NTFY_ENV_ENABLED, env_val)
    monkeypatch.setenv(NTFY_ENV_TOPIC, "test-topic-abc")

    client = _mock_client()
    result = _call(httpx_client=client)

    assert result.ok is False
    assert result.detail == "push_disabled"
    client.post.assert_not_called()


# ---------------------------------------------------------------------------
# Missing env vars
# ---------------------------------------------------------------------------


def test_send_push_missing_topic(monkeypatch) -> None:
    """Missing NTFY_TOPIC → ok=False with detail='missing_env_NTFY_TOPIC'."""
    monkeypatch.setenv(NTFY_ENV_ENABLED, "true")
    monkeypatch.delenv(NTFY_ENV_TOPIC, raising=False)

    client = _mock_client()
    result = _call(httpx_client=client)

    assert result.ok is False
    assert result.detail == f"missing_env_{NTFY_ENV_TOPIC}"
    client.post.assert_not_called()


# ---------------------------------------------------------------------------
# NTFY_ACCESS_TOKEN whitespace handling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token_val,desc", [
    ("   ", "whitespace only"),
    ("# only if auth configured", "shell comment artifact"),
])
def test_send_push_token_treated_as_empty_no_auth_header(monkeypatch, token_val, desc) -> None:
    """NTFY_ACCESS_TOKEN that is empty/whitespace/comment-artifact → no Authorization header."""
    monkeypatch.setenv(NTFY_ENV_ENABLED, "true")
    monkeypatch.setenv(NTFY_ENV_TOPIC, "test-topic-abc")
    monkeypatch.setenv(NTFY_ENV_ACCESS_TOKEN, token_val)

    client = _mock_client(200)
    result = _call(httpx_client=client)

    assert result.ok is True, f"expected ok=True for token_val={token_val!r} ({desc})"
    sent_headers = client.post.call_args[1]["headers"]
    assert "Authorization" not in sent_headers, f"expected no Auth header for {desc}"


# ---------------------------------------------------------------------------
# HTTP error responses
# ---------------------------------------------------------------------------


def test_send_push_http_4xx(monkeypatch) -> None:
    """ntfy returns 403 → ok=False, detail='http_403'."""
    monkeypatch.setenv(NTFY_ENV_ENABLED, "true")
    monkeypatch.setenv(NTFY_ENV_TOPIC, "test-topic-abc")
    monkeypatch.delenv(NTFY_ENV_ACCESS_TOKEN, raising=False)

    client = _mock_client(403, "Forbidden")
    result = _call(httpx_client=client)

    assert result.ok is False
    assert result.detail == "http_403"


def test_send_push_http_5xx(monkeypatch) -> None:
    """ntfy returns 503 → ok=False, detail='http_503'."""
    monkeypatch.setenv(NTFY_ENV_ENABLED, "true")
    monkeypatch.setenv(NTFY_ENV_TOPIC, "test-topic-abc")
    monkeypatch.delenv(NTFY_ENV_ACCESS_TOKEN, raising=False)

    client = _mock_client(503, "Service Unavailable")
    result = _call(httpx_client=client)

    assert result.ok is False
    assert result.detail == "http_503"


# ---------------------------------------------------------------------------
# Network error
# ---------------------------------------------------------------------------


def test_send_push_network_error(monkeypatch) -> None:
    """httpx.ConnectError → ok=False, detail contains 'request_error'."""
    monkeypatch.setenv(NTFY_ENV_ENABLED, "true")
    monkeypatch.setenv(NTFY_ENV_TOPIC, "test-topic-abc")
    monkeypatch.delenv(NTFY_ENV_ACCESS_TOKEN, raising=False)

    client = MagicMock(spec=httpx.Client)
    client.post.side_effect = httpx.ConnectError("connection refused")
    result = _call(httpx_client=client)

    assert result.ok is False
    assert "request_error" in result.detail
    assert "ConnectError" in result.detail
