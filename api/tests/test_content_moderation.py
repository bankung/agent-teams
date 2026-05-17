"""Kanban #1121 (2026-05-17, L14 prevention) — content-moderation scanner +
auto-headless gate tests.

Defends against the red-team Phase 7 sleeper-attack chain: API previously had
ZERO content moderation on task fields. A description containing
`TRUNCATE tasks_history` looked indistinguishable from a benign feature task;
combined with auto-headless + a stale recurrence template, that's the
end-to-end destruction path.

Two layers tested:

1. **Scanner unit tests** — pure regex; no DB / HTTP. Covers per-pattern
   positives + the false-positive guard ("Add cleanup migration for archived
   rows" does NOT match).
2. **Wire-contract tests via the HTTP layer** — POST flags, PATCH auto-headless
   gate, reviewer-clear flow.

Per spec (AC 5/6/7), the wire tests are the load-bearing acceptance evidence.
"""

from __future__ import annotations

import uuid

import pytest


# ----------------------------------------------------------------------------
# Helpers (mirror the test_routes_smoke / test_payload_size_limits style)
# ----------------------------------------------------------------------------


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"test fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


async def _make_project(client, scaffold_cleanup, prefix: str) -> int:
    name = _unique_name(prefix)
    scaffold_cleanup(name)
    proj = await client.post("/api/projects", json=_project_create_payload(name))
    assert proj.status_code in (200, 201), proj.text
    return proj.json()["id"]


async def _grant_consent(client, project_id: int) -> None:
    """Grant project consent so the consent gate doesn't shadow the L14 gate
    we're trying to assert. Returns nothing — assertion lives at call site."""
    # We need the project's name to confirm — fetch it back.
    proj = await client.get(f"/api/projects/{project_id}")
    assert proj.status_code == 200, proj.text
    name = proj.json()["name"]
    grant = await client.post(
        f"/api/projects/{project_id}/grant-consent",
        json={"confirm_name": name},
    )
    assert grant.status_code == 200, grant.text


# ============================================================================
# 1. Scanner unit tests — pure regex, no DB
# ============================================================================


@pytest.mark.parametrize(
    "text",
    [
        "DROP TABLE projects",
        "drop database agent_teams",
        "DROP SCHEMA public CASCADE",
        "drop index ix_tasks_project_id",
        "DROP TRIGGER tasks_audit_trg ON tasks",
        "drop function some_fn()",
    ],
)
def test_scanner_matches_drop_patterns(text: str) -> None:
    """AC 1: DROP variants match the destructive scanner."""
    from src.services.content_moderation import contains_destructive_intent

    assert contains_destructive_intent(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "TRUNCATE tasks",
        "TRUNCATE TABLE tasks_history",
        "truncate projects",
    ],
)
def test_scanner_matches_truncate_patterns(text: str) -> None:
    from src.services.content_moderation import contains_destructive_intent

    assert contains_destructive_intent(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "DELETE FROM tasks WHERE project_id = 1",
        "delete from projects",
    ],
)
def test_scanner_matches_delete_from(text: str) -> None:
    from src.services.content_moderation import contains_destructive_intent

    assert contains_destructive_intent(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "ALTER TABLE tasks DISABLE TRIGGER tasks_audit_trg",
        "alter table projects drop column name",
        "ALTER TABLE x DROP CONSTRAINT fk_y",
        # Multi-line via DOTALL flag
        "ALTER TABLE tasks\n  DISABLE TRIGGER tasks_audit_trg;",
    ],
)
def test_scanner_matches_alter_disable_drop(text: str) -> None:
    from src.services.content_moderation import contains_destructive_intent

    assert contains_destructive_intent(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "GRANT ALL ON tasks TO public",
        "grant select on schema.tbl to other_user",
        "REVOKE ALL ON tasks FROM pytest_runner",
    ],
)
def test_scanner_matches_grant_revoke(text: str) -> None:
    from src.services.content_moderation import contains_destructive_intent

    assert contains_destructive_intent(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "docker compose down -v",
        "docker exec db psql -c 'DELETE FROM tasks'",
        "docker exec api-db dropdb agent_teams",
        "docker exec db dropdb agent_teams_test",
    ],
)
def test_scanner_matches_docker_shell_escapes(text: str) -> None:
    from src.services.content_moderation import contains_destructive_intent

    assert contains_destructive_intent(text) is True


