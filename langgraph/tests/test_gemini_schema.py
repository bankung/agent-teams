"""Kanban #1951 — Gemini native function-calling schema sanitizer tests.

Locks the fix for the 400 INVALID_ARGUMENT Gemini throws when a bound tool's
schema has an `array` (top-level / nested / inside anyOf) without a usable
`items`. Concrete trigger: `HttpPostInput.body: dict | list | str` whose `list`
branch is `{"type": "array", "items": {}}`.

Two layers:
  1. Pure-function tests on `sanitize_json_schema` — no provider SDK needed.
     POSITIVE: the sanitizer DOES inject items into bare arrays (incl. anyOf
     and nested). NEGATIVE: a schema with NO arrays is returned byte-identical
     (the sanitizer doesn't whack non-array schemas).
  2. genai-conversion integration test (skipped if langchain-google-genai is
     not importable) — proves the sanitized real registry tools convert to
     Gemini-valid FunctionDeclarations with NO array lacking items.type, and
     that the UNSANITIZED http_post still reproduces the bug (test is not
     vacuous).
"""

from __future__ import annotations

import pytest

from gemini_schema import sanitize_json_schema, sanitize_tools_for_gemini


# ---------------------------------------------------------------------------
# Layer 1 — pure sanitizer logic (no provider SDK)
# ---------------------------------------------------------------------------


def test_bare_array_inside_anyof_gets_items():
    """The http_post `body` shape: anyOf with a bare-array branch.

    POSITIVE: the array branch gains a concrete items.type.
    Locks: anyOf[1] (the array) must end up with items {"type": "string"}.
    """
    schema = {
        "type": "object",
        "properties": {
            "body": {
                "anyOf": [
                    {"type": "object", "additionalProperties": True},
                    {"type": "array", "items": {}},  # the offending branch
                    {"type": "string"},
                ]
            }
        },
    }
    out = sanitize_json_schema(schema)
    array_branch = out["properties"]["body"]["anyOf"][1]
    assert array_branch["type"] == "array"
    assert array_branch["items"] == {"type": "string"}, array_branch
    # The other branches are untouched (object + string survive verbatim).
    assert out["properties"]["body"]["anyOf"][0]["type"] == "object"
    assert out["properties"]["body"]["anyOf"][2]["type"] == "string"


def test_top_level_and_nested_bare_arrays_get_items():
    """Arrays at the top property level AND nested inside items/properties."""
    schema = {
        "type": "object",
        "properties": {
            "tags": {"type": "array"},  # missing items entirely
            "matrix": {"type": "array", "items": {"type": "array"}},  # nested bare
        },
    }
    out = sanitize_json_schema(schema)
    assert out["properties"]["tags"]["items"] == {"type": "string"}
    # Outer array keeps its (now-fixed) inner array; inner array gains items.
    inner = out["properties"]["matrix"]["items"]
    assert inner["type"] == "array"
    assert inner["items"] == {"type": "string"}


def test_array_with_usable_items_is_left_alone():
    """An array that already declares items.type must NOT be rewritten.

    NEGATIVE assertion: the sanitizer is surgical — a Gemini-valid array is
    returned unchanged (no clobbering an existing `{"type": "integer"}`).
    """
    schema = {
        "type": "object",
        "properties": {
            "ids": {"type": "array", "items": {"type": "integer"}},
        },
    }
    out = sanitize_json_schema(schema)
    assert out["properties"]["ids"]["items"] == {"type": "integer"}


def test_schema_without_arrays_is_byte_identical():
    """A schema with no arrays anywhere is returned equal to the input.

    NEGATIVE: proves the sanitizer doesn't gratuitously mutate non-array
    schemas — only arrays are touched. (Pairs with the positive tests above so
    the equality check can't vacuously pass against an all-array input.)
    """
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "count": {"type": "integer"},
            "meta": {"type": "object", "properties": {"k": {"type": "string"}}},
        },
        "required": ["name"],
    }
    out = sanitize_json_schema(schema)
    assert out == schema
    # And the input itself was not mutated (pure function).
    assert "items" not in schema["properties"]["name"]


def test_sanitize_does_not_mutate_input():
    """`sanitize_json_schema` is pure — input dict is untouched."""
    schema = {"type": "array", "items": {}}
    out = sanitize_json_schema(schema)
    assert schema["items"] == {}  # input unchanged
    assert out["items"] == {"type": "string"}  # output fixed


def test_allof_items_schema_passes_through_unclobbered():
    """#1961 nit: an items dict containing allOf is USABLE — must NOT be replaced.

    POSITIVE: allOf-items survive the sanitizer intact.
    NEGATIVE: a genuinely empty items {} on a sibling array IS replaced.
    """
    schema = {
        "type": "object",
        "properties": {
            "refs": {
                "type": "array",
                "items": {"allOf": [{"type": "string"}, {"minLength": 1}]},
            },
            "bare": {"type": "array", "items": {}},
        },
    }
    out = sanitize_json_schema(schema)
    # allOf-items must survive unchanged.
    assert out["properties"]["refs"]["items"] == {"allOf": [{"type": "string"}, {"minLength": 1}]}
    # bare items must be fixed.
    assert out["properties"]["bare"]["items"] == {"type": "string"}


