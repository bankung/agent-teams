"""Auto-handoff spawn service (Kanban #1004).

When a task carrying `handoff_template_id` flips from `process_status != 5`
to `process_status = 5` via PATCH /api/tasks/{id}, the router calls
`spawn_child_from_handoff(...)` BEFORE committing — so the parent DONE-flip
and the child INSERT land in the same transaction (atomic from the caller's
perspective).

Loop guard (AC6): the child's `handoff_template_id` is explicitly set to
NULL. A chain of templates spawning further templates is structurally
impossible — the spawn hook reads the PATCHed task's `handoff_template_id`,
not the child's.

Title rendering: Python stdlib `str.format(parent_title=...)` is the chosen
renderer. The schema validator (`schemas/handoff_template.py::_validate_title_pattern`)
already enforces:
  - syntactically-valid `.format` template;
  - `{parent_title}` is referenced (signal-of-intent gate).
Runtime KeyError / IndexError on an unknown placeholder still surfaces from
`.format()` — we catch it here and surface 422 with a clear actionable
detail, atomically rolling back the parent flip.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import RecordStatus, TaskStatus
from src.models.handoff_template import HandoffTemplate
from src.models.task import Task

logger = logging.getLogger(__name__)

# Source-text-locked (#122 pattern). Pinned by
# test_handoff_templates_smoke.py::test_handoff_malformed_title_pattern_surfaces_422.
_DETAIL_TITLE_PATTERN_RENDER_FAILED = (
    "handoff_template id={template_id} title_pattern is malformed: "
    "{error}. Fix the template (PATCH /api/handoff-templates/{template_id}) "
    "and re-PATCH the parent to DONE."
)


async def spawn_child_from_handoff(
    session: AsyncSession,
    parent: Task,
) -> Task | None:
    """Build + add a child Task derived from the parent's handoff template.

    Returns the new Task (already added to the session via `session.add`)
    so the caller can flush / commit in the same transaction. Returns None
    when the template lookup fails (deleted / hard-deleted) — the caller
    logs and proceeds without raising.

    Raises HTTPException(422) when the template's `title_pattern` cannot be
    rendered (unknown placeholder, etc.). The caller is expected to bubble
    this up so the parent PATCH rejects atomically (no half-spawn).

    The child:
      - `project_id` = parent's
      - `parent_task_id` = parent's id
      - `handoff_template_id` = NULL  (LOOP GUARD AC6)
      - `title` = `template.title_pattern.format(parent_title=parent.title)`
      - `task_kind` / `task_type` / `priority` = template fields
      - `assigned_role` = template.default_assigned_role
      - `acceptance_criteria` = [{text, status='pending'} for text in template.ac_outline]
      - `description` = context block when `template.carry_context_to_comment=true`
      - `resume_context` = {"handoff": {"template_id": <id>, "template_version": null, "parent_task_id": <parent.id>}}
    """
    template_id = parent.handoff_template_id
    if template_id is None:  # defensive — caller checks this already
        return None

    template = await session.get(HandoffTemplate, template_id)
    if template is None or template.status == RecordStatus.DELETED:
        # Soft-deleted (or hard-gone) template — no spawn. Log a WARNING so
        # the operator can investigate; do not raise. The parent DONE-flip
        # still lands (it's a higher-level event than the auto-handoff).
        logger.warning(
            "handoff: parent #%d points at template id=%d which is "
            "missing or soft-deleted; spawn skipped",
            parent.id,
            template_id,
        )
        return None

    # Render the title. Schema-time validation guaranteed the pattern is
    # syntactically valid AND references {parent_title}. Other unknown
    # placeholders surface at runtime here; we re-raise as 422.
    try:
        rendered_title = template.title_pattern.format(parent_title=parent.title)
    except (KeyError, IndexError) as exc:
        logger.warning(
            "handoff: parent #%d template id=%d title_pattern render failed: %s",
            parent.id,
            template_id,
            exc,
        )
        raise HTTPException(
            status_code=422,
            detail=_DETAIL_TITLE_PATTERN_RENDER_FAILED.format(
                template_id=template_id, error=str(exc)
            ),
        ) from exc

    # Cap rendered title at 200 chars (tasks.title hard max via Pydantic
    # TaskCreate). A pathological template + long parent title could push
    # past — truncate with an ellipsis marker so the row still inserts.
    if len(rendered_title) > 200:
        rendered_title = rendered_title[:197] + "..."

    # Build acceptance_criteria from the template's ac_outline (list[str]).
    # Each entry → {text, status='pending'} dict so it round-trips through
    # the JSONB column identically to how the tasks router writes it
    # (AcceptanceCriterion.model_dump(mode='json')).
    acceptance_criteria: list[dict[str, Any]] = [
        {
            "text": text_entry,
            "status": "pending",
            "verified_by": None,
            "verified_at": None,
            "notes": None,
        }
        for text_entry in (template.ac_outline or [])
    ]

    # Optional context block on the child description.
    description: str | None = None
    if template.carry_context_to_comment:
        reason = parent.status_change_reason or "(none)"
        description = (
            f"Auto-spawned from parent #{parent.id} ({parent.title}).\n\n"
            f"Parent status_change_reason: {reason}\n"
        )

    # resume_context records template provenance (mirrors #1006 action_template
    # provenance pattern). `template_version` is reserved for future template-
    # versioning slices even though the current handoff_templates table has
    # no version column.
    resume_context = {
        "handoff": {
            "template_id": template.id,
            "template_version": None,
            "parent_task_id": parent.id,
        }
    }

    child = Task(
        project_id=parent.project_id,
        parent_task_id=parent.id,
        # AC6 LOOP GUARD — explicit NULL on the child:
        handoff_template_id=None,
        title=rendered_title,
        description=description,
        # process_status / priority / status default via DB / ORM.
        priority=template.default_priority,
        assigned_role=template.default_assigned_role,
        task_kind=template.task_kind,
        task_type=template.task_type,
        acceptance_criteria=acceptance_criteria if acceptance_criteria else None,
        resume_context=resume_context,
    )
    session.add(child)
    logger.info(
        "handoff: spawned child from parent #%d via template #%d "
        "(parent title=%r → child title=%r)",
        parent.id,
        template.id,
        parent.title,
        rendered_title,
    )
    return child
