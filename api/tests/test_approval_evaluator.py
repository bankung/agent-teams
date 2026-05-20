"""Unit tests for `src.services.approval_evaluator.evaluate_policy` (Kanban #957).

Pure-function tests — no DB, no HTTP, no fixtures beyond plain dicts. The
evaluator is the load-bearing decision point between a pending HITL prompt
and the worker's auto-approve / auto-deny / require-attention branches.

Coverage rationale:
  - Empty / malformed policies → REQUIRE_ATTENTION (defensive default).
  - Each predicate exercised independently (text_contains family, amount
    parsing, options_include).
  - Combinators: predicates ANDed within a rule; rules ORed left-to-right
    (first match wins).
  - default_answer resolution order: rule.default_answer → options[0] → "accept".
  - auto_deny action returns no default_answer.
  - Edge cases: unknown action skipped (other rules still evaluated);
    malformed rule (non-dict, empty match) skipped; unknown predicate fails
    the rule defensively.
"""

from __future__ import annotations

import pytest

from src.services.approval_evaluator import evaluate_policy


# ---------------------------------------------------------------------------
# 1. Defensive defaults — None / malformed policies → REQUIRE_ATTENTION
# ---------------------------------------------------------------------------


def test_none_policies_returns_require_attention() -> None:
    action, answer, rule = evaluate_policy({"question": "x"}, None)
    assert action == "require_attention"
    assert answer is None
    assert rule is None


def test_empty_dict_policies_returns_require_attention() -> None:
    # Missing 'rules' key — common state when no policy authored yet.
    action, _, rule = evaluate_policy({"question": "x"}, {})
    assert action == "require_attention"
    assert rule is None


def test_rules_explicit_null_returns_require_attention() -> None:
    action, _, _ = evaluate_policy({"question": "x"}, {"rules": None})
    assert action == "require_attention"


def test_rules_not_a_list_returns_require_attention(caplog) -> None:
    """`rules` is a dict not a list — log a warning, fall back."""
    with caplog.at_level("WARNING", logger="src.services.approval_evaluator"):
        action, _, _ = evaluate_policy(
            {"question": "x"}, {"rules": {"oops": True}}
        )
    assert action == "require_attention"
    assert any("expected list" in rec.message for rec in caplog.records)


def test_policies_not_a_dict_returns_require_attention(caplog) -> None:
    with caplog.at_level("WARNING", logger="src.services.approval_evaluator"):
        action, _, _ = evaluate_policy({"question": "x"}, ["nope"])  # type: ignore[arg-type]
    assert action == "require_attention"
    assert any("expected dict" in rec.message for rec in caplog.records)


def test_question_payload_not_a_dict_returns_require_attention() -> None:
    """Defensive guard against an upstream refactor that hands us a non-dict."""
    policies = {"rules": [
        {"name": "x", "match": {"text_contains": "anything"}, "action": "auto_approve"}
    ]}
    action, _, _ = evaluate_policy("not a dict", policies)  # type: ignore[arg-type]
    assert action == "require_attention"


def test_empty_rules_list_returns_require_attention() -> None:
    action, _, _ = evaluate_policy({"question": "x"}, {"rules": []})
    assert action == "require_attention"


# ---------------------------------------------------------------------------
# 2. text_contains — case-insensitive substring
# ---------------------------------------------------------------------------


def test_text_contains_matches_case_insensitive() -> None:
    policies = {"rules": [
        {
            "name": "approve spend",
            "match": {"text_contains": "SPEND"},
            "action": "auto_approve",
        }
    ]}
    payload = {"question": "Approve $2 LLM spend?"}
    action, answer, rule = evaluate_policy(payload, policies)
    assert action == "auto_approve"
    assert answer == "accept"  # no options, no default_answer → fallback
    assert rule == "approve spend"


def test_text_contains_no_match_returns_require_attention() -> None:
    policies = {"rules": [
        {"name": "x", "match": {"text_contains": "deploy"}, "action": "auto_approve"}
    ]}
    payload = {"question": "Approve $2 LLM spend?"}
    action, _, _ = evaluate_policy(payload, policies)
    assert action == "require_attention"


def test_text_contains_non_string_value_fails_predicate() -> None:
    """text_contains: 5 (int) → predicate fails defensively."""
    policies = {"rules": [
        {"name": "x", "match": {"text_contains": 5}, "action": "auto_approve"}
    ]}
    action, _, _ = evaluate_policy({"question": "5 dollars"}, policies)
    assert action == "require_attention"


# ---------------------------------------------------------------------------
# 3. text_contains_all — ALL substrings must appear
# ---------------------------------------------------------------------------


