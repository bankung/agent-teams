"""Contract-smoke: list_decisions skips malformed option entries with a WARNING.

Covers:
- A malformed option entry is skipped (no 500, no inclusion in output).
- A WARNING is logged naming the offending entry.
- Valid entries in the same decision are still returned.

No DB required — patches the session and tests the function directly.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.routers.decisions import list_decisions
from src.schemas.task import DecisionListItem


def _make_row(payload: dict) -> MagicMock:
    row = MagicMock()
    row.id = 1
    row.title = "Test decision"
    row.question_payload = payload
    return row


@pytest.mark.asyncio
async def test_malformed_option_skipped_and_warned(caplog):
    """Malformed option is skipped, valid option survives, WARNING is emitted."""
    # One valid option, one missing the required 'id' field (will fail OptionItem(**opt)).
    payload = {
        "chosen_id": "opt-valid",
        "chosen_at": datetime.now(timezone.utc).isoformat(),
        "options": [
            {"id": "opt-valid", "label": "Good option"},
            {"broken_key": "no id field here"},  # missing required fields → ValidationError
        ],
    }
    row = _make_row(payload)

    mock_session = AsyncMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [row]
    mock_execute = MagicMock()  # the awaited Result is sync: .scalars().all() are not coroutines
    mock_execute.scalars.return_value = mock_scalars
    mock_session.execute.return_value = mock_execute

    with caplog.at_level(logging.WARNING, logger="src.routers.decisions"):
        result = await list_decisions(
            session_project_id=1,
            since=None,
            limit=50,
            offset=0,
            session=mock_session,
        )

    # Valid entry still returned.
    assert len(result) == 1
    item: DecisionListItem = result[0]
    assert item.chosen_id == "opt-valid"
    # Only the valid option survives; malformed one was skipped.
    assert len(item.options) == 1
    assert item.options[0].id == "opt-valid"

    # WARNING was logged naming the offending entry.
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "skipped malformed option entry" in warnings[0].message
    assert "broken_key" in warnings[0].message