def test_cyclic_schema_terminates():
    """#1961 nit: a cyclic schema dict does NOT cause infinite recursion.

    copy.deepcopy handles cycles via memo internally, so sanitize_json_schema
    receives a non-cyclic deep copy — but we also guard _sanitize_node with an
    id()-based visited set as defence-in-depth. This test builds a shallow cycle
    (a dict that points back to itself via 'properties') and verifies the call
    returns without RecursionError.
    """
    # Build a cyclic dict WITHOUT deepcopy (simulate a pathological input).
    import gemini_schema as _mod

    cyclic: dict = {"type": "object", "properties": {}}
    cyclic["properties"]["self"] = cyclic  # type: ignore[assignment]

    # _sanitize_node operates in-place and should not recurse infinitely.
    # We call it directly (not through sanitize_json_schema which would deepcopy).
    _mod._sanitize_node(cyclic)  # must return without RecursionError


# ---------------------------------------------------------------------------
# Layer 2 — real registry tools + genai conversion (provider SDK gated)
# ---------------------------------------------------------------------------


def _genai_available() -> bool:
    try:
        import langchain_google_genai._function_utils  # noqa: F401
    except Exception:
        return False
    return True


pytestmark_genai = pytest.mark.skipif(
    not _genai_available(),
    reason="langchain-google-genai not importable (runs in the langgraph container)",
)


@pytestmark_genai
def test_http_post_flagged_as_needing_sanitizing():
    """sanitize_tools_for_gemini flags a tool whose args_schema contains a
    bare array (no items).

    Uses a SYNTHETIC tool stub (not from the real registry) so this test is
    not sensitive to pydantic-version-dependent schema emission.
    (2026-06-10: on pydantic 2.13.4 the registry path already emits valid
    items, so the negative "must-be-broken" precondition was vacuous.)

    sanitize_tools_for_gemini accepts any object with name/description and a
    dict args_schema — no LangChain import needed.
    """
    import types

    # A dict args_schema is used verbatim by _tool_json_schema, so we can
    # inject a bare array without any pydantic / StructuredTool machinery.
    synthetic = types.SimpleNamespace(
        name="synthetic_http_post",
        description="test tool with bare array",
        args_schema={
            "type": "object",
            "properties": {"tags": {"type": "array"}},  # bare: no items
            "required": ["tags"],
        },
    )

    _, fixed = sanitize_tools_for_gemini([synthetic])
    assert "synthetic_http_post" in fixed, fixed


@pytestmark_genai
def test_all_registry_tools_convert_to_valid_gemini_declarations():
    """After sanitizing the real registry, NO genai array schema lacks items.type.

    POSITIVE: sanitized tools convert cleanly to Gemini FunctionDeclarations.
    The 'unsanitized must reproduce the bug' precondition is dropped — schema
    emission is pydantic-version-dependent (see 2026-06-10 investigation;
    pydantic 2.13.4 emits valid items on the registry path, but the runtime
    per-project builder still hits the bug).  Non-vacuousness is proved via a
    synthetic bare-array tool in test_http_post_flagged_as_needing_sanitizing.
    """
    from langchain_google_genai._function_utils import (
        convert_to_genai_function_declarations,
    )

    from tools import GLOBAL_REGISTRY

    lc_tools = GLOBAL_REGISTRY.all_tools_as_langchain()

    def array_problems(schema, path):
        problems = []
        if schema is None:
            return problems
        tname = getattr(getattr(schema, "type", None), "name", "")
        if tname == "ARRAY":
            items = getattr(schema, "items", None)
            items_t = (
                getattr(getattr(items, "type", None), "name", None)
                if items is not None
                else None
            )
            if items is None or not items_t or items_t == "TYPE_UNSPECIFIED":
                problems.append(f"{path}: ARRAY missing items.type")
        for k, v in (getattr(schema, "properties", None) or {}).items():
            problems += array_problems(v, f"{path}.properties[{k}]")
        if getattr(schema, "items", None) is not None:
            problems += array_problems(schema.items, f"{path}.items")
        for i, sub in enumerate(getattr(schema, "any_of", None) or []):
            problems += array_problems(sub, f"{path}.any_of[{i}]")
        return problems

    def all_problems(tools):
        gt_list = convert_to_genai_function_declarations(tools)
        problems = []
        for tool_obj in gt_list:
            for fd in tool_obj.function_declarations or []:
                problems += array_problems(fd.parameters, fd.name)
        return problems

    # POSITIVE: after sanitize, the full registry converts with zero bare arrays.
    sanitized, _ = sanitize_tools_for_gemini(lc_tools)
    assert all_problems(sanitized) == [], all_problems(sanitized)
