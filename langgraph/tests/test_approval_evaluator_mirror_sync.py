"""Structural (source-level) parity test — Kanban #2840.

Both approval_evaluator.py copies declare themselves a "VERBATIM mirror" of
each other (see either file's module docstring), but nothing enforced that
claim at the SOURCE level — only at the BEHAVIORAL level
(test_approval_evaluator_parity.py / test_approval_evaluator_parity_fixture.py
run both copies against the same inputs and assert equal outputs).

That's exactly why the #2840 drift went undetected: #2701 made
`_extract_amount_usd` lazy in the api copy only (threading `amount` as an
eager `_match_predicate` param in langgraph's copy vs. computing it inline
in api's copy) — a pure REFACTOR with byte-identical behavior for every
input. The behavioral parity suites all still passed; nothing caught the
one-sided edit.

This test closes that gap with an AST-level structural diff: for every
top-level function present in BOTH files, strip docstrings (the two files
document themselves differently on purpose — langgraph's is terse and
defers to the api copy, see its module docstring) and compare the
remaining AST. Comments are never part of the AST at all (Python discards
them at the tokenizer), so this is naturally insensitive to comment-only
differences too (e.g. the annotated regex-pattern comments in api's
_AMOUNT_RE vs. langgraph's plain one). Module-level statements (the
differently-named `logger = logging.getLogger(...)`, the module docstring)
are intentionally NOT compared — only function bodies are, since "logic"
drift is what actually causes a behavioral divergence risk.
"""

from __future__ import annotations

import ast
from pathlib import Path

# /repo is the bind-mount root inside the langgraph container (matches the
# convention already used by test_approval_evaluator_parity.py and
# test_approval_evaluator_parity_fixture.py in this same directory).
_REPO_ROOT = Path("/repo")
_LANGGRAPH_FILE = _REPO_ROOT / "langgraph" / "approval_evaluator.py"
_API_FILE = _REPO_ROOT / "api" / "src" / "services" / "approval_evaluator.py"


def _strip_docstrings(tree: ast.AST) -> ast.AST:
    """Remove the leading docstring Expr-statement from every body, in place.

    A "docstring" here is Python's own convention: the first statement of
    any body (module / function / class) that is a bare string-literal
    expression. Mutates `tree` and returns it for chaining convenience.
    """
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            del body[0]
    return tree


def _top_level_functions(path: Path) -> dict[str, ast.AST]:
    tree = _strip_docstrings(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_approval_evaluator_source_files_exist() -> None:
    """Sanity guard — a missing file must fail loudly, not silently pass
    the diff tests below with an empty function set."""
    assert _LANGGRAPH_FILE.is_file(), f"missing {_LANGGRAPH_FILE}"
    assert _API_FILE.is_file(), f"missing {_API_FILE}"


def test_approval_evaluator_mirrors_share_identical_function_set() -> None:
    """Same top-level function names on both sides — catches a function
    added/removed on only one copy."""
    langgraph_fns = _top_level_functions(_LANGGRAPH_FILE)
    api_fns = _top_level_functions(_API_FILE)
    assert set(langgraph_fns) == set(api_fns), (
        f"approval_evaluator.py mirrors define different function sets: "
        f"langgraph-only={sorted(set(langgraph_fns) - set(api_fns))}, "
        f"api-only={sorted(set(api_fns) - set(langgraph_fns))}"
    )


def test_approval_evaluator_mirrors_have_identical_function_bodies() -> None:
    """AST-diff (docstrings stripped) every shared function.

    This is the test that would have failed the #2840 drift: langgraph's
    `_match_predicate` carried an extra `amount` parameter and `_match_group`
    computed it eagerly, while api's copy computed it lazily inline —
    behaviorally identical, structurally different.
    """
    langgraph_fns = _top_level_functions(_LANGGRAPH_FILE)
    api_fns = _top_level_functions(_API_FILE)
    shared = set(langgraph_fns) & set(api_fns)
    assert shared, "no shared functions found — parser or path problem"

    mismatches = []
    for name in sorted(shared):
        lg_dump = ast.dump(langgraph_fns[name], annotate_fields=False)
        api_dump = ast.dump(api_fns[name], annotate_fields=False)
        if lg_dump != api_dump:
            mismatches.append(name)

    assert not mismatches, (
        f"approval_evaluator.py mirrors drifted in function(s) {mismatches} "
        f"— edit BOTH langgraph/approval_evaluator.py and "
        f"api/src/services/approval_evaluator.py together (see either "
        f"file's module docstring), or update this test if the drift is "
        f"an intentional, documented divergence."
    )
