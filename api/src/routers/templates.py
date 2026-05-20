"""Action-templates router (Kanban #1006, AC3).

Mounted at `/api/templates`.  Exposes a single read-only endpoint:
  GET /api/templates/actions — list all loaded action templates.

No `X-Project-Id` header required — templates are global, not per-project.
The response is powered by the in-memory cache in
`src.services.action_templates`; a malformed YAML file is skipped with a
WARNING logged at load time, so this endpoint never 500s due to a bad file.
"""

from __future__ import annotations

from fastapi import APIRouter

from src.schemas.action_template import ActionTemplateRead
from src.services.action_templates import list_templates

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("/actions", response_model=list[ActionTemplateRead])
async def list_action_templates() -> list[ActionTemplateRead]:
    """Kanban #1006 (AC3) — list all starter action templates.

    Returns every successfully loaded template in alphabetical order by name.
    The list is populated from .claude/templates/actions/*.yaml at first call
    and cached for the app's lifetime.

    No `X-Project-Id` header required — templates are global.

    The chip row on the task-create modal (AC5, dev-sr-frontend follow-up)
    renders directly from this response: each item carries `id`, `name`,
    `description`, `default_task_kind`, `default_task_type`, `default_priority`,
    `ac_outline`, `hints`, and `suggested_attachments`.
    """
    return list_templates()
