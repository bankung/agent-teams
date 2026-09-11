"""Schema-level regression tests for the email search request models
(Kanban #2857 — Outlook max_results cap raised 50 -> 300, Gmail unchanged).

Scope: pure Pydantic validation, no DB / network / router involved.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.schemas.tools_email import GmailSearchRequest, OutlookSearchRequest


@pytest.mark.parametrize(
    "schema_cls, max_results, expect_error",
    [
        # Outlook: cap raised to 300 (#2857).
        (OutlookSearchRequest, 300, False),
        (OutlookSearchRequest, 301, True),
        # Gmail: unchanged at 50 — anti-regression guard proving the Outlook
        # cap raise did NOT bleed into Gmail.
        (GmailSearchRequest, 50, False),
        (GmailSearchRequest, 51, True),
    ],
)
def test_search_request_max_results_cap(schema_cls, max_results, expect_error):
    if expect_error:
        with pytest.raises(ValidationError):
            schema_cls(query="x", max_results=max_results)
    else:
        instance = schema_cls(query="x", max_results=max_results)
        assert instance.max_results == max_results
