"""In-code webhook-to-task template registry (Kanban #1328 M4b).

Pre-X.5 (no ``task_templates`` table yet): hardcoded templates live in
``TEMPLATE_REGISTRY`` below. When X.5 lands, this module becomes a DB-backed
loader with the same ``get_template()`` signature so router code does not
change.

Template substitution is Mustache-flat:

  ``{{a.b.c}}`` → dot-path lookup into the supplied context dict.

A missing field raises ``MissingTemplateField`` so the router can surface a
422 with the exact field path that failed (no silent empty-string).

Out of scope (deferred / "v1 lean"):
  - Loops ``{{#each items}}`` — not supported.
  - Conditionals ``{{#if x}}`` — not supported.
  - Jinja-style filters — not supported.

Operators who need richer templating can wait for the X.5 DB-backed registry,
which will hold the template body as a Jinja string and run a sandboxed
``jinja2.Environment``.
"""

from __future__ import annotations

import json
import re
from typing import Any, Final

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MissingTemplateField(KeyError):
    """Raised by ``substitute`` when a ``{{path}}`` placeholder cannot resolve.

    Subclass of KeyError so existing callers that catch KeyError still trip on
    it, but the router catches it explicitly to map to HTTP 422 with the
    specific failing field path.
    """

    def __init__(self, field_path: str) -> None:
        self.field_path = field_path
        super().__init__(field_path)

    def __str__(self) -> str:  # pragma: no cover — convenience
        return self.field_path


# ---------------------------------------------------------------------------
# Template model
# ---------------------------------------------------------------------------


class WebhookTemplate(BaseModel):
    """One named webhook-to-task template.

    The two template strings are Mustache-flat (see ``substitute``). The four
    task fields (kind/type/priority + their defaults) seed the ``Task`` row
    when the router builds it; ``assigned_role`` is intentionally absent —
    inbound webhook tasks default to ``NULL`` (operator triage).
    """

    title_template: str
    description_template: str
    task_kind: str = "human"
    task_type: str = "feature"
    priority: int = 2


# ---------------------------------------------------------------------------
# Built-in templates (v1 lean — in-code dict; replace with DB-backed in X.5)
# ---------------------------------------------------------------------------


TEMPLATE_REGISTRY: Final[dict[str, WebhookTemplate]] = {
    # Calendly "invitee.created" — booking notification. The operator wires
    # Calendly's webhook (Account Settings → Webhooks) to this endpoint with
    # tag=calendly + the matching shared secret in the M3 vault under
    # ``webhook_calendly``.
    "calendly": WebhookTemplate(
        title_template="Booking: {{payload.name}} — {{payload.event_type}}",
        description_template=(
            "New booking via Calendly:\n"
            "  Name:       {{payload.name}}\n"
            "  Email:      {{payload.email}}\n"
            "  Event type: {{payload.event_type}}\n"
            "  Start time: {{payload.start_time}}\n\n"
            "Prepare brief; send confirmation by 24h before."
        ),
        task_kind="human",
        task_type="feature",
        priority=2,
    ),
    # GitHub "issues" event (opened). Operator wires per-repo webhook
    # (Settings → Webhooks → Add) with tag=github_issue + the matching shared
    # secret. The classification is `task_type='bug'` — issues land in the
    # bug bucket by default; operator can re-classify on triage.
    "github_issue": WebhookTemplate(
        title_template="GitHub issue: {{issue.title}} (#{{issue.number}})",
        description_template=(
            "From: {{issue.user.login}}\n"
            "URL:  {{issue.html_url}}\n\n"
            "{{issue.body}}"
        ),
        task_kind="human",
        task_type="bug",
        priority=2,
    ),
    # Generic website contact-form post. Operator's form backend forwards
    # name/email/message + a shared secret to this endpoint with tag=contact_form.
    "contact_form": WebhookTemplate(
        title_template="Contact form: {{name}}",
        description_template=(
            "From: {{name}} <{{email}}>\n\n"
            "{{message}}"
        ),
        task_kind="human",
        task_type="feature",
        priority=2,
    ),
}


# Special-cased default for tags with no registered template — the router
# pre-populates ``__tag`` + ``__pretty_payload`` into the context BEFORE running
# substitute() so operators still get a usable task even pre-configuration.
DEFAULT_FALLBACK_TEMPLATE: Final[WebhookTemplate] = WebhookTemplate(
    title_template="Webhook: {{__tag}}",
    description_template=(
        "Inbound webhook from `{{__tag}}` (no template registered).\n"
        "Full payload:\n"
        "```json\n"
        "{{__pretty_payload}}\n"
        "```"
    ),
    task_kind="human",
    task_type="feature",
    priority=2,
)


# ---------------------------------------------------------------------------
# Lookup + substitution
# ---------------------------------------------------------------------------


def get_template(tag: str) -> WebhookTemplate | None:
    """Return the named template, or None if no entry is registered.

    The router maps None → ``DEFAULT_FALLBACK_TEMPLATE`` so operators always
    get a task even for unconfigured tags.
    """
    return TEMPLATE_REGISTRY.get(tag)


_PLACEHOLDER_RE: Final[re.Pattern[str]] = re.compile(r"\{\{\s*([^\s{}]+)\s*\}\}")


def _walk_dot_path(payload: dict[str, Any], path: str) -> Any:
    """Walk a dot-path into a nested dict and return the value.

    Raises ``MissingTemplateField`` if any segment is missing OR if an
    intermediate value is not a dict (e.g. ``payload.user.login`` when
    ``payload.user`` is a string). The router catches this and emits a
    422 with the original path for the operator.
    """
    cur: Any = payload
    segments = path.split(".")
    for i, segment in enumerate(segments):
        if isinstance(cur, dict) and segment in cur:
            cur = cur[segment]
            continue
        # Either non-dict intermediate OR missing key — both surface the
        # SAME full path so the operator sees what they asked for.
        raise MissingTemplateField(path)
    return cur


def substitute(template_str: str, payload: dict[str, Any]) -> str:
    """Replace ``{{a.b.c}}`` placeholders with values from ``payload``.

    The value is rendered via ``str()`` (so ints, floats, bools render as
    Python repr; lists/dicts render via their default ``__str__``). Missing
    paths raise ``MissingTemplateField`` carrying the offending dot-path.

    The router has the option to wrap a single ``substitute`` call in
    ``try/except MissingTemplateField`` and convert to a 422 response.
    """

    def _replace(match: re.Match[str]) -> str:
        path = match.group(1)
        value = _walk_dot_path(payload, path)
        return str(value)

    return _PLACEHOLDER_RE.sub(_replace, template_str)


def pretty_dump_for_fallback(payload: dict[str, Any]) -> str:
    """Pretty-print a payload dict for the default-fallback description.

    Indent=2 for human readability; ``ensure_ascii=False`` so unicode in the
    inbound payload survives intact. Used by the router when wiring
    ``DEFAULT_FALLBACK_TEMPLATE`` — kept here so tests can exercise the same
    helper independently.
    """
    try:
        return json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        # Belt-and-braces — should be unreachable since FastAPI already
        # decoded the body via json.loads. ``default=str`` covers most
        # exotic types but a circular reference is the one remaining
        # failure mode.
        return repr(payload)
