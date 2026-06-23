"""Unit test: _mark_alert_sent_today evicts prior-day keys.

Covers:
- After seeding _ALERT_SENT with a prior-day key, calling
  _mark_alert_sent_today(..., today, ...) removes the prior-day key.
- The new today key is present after the call.
- Existing today keys for other projects/events are not removed.

Pure unit test — no DB required.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.services import budget_gate
from src.services.budget_gate import _mark_alert_sent_today


@pytest.fixture(autouse=True)
def _clear_cache():
    """Reset module-level alert de-dupe cache between tests (matches existing convention)."""
    budget_gate._ALERT_SENT.clear()
    yield
    budget_gate._ALERT_SENT.clear()


def test_prior_day_key_is_evicted():
    """Prior-day key is removed when _mark_alert_sent_today is called with today."""
    today = date.today()
    yesterday = today - timedelta(days=1)

    # Seed a prior-day entry.
    budget_gate._ALERT_SENT[(1, yesterday, "budget_threshold_80")] = None
    assert (1, yesterday, "budget_threshold_80") in budget_gate._ALERT_SENT

    _mark_alert_sent_today(1, today, "budget_threshold_80")

    # Prior-day key is gone.
    assert (1, yesterday, "budget_threshold_80") not in budget_gate._ALERT_SENT
    # Today's key was inserted.
    assert (1, today, "budget_threshold_80") in budget_gate._ALERT_SENT


def test_today_key_for_other_project_is_kept():
    """Eviction only targets prior-day keys; today's entries for other projects survive."""
    today = date.today()
    yesterday = today - timedelta(days=1)

    # Another project's today entry — must survive.
    budget_gate._ALERT_SENT[(2, today, "budget_threshold_80")] = None
    # A stale yesterday entry.
    budget_gate._ALERT_SENT[(1, yesterday, "budget_threshold_80")] = None

    _mark_alert_sent_today(1, today, "budget_threshold_80")

    # Stale key removed.
    assert (1, yesterday, "budget_threshold_80") not in budget_gate._ALERT_SENT
    # Other project's today key untouched.
    assert (2, today, "budget_threshold_80") in budget_gate._ALERT_SENT
