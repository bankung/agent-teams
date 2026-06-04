"""Daily-digest email template renderer and open-flags query helper (Kanban #1217).

Two responsibilities:
1. Template rendering: `render_subject`, `render_text`, `render_html`.
2. Open-flags query: `fetch_open_audit_flags` — flag task summaries across
   all active (non-killed, non-paused) projects.

Template design constraints (spam hygiene): no external images/tracking pixels,
minimal inline CSS, plaintext alternative mirrors HTML.
Deep links: `/review?flag=<id>` served at `web/app/review/page.tsx`.
"""

from __future__ import annotations

import logging
from datetime import date as _date
from typing import Any

from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import RecordStatus, TaskInteractionKind, TaskStatus
from src.models.project import Project
from src.models.task import Task
from src.settings import get_settings

logger = logging.getLogger(__name__)

# Salt is action-scoped so the same SECRET_KEY can be reused for future token
# types without cross-action forgery risk (Kanban #1437).
_OPTOUT_SALT = "digest-optout-v1"


def make_optout_token(project_id: int) -> str:
    """Produce a signed opt-out token for the given project_id.

    Token is URL-safe, HMAC-signed, and carries a 90-day expiry.
    Validated server-side by `verify_optout_token`.
    """
    s = URLSafeTimedSerializer(get_settings().secret_key, salt=_OPTOUT_SALT)
    return s.dumps({"pid": project_id, "action": "digest_optout"})


def verify_optout_token(token: str, max_age_seconds: int = 60 * 60 * 24 * 90) -> dict:
    """Verify and decode a signed opt-out token.

    Returns the payload dict on success.
    Raises `itsdangerous.BadSignature` / `itsdangerous.SignatureExpired`
    (both subclasses of `itsdangerous.BadData`) on failure — callers should
    catch `BadData` for a unified error path.
    """
    s = URLSafeTimedSerializer(get_settings().secret_key, salt=_OPTOUT_SALT)
    return s.loads(token, max_age=max_age_seconds)  # type: ignore[return-value]

# Deep-link path — must match web/app/review/page.tsx route.
_FLAG_DEEP_LINK_PATH = "/review?flag={id}"


def render_subject(flag_count: int, date: _date | str) -> str:
    """Build the email subject line.

    Examples:
        "Digest 2026-05-22 — 3 open flags"
        "Digest 2026-05-22 — no open flags"
    """
    date_str = str(date)
    if flag_count == 0:
        return f"Digest {date_str} — no open flags"
    noun = "flag" if flag_count == 1 else "flags"
    return f"Digest {date_str} — {flag_count} open {noun}"


def _unpack_flag(flag: dict[str, Any], base_url: str) -> tuple[Any, str, str, Any, str, str, str]:
    """Extract the 7 display fields from a flag dict + compose the deep link."""
    fid = flag.get("id", "?")
    project = flag.get("project", "?")
    title = str(flag.get("title", ""))[:120]
    streak = flag.get("streak", 1)
    severity = flag.get("severity") or "unspecified"
    verdict = flag.get("verdict") or "review"
    link = base_url + _FLAG_DEEP_LINK_PATH.format(id=fid)
    return fid, project, title, streak, severity, verdict, link


def render_text(payload: dict[str, Any]) -> str:
    """Render the digest as plain text."""
    date_str = payload.get("date", "")
    flags: list[dict[str, Any]] = payload.get("flags") or []
    base_url = (payload.get("base_url") or "").rstrip("/")
    # Kanban #1437 — project_id defaults to 1 (agent-teams control project).
    project_id: int = int(payload.get("project_id") or 1)

    lines: list[str] = [
        f"Agent-Teams Daily Digest — {date_str}",
        "=" * 40,
        "",
    ]

    if not flags:
        lines += ["No open audit flags today.", ""]
    else:
        lines.append(f"Open audit flags ({len(flags)}):")
        for flag in flags:
            fid, project, title, streak, severity, verdict, link = _unpack_flag(flag, base_url)
            lines.append(
                f"  - [#{fid}] {project}: {title}"
                f" (streak={streak}, severity={severity}, verdict={verdict})"
            )
            lines.append(f"    Deep link: {link}")
        lines.append("")

    # Kanban #1223 — skill/runbook stub proposals (optional — present when
    # the digest pipeline ran the detector this cycle).
    skill_stubs: dict[str, Any] = payload.get("skill_stubs") or {}
    if skill_stubs:
        proposed_count: int = int(skill_stubs.get("proposed_count") or 0)
        stub_dir: str = str(skill_stubs.get("stub_dir") or "")
        if proposed_count > 0 and stub_dir:
            noun = "stub" if proposed_count == 1 else "stubs"
            lines.append(
                f"Skill/runbook proposals: {proposed_count} new {noun} proposed."
            )
            lines.append(f"  Review at: {stub_dir}")
            lines.append("")
        elif proposed_count == 0:
            lines.append("Skill/runbook proposals: no new patterns detected.")
            lines.append("")

    # Kanban #1222 — stale-doc curator section (optional — present when the
    # digest pipeline ran the curator this cycle).
    stale_docs: dict[str, Any] = payload.get("stale_docs") or {}
    if stale_docs:
        stale_count: int = int(stale_docs.get("stale_count") or 0)
        contradiction_count: int = int(stale_docs.get("contradiction_count") or 0)
        report_path_sd: str = str(stale_docs.get("report_path") or "")
        scanned: int = int(stale_docs.get("scanned_count") or 0)
        if stale_count > 0 or contradiction_count > 0:
            lines.append(
                f"Stale-doc audit: {stale_count} stale, "
                f"{contradiction_count} contradiction flag(s) "
                f"(scanned {scanned} docs)."
            )
            if report_path_sd:
                lines.append(f"  Report: {report_path_sd}")
            lines.append("")
        else:
            lines.append(f"Stale-doc audit: all {scanned} docs fresh, no contradictions.")
            lines.append("")

    token = make_optout_token(project_id)
    optout_url = f"{base_url}/api/notifications/digest-optout?token={token}"
    lines += [
        "--",
        "This digest was generated by agent-teams.",
        f"Unsubscribe: {optout_url}",
    ]
    return "\n".join(lines)


