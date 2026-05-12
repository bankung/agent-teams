"""Tests for GET /api/scaffold/{team}/files (Kanban #795, MVP-D/3).

Endpoint serves the orchestration scaffold manifest + base64-encoded file
bytes so a host-side CLI (MVP-E, #796) can write the harness to a target
path the API container can't reach.

Uses the real agent-teams repo at `/repo` as the source (same convention as
test_zero_config_scaffold.py — the container's settings.repo_root binds there).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

# Real source repo inside the container (FastAPI volume-mount at /repo).
AGENT_TEAMS_ROOT = Path("/repo")


def _rel_paths(body: dict) -> list[str]:
    return [f["rel_path"] for f in body["files"]]


@pytest.mark.asyncio
async def test_scaffold_endpoint_dev_team_returns_dev_files(client) -> None:
    """GET /api/scaffold/dev/files → 200 + includes universal + dev manifest;
    no novel-* files leak in."""
    resp = await client.get(
        "/api/scaffold/dev/files",
        params={"project_name": "foo", "project_id": 99},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["team"] == "dev"
    assert body["project_name"] == "foo"
    assert body["project_id"] == 99

    rels = _rel_paths(body)
    # Universal entries
    assert "CLAUDE.md" in rels
    assert ".claude/settings.json" in rels
    assert ".claude/agents/dev-analyst.md" in rels
    # Dev manifest
    assert ".claude/agents/dev-backend.md" in rels
    assert ".claude/teams/dev.md" in rels
    # Glob expansion landed
    assert any(r.startswith("context/standards/") for r in rels)
    assert any(r.startswith("context/teams/dev/") for r in rels)
    # Novel-only files MUST NOT appear in the dev manifest
    assert ".claude/agents/novel-writer.md" not in rels
    assert ".claude/agents/novel-editor.md" not in rels
    assert ".claude/teams/novel.md" not in rels


@pytest.mark.asyncio
async def test_scaffold_endpoint_novel_team_returns_novel_files(client) -> None:
    """GET /api/scaffold/novel/files → 200 + novel-* files present, dev-* absent."""
    resp = await client.get(
        "/api/scaffold/novel/files",
        params={"project_name": "story-a", "project_id": 42},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["team"] == "novel"

    rels = _rel_paths(body)
    # Novel manifest
    assert ".claude/agents/novel-writer.md" in rels
    assert ".claude/agents/novel-editor.md" in rels
    assert ".claude/teams/novel.md" in rels
    # Dev-only must NOT appear
    assert ".claude/agents/dev-backend.md" not in rels
    assert ".claude/agents/dev-frontend.md" not in rels
    assert ".claude/teams/dev.md" not in rels
    # Universal still present
    assert "CLAUDE.md" in rels
    assert ".claude/agents/dev-analyst.md" in rels


@pytest.mark.asyncio
async def test_scaffold_endpoint_unknown_team_falls_back_to_dev(client) -> None:
    """Unknown team string → service falls back to the dev manifest (matches
    _resolve_manifest's defensive fallback)."""
    resp = await client.get(
        "/api/scaffold/xyz/files",
        params={"project_name": "foo", "project_id": 7},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Endpoint echoes the requested team verbatim — fallback is in the file set,
    # not the response.team field.
    assert body["team"] == "xyz"

    rels = _rel_paths(body)
    # Dev manifest landed via fallback.
    assert ".claude/agents/dev-backend.md" in rels
    assert ".claude/teams/dev.md" in rels
    # Novel manifest must NOT leak in.
    assert ".claude/agents/novel-writer.md" not in rels


@pytest.mark.asyncio
async def test_scaffold_endpoint_base64_decodes_to_source_bytes(client) -> None:
    """Pick CLAUDE.md, base64-decode → bytes match /repo/CLAUDE.md verbatim."""
    resp = await client.get(
        "/api/scaffold/dev/files",
        params={"project_name": "foo", "project_id": 99},
    )
    assert resp.status_code == 200, resp.text

    files = {f["rel_path"]: f["content_b64"] for f in resp.json()["files"]}
    assert "CLAUDE.md" in files

    decoded = base64.b64decode(files["CLAUDE.md"])
    source = (AGENT_TEAMS_ROOT / "CLAUDE.md").read_bytes()
    assert decoded == source, (
        "decoded CLAUDE.md bytes differ from /repo/CLAUDE.md — "
        "endpoint must serve source bytes verbatim"
    )


@pytest.mark.asyncio
async def test_scaffold_endpoint_settings_json_filtered(client) -> None:
    """The base64-decoded settings.json must NOT contain any agent-teams self-
    reference (project name `agent-teams`, id=1 hard-codes, or
    /context/projects/agent-teams/ paths) in permissions.allow / permissions.ask.
    """
    resp = await client.get(
        "/api/scaffold/dev/files",
        params={"project_name": "new-proj", "project_id": 99},
    )
    assert resp.status_code == 200, resp.text

    files = {f["rel_path"]: f["content_b64"] for f in resp.json()["files"]}
    assert ".claude/settings.json" in files

    decoded_bytes = base64.b64decode(files[".claude/settings.json"])
    data = json.loads(decoded_bytes)

    forbidden = (
        "by-name/agent-teams",
        "/api/projects/1/",
        '/api/projects/1"',
        "/context/projects/agent-teams/",
    )
    allow = data.get("permissions", {}).get("allow", [])
    ask = data.get("permissions", {}).get("ask", [])
    for entry in allow + ask:
        if not isinstance(entry, str):
            continue
        for needle in forbidden:
            assert needle not in entry, (
                f"forbidden substring {needle!r} found in served settings.json "
                f"entry {entry!r}"
            )

    # Sanity: the filter did not nuke the whole allow list.
    assert len(allow) > 0, "filter wiped permissions.allow entirely"


@pytest.mark.asyncio
async def test_scaffold_endpoint_required_params_400(client) -> None:
    """Missing project_name or project_id → 422 (FastAPI default for query
    validation errors)."""
    # No params at all
    resp = await client.get("/api/scaffold/dev/files")
    assert resp.status_code == 422, resp.text

    # Missing project_id
    resp = await client.get(
        "/api/scaffold/dev/files", params={"project_name": "foo"}
    )
    assert resp.status_code == 422, resp.text

    # Missing project_name
    resp = await client.get(
        "/api/scaffold/dev/files", params={"project_id": 99}
    )
    assert resp.status_code == 422, resp.text
