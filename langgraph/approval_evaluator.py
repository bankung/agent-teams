"""Per-project HITL approval-policy evaluator — engine-side copy (Kanban #957).

VERBATIM mirror of `api/src/services/approval_evaluator.py`. The two copies
are intentionally duplicated rather than cross-imported: the langgraph
container does not share Python sys.path with the api container (same
precedent as the STATUS_BLOCKED/STATUS_DONE constants in `worker.py`, which
also re-declare rather than import from api).

If you edit this file, edit `api/src/services/approval_evaluator.py` too.
A drift would surface as a regression in the cross-suite policy tests:
`api/tests/test_approval_evaluator.py` exercises the api copy;
`langgraph/tests/test_worker_policy_hook.py` exercises the engine copy.

See the docstring of the api copy for the policy-shape contract, including
the `task_context` parameter accepted by `evaluate_policy` (optional dict of
task fields — absent context/field fails `_not` predicates closed, not open).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("langgraph.approval_evaluator")

_VALID_ACTIONS = ("auto_approve", "auto_deny", "require_attention", "requires_attention")

_AMOUNT_RE = re.compile(
    r"""
    (?:
        \$\s*(\d+(?:\.\d+)?)
    )
    |
    (?:
        (\d+(?:\.\d+)?)\s*USD\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _extract_amount_usd(text: str) -> float | None:
    m = _AMOUNT_RE.search(text or "")
    if not m:
        return None
    value = m.group(1) or m.group(2)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _task_age_hours(task_context: dict[str, Any] | None) -> float | None:
    if not isinstance(task_context, dict):
        return None
    created_at = _parse_datetime(task_context.get("created_at"))
    if created_at is None:
        return None
    return (datetime.now(timezone.utc) - created_at.astimezone(timezone.utc)).total_seconds() / 3600


def _task_text(task_context: dict[str, Any] | None, key: str) -> str:
    if not isinstance(task_context, dict):
        return ""
    value = task_context.get(key)
    return value if isinstance(value, str) else ""


def _task_number(task_context: dict[str, Any] | None, key: str) -> float | None:
    if not isinstance(task_context, dict):
        return None
    value = task_context.get(key)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _match_predicate(
    predicate_key: str,
    predicate_value: Any,
    question_text: str,
    options: list[str],
    amount: float | None,
    task_context: dict[str, Any] | None,
) -> bool:
    q_lower = question_text.lower()
    if predicate_key == "text_contains":
        if not isinstance(predicate_value, str):
            return False
        return predicate_value.lower() in q_lower
    if predicate_key == "text_contains_all":
        if not isinstance(predicate_value, list) or not predicate_value:
            return False
        return all(
            isinstance(s, str) and s.lower() in q_lower for s in predicate_value
        )
    if predicate_key == "text_contains_any":
        if not isinstance(predicate_value, list) or not predicate_value:
            return False
        return any(
            isinstance(s, str) and s.lower() in q_lower for s in predicate_value
        )
    if predicate_key == "amount_usd_lt":
        if amount is None or not isinstance(predicate_value, (int, float)):
            return False
        return amount < float(predicate_value)
    if predicate_key == "amount_usd_gt":
        if amount is None or not isinstance(predicate_value, (int, float)):
            return False
        return amount > float(predicate_value)
    if predicate_key == "options_include":
        if not isinstance(predicate_value, str):
            return False
        return predicate_value in options
    if predicate_key == "task_title_contains":
        if not isinstance(predicate_value, str):
            return False
        return predicate_value.lower() in _task_text(task_context, "title").lower()
    if predicate_key == "task_description_contains":
        if not isinstance(predicate_value, str):
            return False
        return predicate_value.lower() in _task_text(task_context, "description").lower()
    if predicate_key in ("task_type", "task_type_not", "operator_gate", "operator_gate_not", "run_mode", "run_mode_not", "task_kind", "task_kind_not"):
        if not isinstance(predicate_value, str):
            return False
        field = predicate_key.removesuffix("_not")
        actual = _task_text(task_context, field)
        # _not must fail-closed on absent context/field: "" != "chore" is True
        # (over-approval). Guard: absent field → False regardless of predicate_value.
        return (actual != "" and actual != predicate_value) if predicate_key.endswith("_not") else actual == predicate_value
    if predicate_key in ("priority", "project_id", "assigned_role", "acceptance_criteria_count"):
        if not isinstance(predicate_value, (int, float)):
            return False
        if predicate_key == "acceptance_criteria_count":
            ac = task_context.get("acceptance_criteria") if isinstance(task_context, dict) else None
            actual = float(len(ac)) if isinstance(ac, list) else 0.0
        else:
            actual = _task_number(task_context, predicate_key)
        return actual is not None and actual == float(predicate_value)
    if predicate_key in ("priority_lt", "priority_gt", "age_hours_lt", "age_hours_gt", "acceptance_criteria_count_lt", "acceptance_criteria_count_gt"):
        if not isinstance(predicate_value, (int, float)):
            return False
        if predicate_key.startswith("priority_"):
            actual = _task_number(task_context, "priority")
        elif predicate_key.startswith("age_hours_"):
            actual = _task_age_hours(task_context)
        else:
            ac = task_context.get("acceptance_criteria") if isinstance(task_context, dict) else None
            actual = float(len(ac)) if isinstance(ac, list) else 0.0
        if actual is None:
            return False
        return actual < float(predicate_value) if predicate_key.endswith("_lt") else actual > float(predicate_value)
    logger.debug("approval_evaluator: unknown predicate %r — failing rule", predicate_key)
    return False


def _match_group(
    match_dict: dict[str, Any],
    question_payload: dict[str, Any],
    task_context: dict[str, Any] | None,
) -> bool:
    if not isinstance(match_dict, dict) or not match_dict:
        return False
    question_text = str(question_payload.get("question") or "")
    raw_options = question_payload.get("options")
    options = list(raw_options) if isinstance(raw_options, list) else []
    amount = _extract_amount_usd(question_text)
    for key, value in match_dict.items():
        if not _match_predicate(key, value, question_text, options, amount, task_context):
            return False
    return True


def _rule_matches(
    rule: dict[str, Any],
    question_payload: dict[str, Any],
    task_context: dict[str, Any] | None,
) -> bool:
    match_dict = rule.get("match")
    match_any = rule.get("match_any")
    has_match = isinstance(match_dict, dict) and bool(match_dict)
    has_any = isinstance(match_any, list) and bool(match_any)
    if not has_match and not has_any:
        return False
    if has_match and not _match_group(match_dict, question_payload, task_context):
        return False
    if has_any:
        return any(
            isinstance(group, dict) and _match_group(group, question_payload, task_context)
            for group in match_any
        )
    return True


def _resolve_default_answer(
    rule: dict[str, Any], question_payload: dict[str, Any]
) -> str:
    explicit = rule.get("default_answer")
    if isinstance(explicit, str) and explicit.strip():
        return explicit
    raw_options = question_payload.get("options")
    if isinstance(raw_options, list) and raw_options:
        first = raw_options[0]
        if isinstance(first, str) and first:
            return first
    return "accept"


def evaluate_policy(
    question_payload: dict[str, Any] | None,
    policies: dict[str, Any] | None,
    task_context: dict[str, Any] | None = None,
) -> tuple[str, str | None, str | None]:
    """Evaluate `question_payload` against `policies`.

    Returns `(action, default_answer, matched_rule_name)`:
      - action in {'auto_approve', 'auto_deny', 'require_attention'}
      - default_answer: string to feed Command(resume=...) on auto_approve;
        None when action != auto_approve.
      - matched_rule_name: rule's `name` that matched; None on no match.
    """
    default = ("require_attention", None, None)
    if policies is None:
        return default
    if not isinstance(policies, dict):
        logger.warning(
            "approval_evaluator: policies is %s, expected dict; falling back to REQUIRE_ATTENTION",
            type(policies).__name__,
        )
        return default
    rules = policies.get("rules")
    if rules is None:
        return default
    if not isinstance(rules, list):
        logger.warning(
            "approval_evaluator: rules is %s, expected list; falling back to REQUIRE_ATTENTION",
            type(rules).__name__,
        )
        return default
    if not isinstance(question_payload, dict):
        return default

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if rule.get("enabled") is False:
            continue
        action = rule.get("action")
        if action not in _VALID_ACTIONS:
            continue
        try:
            if not _rule_matches(rule, question_payload, task_context):
                continue
        except Exception:
            logger.warning(
                "approval_evaluator: rule %r raised during match; skipping",
                rule.get("name"),
                exc_info=True,
            )
            continue
        rule_name = rule.get("name") if isinstance(rule.get("name"), str) else None
        if action == "auto_approve":
            answer = _resolve_default_answer(rule, question_payload)
            return ("auto_approve", answer, rule_name)
        if action in ("require_attention", "requires_attention"):
            # Explicit-match require_attention rule: same outcome as default
            # (HITL pause) but preserves rule_name attribution.
            return ("require_attention", None, rule_name)
        return ("auto_deny", None, rule_name)

    return default
