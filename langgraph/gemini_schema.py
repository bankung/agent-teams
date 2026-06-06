"""Gemini native function-calling schema sanitizer (Kanban #1951).

WHY this exists
---------------
`ChatGoogleGenerativeAI` (native `langchain-google-genai`) converts every bound
tool's JSON schema into a Gemini `FunctionDeclaration` via
`convert_to_genai_function_declarations`. Gemini's `GenerateContentRequest`
validation is STRICTER than the OpenAI-compat shim: every `array` schema — at
the top level, nested inside `properties`/`items`, OR inside an
`anyOf`/`any_of` branch — MUST carry an `items` definition with a concrete
`type`. The OpenAI-compat path tolerated a bare `array`; Gemini native rejects
it with:

    400 INVALID_ARGUMENT: ...parameters.properties[body].any_of[1].items: missing field

Concrete trigger: `HttpPostInput.body: dict | list | str`. Pydantic emits the
`list` branch as `{"type": "array", "items": {}}` — `items` is present but
EMPTY. langchain-google-genai's `_dict_to_genai_schema` calls
`_dict_to_genai_schema({})` on that empty `items`, the `if schema:` guard treats
the empty dict as falsy and returns `None`, so the genai `Schema` lands with
`type=ARRAY, items=None` → Gemini 400.

WHAT this does
--------------
`sanitize_tools_for_gemini(tools)` takes the langchain tools the engine would
bind and returns a NEW list of `StructuredTool`s whose `args_schema` is a
sanitized JSON-schema dict. The walk guarantees every `array` node anywhere in
the schema (including inside `anyOf`/`any_of`) has a non-empty `items` carrying
a concrete `type`, defaulting a missing/empty `items` to `{"type": "string"}`.

This is applied ONLY on the google bind path (see
`nodes._bind_tools_safely`). It does NOT touch:
  - the registry's Tool objects or their Pydantic `input_schema`,
  - any tool's RUNTIME contract (http_post still accepts dict/list/str bodies —
    tool execution goes through `GLOBAL_REGISTRY.get(name).invoke(...)`, NOT
    through the bound langchain tool, which is used only to DECLARE the schema),
  - the bind path of any other provider (anthropic/openai/ollama/deepseek).

The langchain genai conversion accepts a plain dict for `args_schema`
(`_format_base_tool_to_function_declaration` branches on `isinstance(dict)`),
so swapping in the sanitized dict is the minimal hook that survives the
conversion intact.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

logger = logging.getLogger("langgraph.gemini_schema")

# Default `items.type` when an array declares no usable item type. STRING is the
# safest universal default — Gemini accepts it for any array, and the value is
# only used to satisfy the schema validator; the tool's real Pydantic model
# (used at invocation time) still governs actual argument coercion.
_DEFAULT_ITEMS_SCHEMA: dict[str, Any] = {"type": "string"}


def _is_array_node(node: dict[str, Any]) -> bool:
    """True iff this schema node declares an `array` type.

    Pydantic emits `"type": "array"`. Some tools may use a list-form type
    (`"type": ["array", "null"]`) — handle both so a future nullable list
    branch doesn't slip an items-less array past the sanitizer.
    """
    t = node.get("type")
    if t == "array":
        return True
    if isinstance(t, list) and "array" in t:
        return True
    return False


def _items_is_usable(items: Any) -> bool:
    """True iff `items` is a schema the genai converter will keep as non-None.

    The converter drops an empty `items` ({}) to None and also leaves an items
    schema with no resolvable `type`/`anyOf`/`$ref` unusable for Gemini. We
    require a dict that carries at least one type-bearing key.
    """
    if not isinstance(items, dict) or not items:
        return False
    # A type-bearing items schema is fine (incl. nested array/object).
    if items.get("type"):
        return True
    # anyOf/oneOf/$ref item schemas also resolve to a concrete shape downstream.
    if items.get("anyOf") or items.get("any_of") or items.get("oneOf") or items.get("$ref"):
        return True
    return False


def _sanitize_node(node: Any) -> None:
    """Recursively ensure every array node has a usable `items` (in place).

    Walks the standard JSON-schema containers Pydantic + langchain-google-genai
    care about: `properties`, `items`, `anyOf`/`any_of`, `oneOf`, `allOf`,
    `$defs`/`definitions`, and `additionalProperties` (when it is a schema).
    Mutates `node` in place — callers pass a deep copy.
    """
    if isinstance(node, list):
        for item in node:
            _sanitize_node(item)
        return
    if not isinstance(node, dict):
        return

    # Fix THIS node first if it is an array lacking usable items.
    if _is_array_node(node) and not _items_is_usable(node.get("items")):
        node["items"] = dict(_DEFAULT_ITEMS_SCHEMA)

    # Recurse into every sub-schema container.
    props = node.get("properties")
    if isinstance(props, dict):
        for sub in props.values():
            _sanitize_node(sub)

    items = node.get("items")
    if isinstance(items, (dict, list)):
        _sanitize_node(items)

    for combinator in ("anyOf", "any_of", "oneOf", "allOf"):
        branch = node.get(combinator)
        if isinstance(branch, list):
            for sub in branch:
                _sanitize_node(sub)

    for defs_key in ("$defs", "definitions"):
        defs = node.get(defs_key)
        if isinstance(defs, dict):
            for sub in defs.values():
                _sanitize_node(sub)

    addl = node.get("additionalProperties")
    if isinstance(addl, dict):
        _sanitize_node(addl)


def sanitize_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a deep-copied schema with every array given a usable `items`.

    Pure function — does not mutate the input. The returned dict is safe to
    hand to langchain-google-genai as a tool `args_schema`.
    """
    sanitized = copy.deepcopy(schema)
    _sanitize_node(sanitized)
    return sanitized