def render_html(payload: dict[str, Any]) -> str:
    """Render the digest as minimal HTML suitable for email delivery."""
    date_str = payload.get("date", "")
    flags: list[dict[str, Any]] = payload.get("flags") or []
    base_url = (payload.get("base_url") or "").rstrip("/")
    # Kanban #1437 — project_id defaults to 1 (agent-teams control project).
    project_id: int = int(payload.get("project_id") or 1)

    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="UTF-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
        f"<title>Agent-Teams Digest {_esc(date_str)}</title>",
        "</head>",
        '<body style="font-family: Arial, Helvetica, sans-serif; font-size: 14px; color: #222; max-width: 600px; margin: 0 auto; padding: 16px;">',
        f'<h1 style="font-size: 18px; color: #333; border-bottom: 1px solid #ddd; padding-bottom: 8px;">Agent-Teams Daily Digest &mdash; {_esc(date_str)}</h1>',
    ]

    if not flags:
        parts.append("<p>No open audit flags today.</p>")
    else:
        parts.append(f"<p><strong>Open audit flags ({len(flags)}):</strong></p>")
        parts.append('<table style="width:100%; border-collapse:collapse;">')
        for flag in flags:
            fid, project, title, streak, severity, verdict, link = _unpack_flag(flag, base_url)
            parts.append('<tr><td style="padding: 8px 0; border-bottom: 1px solid #eee;">')
            parts.append(
                f'<strong>[#{fid}] {_esc(project)}:</strong> {_esc(title)}'
            )
            parts.append(
                f'<br><span style="font-size: 12px; color: #777;">'
                f"streak={streak}, severity={_esc(severity)}, verdict={_esc(verdict)}"
                f"</span>"
            )
            parts.append(
                f'<br><a href="{_esc(link)}" style="color: #1a73e8;">Review flag #{fid}</a>'
            )
            parts.append("</td></tr>")
        parts.append("</table>")

    # Kanban #1223 — skill/runbook stub proposals section.
    skill_stubs_h: dict[str, Any] = payload.get("skill_stubs") or {}
    if skill_stubs_h:
        proposed_count_h: int = int(skill_stubs_h.get("proposed_count") or 0)
        stub_dir_h: str = str(skill_stubs_h.get("stub_dir") or "")
        parts.append('<hr style="border: none; border-top: 1px solid #eee; margin: 16px 0;">')
        if proposed_count_h > 0 and stub_dir_h:
            noun_h = "stub" if proposed_count_h == 1 else "stubs"
            parts.append(
                f'<p><strong>Skill/runbook proposals:</strong> '
                f'{proposed_count_h} new {noun_h} proposed.</p>'
            )
            parts.append(
                f'<p style="font-size: 12px; color: #555;">Review at: '
                f'<code>{_esc(stub_dir_h)}</code></p>'
            )
        else:
            parts.append(
                "<p><strong>Skill/runbook proposals:</strong> no new patterns detected.</p>"
            )

    # Kanban #1222 — stale-doc curator section.
    stale_docs_h: dict[str, Any] = payload.get("stale_docs") or {}
    if stale_docs_h:
        stale_count_h: int = int(stale_docs_h.get("stale_count") or 0)
        contradiction_count_h: int = int(stale_docs_h.get("contradiction_count") or 0)
        report_path_h: str = str(stale_docs_h.get("report_path") or "")
        scanned_h: int = int(stale_docs_h.get("scanned_count") or 0)
        parts.append('<hr style="border: none; border-top: 1px solid #eee; margin: 16px 0;">')
        if stale_count_h > 0 or contradiction_count_h > 0:
            parts.append(
                f'<p><strong>Stale-doc audit:</strong> '
                f'{stale_count_h} stale, {contradiction_count_h} contradiction flag(s) '
                f'(scanned {scanned_h} docs).</p>'
            )
            if report_path_h:
                parts.append(
                    f'<p style="font-size: 12px; color: #555;">Report: '
                    f'<code>{_esc(report_path_h)}</code></p>'
                )
        else:
            parts.append(
                f'<p><strong>Stale-doc audit:</strong> all {scanned_h} docs fresh, '
                f'no contradictions.</p>'
            )

    token = make_optout_token(project_id)
    optout_url = f"{base_url}/api/notifications/digest-optout?token={token}"
    parts += [
        '<p style="font-size: 12px; color: #aaa; margin-top: 24px; border-top: 1px solid #eee; padding-top: 8px;">',
        "This digest was generated by agent-teams.<br>",
        f'<a href="{_esc(optout_url)}" style="color: #aaa;">Unsubscribe from these emails</a>',
        "</p>",
        "</body>",
        "</html>",
    ]
    return "\n".join(parts)


