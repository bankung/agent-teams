"""Email ingest pure-logic helpers (Kanban #1327 M4a).

Email-to-task ingest receives a Mailgun-shape JSON payload from an email
forwarding service (Cloudflare Email Routing + Worker by default — see
``context/standards/integrations/email-ingest-setup.md`` once promoted),
authenticates via a shared secret stored in the M3 vault, routes to a project,
and creates a task.

This module isolates the pure logic away from the HTTP / DB layer so each
piece is straightforward to unit-test:

  - ``parse_project_tag(to_address)``         — extract ``inbox+<tag>@...``
  - ``resolve_target_project(...)``           — DB lookup with default fallback
  - ``extract_body(req)``                     — prefer body_text, strip HTML
  - ``sanitize_filename(name)``               — strip path separators + cap
  - ``resolve_attachment_path(...)``          — anchored at project.working_path
                                                 or repo_root/_runtime fallback

The router is the only writer; it owns the session.add + commit.

Implementation notes:
  - ``_resolve_fallback_base`` from ``notification_router.py`` (Kanban #1285)
    handles the project.working_path resolution + Windows-on-Linux guard. We
    use a sibling helper here because the attachment fallback root is
    ``<repo_root>/_runtime/email_attachments`` (not ``<repo_root>/context/projects/<name>``
    where notifications land); the parts before the trailing component differ.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import RecordStatus
from src.models.project import Project

if TYPE_CHECKING:
    from src.schemas.email_ingest import EmailIngestRequest

logger = logging.getLogger(__name__)


# Regex for the project-tag form ``inbox+<tag>@<domain>``. Lowercase + digits
# + hyphen mirrors the project-name CHECK constraint in migration 0001.
_PROJECT_TAG_RE = re.compile(r"inbox\+([a-z0-9-]+)@", re.IGNORECASE)

# Compiled outside any function so the test for filename-sanitization can
# exercise the same predicate the router uses. NB: ``\\`` matches a literal
# backslash for Windows-pasted filenames.
_FILENAME_BAD_CHARS_RE = re.compile(r"[^A-Za-z0-9._-]")

# Simple HTML-strip regex — good enough for "the body had no plaintext part
# but did have an HTML part". The agent that processes the task can do a
# proper parse if needed; here we just want a readable description.
_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Source-text-locked detail strings — pinned by tests.
_DETAIL_PROJECT_NOT_FOUND = (
    "target project not found — set EMAIL_INGEST_DEFAULT_PROJECT or include "
    "'inbox+<projectname>@' in the 'to' field"
)


def parse_project_tag(to_address: str | None) -> str | None:
    """Return the tag in ``inbox+<tag>@<domain>`` form, or None when absent.

    Case-insensitive on the literal ``inbox+`` prefix; the captured group is
    returned in lowercase so a later DB lookup is deterministic regardless of
    how the operator's MTA cased the local-part. ``None`` / empty string in,
    ``None`` out.
    """
    if not to_address:
        return None
    m = _PROJECT_TAG_RE.search(to_address)
    if m is None:
        return None
    return m.group(1).lower()


async def resolve_target_project(
    session: AsyncSession,
    to_address: str | None,
    default_name: str,
) -> Project:
    """Resolve the project the inbound email is routed to.

    Order:
      1. ``inbox+<tag>@`` in ``to_address`` → look up by name. If hit + active,
         return it. If miss / inactive, fall through to default (NOT 404 — a
         bad tag is operator typo, default is the safe inbox).
      2. ``default_name`` (from env ``EMAIL_INGEST_DEFAULT_PROJECT``) → look up
         by name; if missing or soft-deleted → 404 with the fixed hint string.

    The 404 detail string is locked by the router-level test
    ``test_post_email_default_project_missing_returns_404``.
    """
    tag = parse_project_tag(to_address)
    if tag is not None:
        project = await _fetch_active_project_by_name(session, tag)
        if project is not None:
            return project
        # Tag didn't resolve — log and fall through to the default. A typo'd
        # `inbox+myporject@` should not bounce the email; it should land in
        # the default inbox so the operator can triage.
        logger.info(
            "email_ingest: tag %r resolved to no active project; falling back "
            "to default %r",
            tag, default_name,
        )

    default = await _fetch_active_project_by_name(session, default_name)
    if default is None:
        raise HTTPException(
            status_code=404,
            detail=_DETAIL_PROJECT_NOT_FOUND,
        )
    return default


async def _fetch_active_project_by_name(
    session: AsyncSession, name: str
) -> Project | None:
    """Look up a project by ``name`` with ``status=ACTIVE``. None on miss.

    Soft-deleted projects (``status=0``) are treated as not found here — an
    archived project should not be a destination for new inbound work.
    """
    stmt = (
        select(Project)
        .where(Project.name == name)
        .where(Project.status == RecordStatus.ACTIVE)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def extract_body(req: "EmailIngestRequest") -> str:
    """Prefer ``body_text``; else strip HTML tags from ``body_html``; else
    return the fixed placeholder ``"(empty body)"``.

    The agent that processes the task can do a richer parse downstream. Here
    we just want readable description text — no `&amp;` decode, no signature
    stripping, no quote collapse. Lean by design (#1327 Out of scope).
    """
    if req.body_text:
        return req.body_text
    if req.body_html:
        stripped = _HTML_TAG_RE.sub("", req.body_html)
        # Collapse runs of whitespace introduced by the regex sub.
        return re.sub(r"\s+\n", "\n", stripped).strip() or "(empty body)"
    return "(empty body)"


def sanitize_filename(name: str) -> str:
    """Strip path separators + replace non-``[A-Za-z0-9._-]`` chars with ``_``,
    cap at 100 characters.

    Path-traversal defense: ``..`` segments + leading slashes are removed
    BEFORE the char-class sub — a payload like ``../../etc/passwd`` collapses
    to ``etcpasswd`` (acceptable; the resulting path is still under the target
    directory because the sub strips the slashes too).
    """
    # Strip path separators outright + drop any ``..`` literal.
    cleaned = name.replace("/", "").replace("\\", "").replace("..", "")
    # Replace anything not in the safe set with ``_``.
    safe = _FILENAME_BAD_CHARS_RE.sub("_", cleaned)
    # Cap length. A leading/trailing dash or underscore is fine on every FS we
    # target; no need to strip those.
    safe = safe[:100]
    # Defensive: a payload of `///` would collapse to empty — fall back to a
    # placeholder so the path stays well-formed.
    return safe or "unnamed"


def resolve_attachment_base(
    project: Project, repo_root: Path
) -> Path:
    """Return the absolute directory under which attachments are written.

    - If ``project.working_path`` is set + is an absolute path that exists →
      ``<working_path>/data/ingest``.
    - Else (working_path null OR Windows-path-on-Linux OR missing) →
      ``<repo_root>/_runtime/email_attachments``.

    Mirrors the ``notification_router._resolve_fallback_base`` predicate (the
    is_absolute() + exists() guard catches Windows-style paths on Linux + an
    unmounted volume). Logs a WARNING on fallback so the operator can fix the
    project row.
    """
    if project.working_path:
        candidate = Path(project.working_path)
        if candidate.is_absolute() and candidate.exists():
            return candidate / "data" / "ingest"
        logger.warning(
            "email_ingest: project.working_path %r is not usable on this "
            "platform (is_absolute=%s, exists=%s); falling back to "
            "repo_root/_runtime/email_attachments",
            project.working_path,
            candidate.is_absolute(),
            candidate.exists(),
        )
    return repo_root / "_runtime" / "email_attachments"


def resolve_attachment_path(
    project: Project,
    task_id: int,
    filename: str,
    repo_root: Path,
) -> Path:
    """Combine ``resolve_attachment_base`` + ``sanitize_filename`` into the
    final absolute path the router writes to.

    Filename collisions across attachments on the SAME task are handled by the
    ``<task_id>-<sanitized>`` prefix; an attachment list with two ``invoice.pdf``
    entries would clobber, but that's a payload-side mistake and outside the
    M4a contract — the agent processing the task will see one survivor on disk
    and the description will list both with the same path (downstream auditor
    flags the discrepancy).
    """
    base = resolve_attachment_base(project, repo_root)
    safe_name = sanitize_filename(filename)
    return base / f"{task_id}-{safe_name}"