def test_text_contains_all_requires_every_substring() -> None:
    policies = {"rules": [
        {
            "name": "deny push to main",
            "match": {"text_contains_all": ["git push", "main"]},
            "action": "auto_deny",
        }
    ]}
    action, _, rule = evaluate_policy(
        {"question": "About to git push to main; ok?"}, policies
    )
    assert action == "auto_deny"
    assert rule == "deny push to main"


def test_text_contains_all_fails_if_any_substring_missing() -> None:
    policies = {"rules": [
        {
            "name": "x",
            "match": {"text_contains_all": ["git push", "main"]},
            "action": "auto_deny",
        }
    ]}
    action, _, _ = evaluate_policy(
        {"question": "git push to staging"}, policies
    )
    assert action == "require_attention"


def test_text_contains_all_empty_list_fails() -> None:
    """text_contains_all: [] would otherwise vacuously match — fail defensively."""
    policies = {"rules": [
        {"name": "x", "match": {"text_contains_all": []}, "action": "auto_approve"}
    ]}
    action, _, _ = evaluate_policy({"question": "anything"}, policies)
    assert action == "require_attention"


# ---------------------------------------------------------------------------
# 4. text_contains_any — ANY substring matches
# ---------------------------------------------------------------------------


def test_text_contains_any_matches_when_one_appears() -> None:
    policies = {"rules": [
        {
            "name": "approve read tools",
            "match": {"text_contains_any": ["read", "list", "grep"]},
            "action": "auto_approve",
        }
    ]}
    action, _, _ = evaluate_policy(
        {"question": "Tool wants to grep the repo"}, policies
    )
    assert action == "auto_approve"


def test_text_contains_any_no_match() -> None:
    policies = {"rules": [
        {"name": "x", "match": {"text_contains_any": ["foo", "bar"]}, "action": "auto_approve"}
    ]}
    action, _, _ = evaluate_policy(
        {"question": "completely unrelated"}, policies
    )
    assert action == "require_attention"


# ---------------------------------------------------------------------------
# 5. amount_usd_lt / amount_usd_gt — parse $N or N USD
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question,expected",
    [
        ("Approve $5 LLM spend?", True),     # 5 < 10
        ("Approve $9.99 LLM spend?", True),  # 9.99 < 10
        ("Approve $10 LLM spend?", False),   # 10 not < 10
        ("Approve 5 USD spend?", True),
        ("Approve 5.00 USD spend?", True),
        ("Approve 50 USD spend?", False),
        ("No amount here", False),           # parse fail → predicate fail
    ],
)
def test_amount_usd_lt_thresholds(question: str, expected: bool) -> None:
    policies = {"rules": [
        {"name": "small", "match": {"amount_usd_lt": 10.0}, "action": "auto_approve"}
    ]}
    action, _, _ = evaluate_policy({"question": question}, policies)
    assert (action == "auto_approve") is expected


def test_amount_usd_gt_threshold() -> None:
    policies = {"rules": [
        {"name": "big", "match": {"amount_usd_gt": 100.0}, "action": "auto_deny"}
    ]}
    action, _, rule = evaluate_policy(
        {"question": "Approve $500 spend?"}, policies
    )
    assert action == "auto_deny"
    assert rule == "big"


def test_amount_usd_non_numeric_threshold_fails() -> None:
    """amount_usd_lt: 'cheap' (str) → predicate fails defensively."""
    policies = {"rules": [
        {"name": "x", "match": {"amount_usd_lt": "cheap"}, "action": "auto_approve"}
    ]}
    action, _, _ = evaluate_policy({"question": "Approve $1 spend?"}, policies)
    assert action == "require_attention"


# ---------------------------------------------------------------------------
# 6. options_include — match against question_payload.options
# ---------------------------------------------------------------------------


def test_options_include_matches_when_option_present() -> None:
    policies = {"rules": [
        {
            "name": "approve staging",
            "match": {"options_include": "staging"},
            "action": "auto_approve",
        }
    ]}
    action, answer, _ = evaluate_policy(
        {"question": "Deploy where?", "options": ["staging", "prod"]},
        policies,
    )
    assert action == "auto_approve"
    # No explicit default_answer → fallback to options[0].
    assert answer == "staging"


def test_options_include_no_match() -> None:
    policies = {"rules": [
        {"name": "x", "match": {"options_include": "dev"}, "action": "auto_approve"}
    ]}
    action, _, _ = evaluate_policy(
        {"question": "Deploy where?", "options": ["staging", "prod"]}, policies
    )
    assert action == "require_attention"


def test_options_include_when_options_missing_fails() -> None:
    policies = {"rules": [
        {"name": "x", "match": {"options_include": "staging"}, "action": "auto_approve"}
    ]}
    action, _, _ = evaluate_policy({"question": "free form"}, policies)
    assert action == "require_attention"


