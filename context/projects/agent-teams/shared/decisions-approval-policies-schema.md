# `projects.approval_policies` — two-consumer coexistence schema

**Status:** locked 2026-05-20 (Kanban #1279 — followup of #1274 Pattern 5 hook impl).

The `projects.approval_policies` JSONB column (added Kanban #953/#957) is read by **two independent consumer layers** that share the same row but use disjoint matcher vocabularies. This doc pins the coexistence contract so future predicate additions don't accidentally break either layer.

## The two consumers

### Layer A — worker-side labeling evaluator (`services/approval_evaluator.py`)

- Two file copies: `api/src/services/approval_evaluator.py` + verbatim mirror `langgraph/approval_evaluator.py` (sys.path isolation — same precedent as the STATUS_BLOCKED constant in `worker.py`).
- Called by `_poll_once` BEFORE writing the BLOCKED finalize PATCH.
- Drives one of three branches at the **graph-execution** layer: `auto_approve` (worker resumes graph with `default_answer`), `auto_deny` (worker halts as `operator_rejected`), `require_attention` (normal HITL pause).
- **Matcher vocabulary (Phase 1, locked Kanban #957):** `text_contains`, `text_contains_all`, `text_contains_any`, `amount_usd_lt`, `amount_usd_gt`, `options_include`.

### Layer B — Pattern 5 harness PreToolUse hook (`approval-policies-gate.ps1`)

- Single PowerShell script under `.claude/hooks/` (operator-cp'd from `_scratch/draft-approval-policies-gate.ps1` after #1274).
- Called by the Claude Code harness BEFORE tool execution (WebFetch / mcp__Claude_in_Chrome__* / Bash with `curl` detection — per `.claude/settings.json` registration).
- Drives the **harness** decision at tool-invocation time: `allow` / `deny` / `requires-attention` (operator confirmation).
- **Matcher vocabulary (Phase 1, locked Kanban #1274):** `tool_name` (exact), `target_url_pattern` (regex), `content_predicate` (substring/regex).

## Why both can read the same JSONB without conflict

Both evaluators follow a **defensive unknown-key-fails-rule** pattern:

```python
# api/src/services/approval_evaluator.py — _match_predicate (lines 127–159)
if predicate_key == "text_contains":
    ...
# ... known keys enumerated ...
logger.debug("approval_evaluator: unknown predicate %r — failing rule", predicate_key)
return False
```

```python
# _rule_matches: every predicate in a rule's `match` dict must pass.
# Unknown-key → False → rule SKIPPED for this layer.
for key, value in match_dict.items():
    if not _match_predicate(...):
        return False
```

Same pattern is mirrored in the hook: it evaluates only the recognized Layer-B matchers (`tool_name` / `target_url_pattern` / `content_predicate`). **#1614:** a rule carrying NO recognized Layer-B key (a Layer-A-only rule, or an empty `match`) is SKIPPED — it does not match. Previously such a rule fell through to `return $true` and matched everything (the match-all bug, #1614 Scope 1).

**The consequence:** a Pattern 5–shaped rule (keys: `tool_name`, `target_url_pattern`, `content_predicate`) presented to Layer A's evaluator will return `False` from every predicate match → the rule is skipped → falls to default `require_attention`. Symmetric on *matching*: a Layer A–shaped rule (keys: `text_contains`, `amount_usd_lt`, …) presented to Layer B's hook has no recognized Layer-B key → rule skipped.

The two layers coexist by **disjoint matcher namespaces**, but their **no-match default DIVERGES** (#1614, 2026-05-31):

- **Layer A (worker)** — no rule matched → `require_attention` (over-block; the worker pauses for HITL).
- **Layer B (hook)** — no Layer-B rule matched (incl. foreign-key-only / empty-match / null-or-empty `approval_policies`) → **`allow`** (default-allow: the gate has nothing to say, so it does not block; other harness layers still apply). The **one exception is Fail-Open-Ask** — genuine infra errors (API unreachable, malformed payload, missing/invalid `_runtime/lead_project_id.txt`) still fall to `ask`, never auto-approving on uncertainty.

Neither layer raises, neither silently breaks the other.

## Rule-authoring guidance

**Per-rule discipline:** a single rule's `match` dict SHOULD use keys from exactly ONE layer's vocabulary. Mixing layers in a single rule is well-defined (both layers fail-rule defensively) but semantically pointless — neither layer will ever fire that rule.

**Recommended shape:**

```json
{
  "rules": [
    {
      "name": "auto-approve small llm spend (Layer A — worker)",
      "match": {"text_contains": "spend", "amount_usd_lt": 5.0},
      "action": "auto_approve",
      "default_answer": "accept"
    },
    {
      "name": "deny WebFetch to job-board domains (Layer B — hook)",
      "match": {"tool_name": "WebFetch", "target_url_pattern": "(jobsdb|linkedin)\\.com"},
      "action": "auto_deny"
    }
  ]
}
```

**Optional layer tagging:** rules MAY carry a `layer` field (`"layer": "worker"` / `"layer": "hook"`) for human readability. The evaluators ignore unknown top-level keys (they only inspect `name` / `match` / `action` / `default_answer`), so adding the tag is forward-compatible. **Not enforced** — matcher-vocabulary alone is sufficient to disambiguate today.

## Adding a new predicate to either layer

When extending the vocabulary (e.g., adding `body_size_lt: int` to Layer A):

1. Implement the new predicate in BOTH layer copies (api + langgraph copies for Layer A; PowerShell hook for Layer B).
2. Add a unit test in `api/tests/test_approval_evaluator.py` (or `_scratch/draft-approval-policies-gate.smoke.ps1` for Layer B).
3. Run the regression guard `test_pattern5_keys_unknown_to_worker_fall_to_require_attention` (added Kanban #1279) to confirm the OTHER layer's keys still fail defensively.
4. Document the new predicate in this file's "Matcher vocabulary" list for the affected layer.

**Anti-pattern:** introducing a predicate name that collides between layers (e.g., adding `tool_name` to Layer A). If a key MUST be cross-layer (rare), implement it consistently in BOTH layers OR document the divergence here. Default posture is disjoint — colliding namespaces invite silent behavior drift across layers.

## Regression-guard test (Kanban #1279 AC1)

A coexistence test pins this contract:

```python
# api/tests/test_approval_evaluator.py
def test_pattern5_keys_unknown_to_worker_fall_to_require_attention() -> None:
    """A rule with ONLY Layer B (Pattern 5 hook) matchers must fail defensively
    at the worker layer — no auto-approve, no crash, falls to require_attention.
    Pins the disjoint-namespace coexistence contract documented in
    context/projects/agent-teams/shared/decisions-approval-policies-schema.md.
    """
    policies = {"rules": [{
        "name": "deny linkedin posts",
        "match": {
            "tool_name": "mcp__Claude_in_Chrome__navigate",
            "target_url_pattern": "linkedin\\.com",
            "content_predicate": "publish",
        },
        "action": "auto_deny",
    }]}
    action, _, _ = evaluate_policy({"question": "Post to LinkedIn?"}, policies)
    assert action == "require_attention"
```

If a future predicate-vocabulary expansion accidentally adds `tool_name` to Layer A, this test breaks → forces the author to read this doc + make a deliberate coexistence decision.

## Cross-references

- Kanban #953 — `approval_policies` JSONB column owner
- Kanban #957 — Layer A (worker evaluator) Phase 1 impl
- Kanban #1274 — Layer B (Pattern 5 hook) impl
- Kanban #1205 — Mode B authorization-chain design (parent of #1274)
- `context/projects/agent-teams/shared/design/mode-b-authorization-chain.md` — full chain doctrine
- `api/src/services/approval_evaluator.py:127-159` — worker `_match_predicate` source of truth
- `api/tests/test_approval_evaluator.py:433` — `test_unknown_predicate_fails_rule` (typo-level coverage; #1279 adds Pattern-5-shaped coverage)
