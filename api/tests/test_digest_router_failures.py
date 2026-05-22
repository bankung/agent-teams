"""Kanban #1217 — POST /api/digest/fire network-failure path tests.

Area 1: verifies that SMTP transport failures (SMTPException, ConnectionError,
socket.timeout, SMTPConnectError) are absorbed as soft failures and returned as
200 ok=False — the endpoint never 500s on send failure.

The router creates GmailSmtpSender() without a smtplib_factory override, so we
patch smtplib.SMTP at the module level.

Anti-hackable-test note: each failure case is parametrized into a single test.
The positive control (ok=True on success) lives in test_digest_router.py.
"""

from __future__ import annotations

import smtplib
import socket
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_smtp_raising(exc: BaseException) -> MagicMock:
    """Return a smtplib.SMTP mock whose __enter__ raises `exc`.

    Simulates a connection-level failure before any SMTP command is issued.
    Note: SMTPAuthenticationError raises on smtp.login() not __enter__ — build
    a separate seam if that case is ever added to this parametrize list.
    """
    smtp = MagicMock()
    smtp.__enter__ = MagicMock(side_effect=exc)
    smtp.__exit__ = MagicMock(return_value=False)
    return smtp


# ---------------------------------------------------------------------------
# Area 1 — SMTP transport failures (parametrized)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("exc,expected_detail_fragment", [
    (smtplib.SMTPException("simulated DATA error"), "smtp_error"),
    (ConnectionError("connection refused"), "network_error"),
    (socket.timeout("timed out"), "network_error"),
    (smtplib.SMTPConnectError(421, b"Service temporarily unavailable"), "smtp_error"),
])
async def test_digest_fire_transport_failure_returns_200_ok_false(
    client, smtp_env, exc, expected_detail_fragment
) -> None:
    """SMTP transport failures are absorbed — always 200, ok=False, detail describes error.

    Anti-hackable check: the smtp_env fixture enables the send gate so ok=True
    IS reachable (proven by test_digest_router.py::test_digest_fire_enabled_with_mock_smtp_returns_ok_true).
    A vacuous ok=False (gate disabled) would not reach this code path.
    """
    smtp_mock = _make_smtp_raising(exc)
    with patch("smtplib.SMTP", return_value=smtp_mock):
        resp = await client.post("/api/digest/fire")

    assert resp.status_code == 200

    body = resp.json()
    assert body["ok"] is False
    assert expected_detail_fragment in body["detail"]
    assert isinstance(body["flag_count"], int)
    assert isinstance(body["subject"], str)
    assert body["recipient"] == "dest@example.com"