def _tool_json_schema(tool: Any) -> dict[str, Any] | None:
    """Extract a tool's args JSON schema as a plain dict, or None.

    Mirrors langchain-google-genai's own extraction in
    `_format_base_tool_to_function_declaration`: a dict args_schema is used
    verbatim, a Pydantic class is converted via `model_json_schema()`.
    """
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is None:
        return None
    if isinstance(args_schema, dict):
        return args_schema
    model_json_schema = getattr(args_schema, "model_json_schema", None)
    if callable(model_json_schema):
        return model_json_schema()
    # Pydantic v1 fallback (langchain still supports BaseModelV1).
    schema_fn = getattr(args_schema, "schema", None)
    if callable(schema_fn):
        return schema_fn()
    return None


def sanitize_tools_for_gemini(tools: list[Any]) -> tuple[list[Any], list[str]]:
    """Return (sanitized_tools, names_that_needed_sanitizing) for the google bind.

    For each langchain tool: build its args JSON schema, sanitize it, and — only
    if the sanitize changed the schema — rebuild a `StructuredTool` copy whose
    `args_schema` is the sanitized dict. Tools whose schema was already
    Gemini-valid are passed through UNCHANGED (same object), so the bind surface
    is byte-identical to today for everything except the arrays we had to fix.

    The second element of the tuple is the list of tool names that required a
    fix — useful for logging which tools would otherwise have 400'd.
    """
    from langchain_core.tools import StructuredTool

    out: list[Any] = []
    fixed: list[str] = []
    for tool in tools:
        schema = _tool_json_schema(tool)
        if schema is None:
            out.append(tool)
            continue
        sanitized = sanitize_json_schema(schema)
        if sanitized == schema:
            # Already Gemini-valid — pass the original tool through untouched.
            out.append(tool)
            continue

        # Rebuild a StructuredTool that DECLARES the sanitized schema but keeps
        # the original tool's runtime callables. The bound langchain tool is
        # never executed by the engine (execution goes through GLOBAL_REGISTRY),
        # so swapping args_schema only affects the declaration Gemini sees.
        rebuilt = StructuredTool(
            name=tool.name,
            description=tool.description,
            args_schema=sanitized,
            func=getattr(tool, "func", None),
            coroutine=getattr(tool, "coroutine", None),
        )
        out.append(rebuilt)
        fixed.append(tool.name)

    if fixed:
        logger.info(
            "gemini_schema: sanitized array-without-items in tool schemas: %s",
            ", ".join(sorted(fixed)),
        )
    return out, fixed