@pytest.mark.parametrize(
    "text",
    [
        # AC 7 — the load-bearing false-positive guard.
        "Add cleanup migration for archived rows",
        # Plain English with NO destructive SQL keywords.
        "We need to delete the obsolete docs from the wiki",
        "Improve performance of the user dashboard",
        "Refactor task list endpoint to use cursor pagination",
        # The word "drop" alone (without a relation keyword) is benign.
        "Drop the existing approach and try again",
        # The word "truncate" alone (no identifier) is benign.
        "Use truncate-style ellipsis for long titles",
        # Plain prose mentioning DELETE without FROM.
        "Allow the user to delete their own comments",
        # ALTER TABLE without a destructive verb.
        "ALTER TABLE tasks ADD COLUMN new_field TEXT",
    ],
)
def test_scanner_does_not_flag_benign_content(text: str) -> None:
    """AC 7: false-positive guard — legitimate task content does NOT match."""
    from src.services.content_moderation import contains_destructive_intent

    assert contains_destructive_intent(text) is False, (
        f"benign text matched: {text!r}"
    )


def test_scanner_handles_none_and_empty() -> None:
    """Defensive: None / empty / whitespace returns False."""
    from src.services.content_moderation import contains_destructive_intent

    assert contains_destructive_intent(None) is False
    assert contains_destructive_intent("") is False
    assert contains_destructive_intent("   ") is False


def test_scan_task_payload_returns_matched_fields() -> None:
    """scan_task_payload returns the list of matched field names in
    insertion order — title, description, AC items, halt_reason,
    status_change_reason."""
    from src.services.content_moderation import scan_task_payload

    matched = scan_task_payload(
        title="TRUNCATE tasks_history",
        description="benign body",
        acceptance_criteria=[
            {"text": "DROP TABLE projects"},
            {"text": "do the thing"},
        ],
        halt_reason=None,
        status_change_reason="DELETE FROM users",
    )
    assert matched == [
        "title",
        "acceptance_criteria[0].text",
        "status_change_reason",
    ]


def test_scan_task_payload_clean_returns_empty_list() -> None:
    from src.services.content_moderation import scan_task_payload

    assert scan_task_payload(
        title="Add cleanup migration for archived rows",
        description="Use a soft-delete-then-purge pattern.",
        acceptance_criteria=[{"text": "purge after 30 days"}],
        halt_reason=None,
        status_change_reason=None,
    ) == []


def test_scan_task_payload_accepts_pydantic_models() -> None:
    """The router post-Pydantic passes either dicts or model instances; both
    must be handled."""
    from src.schemas.task import AcceptanceCriterion
    from src.services.content_moderation import scan_task_payload

    matched = scan_task_payload(
        title="ok",
        acceptance_criteria=[
            AcceptanceCriterion(text="DROP TABLE foo"),
            AcceptanceCriterion(text="benign"),
        ],
    )
    assert matched == ["acceptance_criteria[0].text"]


# ============================================================================
# 2. POST /api/tasks — scanner sets requires_human_review (AC 5)
# ============================================================================


@pytest.mark.asyncio
async def test_post_task_with_truncate_in_description_flags_review(
    client, scaffold_cleanup
) -> None:
    """AC 5: POST a task with TRUNCATE in description → 201 + requires_human_review=true."""
    project_id = await _make_project(client, scaffold_cleanup, "l14-trunc-desc")
    headers = {"X-Project-Id": str(project_id)}

    resp = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "Quarterly archive purge",
            "description": "Run TRUNCATE tasks_history to reclaim space",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["requires_human_review"] is True, body
    # Sanity — task still landed; the flag is a TAG, not a block.
    assert body["title"] == "Quarterly archive purge"


