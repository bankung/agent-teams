"""Mustache-style placeholder substitution for task templates (Kanban #1303).

A pure helper module — no DB, no FastAPI. `render_template(text, values)`
substitutes every `{{key}}` token in `text` with `values[key]`. A token whose
key is ABSENT from `values` is a hard ERROR (raises `MissingPlaceholderError`,
naming the missing key) — NOT a silent passthrough. This is intentional: a task
spawned from a template with an un-filled placeholder would otherwise ship a
literal `{{file}}` into a real Kanban task, which the operator never wants.

`render_ac_template` applies the same rendering to each AC object's `text` field
(the AC template array is `[{"text": "...{{placeholder}}...", ...}, ...]`) and
returns a NEW list with the `text` rendered, every other key preserved. This is
the slice's answer to the spec's "decide and document whether it also renders
the AC template array": YES — the AC `text` fields are rendered with the same
helper so a template's description and its acceptance criteria share one
placeholder vocabulary.

Token syntax (deliberately minimal — #1303 is not a templating engine):
  - `{{key}}` where `key` matches `[A-Za-z0-9_]+`. Optional surrounding
    whitespace inside the braces is tolerated: `{{ key }}` == `{{key}}`.
  - Anything not matching that token shape (a lone `{`, `{{}}`, `{{a-b}}`) is
    left verbatim — it is NOT a placeholder, so it neither renders nor errors.
  - Values are coerced to `str()` so non-string values (e.g. an int) substitute
    cleanly.
"""

from __future__ import annotations

import re
from typing import Any

# A placeholder token: {{ key }} with optional inner whitespace. The key is a
# conservative identifier ([A-Za-z0-9_]+) so we don't accidentally treat
# arbitrary `{{...}}` braces (JSON snippets, code samples) as placeholders.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


class MissingPlaceholderError(KeyError):
    """A `{{key}}` token had no matching entry in the supplied values dict.

    Subclasses KeyError (a missing-key error IS a KeyError) but carries the
    offending key as `.key` and a readable message. Raising rather than silently
    passing the token through is the load-bearing contract (#1303): an un-filled
    placeholder must never leak into a spawned task.
    """

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(key)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"missing placeholder value for {self.key!r}"


def render_template(text: str, values: dict[str, Any]) -> str:
    """Substitute every `{{key}}` token in `text` with `str(values[key])`.

    Raises `MissingPlaceholderError(key)` on the FIRST token whose key is not in
    `values`. Tokens are rendered left-to-right; the substituted text is NOT
    re-scanned for further tokens (no recursive expansion), so a value that
    itself contains `{{...}}` is emitted verbatim.

    Args:
        text: the template body (may contain zero or more `{{key}}` tokens).
        values: placeholder name -> value. Values are `str()`-coerced.

    Returns:
        The rendered string.

    Raises:
        MissingPlaceholderError: a referenced placeholder key is absent.
    """

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise MissingPlaceholderError(key)
        return str(values[key])

    return _PLACEHOLDER_RE.sub(_replace, text)


def render_ac_template(
    ac_template: list[dict[str, Any]], values: dict[str, Any]
) -> list[dict[str, Any]]:
    """Render the `text` field of each AC object in `ac_template`.

    Returns a NEW list of NEW dicts — the input is not mutated. Each object's
    `text` (if present and a str) is rendered via `render_template`; every other
    key is copied through unchanged. An object without a `text` key is copied
    verbatim (no error — a malformed AC object is the caller's concern, and the
    same missing-placeholder discipline only applies to actual `{{key}}` tokens).

    Raises:
        MissingPlaceholderError: any AC `text` references an absent placeholder.
    """
    rendered: list[dict[str, Any]] = []
    for obj in ac_template:
        new_obj = dict(obj)
        text_val = new_obj.get("text")
        if isinstance(text_val, str):
            new_obj["text"] = render_template(text_val, values)
        rendered.append(new_obj)
    return rendered
