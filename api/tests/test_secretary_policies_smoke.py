"""Regression guard for the canonical `secretary` project approval_policies.

Distinct from `test_approval_evaluator.py` (which tests the evaluator engine
with synthetic policies). This file tests REAL operational policies — the
6-rule default that secretary project ships with — against representative
operator HITL questions, asserting each rule fires (or doesn't) per design
intent.

## Why this file exists

2026-05-17 bug: `_VALID_ACTIONS = ("auto_approve", "auto_deny")` excluded
`"require_attention"`. 4 of secretary's 6 rules use that action and were
silently no-op'd at runtime. The evaluator's 38 unit tests passed cleanly
because none exercised "valid rule with action='require_attention' matches
+ surfaces rule_name". Bug detected only by Lead's verify-sweep across the
real policy. Fixed in commit 831a228 + 3 new evaluator tests guard the
EVALUATOR contract.

This file guards the POLICY contract — i.e., "next time someone edits the
canonical secretary policies, do the intended triggers still produce the
intended actions?". A regression in the rule shapes (e.g., typo, removed
predicate, action change) fails this test. A regression in the evaluator
engine fails the engine unit tests. Two distinct surfaces.

## Convention

If operator changes the canonical defaults (via Lead PATCH or repo edit),
this test's `SECRETARY_DEFAULT_POLICIES` constant MUST be updated in the
same change, with the expected-match table re-validated. The test failure
on policy drift IS the gate — it forces explicit acknowledgement of an
intentional policy change vs accidental regression.
"""

from __future__ import annotations

import pytest

from src.services.approval_evaluator import evaluate_policy


# ============================================================================
# Canonical secretary project default policies (seeded 2026-05-17 on project_id=599)
# ============================================================================

SECRETARY_DEFAULT_POLICIES = {
    "rules": [
        {
            "name": "auto-deny destructive account actions",
            "match": {
                "text_contains_any": [
                    "delete account",
                    "unsubscribe from all",
                    "cancel subscription",
                    "close account",
                    "delete all",
                ]
            },
            "action": "auto_deny",
        },
        {
            "name": "auto-deny financial actions",
            "match": {
                "text_contains_any": [
                    "pay ",
                    "purchase",
                    "subscribe to",
                    "upgrade plan",
                    "buy now",
                    "checkout",
                ]
            },
            "action": "auto_deny",
        },
        {
            "name": "require-attention on submit application",
            "match": {
                "text_contains_any": [
                    "submit application",
                    "apply for",
                    "send application",
                ]
            },
            "action": "require_attention",
        },
        {
            "name": "require-attention on post / publish",
            "match": {
                "text_contains_any": [
                    "post to linkedin",
                    "publish post",
                    "post draft",
                    "go live",
                    "publish content",
                ]
            },
            "action": "require_attention",
        },
        {
            "name": "require-attention on send email",
            "match": {
                "text_contains_any": [
                    "send reply",
                    "send email",
                    "send draft",
                    "reply to ",
                ]
            },
            "action": "require_attention",
        },
        {
            "name": "require-attention on send DM / message",
            "match": {
                "text_contains_any": [
                    "send dm",
                    "send message",
                    "send invite",
                    "send connection",
                ]
            },
            "action": "require_attention",
        },
    ]
}


# ============================================================================
# Expected matches — (question_text, expected_action, expected_rule_name)
#
# Add cases here when adding/changing rules. Each case documents the
# operator-facing intent: "if a HITL question contains this text, secretary
# should take this action via this rule".
# ============================================================================

EXPECTED_MATCHES = [
    # auto-deny destructive
    ("Delete account please", "auto_deny", "auto-deny destructive account actions"),
    ("Unsubscribe from all newsletters", "auto_deny", "auto-deny destructive account actions"),
    ("Cancel subscription to Stripe", "auto_deny", "auto-deny destructive account actions"),
    ("Close account on GitHub", "auto_deny", "auto-deny destructive account actions"),

    # auto-deny financial
    ("Pay $50 to vendor", "auto_deny", "auto-deny financial actions"),
    ("Purchase domain name?", "auto_deny", "auto-deny financial actions"),
    ("Subscribe to Pro plan", "auto_deny", "auto-deny financial actions"),
    ("Upgrade plan to enterprise?", "auto_deny", "auto-deny financial actions"),

    # require-attention on submit application
    ("Submit application to FakeCo for senior backend role", "require_attention",
     "require-attention on submit application"),
    ("Apply for the staff engineering position?", "require_attention",
     "require-attention on submit application"),
    ("Send application via the careers portal", "require_attention",
     "require-attention on submit application"),

    # require-attention on post / publish
    ("Post to LinkedIn as drafted?", "require_attention",
     "require-attention on post / publish"),
    ("Publish post on auditor pattern", "require_attention",
     "require-attention on post / publish"),
    ("Go live with the announcement", "require_attention",
     "require-attention on post / publish"),

    # require-attention on send email
    ("Send reply to recruiter Anna?", "require_attention",
     "require-attention on send email"),
    ("Send email to hiring manager?", "require_attention",
     "require-attention on send email"),
    ("Reply to Sarah's interview confirmation", "require_attention",
     "require-attention on send email"),

    # require-attention on send DM / message
    ("Send DM to candidate", "require_attention",
     "require-attention on send DM / message"),
    ("Send message via LinkedIn?", "require_attention",
     "require-attention on send DM / message"),
    ("Send connection request", "require_attention",
     "require-attention on send DM / message"),

    # No-match — falls back to default require_attention (rule_name=None)
    ("Schedule meeting next Tuesday at 3pm", "require_attention", None),
    ("Read the next 5 unread emails", "require_attention", None),
    ("List jobs matching criteria", "require_attention", None),
]


