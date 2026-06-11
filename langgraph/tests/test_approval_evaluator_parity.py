"""Parity tests — langgraph vs api approval_evaluator copies (Kanban #2179 A3).

Purpose: turns silent mirror drift into a red suite. If either copy diverges
in evaluate_policy behaviour across any of the 6 predicate keys, one of the
assertions below will fail immediately.

Lead verified parity 2026-06-10.

Module mapping
--------------
- `langgraph_eval` = langgraph/approval_evaluator.py  (normal import from
  the package's own namespace — the langgraph container's sys.path includes
  /repo/langgraph).
- `api_eval` = api/src/services/approval_evaluator.py  (loaded via
  importlib.util.spec_from_file_location so we don't need api on sys.path).

Return-shape mapping
--------------------
Both copies return:
    (action: str, default_answer: str | None, matched_rule_name: str | None)

The decision-relevant field is `action` (index 0); `default_answer` (index 1)
is relevant on auto_approve; `matched_rule_name` (index 2) is attribution only.
All three are compared — they must be identical across both copies.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Load both modules
# ---------------------------------------------------------------------------

# langgraph copy — normal import (available on sys.path in the container).
import approval_evaluator as langgraph_eval  # type: ignore[import]

# api copy — load from absolute path without touching sys.path.
_API_EVAL_PATH = Path("/repo/api/src/services/approval_evaluator.py")
_spec = importlib.util.spec_from_file_location("api_approval_evaluator", _API_EVAL_PATH)
assert _spec is not None and _spec.loader is not None, (
    f"Could not load api approval_evaluator from {_API_EVAL_PATH}. "
    "Ensure the file exists at the expected container path."
)
api_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api_eval)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _both(
    question_payload: dict[str, Any] | None,
    policies: dict[str, Any] | None,
) -> tuple[tuple, tuple]:
    """Return (langgraph_result, api_result) as plain tuples for comparison."""
    lg = langgraph_eval.evaluate_policy(question_payload, policies)
    ap = api_eval.evaluate_policy(question_payload, policies)
    return lg, ap


def _policy(*rules: dict) -> dict:
    return {"rules": list(rules)}


def _rule(action: str, match: dict, name: str = "r", default_answer: str | None = None) -> dict:
    r: dict = {"name": name, "action": action, "match": match}
    if default_answer is not None:
        r["default_answer"] = default_answer
    return r


# ---------------------------------------------------------------------------
# text_contains
# ---------------------------------------------------------------------------

def test_text_contains_match() -> None:
    policies = _policy(_rule("auto_approve", {"text_contains": "deploy"}, name="tc-match"))
    q = {"question": "Please deploy the service now."}
    lg, ap = _both(q, policies)
    assert lg == ap
    assert lg[0] == "auto_approve"
    assert lg[2] == "tc-match"


def test_text_contains_no_match() -> None:
    policies = _policy(_rule("auto_approve", {"text_contains": "deploy"}))
    q = {"question": "Send an email to the team."}
    lg, ap = _both(q, policies)
    assert lg == ap
    assert lg[0] == "require_attention"


def test_text_contains_case_insensitive() -> None:
    policies = _policy(_rule("auto_approve", {"text_contains": "DEPLOY"}))
    q = {"question": "Please deploy the service now."}
    lg, ap = _both(q, policies)
    assert lg == ap
    assert lg[0] == "auto_approve"


# ---------------------------------------------------------------------------
# text_contains_all
# ---------------------------------------------------------------------------

def test_text_contains_all_match() -> None:
    policies = _policy(
        _rule("auto_deny", {"text_contains_all": ["git push", "main"]}, name="tca-match")
    )
    q = {"question": "About to git push to main branch."}
    lg, ap = _both(q, policies)
    assert lg == ap
    assert lg[0] == "auto_deny"
    assert lg[2] == "tca-match"


def test_text_contains_all_partial_no_match() -> None:
    # Only one of the two terms present — must NOT match.
    policies = _policy(_rule("auto_deny", {"text_contains_all": ["git push", "main"]}))
    q = {"question": "About to git push to staging."}
    lg, ap = _both(q, policies)
    assert lg == ap
    assert lg[0] == "require_attention"


# ---------------------------------------------------------------------------
# text_contains_any
# ---------------------------------------------------------------------------

def test_text_contains_any_first_term_matches() -> None:
    policies = _policy(
        _rule("auto_approve", {"text_contains_any": ["approve", "accept"]}, name="tcy-match")
    )
    q = {"question": "Please approve this change."}
    lg, ap = _both(q, policies)
    assert lg == ap
    assert lg[0] == "auto_approve"


def test_text_contains_any_neither_matches() -> None:
    policies = _policy(_rule("auto_approve", {"text_contains_any": ["approve", "accept"]}))
    q = {"question": "Please review this change."}
    lg, ap = _both(q, policies)
    assert lg == ap
    assert lg[0] == "require_attention"


# ---------------------------------------------------------------------------
# amount_usd_lt
# ---------------------------------------------------------------------------

def test_amount_usd_lt_match() -> None:
    policies = _policy(
        _rule("auto_approve", {"amount_usd_lt": 10.0}, name="lt-match", default_answer="accept")
    )
    q = {"question": "Spend $5 on LLM tokens."}
    lg, ap = _both(q, policies)
    assert lg == ap
    assert lg[0] == "auto_approve"
    assert lg[1] == "accept"


def test_amount_usd_lt_above_threshold_no_match() -> None:
    policies = _policy(_rule("auto_approve", {"amount_usd_lt": 10.0}))
    q = {"question": "Spend $50 on LLM tokens."}
    lg, ap = _both(q, policies)
    assert lg == ap
    assert lg[0] == "require_attention"


def test_amount_usd_lt_missing_amount_no_match() -> None:
    # No parseable amount in the question — predicate must fail (not match).
    policies = _policy(_rule("auto_approve", {"amount_usd_lt": 10.0}))
    q = {"question": "Approve the budget request."}
    lg, ap = _both(q, policies)
    assert lg == ap
    assert lg[0] == "require_attention"


# ---------------------------------------------------------------------------
# amount_usd_gt
# ---------------------------------------------------------------------------

def test_amount_usd_gt_match() -> None:
    policies = _policy(
        _rule("require_attention", {"amount_usd_gt": 100.0}, name="gt-match")
    )
    q = {"question": "This will cost $500 USD."}
    lg, ap = _both(q, policies)
    assert lg == ap
    assert lg[0] == "require_attention"
    assert lg[2] == "gt-match"


def test_amount_usd_gt_below_threshold_no_match() -> None:
    policies = _policy(_rule("require_attention", {"amount_usd_gt": 100.0}))
    q = {"question": "This will cost $5 USD."}
    lg, ap = _both(q, policies)
    assert lg == ap
    # No rule matched → default require_attention, but rule_name is None.
    assert lg[0] == "require_attention"
    assert lg[2] is None


def test_amount_usd_gt_missing_amount_no_match() -> None:
    policies = _policy(_rule("auto_deny", {"amount_usd_gt": 0.0}))
    q = {"question": "No dollar amount here."}
    lg, ap = _both(q, policies)
    assert lg == ap
    assert lg[0] == "require_attention"


# ---------------------------------------------------------------------------
# options_include
# ---------------------------------------------------------------------------

def test_options_include_match() -> None:
    policies = _policy(
        _rule("auto_approve", {"options_include": "yes"}, name="oi-match", default_answer="yes")
    )
    q = {"question": "Proceed?", "options": ["yes", "no"]}
    lg, ap = _both(q, policies)
    assert lg == ap
    assert lg[0] == "auto_approve"
    assert lg[1] == "yes"


def test_options_include_absent_no_match() -> None:
    policies = _policy(_rule("auto_approve", {"options_include": "yes"}))
    q = {"question": "Proceed?", "options": ["continue", "abort"]}
    lg, ap = _both(q, policies)
    assert lg == ap
    assert lg[0] == "require_attention"


def test_options_include_empty_options_no_match() -> None:
    policies = _policy(_rule("auto_approve", {"options_include": "yes"}))
    q = {"question": "Proceed?", "options": []}
    lg, ap = _both(q, policies)
    assert lg == ap
    assert lg[0] == "require_attention"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_no_policies_returns_require_attention() -> None:
    lg, ap = _both({"question": "anything"}, None)
    assert lg == ap
    assert lg[0] == "require_attention"
    assert lg[1] is None
    assert lg[2] is None


def test_empty_rules_returns_require_attention() -> None:
    lg, ap = _both({"question": "anything"}, {"rules": []})
    assert lg == ap
    assert lg[0] == "require_attention"


def test_first_matching_rule_wins() -> None:
    # Two rules: first matches and auto_approves; second would auto_deny.
    policies = _policy(
        _rule("auto_approve", {"text_contains": "deploy"}, name="first"),
        _rule("auto_deny", {"text_contains": "deploy"}, name="second"),
    )
    q = {"question": "Please deploy."}
    lg, ap = _both(q, policies)
    assert lg == ap
    assert lg[0] == "auto_approve"
    assert lg[2] == "first"


def test_default_answer_falls_back_to_options_first() -> None:
    # No default_answer on rule → should use options[0].
    policies = _policy(_rule("auto_approve", {"text_contains": "ok"}, name="da-test"))
    q = {"question": "ok proceed", "options": ["proceed", "abort"]}
    lg, ap = _both(q, policies)
    assert lg == ap
    assert lg[0] == "auto_approve"
    assert lg[1] == "proceed"


def test_default_answer_fallback_accept_when_no_options() -> None:
    # No default_answer, no options → "accept".
    policies = _policy(_rule("auto_approve", {"text_contains": "ok"}, name="da-fallback"))
    q = {"question": "ok proceed"}
    lg, ap = _both(q, policies)
    assert lg == ap
    assert lg[0] == "auto_approve"
    assert lg[1] == "accept"