@pytest.mark.asyncio
async def test_post_task_with_drop_in_title_flags_review(
    client, scaffold_cleanup
) -> None:
    """POST with DROP TABLE in the title → flagged."""
    project_id = await _make_project(client, scaffold_cleanup, "l14-drop-title")
    headers = {"X-Project-Id": str(project_id)}

    resp = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "DROP TABLE archived_rows",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["requires_human_review"] is True


@pytest.mark.asyncio
async def test_post_task_with_destructive_ac_flags_review(
    client, scaffold_cleanup
) -> None:
    """POST with destructive text in an acceptance_criteria entry → flagged."""
    project_id = await _make_project(client, scaffold_cleanup, "l14-ac-flag")
    headers = {"X-Project-Id": str(project_id)}

    resp = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "Database hygiene sweep",
            "acceptance_criteria": [
                {"text": "Run DELETE FROM tasks_history WHERE older_than 90d"},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["requires_human_review"] is True


@pytest.mark.asyncio
async def test_post_task_benign_content_not_flagged(
    client, scaffold_cleanup
) -> None:
    """AC 7: benign 'Add cleanup migration for archived rows' → NOT flagged."""
    project_id = await _make_project(client, scaffold_cleanup, "l14-benign")
    headers = {"X-Project-Id": str(project_id)}

    resp = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "Add cleanup migration for archived rows",
            "description": "Use soft-delete pattern; purge later.",
            "acceptance_criteria": [
                {"text": "Migration adds archived_at column"},
                {"text": "Purge job sweeps after 30 days"},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["requires_human_review"] is False


# ============================================================================
# 3. PATCH auto-headless gate (AC 6)
# ============================================================================


@pytest.mark.asyncio
async def test_patch_to_auto_headless_blocked_when_flagged(
    client, scaffold_cleanup
) -> None:
    """AC 6: PATCH a flagged task to auto_headless → 422 with stable detail.

    Project consent IS granted so the consent gate doesn't shadow this — we
    want to assert the L14 gate fires specifically.
    """
    project_id = await _make_project(client, scaffold_cleanup, "l14-headless")
    await _grant_consent(client, project_id)
    headers = {"X-Project-Id": str(project_id)}

    # POST a flagged task.
    create = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "Quarterly archive purge",
            "description": "Run TRUNCATE tasks_history overnight.",
        },
        headers=headers,
    )
    assert create.status_code == 201, create.text
    task_id = create.json()["id"]
    assert create.json()["requires_human_review"] is True

    # PATCH to auto_headless → 422.
    resp = await client.patch(
        f"/api/tasks/{task_id}",
        json={"run_mode": "auto_headless"},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "task requires human review" in detail, detail
    # The detail should advertise the unblock path.
    assert "requires_human_review=false" in detail, detail


@pytest.mark.asyncio
async def test_patch_to_auto_headless_allowed_after_reviewer_clears(
    client, scaffold_cleanup
) -> None:
    """Reviewer ack flow: PATCH requires_human_review=false first, then PATCH
    run_mode=auto_headless → 200."""
    project_id = await _make_project(client, scaffold_cleanup, "l14-cleared")
    await _grant_consent(client, project_id)
    headers = {"X-Project-Id": str(project_id)}

    create = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "ops: TRUNCATE old logs table",
        },
        headers=headers,
    )
    assert create.status_code == 201, create.text
    task_id = create.json()["id"]
    assert create.json()["requires_human_review"] is True

    # Reviewer clears the flag.
    clear = await client.patch(
        f"/api/tasks/{task_id}",
        json={"requires_human_review": False},
        headers=headers,
    )
    assert clear.status_code == 200, clear.text
    assert clear.json()["requires_human_review"] is False

    # Now auto-headless flip succeeds.
    flip = await client.patch(
        f"/api/tasks/{task_id}",
        json={"run_mode": "auto_headless"},
        headers=headers,
    )
    assert flip.status_code == 200, flip.text
    assert flip.json()["run_mode"] == "auto_headless"