def render_push_title(flag_count: int, date: _date | str) -> str:
    """Build a short push-notification title (≤ 80 chars).

    ASCII-only: title flows into ntfy's X-Title HTTP header which rejects
    non-ASCII characters (UnicodeEncodeError on em-dash — Kanban #1218).

    Examples:
        "Agent-Teams digest - 3 flag(s)"
        "Agent-Teams digest - all clear"
    """
    if flag_count == 0:
        return "Agent-Teams digest - all clear"
    noun = "flag" if flag_count == 1 else "flags"
    return f"Agent-Teams digest - {flag_count} {noun}"


def render_push_body(flags: list[Any], top_n: int = 3) -> str:
    """Build a short push-notification body (1-2 sentences, ≤ ~200 chars).

    Lists up to `top_n` project names with per-project flag counts; appends
    an overflow note when there are more. Returns "All clear — no open flags."
    when the list is empty.

    Examples (flags from 3 distinct projects, top_n=3):
        "Open flags: proj-a (2), proj-b (1), proj-c (1)."
        "Open flags: proj-a (2), proj-b (1), proj-c (1). +1 more project."
    """
    if not flags:
        return "All clear — no open flags."

    # Aggregate per project (order-stable — flags are sorted project_id ASC).
    project_counts: dict[str, int] = {}
    for flag in flags:
        name = str(flag.get("project", "?"))
        project_counts[name] = project_counts.get(name, 0) + 1

    projects = list(project_counts.items())  # [(name, count), ...]
    shown = projects[:top_n]
    overflow = len(projects) - len(shown)

    parts = ", ".join(f"{name} ({cnt})" for name, cnt in shown)
    body = f"Open flags: {parts}."
    if overflow > 0:
        noun = "project" if overflow == 1 else "projects"
        body += f" +{overflow} more {noun}."
    return body


def _esc(text: str) -> str:
    """Minimal HTML entity escaping for text inserted into HTML attributes/content."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


# ---------------------------------------------------------------------------
# Open-flags query helper
# ---------------------------------------------------------------------------


async def fetch_open_audit_flags(
    session: AsyncSession,
) -> list[dict[str, Any]]:
    """Return open GOV3 audit-flag task summaries across active projects.

    "Active" means: project.status=1, NOT is_killed, NOT is_paused.
    "Open flag" means: task is an GOV3 audit-flag question task with
      process_status IN {TODO, IN_PROGRESS, REVIEW, BLOCKED} AND status=1
      AND question_payload->>'is_audit_flag' = 'true'.

    Returns a list of dicts shaped for use as `payload['flags']` in
    render_text / render_html:
        id       : int
        project  : str
        title    : str
        streak   : int   — breach_streak_days; 1 when missing
        severity : str
        verdict  : str

    Sorted by project_id ASC, task.id ASC (deterministic).
    """
    stmt = (
        select(Task, Project)
        .join(Project, Task.project_id == Project.id)
        .where(
            # Project guards — active, not killed, not paused.
            Project.status == RecordStatus.ACTIVE,
            Project.is_killed.is_(False),
            Project.is_paused.is_(False),
            # Task guards — open GOV3 flag.
            Task.status == RecordStatus.ACTIVE,
            Task.interaction_kind == TaskInteractionKind.QUESTION,
            Task.process_status.in_(
                [TaskStatus.TODO, TaskStatus.IN_PROGRESS, TaskStatus.REVIEW, TaskStatus.BLOCKED]
            ),
            Task.question_payload["is_audit_flag"].astext == "true",
        )
        .order_by(Task.project_id.asc(), Task.id.asc())
    )
    rows = (await session.execute(stmt)).all()

    flags: list[dict[str, Any]] = []
    for task, project in rows:
        qp = task.question_payload or {}
        latest_summary = qp.get("latest_audit_summary") or {}
        flags.append(
            {
                "id": task.id,
                "project": project.name,
                "title": task.title or "",
                "streak": int(qp.get("breach_streak_days") or 1),
                "severity": latest_summary.get("severity") or "unspecified",
                "verdict": latest_summary.get("verdict") or "review",
            }
        )
    return flags
