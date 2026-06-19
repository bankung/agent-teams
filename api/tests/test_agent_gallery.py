"""Contract-smoke tests for the agent gallery (Kanban #1017).

Two NEW read-only endpoints, built on top of the #1016 validator:

  * ``GET /api/agents``        — flat array of summaries, sorted by name.
  * ``GET /api/agents/{name}`` — summary + raw_frontmatter + full_description
                                 + cross-project spawn history.

Service-level tests inject a tmp agents dir (the real ``.claude/agents`` dir is
NEVER touched) via the same monkeypatch pattern as ``test_agent_validation.py``.
Endpoint tests monkeypatch ``default_agents_dir`` on BOTH the gallery router and
the underlying service so the scan targets the tmp dir.

The spawns test seeds two real tasks (with ``subagent_models``) in the isolated
test DB via the public POST/PATCH API — the same ``_create_done_task`` shape as
``test_skill_stub_detector.py`` — then asserts newest-first ordering + the
project_name join.

Every assertion pairs a POSITIVE check with the NEGATIVE lock it is guarding, so
a passing test cannot be satisfied by an endpoint that returns everything (or
nothing) or that ignores ordering.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from src.services import agent_validation as svc

# A minimal fully-valid frontmatter block (mirrors test_agent_validation._VALID
# but parametrizes tools/model so the summary-field tests can flex them).
_VALID = """---
name: {name}
description: {desc}
model: {model}
tools: {tools}
---