@pytest.mark.asyncio
async def test_patch_clear_and_flip_in_same_body_succeeds(
    client, scaffold_cleanup
) -> None:
    """Combined PATCH: reviewer can clear + flip in a single body. The L14
    gate evaluates the RESOLVED final flag, so an explicit false in the same
    PATCH dominates over the stored true."""
    project_id = await _make_project(client, scaffold_cleanup, "l14-combined")
    await _grant_consent(client, project_id)
    headers = {"X-Project-Id": str(project_id)}

    create = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "Archive sweep",
            "description": "Will TRUNCATE TABLE old_rows",
        },
        headers=headers,
    )
    assert create.status_code == 201
    task_id = create.json()["id"]

    resp = await client.patch(
        f"/api/tasks/{task_id}",
        json={
            "requires_human_review": False,
            "run_mode": "auto_headless",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["requires_human_review"] is False
    assert resp.json()["run_mode"] == "auto_headless"


@pytest.mark.asyncio
async def test_patch_destructive_description_flags_task(
    client, scaffold_cleanup
) -> None:
    """PATCH-time scan: writing destructive content into a previously-clean
    task escalates the flag false → true."""
    project_id = await _make_project(client, scaffold_cleanup, "l14-patch-flag")
    headers = {"X-Project-Id": str(project_id)}

    create = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "Benign task",
            "description": "Initial body, nothing scary.",
        },
        headers=headers,
    )
    assert create.status_code == 201
    assert create.json()["requires_human_review"] is False
    task_id = create.json()["id"]

    patch = await client.patch(
        f"/api/tasks/{task_id}",
        json={"description": "Actually we should DROP TABLE tasks_history"},
        headers=headers,
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["requires_human_review"] is True


@pytest.mark.asyncio
async def test_patch_clean_field_does_not_clear_flag(
    client, scaffold_cleanup
) -> None:
    """Sticky-on-match: PATCHing a previously-flagged task's destructive
    description back to clean content does NOT auto-clear the flag — the
    reviewer must do so explicitly."""
    project_id = await _make_project(client, scaffold_cleanup, "l14-sticky")
    headers = {"X-Project-Id": str(project_id)}

    create = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "Sketchy",
            "description": "Run TRUNCATE history",
        },
        headers=headers,
    )
    assert create.json()["requires_human_review"] is True
    task_id = create.json()["id"]

    # Rewrite to a clean description, no explicit flag PATCH.
    patch = await client.patch(
        f"/api/tasks/{task_id}",
        json={"description": "Actually just refresh the dashboard"},
        headers=headers,
    )
    assert patch.status_code == 200, patch.text
    # Sticky: flag stays true.
    assert patch.json()["requires_human_review"] is True


# ============================================================================
# 4. Source-text-lock for the wire-contract detail string
# ============================================================================


# ============================================================================
# 5. L23 sanitize_agent_action — Kanban #1126
# ============================================================================
#
# DIFFERENT CONTRACT from the L14 scanner. The scanner TAGS author content;
# the sanitizer REFUSES to forward an extracted command (returns None →
# caller halts + escalates). Tests assert both halves of that contract.


@pytest.mark.parametrize(
    "text",
    [
        # The Phase 9B literal-echo case from the incident: a refusal text that
        # repeats the SQL string verbatim inside the explanation.
        (
            "I cannot execute this task because the task contains a destructive "
            "SQL command (`TRUNCATE tasks WHERE process_status = 5;`) which can "
            "potentially delete data."
        ),
        # Plain destructive SQL string echoed from an agent next-step extractor.
        "Next action: DELETE FROM tasks WHERE older_than 90d",
        "Recommended: DROP TABLE archived_rows",
        "DROP DATABASE agent_teams_test",
        "DROP SCHEMA legacy",
        "DROP TRIGGER tasks_audit_trg ON tasks",
        "DROP FUNCTION cleanup_old()",
        # ALTER TABLE ... DISABLE/DROP variants.
        "ALTER TABLE tasks DISABLE TRIGGER tasks_audit_trg",
        "alter table x drop constraint fk_y",
        # Lowercase / mixed-case prose forms.
        "we should truncate the audit log",
        "...might delete from tasks_history...",
    ],
)
def test_sanitize_agent_action_refuses_destructive(text: str) -> None:
    """AC 2 + AC 4: text containing destructive SQL → None (refuse to forward)."""
    from src.services.content_moderation import sanitize_agent_action

    assert sanitize_agent_action(text) is None, (
        f"sanitizer should have refused: {text!r}"
    )