# ============================================================================
# Tests
# ============================================================================


@pytest.mark.parametrize(
    "question,expected_action,expected_rule_name",
    EXPECTED_MATCHES,
    ids=[f"'{q[:35]}...'" if len(q) > 35 else f"'{q}'" for q, _, _ in EXPECTED_MATCHES],
)
def test_secretary_policy_rule_matches_intent(
    question: str, expected_action: str, expected_rule_name: str | None
) -> None:
    """Each canonical secretary policy rule fires (or defaults) per intent.

    Failure mode 1: action mismatch — rule action changed without expected
    update; OR predicate shape changed; OR evaluator engine bug.
    Failure mode 2: rule_name mismatch — rule renamed without expected
    update; OR a different rule fired first (rule order changed).

    To update this test: change the EXPECTED_MATCHES table in the SAME
    commit as the rule change in SECRETARY_DEFAULT_POLICIES.
    """
    qp = {"question": question, "options": ["approve", "reject"]}
    action, _default_answer, rule_name = evaluate_policy(qp, SECRETARY_DEFAULT_POLICIES)
    assert action == expected_action, (
        f"Question {question!r}: expected action={expected_action!r}, got {action!r}"
    )
    assert rule_name == expected_rule_name, (
        f"Question {question!r}: expected rule_name={expected_rule_name!r}, "
        f"got {rule_name!r}"
    )


def test_secretary_policy_has_all_six_canonical_rules() -> None:
    """Guard against accidental rule deletion. If a rule is intentionally
    removed, update this test count + EXPECTED_MATCHES in the same commit."""
    assert len(SECRETARY_DEFAULT_POLICIES["rules"]) == 6, (
        f"Expected 6 canonical secretary rules; got "
        f"{len(SECRETARY_DEFAULT_POLICIES['rules'])}. Update test if intentional."
    )


def test_secretary_policy_action_distribution() -> None:
    """Guard against accidental action mix-up. Secretary defaults are
    2 auto_deny (destructive + financial) + 4 require_attention (submit /
    post / email / DM). No auto_approve at default — operator must opt in
    explicitly to auto_approve any pattern (privacy-conservative posture)."""
    actions = [r["action"] for r in SECRETARY_DEFAULT_POLICIES["rules"]]
    assert actions.count("auto_deny") == 2
    assert actions.count("require_attention") == 4
    assert actions.count("auto_approve") == 0, (
        "Secretary defaults must NOT auto_approve any pattern. Operator opts in."
    )


def test_secretary_policy_rule_names_unique() -> None:
    """Rule names are the audit-trail key. Duplicates would make audit
    attribution ambiguous."""
    names = [r["name"] for r in SECRETARY_DEFAULT_POLICIES["rules"]]
    assert len(names) == len(set(names)), f"Duplicate rule names: {names}"


def test_secretary_policy_rule_names_match_expected_matches_table() -> None:
    """Every rule in SECRETARY_DEFAULT_POLICIES must have at least 1 case in
    EXPECTED_MATCHES (otherwise the rule is untested). The reverse isn't
    enforced — multiple cases per rule are fine (e.g., different trigger
    keywords)."""
    rule_names_in_policy = {r["name"] for r in SECRETARY_DEFAULT_POLICIES["rules"]}
    rule_names_in_tests = {
        rule_name for _, _, rule_name in EXPECTED_MATCHES if rule_name is not None
    }
    untested = rule_names_in_policy - rule_names_in_tests
    assert not untested, (
        f"Rules without test cases in EXPECTED_MATCHES: {untested}. "
        f"Add at least 1 trigger example per rule."
    )