# ---------------------------------------------------------------------------
# 7. Combining predicates within a rule — AND
# ---------------------------------------------------------------------------


def test_predicates_combine_with_and() -> None:
    """text_contains + amount_usd_lt both must hit."""
    policies = {"rules": [
        {
            "name": "small llm",
            "match": {
                "text_contains": "llm",
                "amount_usd_lt": 5.0,
            },
            "action": "auto_approve",
        }
    ]}
    # Both predicates match.
    assert evaluate_policy(
        {"question": "Approve $2 LLM call?"}, policies
    )[0] == "auto_approve"
    # text matches, amount fails (10 not < 5).
    assert evaluate_policy(
        {"question": "Approve $10 LLM call?"}, policies
    )[0] == "require_attention"
    # Amount matches, text fails (no "llm" substring).
    assert evaluate_policy(
        {"question": "Approve $2 git commit?"}, policies
    )[0] == "require_attention"


# ---------------------------------------------------------------------------
# 8. Rule ordering — first match wins
# ---------------------------------------------------------------------------


def test_first_matching_rule_wins() -> None:
    policies = {"rules": [
        {
            "name": "first approve",
            "match": {"text_contains": "deploy"},
            "action": "auto_approve",
            "default_answer": "yes",
        },
        {
            "name": "second deny",
            "match": {"text_contains": "deploy"},
            "action": "auto_deny",
        },
    ]}
    action, answer, rule = evaluate_policy(
        {"question": "Deploy to prod?"}, policies
    )
    assert action == "auto_approve"
    assert answer == "yes"
    assert rule == "first approve"


def test_unknown_action_skips_rule_but_continues() -> None:
    """A rule with an unknown action is skipped; later rules still evaluated."""
    policies = {"rules": [
        {"name": "garbage", "match": {"text_contains": "x"}, "action": "auto_archive"},
        {"name": "good", "match": {"text_contains": "x"}, "action": "auto_approve"},
    ]}
    action, _, rule = evaluate_policy({"question": "x"}, policies)
    assert action == "auto_approve"
    assert rule == "good"


def test_non_dict_rule_skipped() -> None:
    policies = {"rules": [
        "garbage",
        {"name": "good", "match": {"text_contains": "x"}, "action": "auto_approve"},
    ]}
    action, _, _ = evaluate_policy({"question": "x"}, policies)
    assert action == "auto_approve"


# ---------------------------------------------------------------------------
# 9. auto_deny
# ---------------------------------------------------------------------------


def test_auto_deny_returns_no_default_answer() -> None:
    policies = {"rules": [
        {"name": "deny", "match": {"text_contains": "rm -rf"}, "action": "auto_deny"}
    ]}
    action, answer, rule = evaluate_policy(
        {"question": "Run rm -rf /tmp/foo?"}, policies
    )
    assert action == "auto_deny"
    assert answer is None
    assert rule == "deny"


# ---------------------------------------------------------------------------
# 10. default_answer resolution order
# ---------------------------------------------------------------------------


def test_default_answer_explicit_wins_over_options() -> None:
    policies = {"rules": [
        {
            "name": "x",
            "match": {"text_contains": "deploy"},
            "action": "auto_approve",
            "default_answer": "yolo",
        }
    ]}
    _, answer, _ = evaluate_policy(
        {"question": "Deploy where?", "options": ["staging", "prod"]}, policies
    )
    assert answer == "yolo"


def test_default_answer_options_first_when_no_explicit() -> None:
    policies = {"rules": [
        {"name": "x", "match": {"text_contains": "deploy"}, "action": "auto_approve"}
    ]}
    _, answer, _ = evaluate_policy(
        {"question": "Deploy where?", "options": ["staging", "prod"]}, policies
    )
    assert answer == "staging"


def test_default_answer_accept_fallback_for_free_text() -> None:
    policies = {"rules": [
        {"name": "x", "match": {"text_contains": "describe"}, "action": "auto_approve"}
    ]}
    _, answer, _ = evaluate_policy(
        {"question": "Describe the bug"}, policies
    )
    assert answer == "accept"


def test_default_answer_empty_string_treated_as_unset() -> None:
    """An empty default_answer should fall through to options[0] / accept."""
    policies = {"rules": [
        {
            "name": "x",
            "match": {"text_contains": "x"},
            "action": "auto_approve",
            "default_answer": "   ",
        }
    ]}
    _, answer, _ = evaluate_policy(
        {"question": "x", "options": ["A", "B"]}, policies
    )
    assert answer == "A"


# ---------------------------------------------------------------------------
# 11. Defensive — unknown predicate fails the rule, doesn't crash
# ---------------------------------------------------------------------------