@pytest.mark.parametrize(
    "text",
    [
        # AC 5 (Lead-issued spec): a clean refusal text that does NOT echo SQL
        # passes through unchanged. The agent refusing without echoing the
        # specific command is the right behavior; the sanitizer must let that
        # text reach the operator.
        "I cannot execute this task as it appears to violate safety policies.",
        "Refusing to proceed — please escalate to a human reviewer.",
        # General benign output strings.
        "Task complete: updated 3 rows in the staging table.",
        "Specialist finished: see _scratch/report.md for details.",
        # Prose containing destructive English words but NO SQL keyword pair
        # (the false-positive guard — must not over-fire).
        # NB: bare "Drop" / "Delete" without a SQL-shape neighbor must NOT match.
        # The L23 pattern is intentionally more aggressive than L14 (e.g., it
        # matches bare `TRUNCATE\b` because "halt for human review" is the
        # cheaper failure mode at the extraction site), so we don't probe
        # truncate-style English here — see the spec at #1126 for that
        # trade-off.
        "Drop the existing approach and try again",
        "Delete the obsolete docs from the wiki",
        # ALTER TABLE WITHOUT a destructive verb is forwardable.
        "ALTER TABLE tasks ADD COLUMN new_field TEXT",
    ],
)
def test_sanitize_agent_action_forwards_clean(text: str) -> None:
    """AC 5: clean text passes through unchanged."""
    from src.services.content_moderation import sanitize_agent_action

    assert sanitize_agent_action(text) == text


def test_sanitize_agent_action_handles_none_and_empty() -> None:
    """Defensive: None / empty / whitespace pass through unchanged (no
    extraction happened — nothing to refuse)."""
    from src.services.content_moderation import sanitize_agent_action

    assert sanitize_agent_action(None) is None
    assert sanitize_agent_action("") == ""
    # Whitespace is treated as 'not text' by the `if not text:` short-circuit;
    # this is documented behaviour (no SQL → no risk → return as-is).
    assert sanitize_agent_action("   ") == "   "


def test_sanitize_agent_action_contract_diverges_from_scanner() -> None:
    """The L23 sanitizer is intentionally TIGHTER than the L14 scanner.

    L14 catches GRANT/REVOKE prose + docker shell escapes — those matter for
    AUTHOR-time intent flagging but a refusal text legitimately discussing
    "we should not GRANT ALL on tasks to public" should still be forwardable
    by L23 (whose false-positive cost is "halt the task" not "ack to unblock").

    This test pins that contract: the GRANT prose triggers contains_destructive_intent
    (TAG) but is forwarded by sanitize_agent_action (REFUSE only the tightest set).
    """
    from src.services.content_moderation import (
        contains_destructive_intent,
        sanitize_agent_action,
    )

    grant_text = "we should NOT GRANT ALL ON tasks TO public"
    assert contains_destructive_intent(grant_text) is True
    assert sanitize_agent_action(grant_text) == grant_text


def test_l14_detail_string_pinned_in_router_source() -> None:
    """The wire-contract detail string for the L14 gate is text-locked so
    a future refactor can't silently drift its public-facing message.

    The constant is built via Python implicit string concatenation across two
    source lines (so a future formatter can wrap it without breaking this
    test); we check each half independently.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src" / "routers" / "tasks.py"
    contents = src.read_text(encoding="utf-8")
    # First half — the imperative + interpolation placeholder.
    assert (
        "task requires human review before auto-run (matched fields: {matched})."
        in contents
    ), (
        "Kanban #1121 L14 detail string (first half) drifted in routers/tasks.py — "
        "update test or restore the locked text."
    )
    # Second half — the unblock instruction.
    assert (
        "PATCH requires_human_review=false explicitly to unblock." in contents
    ), (
        "Kanban #1121 L14 detail string (second half) drifted in routers/tasks.py — "
        "update test or restore the locked text."
    )
