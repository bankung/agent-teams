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
    """The real registry's http_post is the tool that needs the fix."""
    from tools import GLOBAL_REGISTRY

    lc_tools = GLOBAL_REGISTRY.all_tools_as_langchain()
    _, fixed = sanitize_tools_for_gemini(lc_tools)
    assert "http_post" in fixed, fixed


@pytestmark_genai
def test_all_registry_tools_convert_to_valid_gemini_declarations():
    """After sanitizing, NO genai array schema anywhere lacks items.type.

    Also asserts the UNSANITIZED http_post still reproduces the bug, so the
    positive assertion can't pass vacuously (the bug is real + the fix fixes
    it).
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

    # NEGATIVE: unsanitized tools DO have the bug (proves non-vacuous).
    assert all_problems(lc_tools), (
        "expected unsanitized http_post to reproduce the array-without-items bug"
    )

    # POSITIVE: sanitized tools are fully Gemini-valid.
    sanitized, _ = sanitize_tools_for_gemini(lc_tools)
    assert all_problems(sanitized) == [], all_problems(sanitized)