def test_unknown_predicate_fails_rule() -> None:
    """A typo'd predicate must NOT silently match — fail closed."""
    policies = {"rules": [
        {
            "name": "typo",
            "match": {"text_containss": "x"},  # double-s typo
            "action": "auto_approve",
        }
    ]}
    action, _, _ = evaluate_policy({"question": "x"}, policies)
    assert action == "require_attention"


def test_empty_match_dict_does_not_auto_match() -> None:
    """An empty match dict could be interpreted as 'always true' — fail closed
    instead, since an operator with an empty match dict almost certainly made
    a configuration mistake."""
    policies = {"rules": [
        {"name": "empty", "match": {}, "action": "auto_approve"}
    ]}
    action, _, _ = evaluate_policy({"question": "anything"}, policies)
    assert action == "require_attention"


# ---------------------------------------------------------------------------
# Explicit-match require_attention rule (regression guard — 2026-05-17 bug:
# action='require_attention' was excluded from _VALID_ACTIONS so matching rules
# silently fell through to default; rule_name attribution was lost from audit).
# ---------------------------------------------------------------------------


def test_explicit_require_attention_rule_returns_rule_name() -> None:
    """A rule with action='require_attention' MUST surface (action, None, rule_name)
    on match — operator wants the audit trail to show WHICH rule matched, even
    though the action is the same as default-no-match."""
    policies = {"rules": [
        {
            "name": "require-attention on submit",
            "match": {"text_contains_any": ["submit application"]},
            "action": "require_attention",
        }
    ]}
    qp = {"question": "Submit application to FakeCo?", "options": ["approve", "reject"]}
    action, default_answer, rule_name = evaluate_policy(qp, policies)
    assert action == "require_attention"
    assert default_answer is None
    assert rule_name == "require-attention on submit"


def test_explicit_require_attention_rule_priority_over_later_auto_approve() -> None:
    """First-match-wins applies to require_attention rules too. Rule order
    matters; an earlier require_attention rule should pre-empt a later
    auto_approve rule that would also match."""
    policies = {"rules": [
        {
            "name": "always-pause-on-payment",
            "match": {"text_contains_any": ["payment"]},
            "action": "require_attention",
        },
        {
            "name": "auto-approve-small-amounts",
            "match": {"amount_usd_lt": 5.0},
            "action": "auto_approve",
        }
    ]}
    qp = {"question": "Confirm $2 payment to vendor?"}
    action, _, rule_name = evaluate_policy(qp, policies)
    assert action == "require_attention"
    assert rule_name == "always-pause-on-payment"


def test_no_match_still_returns_require_attention_with_none_rule_name() -> None:
    """The default (no rule matches) path returns rule_name=None — distinct from
    the explicit-rule-match-with-require_attention case above. Both have action
    'require_attention' but only the latter has a rule_name."""
    policies = {"rules": [
        {
            "name": "auto-approve-low-cost",
            "match": {"text_contains": "spend less than 1 usd"},
            "action": "auto_approve",
        }
    ]}
    qp = {"question": "Some unrelated question"}
    action, default_answer, rule_name = evaluate_policy(qp, policies)
    assert action == "require_attention"
    assert default_answer is None
    assert rule_name is None  # distinct from explicit-match case


# ---------------------------------------------------------------------------
# Coexistence regression guard — Kanban #1279 (Pattern 5 hook + worker share
# the same JSONB column with disjoint matcher vocabularies). See
# context/projects/agent-teams/shared/decisions-approval-policies-schema.md.
# ---------------------------------------------------------------------------


def test_pattern5_keys_unknown_to_worker_fall_to_require_attention() -> None:
    """Rules authored for Layer B (Pattern 5 hook) MUST fail defensively at
    the worker layer — no auto-approve, no crash, falls to require_attention.

    Pins the disjoint-namespace coexistence contract: hook keys
    (`tool_name` / `target_url_pattern` / `content_predicate`) are unknown to
    the worker's `_match_predicate`, so every predicate fails → rule skipped
    → default `require_attention`. If a future predicate-vocab expansion
    accidentally adds one of these keys to Layer A, this test breaks and
    forces the author to read the schema decisions doc.
    """
    policies = {"rules": [
        {
            "name": "deny linkedin posts (hook-layer rule)",
            "match": {
                "tool_name": "mcp__Claude_in_Chrome__navigate",
                "target_url_pattern": r"linkedin\.com",
                "content_predicate": "publish",
            },
            "action": "auto_deny",
        }
    ]}
    qp = {"question": "Post 'New role announcement' to LinkedIn?"}
    action, default_answer, rule_name = evaluate_policy(qp, policies)
    assert action == "require_attention"
    assert default_answer is None
    assert rule_name is None  # no rule matched (all predicates unknown to worker)