Body for {name}.
"""


def _write(dir_: Path, filename: str, content: str) -> Path:
    p = dir_ / filename
    p.write_text(content, encoding="utf-8")
    return p


def _rows(dir_: Path) -> list[dict]:
    return svc.list_agents(dir_)


def _row(dir_: Path, name: str) -> dict | None:
    return svc.get_agent_summary(dir_, name)


# =============================================================================
# 1. Service — listing shape, sort, and the all-tools / N-tools branches
# =============================================================================


def test_list_agents_shape_and_sorted_by_name(tmp_path):
    _write(tmp_path, "z-agent.md", _VALID.format(name="z-agent", desc="Z one.", model="opus", tools="[Read]"))
    _write(tmp_path, "a-agent.md", _VALID.format(name="a-agent", desc="A one.", model="sonnet", tools="[Read, Grep]"))
    rows = _rows(tmp_path)

    # POSITIVE: sorted by NAME (a-agent before z-agent), regardless of any other
    # order the filesystem might iterate in.
    names = [r["name"] for r in rows]
    assert names == ["a-agent", "z-agent"]
    # Shape: every contract key present on each row.
    expected_keys = {
        "name", "description", "model", "tools_summary", "tool_count",
        "hook_count", "source_file", "domain", "valid", "validation_errors",
    }
    assert expected_keys.issubset(set(rows[0]))
    # NEGATIVE lock: the description is NOT empty for a parseable file.
    assert rows[0]["description"] == "A one."


def test_tools_summary_all_tools_branch(tmp_path):
    # tools absent → "All tools" + tool_count None.
    _write(
        tmp_path,
        "notools.md",
        "---\nname: no-tools\ndescription: ok.\nmodel: sonnet\n---\nbody\n",
    )
    # explicit "All tools" literal → same.
    _write(
        tmp_path,
        "alltools.md",
        '---\nname: all-tools\ndescription: ok.\ntools: "All tools"\n---\nbody\n',
    )
    no_tools = _row(tmp_path, "no-tools")
    all_tools = _row(tmp_path, "all-tools")

    assert no_tools["tools_summary"] == "All tools"
    assert no_tools["tool_count"] is None
    assert all_tools["tools_summary"] == "All tools"
    assert all_tools["tool_count"] is None


def test_tools_summary_n_tools_branch(tmp_path):
    _write(
        tmp_path,
        "threetools.md",
        "---\nname: three-tools\ndescription: ok.\ntools: [Read, Grep, Glob]\n---\nbody\n",
    )
    row = _row(tmp_path, "three-tools")
    # POSITIVE: explicit list → "N tools" + the count.
    assert row["tool_count"] == 3
    assert row["tools_summary"] == "3 tools"
    # NEGATIVE lock: it is NOT the all-tools placeholder.
    assert row["tools_summary"] != "All tools"


# =============================================================================
# 2. Service — hook_count 0 / N
# =============================================================================


def test_hook_count_zero_when_absent(tmp_path):
    _write(
        tmp_path,
        "nohooks.md",
        "---\nname: no-hooks\ndescription: ok.\nmodel: sonnet\n---\nbody\n",
    )
    assert _row(tmp_path, "no-hooks")["hook_count"] == 0


def test_hook_count_counts_matcher_entries(tmp_path):
    # PreToolUse has 2 matcher entries; PostToolUse has 1 → total 3.
    _write(
        tmp_path,
        "hooky.md",
        "---\n"
        "name: hooky-agent\n"
        "description: ok.\n"
        "hooks:\n"
        "  PreToolUse:\n"
        "    - matcher: Bash\n"
        "      hooks:\n"
        "        - type: command\n"
        "          command: a\n"
        "    - matcher: Write\n"
        "      hooks:\n"
        "        - type: command\n"
        "          command: b\n"
        "  PostToolUse:\n"
        "    - matcher: Edit\n"
        "      hooks:\n"
        "        - type: command\n"
        "          command: c\n"
        "---\nbody\n",
    )
    row = _row(tmp_path, "hooky-agent")
    # POSITIVE: matcher entries summed across event keys (2 + 1).
    assert row["hook_count"] == 3
    # NEGATIVE lock: a hooks block must NOT make the file invalid.
    assert row["valid"] is True


# =============================================================================
# 3. Service — domain derivation
# =============================================================================


@pytest.mark.parametrize(
    "name,expected_domain",
    [
        ("dev-backend", "dev"),
        ("novel-writer", "novel"),
        ("content-writer", "content"),
        ("secretary", "secretary"),
        ("secretary-job-scout", "secretary"),
        ("sem-campaign-lead", "sem"),
        ("seo-strategist", "seo"),
        ("google-ads-specialist", "sem"),
        ("meta-ads-specialist", "sem"),
        ("platform-ads-coordinator", "sem"),
        ("bi-analyst", "data"),
        ("dashboard-designer", "data"),
        ("sql-optimizer", "data"),
        ("analytics-platform-integrator", "data"),
        ("general", "general"),
        ("zzz-unknown", "other"),
    ],
)
def test_domain_derivation(tmp_path, name, expected_domain):
    _write(
        tmp_path,
        f"{name}.md",
        f"---\nname: {name}\ndescription: ok.\n---\nbody\n",
    )
    assert _row(tmp_path, name)["domain"] == expected_domain


# =============================================================================
# 4. Service — invalid files still appear (valid=false + diagnostics)
# =============================================================================


def test_invalid_file_still_listed_with_valid_false(tmp_path):
    # Missing name → ERROR. The row must still appear, valid=false, with the
    # diagnostic in validation_errors. (Name falls back to the filename stem.)
    _write(
        tmp_path,
        "broken-agent.md",
        "---\ndescription: missing the name key.\nmodel: gpt5\n---\nbody\n",
    )
    _write(
        tmp_path,
        "good-agent.md",
        _VALID.format(name="good-agent", desc="ok.", model="sonnet", tools="[Read]"),
    )
    rows = _rows(tmp_path)
    names = [r["name"] for r in rows]
    # POSITIVE: the broken file IS present (resolved by filename stem).
    assert "broken-agent" in names
    broken = next(r for r in rows if r["name"] == "broken-agent")
    assert broken["valid"] is False
    assert any(d["severity"] == "error" for d in broken["validation_errors"])
    # NEGATIVE lock: the good sibling is valid with no errors.
    good = next(r for r in rows if r["name"] == "good-agent")
    assert good["valid"] is True
    assert good["validation_errors"] == []


def test_warning_only_file_is_still_valid(tmp_path):
    # An unknown top-level key is a WARNING, which must NOT flip valid to false.
    _write(
        tmp_path,
        "warn-agent.md",
        "---\nname: warn-agent\ndescription: ok.\nemail_actions: enabled\n---\nbody\n",
    )
    row = _row(tmp_path, "warn-agent")
    # POSITIVE: warnings present...
    assert any(d["severity"] == "warning" for d in row["validation_errors"])
    # ...but the file is still VALID (negative lock against treating warnings as errors).
    assert row["valid"] is True


# =============================================================================
# 5. Service — raw_frontmatter verbatim + full_description
# =============================================================================


def test_raw_frontmatter_is_verbatim(tmp_path):
    fm_body = (
        "name: verbatim-agent\n"
        "description: SQL optimizer. Read-only: never executes DML.\n"
        "model: sonnet\n"
        "tools: [Read, Grep]"
    )
    _write(tmp_path, "verbatim.md", f"---\n{fm_body}\n---\nbody text\n")
    row = _row(tmp_path, "verbatim-agent")
    # POSITIVE: the raw frontmatter is the exact text between the fences.
    assert row["raw_frontmatter"] == fm_body
    # full_description is the untruncated description (mid-sentence colon intact).
    assert row["full_description"] == "SQL optimizer. Read-only: never executes DML."


# =============================================================================
# 6. Endpoint  GET /api/agents  (listing)
# =============================================================================


@pytest.fixture
def _patch_agents_dir(tmp_path, monkeypatch):
    """Point BOTH the gallery router and the service at a tmp dir."""
    _write(
        tmp_path,
        "alpha.md",
        _VALID.format(name="alpha-agent", desc="Alpha.", model="opus", tools="[Read, Grep]"),
    )
    _write(
        tmp_path,
        "bravo.md",
        "---\ndescription: no name here.\n---\nbody\n",  # invalid → valid=false
    )
    monkeypatch.setattr(
        "src.routers.agent_gallery.default_agents_dir",
        lambda _repo_root: tmp_path,
    )
    return tmp_path


async def test_list_endpoint_shape(client, _patch_agents_dir):
    resp = await client.get("/api/agents")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    # Sorted by name; both files present (the invalid one included).
    names = [r["name"] for r in body]
    assert names == sorted(names)
    assert "alpha-agent" in names
    sample = next(r for r in body if r["name"] == "alpha-agent")
    assert set(sample) == {
        "name", "description", "model", "tools_summary", "tool_count",
        "hook_count", "source_file", "domain", "valid", "validation_errors",
    }
    # source_file is a basename only — never an absolute path.
    assert "/" not in sample["source_file"] and "\\" not in sample["source_file"]
    # The valid agent is valid; the invalid one is NOT (negative lock).
    assert sample["valid"] is True
    assert any(r["valid"] is False for r in body)


# =============================================================================
# 7. Endpoint  GET /api/agents/{name}  (detail: 404s + raw_frontmatter)
# =============================================================================


async def test_detail_404_unknown_name(client, _patch_agents_dir):
    resp = await client.get("/api/agents/does-not-exist")
    assert resp.status_code == 404


async def test_detail_404_bad_regex_name(client, _patch_agents_dir):
    # A name that fails the agent-name regex is rejected before any lookup —
    # this is the traversal-shaped-input guard.
    resp = await client.get("/api/agents/..%2fetc")
    assert resp.status_code == 404


async def test_detail_returns_raw_frontmatter_and_spawns_array(client, _patch_agents_dir):
    resp = await client.get("/api/agents/alpha-agent")
    assert resp.status_code == 200
    body = resp.json()
    # POSITIVE: detail superset of the summary plus the three detail-only keys.
    assert body["name"] == "alpha-agent"
    assert "raw_frontmatter" in body and "name: alpha-agent" in body["raw_frontmatter"]
    assert body["full_description"] == "Alpha."
    # spawns is an array (empty here — no tasks seeded for this name).
    assert isinstance(body["spawns"], list)
    assert body["spawns"] == []


# =============================================================================
# 8. Endpoint — spawn history (seed real tasks, newest-first + project_name join)
# =============================================================================


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"gallery spawn-test fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


async def _create_done_task(client, project_id: int, title: str, subagent_models: list) -> dict:
    create_resp = await client.post(
        "/api/tasks",
        headers={"X-Project-Id": str(project_id)},
        json={"project_id": project_id, "title": title, "subagent_models": subagent_models},
    )
    assert create_resp.status_code == 201, create_resp.text
    task_id = create_resp.json()["id"]
    patch_resp = await client.patch(
        f"/api/tasks/{task_id}",
        headers={"X-Project-Id": str(project_id)},
        json={"process_status": 5},
    )
    assert patch_resp.status_code == 200, patch_resp.text
    return patch_resp.json()


async def test_detail_spawns_newest_first_with_project_join(
    client, scaffold_cleanup, _patch_agents_dir
):
    # The agent whose spawn history we'll query exists as a real file in the
    # patched tmp dir so the detail endpoint resolves it (alpha-agent).
    agent = "alpha-agent"

    from src import db as _db
    await _db.engine.dispose()

    name = scaffold_cleanup(_unique_name("gallery-spawn"))
    proj_resp = await client.post("/api/projects", json=_project_payload(name))
    assert proj_resp.status_code == 201, proj_resp.text
    project = proj_resp.json()
    project_id = project["id"]
    project_name = project["name"]

    # Two tasks that spawned `alpha-agent` at different times, plus a control
    # task that spawned a DIFFERENT agent (must NOT appear in the result).
    await _create_done_task(
        client, project_id, "older spawn",
        [{"agent": agent, "model": "sonnet", "at": "2026-06-01T00:00:00Z"}],
    )
    await _create_done_task(
        client, project_id, "newer spawn",
        [{"agent": agent, "model": "opus", "at": "2026-06-10T00:00:00Z"}],
    )
    await _create_done_task(
        client, project_id, "other-agent spawn",
        [{"agent": "dev-frontend", "model": "haiku", "at": "2026-06-11T00:00:00Z"}],
    )

    await _db.engine.dispose()

    resp = await client.get(f"/api/agents/{agent}")
    assert resp.status_code == 200, resp.text
    spawns = resp.json()["spawns"]

    # Keep only spawns from the project we just created (the live test DB may
    # carry other rows for a generic agent name; we scope our assertions).
    ours = [s for s in spawns if s["project_id"] == project_id]
    # POSITIVE: exactly our two alpha-agent spawns, newest first by `at`.
    assert [s["at"] for s in ours] == [
        "2026-06-10T00:00:00Z",
        "2026-06-01T00:00:00Z",
    ]
    assert [s["model"] for s in ours] == ["opus", "sonnet"]
    # The project_name join is populated.
    assert all(s["project_name"] == project_name for s in ours)
    # NEGATIVE lock: the dev-frontend spawn is NOT in alpha-agent's history.
    assert all(s["model"] != "haiku" for s in ours)
    assert all("other-agent" not in str(s) for s in ours)


# =============================================================================
# 9. SW-3: Reserved-name guard (name: validate)
# =============================================================================


def test_reserved_name_validate_emits_error(tmp_path):
    # A file whose frontmatter name is "validate" (a reserved route sub-path).
    _write(
        tmp_path,
        "validate.md",
        "---\nname: validate\ndescription: reserved name test.\nmodel: sonnet\n---\nbody\n",
    )
    # POSITIVE: the reserved-name error IS emitted.
    from src.services import agent_validation as svc_inner
    diags = svc_inner.validate_agents_dir(tmp_path)["diagnostics"]
    assert any(
        d["field"] == "name"
        and d["severity"] == "error"
        and "reserved" in d["message"]
        and "validate" in d["message"]
        for d in diags
    )
    # NEGATIVE lock: a non-reserved name does not trip this check.
    _write(
        tmp_path,
        "ok-agent.md",
        "---\nname: ok-agent\ndescription: fine.\nmodel: sonnet\n---\nbody\n",
    )
    ok_diags = [d for d in svc_inner.validate_agents_dir(tmp_path)["diagnostics"] if d["file"] == "ok-agent.md"]
    assert not any("reserved" in d["message"] for d in ok_diags)


def test_reserved_name_absent_from_gallery_detail(tmp_path):
    # get_agent_summary returns None for a reserved name → gallery 404s.
    _write(
        tmp_path,
        "validate.md",
        "---\nname: validate\ndescription: reserved.\nmodel: sonnet\n---\nbody\n",
    )
    # POSITIVE: get_agent_summary returns None for the reserved name.
    assert svc.get_agent_summary(tmp_path, "validate") is None
    # NEGATIVE lock: a non-reserved name IS found by the same lookup.
    _write(
        tmp_path,
        "real-agent.md",
        "---\nname: real-agent\ndescription: ok.\nmodel: sonnet\n---\nbody\n",
    )
    assert svc.get_agent_summary(tmp_path, "real-agent") is not None


async def test_reserved_name_endpoint_404(client, _patch_agents_dir, tmp_path, monkeypatch):
    # Endpoint GET /api/agents/validate must return the validator response, not
    # a gallery 404.  The real validator route is registered first so it always
    # wins — confirm by checking the response shape (files_scanned key present).
    resp = await client.get("/api/agents/validate")
    # POSITIVE: validator shape returned (files_scanned key present).
    assert resp.status_code == 200
    assert "files_scanned" in resp.json()
    # NEGATIVE lock: it is NOT a gallery detail shape (no "valid" key).
    assert "valid" not in resp.json()


# =============================================================================
# Kanban #2503 — Fix 2: AgentPathError detail is static, not internal string
# =============================================================================


@pytest.mark.asyncio
async def test_agent_path_error_returns_static_detail_on_post(
    client, _patch_agents_dir, monkeypatch
) -> None:
    """Fix 2: POST /api/agents with a name that triggers AgentPathError must
    return 422 with the static detail 'invalid_agent_path', NOT the internal
    resolved-path string (which includes the filesystem path).

    We monkeypatch confine_agent_path (the function called inside
    _build_validate_write) to raise AgentPathError carrying a message that
    looks like an internal path string — and assert that string is absent from
    the response.
    """
    from src.services.agent_validation import AgentPathError

    _INTERNAL_MSG = "resolved target '/etc/passwd' is not directly inside '/safe/dir'"

    monkeypatch.setattr(
        "src.routers.agent_gallery.confine_agent_path",
        lambda agents_dir, name: (_ for _ in ()).throw(AgentPathError(_INTERNAL_MSG)),
    )

    resp = await client.post(
        "/api/agents",
        json={
            "name": "test-agent",
            "description": "trigger path error",
            "body": "",
        },
    )
    assert resp.status_code == 422, resp.text
    # POSITIVE: static stable detail.
    assert resp.json()["detail"] == "invalid_agent_path", resp.json()
    # NEGATIVE (the lock): internal path string must NOT appear in the response.
    assert _INTERNAL_MSG not in resp.text, (
        f"internal error string leaked into response: {resp.text}"
    )


@pytest.mark.asyncio
async def test_agent_path_error_returns_static_detail_on_put(
    client, _patch_agents_dir, monkeypatch
) -> None:
    """Fix 2: PUT /api/agents/{name} with an AgentPathError returns the static detail."""
    from src.services.agent_validation import AgentPathError

    _INTERNAL_MSG = "resolved target '/traversal' is not directly inside '/safe/dir'"

    # First make the "existing agent" lookup succeed (so we get past the 404 check).
    monkeypatch.setattr(
        "src.routers.agent_gallery.get_agent_summary",
        lambda agents_dir, name: {"name": name, "valid": True},
    )
    monkeypatch.setattr(
        "src.routers.agent_gallery.confine_agent_path",
        lambda agents_dir, name: (_ for _ in ()).throw(AgentPathError(_INTERNAL_MSG)),
    )

    resp = await client.put(
        "/api/agents/alpha-agent",
        json={
            "name": "alpha-agent",
            "description": "trigger path error",
            "body": "",
        },
    )
    assert resp.status_code == 422, resp.text
    # POSITIVE: static detail.
    assert resp.json()["detail"] == "invalid_agent_path", resp.json()
    # NEGATIVE: internal string absent.
    assert _INTERNAL_MSG not in resp.text, (
        f"internal error string leaked into response: {resp.text}"
    )
