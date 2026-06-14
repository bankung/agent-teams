from approval_evaluator import evaluate_policy


def test_ui_task_context_predicates_and_disabled_rules() -> None:
    policies = {
        "rules": [
            {
                "name": "disabled",
                "enabled": False,
                "match": {"task_type": "feature"},
                "action": "auto_approve",
            },
            {
                "name": "feature ac gate",
                "match": {
                    "task_type": "feature",
                    "acceptance_criteria_count_gt": 1,
                },
                "action": "auto_approve",
            },
        ]
    }
    task_context = {
        "task_type": "feature",
        "acceptance_criteria": [{}, {}],
    }
    action, _, rule_name = evaluate_policy(
        {"question": "Approve?"}, policies, task_context
    )
    assert action == "auto_approve"
    assert rule_name == "feature ac gate"


def test_ui_match_any_aliases_require_attention() -> None:
    policies = {
        "rules": [
            {
                "name": "urgent or bug",
                "match_any": [{"task_type": "bug"}, {"priority_gt": 3}],
                "action": "requires_attention",
            }
        ]
    }
    action, answer, rule_name = evaluate_policy(
        {"question": "Approve?"}, policies, {"task_type": "feature", "priority": 4}
    )
    assert action == "require_attention"
    assert answer is None
    assert rule_name == "urgent or bug"


# ---------------------------------------------------------------------------
# Security regression: _not predicates must fail-CLOSED on absent context.
# Kanban #1014. Mirror of api/tests/test_approval_evaluator.py section.
# ---------------------------------------------------------------------------

_NOT_RULE = {
    "rules": [
        {
            "name": "not-chore auto-approve",
            "match": {"task_type_not": "chore"},
            "action": "auto_approve",
        }
    ]
}
_PAYLOAD = {"question": "Approve?"}


def test_not_predicate_matches_when_field_present_and_differs() -> None:
    """_not MATCHES: field present and differs from predicate value."""
    action, _, rule_name = evaluate_policy(
        _PAYLOAD, _NOT_RULE, {"task_type": "feature"}
    )
    assert action == "auto_approve"
    assert rule_name == "not-chore auto-approve"


def test_not_predicate_fails_closed_when_field_absent() -> None:
    """_not FAILS CLOSED: task_context provided but field key missing."""
    action, _, rule_name = evaluate_policy(
        _PAYLOAD, _NOT_RULE, {}  # no task_type key
    )
    assert action == "require_attention"
    assert rule_name is None


def test_not_predicate_fails_closed_when_task_context_none() -> None:
    """_not FAILS CLOSED: task_context is None entirely (THE security regression).

    Previously: _task_text(None, 'task_type') == '' and '' != 'chore' -> True
    -> auto_approve on zero context. Must be require_attention post-fix.
    """
    action, _, rule_name = evaluate_policy(
        _PAYLOAD, _NOT_RULE, None
    )
    assert action == "require_attention"
    assert rule_name is None
