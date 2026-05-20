"""Action-template loader (Kanban #1006, AC3).

Reads *.yaml files from .claude/templates/actions/ at first call,
validates each against ActionTemplateRead, and caches the result for the
FastAPI app's lifetime.  A malformed / missing file logs a WARNING and is
skipped — one bad YAML must not 500 the GET /api/templates/actions endpoint.

Cache reset: app restart.  Hot-reload is not supported in V1 (YAML files
are version-controlled; a schema bump bumps version semver).

Path resolution: the loader resolves the templates directory relative to the
*repository root* (two levels above this file: api/src/services/ → api/src/
→ api/ → repo_root → .claude/templates/actions/).  An explicit override via
ACTION_TEMPLATES_DIR env var is supported for testing.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml
from pydantic import ValidationError

from src.schemas.action_template import ActionTemplateRead

logger = logging.getLogger(__name__)

# Module-level cache — populated on first call to list_templates().
# None means "not yet loaded"; empty list means "loaded, zero valid templates".
_CACHE: list[ActionTemplateRead] | None = None


def _templates_dir() -> Path:
    """Return the absolute path to the templates directory.

    Resolution order:
    1. ACTION_TEMPLATES_DIR env var (override for tests / non-standard layout).
    2. <repo_root>/.claude/templates/actions/ — inferred from this file's path.
    """
    override = os.environ.get("ACTION_TEMPLATES_DIR")
    if override:
        return Path(override)
    # __file__ = api/src/services/action_templates.py
    # parents: [services/, src/, api/, repo_root]
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / ".claude" / "templates" / "actions"


def _load_templates() -> list[ActionTemplateRead]:
    """Scan the templates directory and parse every *.yaml file.

    Returns the list of successfully validated ActionTemplateRead instances.
    Files that fail YAML parse or Pydantic validation are logged and skipped.
    """
    tmpl_dir = _templates_dir()
    results: list[ActionTemplateRead] = []

    if not tmpl_dir.exists():
        logger.warning(
            "action_templates: directory %s does not exist — no templates loaded",
            tmpl_dir,
        )
        return results

    for yaml_file in sorted(tmpl_dir.glob("*.yaml")):
        try:
            raw = yaml_file.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "action_templates: cannot read %s — %s",
                yaml_file.name,
                exc,
            )
            continue

        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            logger.warning(
                "action_templates: YAML parse error in %s — %s",
                yaml_file.name,
                exc,
            )
            continue

        if not isinstance(data, dict):
            logger.warning(
                "action_templates: %s is not a YAML mapping — skipped",
                yaml_file.name,
            )
            continue

        # Inject `id` = `name` so the Read schema's `id` field is populated
        # from the YAML `name` field (AC3: FE chip row sends back template.id).
        data.setdefault("id", data.get("name", yaml_file.stem))

        try:
            template = ActionTemplateRead(**data)
        except (ValidationError, TypeError) as exc:
            logger.warning(
                "action_templates: validation error in %s — %s",
                yaml_file.name,
                exc,
            )
            continue

        results.append(template)
        logger.debug("action_templates: loaded %s v%s", template.name, template.version)

    logger.info(
        "action_templates: loaded %d template(s) from %s",
        len(results),
        tmpl_dir,
    )
    return results


def list_templates() -> list[ActionTemplateRead]:
    """Return the cached template list, loading on first call."""
    global _CACHE
    if _CACHE is None:
        _CACHE = _load_templates()
    return _CACHE


def get_template(name: str) -> ActionTemplateRead | None:
    """Look up a template by its `name` (the action_template_id on POST /api/tasks).

    Returns None when the name is not found.
    """
    for tmpl in list_templates():
        if tmpl.name == name:
            return tmpl
    return None


def reset_cache() -> None:
    """Clear the in-memory cache — forces a reload on the next call.

    Used by tests that need to point the loader at a custom directory.
    """
    global _CACHE
    _CACHE = None
