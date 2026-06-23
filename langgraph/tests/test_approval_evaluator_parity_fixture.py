"""Cross-evaluator parity tests — shared fixture, langgraph copy (Kanban #2389).

Loads `_fixtures/approval_policy_parity.json` from the repo root (mounted
at /repo in the langgraph container) and asserts that the langgraph copy of
evaluate_policy returns the expected action for every case.

The companion test in `api/tests/test_approval_evaluator_parity.py` loads
the SAME fixture and exercises the api copy.  Both suites must pass together
to confirm the two evaluator copies stay in sync.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import approval_evaluator as lg_eval  # type: ignore[import]  # normal langgraph import

# ---------------------------------------------------------------------------
# Fixture loader — same logic as the api companion test
# ---------------------------------------------------------------------------

# /repo is the bind-mount root inside the langgraph container.
_REPO_ROOT = Path("/repo")
_FIXTURE_PATH = _REPO_ROOT / "_fixtures" / "approval_policy_parity.json"


def _resolve_task_context(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Substitute __NOW_MINUS_1H__ sentinel with a real ISO timestamp."""
    if raw is None:
        return None
    result: dict[str, Any] = {}
    for k, v in raw.items():
        if v == "__NOW_MINUS_1H__":
            ts = datetime.now(timezone.utc) - timedelta(hours=1)
            result[k] = ts.isoformat()
        else:
            result[k] = v
    return result


def _load_cases() -> list[tuple[str, dict, dict, dict | None, str]]:
    with _FIXTURE_PATH.open(encoding="utf-8") as fh:
        cases = json.load(fh)
    out = []
    for case in cases:
        out.append(
            (
                case["name"],
                case["policies"],
                case["question_payload"],
                case.get("task_context"),
                case["expected_action"],
            )
        )
    return out


_CASES = _load_cases()
_IDS = [c[0] for c in _CASES]


# ---------------------------------------------------------------------------
# Parametrized suite
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,policies,question_payload,raw_ctx,expected_action", _CASES, ids=_IDS)
def test_parity_fixture_case(
    name: str,
    policies: dict,
    question_payload: dict,
    raw_ctx: dict | None,
    expected_action: str,
) -> None:
    task_context = _resolve_task_context(raw_ctx)
    action, _, _ = lg_eval.evaluate_policy(question_payload, policies, task_context)
    assert action == expected_action, (
        f"[{name}] expected={expected_action!r} got={action!r}"
    )
