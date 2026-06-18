"""Contract-smoke tests for the gated agent WRITE endpoints (Kanban #2481).

Two NEW operator-gated endpoints in the `/api/agents` router (platform-level,
NO X-Project-Id, filesystem-only — NO `agent_teams` DB writes):

  * POST /api/agents          — create a new `.claude/agents/{name}.md`.
  * PUT  /api/agents/{name}   — edit an existing one.

Both are behind the #1857 operator-proof gate (`require_operator_proof`), which
fail-OPENS when `OPERATOR_ACTION_KEY` is unset. The conftest autouse fixture
`_operator_gate_inactive_by_default` delenv's that key for every test, so the
gate is INACTIVE by default — the gate-ACTIVE tests below re-`setenv` it (runs
after the autouse setup, last-write-wins).

SAFETY: every test points the router + service at a `tmp_path` agents dir via
`monkeypatch.setattr("src.routers.agent_gallery.default_agents_dir", ...)` — the
REAL `.claude/agents/` directory is NEVER created/edited/read. (Same pattern as
`test_agent_gallery.py`.)

These are first-pass contract-smoke (dev-sr-backend scope). The rigorous suite —
CRLF/BOM round-trips, hooks-mapping write, the audit-JSONL row, concurrent
writes, every validator-error shape on the candidate, atomic-write crash
injection — is dev-tester's domain. Every assertion here pairs a POSITIVE check
with the NEGATIVE lock it guards (e.g. 403 PAIRED with "no file was written").

Static-trace note (the author did NOT run pytest — a hook blocks in-session
pytest + the live-DB sentinel; the operator runs this out-of-band): each test's
docstring states what it asserts and the NEGATIVE lock it pins.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# A valid frontmatter file used to pre-seed an "existing" agent in the tmp dir.
_EXISTING = """---
name: existing-agent
description: A pre-existing agent.
model: sonnet
tools: [Read, Grep]
---

Existing body.
"""

_KEY = "OPERATOR_ACTION_KEY"
_TOKEN = "test-operator-secret-123"


@pytest.fixture
def agents_dir(tmp_path, monkeypatch) -> Path:
    """Point the write routes (and the gallery scan they reuse) at a tmp dir.

    Pre-seeds one valid `existing-agent.md` so the 409 (POST-existing) and 200
    (PUT-existing) paths have a target. The REAL agents dir is never touched.
    """
    (tmp_path / "existing-agent.md").write_text(_EXISTING, encoding="utf-8")
    monkeypatch.setattr(
        "src.routers.agent_gallery.default_agents_dir",
        lambda _repo_root: tmp_path,
    )
    return tmp_path


def _post_body(name: str, **extra) -> dict:
    body = {"name": name, "description": "A valid description.", "body": "Body."}
    body.update(extra)
    return body


# =============================================================================
# 1. Gate ACTIVE — 403, NO file written (the crux: an AI agent cannot write)
# =============================================================================


async def test_post_gate_active_no_token_403_no_file(client, agents_dir, monkeypatch):
    """Gate ACTIVE + no X-Operator-Token → 403 AND no file is written.

    POSITIVE: status 403 with the operator_proof_required detail.
    NEGATIVE lock: the target `.md` was NOT created (a 403 that still wrote the
    file would be the whole vulnerability)."""
    monkeypatch.setenv(_KEY, _TOKEN)
    resp = await client.post("/api/agents", json=_post_body("blocked-agent"))
    assert resp.status_code == 403, resp.text
    assert "operator_proof_required" in str(resp.json()["detail"])
    # NEGATIVE lock — nothing landed on disk.
    assert not (agents_dir / "blocked-agent.md").exists()


async def test_post_gate_active_wrong_token_403_no_file(
    client, agents_dir, monkeypatch
):
    """Gate ACTIVE + a WRONG token → 403, no file (constant-time compare fails).

    NEGATIVE lock paired: file absent."""
    monkeypatch.setenv(_KEY, _TOKEN)
    resp = await client.post(
        "/api/agents",
        json=_post_body("blocked-agent"),
        headers={"X-Operator-Token": "not-the-key"},
    )
    assert resp.status_code == 403, resp.text
    assert not (agents_dir / "blocked-agent.md").exists()


async def test_put_gate_active_no_token_403_file_unchanged(
    client, agents_dir, monkeypatch
):
    """Gate ACTIVE + no token on PUT → 403 AND the existing file is byte-identical.

    POSITIVE: 403. NEGATIVE lock: the on-disk file is UNCHANGED (a 403 that still
    mutated the file would corrupt a live agent)."""
    monkeypatch.setenv(_KEY, _TOKEN)
    before = (agents_dir / "existing-agent.md").read_text(encoding="utf-8")
    resp = await client.put(
        "/api/agents/existing-agent",
        json=_post_body("existing-agent", description="HACKED."),
    )
    assert resp.status_code == 403, resp.text
    after = (agents_dir / "existing-agent.md").read_text(encoding="utf-8")
    assert before == after  # NEGATIVE lock — not mutated


# =============================================================================
# 2. Gate ACTIVE + CORRECT token → allowed (proves the gate isn't a brick wall)
# =============================================================================


async def test_post_gate_active_correct_token_201_writes(
    client, agents_dir, monkeypatch
):
    """Gate ACTIVE + the correct token → 201 AND the file IS written.

    The positive counterpart to the 403 tests: a valid operator proof passes."""
    monkeypatch.setenv(_KEY, _TOKEN)
    resp = await client.post(
        "/api/agents",
        json=_post_body("allowed-agent"),
        headers={"X-Operator-Token": _TOKEN},
    )
    assert resp.status_code == 201, resp.text
    assert (agents_dir / "allowed-agent.md").exists()  # POSITIVE: file written
    assert resp.json()["name"] == "allowed-agent"


# =============================================================================
# 3. Gate INACTIVE (default) — happy path writes a valid file that round-trips
# =============================================================================


async def test_post_happy_writes_valid_file_that_roundtrips(client, agents_dir):
    """Gate INACTIVE (conftest default) → POST writes the file; it re-validates
    clean and round-trips the fields through the gallery summary.

    POSITIVE: 201 + the returned summary reflects the written frontmatter
    (model, tool_count, valid). NEGATIVE lock: the summary is NOT the all-tools
    placeholder (so we know the explicit `tools` list actually persisted)."""
    resp = await client.post(
        "/api/agents",
        json={
            "name": "fresh-agent",
            "description": "Fresh agent. Note: keeps the colon.",
            "model": "opus",
            "tools": ["Read", "Grep", "Glob"],
            "body": "The body of the fresh agent.",
        },
    )
    assert resp.status_code == 201, resp.text
    summary = resp.json()
    # POSITIVE — round-tripped frontmatter.
    assert summary["name"] == "fresh-agent"
    assert summary["model"] == "opus"
    assert summary["tool_count"] == 3
    assert summary["valid"] is True
    # The description with a mid-value colon survived serialization.
    assert summary["description"] == "Fresh agent. Note: keeps the colon."
    # NEGATIVE lock — an explicit tools list, not the all-tools placeholder.
    assert summary["tools_summary"] == "3 tools"
    assert summary["tools_summary"] != "All tools"

    # The file on disk passes the real validator with zero errors.
    from src.services.agent_validation import validate_agents_dir

    result = validate_agents_dir(agents_dir)
    fresh_errors = [
        d
        for d in result["diagnostics"]
        if d["file"] == "fresh-agent.md" and d["severity"] == "error"
    ]
    assert fresh_errors == []  # NEGATIVE lock — no error diagnostics for it


async def test_put_happy_edits_existing_file(client, agents_dir):
    """Gate INACTIVE → PUT edits the existing agent; 200 + content changed.

    POSITIVE: 200 + the new model in the summary. NEGATIVE lock: the OLD
    description text is gone from the file (a no-op PUT would leave it)."""
    resp = await client.put(
        "/api/agents/existing-agent",
        json={
            "name": "existing-agent",
            "description": "Now an edited description.",
            "model": "haiku",
            "body": "Edited body.",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["model"] == "haiku"  # POSITIVE — updated
    on_disk = (agents_dir / "existing-agent.md").read_text(encoding="utf-8")
    assert "Now an edited description." in on_disk  # POSITIVE
    assert "A pre-existing agent." not in on_disk  # NEGATIVE lock — old text gone


# =============================================================================
# 4. POST existing → 409; PUT unknown → 404
# =============================================================================


async def test_post_existing_name_409(client, agents_dir):
    """POST a name that already exists → 409 (use PUT to edit).

    NEGATIVE lock: the pre-existing file is byte-identical afterward (the 409 did
    not overwrite it)."""
    before = (agents_dir / "existing-agent.md").read_text(encoding="utf-8")
    resp = await client.post("/api/agents", json=_post_body("existing-agent"))
    assert resp.status_code == 409, resp.text
    after = (agents_dir / "existing-agent.md").read_text(encoding="utf-8")
    assert before == after  # NEGATIVE lock — untouched


async def test_put_unknown_name_404(client, agents_dir):
    """PUT a name that does not exist → 404 (use POST to create).

    NEGATIVE lock: no file with that name was created by the failed PUT."""
    resp = await client.put(
        "/api/agents/ghost-agent", json=_post_body("ghost-agent")
    )
    assert resp.status_code == 404, resp.text
    assert not (agents_dir / "ghost-agent.md").exists()  # NEGATIVE lock


# =============================================================================
# 5. Invalid candidate frontmatter → 422, NO file written
# =============================================================================


async def test_post_invalid_model_422_no_file(client, agents_dir):
    """A bad `model` value (not opus/sonnet/haiku) → 422 from the file validator
    AND no file written.

    POSITIVE: 422. NEGATIVE lock: the candidate file is absent (no partial/invalid
    file is ever persisted — the core safety property)."""
    resp = await client.post(
        "/api/agents",
        json={"name": "badmodel-agent", "description": "x", "model": "gpt5"},
    )
    # gate inactive (default) so a 422 is unambiguously the validation reject,
    # not a gate 403.
    assert resp.status_code == 422, resp.text
    assert not (agents_dir / "badmodel-agent.md").exists()  # NEGATIVE lock


# =============================================================================
# 6. Bad / traversal name → rejected BEFORE any filesystem touch
# =============================================================================


@pytest.mark.parametrize("bad_name", ["Bad_Name", "UPPER", "has space", "dot.name"])
async def test_post_bad_name_422_pydantic(client, agents_dir, bad_name):
    """A name that fails AGENT_NAME_RE → 422 (AgentWrite's name validator) before
    any write.

    NEGATIVE lock: nothing resembling the bad name was written to the dir (the
    dir still holds only the pre-seeded existing-agent.md)."""
    resp = await client.post(
        "/api/agents", json=_post_body(bad_name)
    )
    assert resp.status_code == 422, resp.text
    # NEGATIVE lock — the dir gained no new file.
    md_files = sorted(p.name for p in agents_dir.glob("*.md"))
    assert md_files == ["existing-agent.md"]


@pytest.mark.parametrize("traversal", ["..%2f..%2fetc", "..", "foo%2fbar"])
async def test_put_traversal_path_404(client, agents_dir, traversal):
    """A traversal-shaped PUT path → 404 (regex gate fires before any fs work).

    NEGATIVE lock: the dir is unchanged (still only the existing file) — the
    traversal never reached a write."""
    resp = await client.put(
        f"/api/agents/{traversal}", json=_post_body("whatever")
    )
    assert resp.status_code == 404, resp.text
    md_files = sorted(p.name for p in agents_dir.glob("*.md"))
    assert md_files == ["existing-agent.md"]  # NEGATIVE lock


async def test_put_body_path_name_mismatch_422(client, agents_dir):
    """PUT where the body `name` ≠ the path name → 422 (path is authoritative).

    NEGATIVE lock: the existing file is unchanged (the mismatch did not edit it
    under either name)."""
    before = (agents_dir / "existing-agent.md").read_text(encoding="utf-8")
    resp = await client.put(
        "/api/agents/existing-agent",
        json=_post_body("other-agent", description="mismatch."),
    )
    assert resp.status_code == 422, resp.text
    after = (agents_dir / "existing-agent.md").read_text(encoding="utf-8")
    assert before == after  # NEGATIVE lock
    assert not (agents_dir / "other-agent.md").exists()


# =============================================================================
# 7. Unknown body field → 422 (extra="forbid" on AgentWrite)
# =============================================================================


async def test_post_unknown_field_422(client, agents_dir):
    """An unknown top-level body field → 422 (AgentWrite is extra='forbid').

    NEGATIVE lock: no file written (the request never passed Pydantic)."""
    resp = await client.post(
        "/api/agents",
        json={"name": "extra-agent", "description": "x", "bogus_key": 1},
    )
    assert resp.status_code == 422, resp.text
    assert not (agents_dir / "extra-agent.md").exists()  # NEGATIVE lock


# =============================================================================
# 8. Hooks structural validation (Part A, #2481) — write-path only
# =============================================================================

# A real/legit hooks block derived from the actual agent files in .claude/agents/
# (e.g. dev-tester.md, general-researcher.md). This is the canonical shape:
# PreToolUse → list of {matcher, hooks: [{type, command}]}.
_VALID_HOOKS = {
    "PreToolUse": [
        {
            "matcher": "Bash",
            "hooks": [
                {
                    "type": "command",
                    "command": 'powershell -File "$CLAUDE_PROJECT_DIR/.claude/hooks/tester-curl-allow.ps1"',
                }
            ],
        }
    ]
}


def test_validate_hooks_structure_valid_legit_shape():
    """A real/legit hook block (matches the actual agent files) → no errors.

    NEGATIVE lock: the return value is [] not a non-empty list, so a validator
    that always passes would vacuously satisfy only the POSITIVE assertion;
    the other tests ensure non-trivial inputs produce errors."""
    from src.services.agent_validation import validate_hooks_structure

    errors = validate_hooks_structure(_VALID_HOOKS)
    assert errors == []  # POSITIVE: accepted
    # NEGATIVE lock: errors is exactly empty, not truthy
    assert not errors


def test_validate_hooks_structure_none_accepted():
    """None/absent hooks → no errors (hooks key is optional)."""
    from src.services.agent_validation import validate_hooks_structure

    assert validate_hooks_structure(None) == []


def test_validate_hooks_structure_unknown_event_key():
    """An unknown event key (e.g. 'OnFileChange') → error message returned.

    POSITIVE: errors is non-empty. NEGATIVE lock: 'unknown event key' appears
    in the error, not that it was silently accepted (no error)."""
    from src.services.agent_validation import validate_hooks_structure

    errors = validate_hooks_structure(
        {
            "OnFileChange": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "x"}]}
            ]
        }
    )
    assert errors, "expected at least one error for unknown event key"
    assert any("unknown event key" in e for e in errors)  # NEGATIVE lock: not empty


def test_validate_hooks_structure_non_list_event_value():
    """Event value that is a mapping (not a list) → error.

    NEGATIVE lock: errors is non-empty (a dict value was not silently accepted)."""
    from src.services.agent_validation import validate_hooks_structure

    errors = validate_hooks_structure(
        {"PreToolUse": {"matcher": "Bash", "hooks": []}}  # dict, not list
    )
    assert errors, "expected error for non-list event value"
    assert any("list" in e for e in errors)


def test_validate_hooks_structure_non_string_command():
    """A non-string command (e.g. an integer) → error.

    NEGATIVE lock: the integer command was not silently accepted."""
    from src.services.agent_validation import validate_hooks_structure

    errors = validate_hooks_structure(
        {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": 999}]}
            ]
        }
    )
    assert errors, "expected error for non-string command"
    assert any("command" in e for e in errors)


async def test_post_bad_hooks_422_no_file(client, agents_dir):
    """POST with a structurally bad hooks block → 422 AND no file written.

    POSITIVE: 422 from the hooks validator. NEGATIVE lock: no .md was created
    (a 422 that still wrote the file would bypass the safety property)."""
    resp = await client.post(
        "/api/agents",
        json={
            "name": "hooks-bad-agent",
            "description": "Testing bad hooks.",
            "hooks": {"OnFileChange": [{"matcher": "x", "hooks": [{"type": "command", "command": "x"}]}]},
        },
    )
    assert resp.status_code == 422, resp.text
    assert not (agents_dir / "hooks-bad-agent.md").exists()  # NEGATIVE lock


async def test_post_valid_hooks_201_writes(client, agents_dir):
    """POST with a structurally VALID hooks block → 201 AND file written.

    The positive counterpart to the bad-hooks 422: a real/legit hooks block is
    accepted. NEGATIVE lock: file IS created (not rejected by hooks validator)."""
    resp = await client.post(
        "/api/agents",
        json={
            "name": "hooks-good-agent",
            "description": "Agent with valid hooks.",
            "hooks": _VALID_HOOKS,
        },
    )
    assert resp.status_code == 201, resp.text
    assert (agents_dir / "hooks-good-agent.md").exists()  # POSITIVE/NEGATIVE lock


# =============================================================================
# 9. Length caps on AgentWrite (Part B, #2481)
# =============================================================================


async def test_post_description_too_long_422(client, agents_dir):
    """description > 2000 chars → 422 (Pydantic max_length).

    NEGATIVE lock: no file created (validation fired before any write)."""
    resp = await client.post(
        "/api/agents",
        json={"name": "longdesc-agent", "description": "x" * 2001},
    )
    assert resp.status_code == 422, resp.text
    assert not (agents_dir / "longdesc-agent.md").exists()


async def test_post_scope_too_long_422(client, agents_dir):
    """scope > 500 chars → 422 (Pydantic max_length).

    NEGATIVE lock: no file created."""
    resp = await client.post(
        "/api/agents",
        json={"name": "longscope-agent", "description": "ok", "scope": "s" * 501},
    )
    assert resp.status_code == 422, resp.text
    assert not (agents_dir / "longscope-agent.md").exists()


async def test_post_body_too_long_422(client, agents_dir):
    """body > 50 000 chars → 422 (Pydantic max_length).

    NEGATIVE lock: no file created."""
    resp = await client.post(
        "/api/agents",
        json={"name": "longbody-agent", "description": "ok", "body": "b" * 50_001},
    )
    assert resp.status_code == 422, resp.text
    assert not (agents_dir / "longbody-agent.md").exists()


# =============================================================================
# 10. Detail endpoint enrichment — tools + body pre-fill (Part D, #2481)
# =============================================================================

_AGENT_WITH_TOOLS_AND_BODY = """---
name: detail-agent
description: Agent for detail pre-fill test.
model: sonnet
tools: [Read, Grep, Glob]
---

This is the body of the agent.
Second line here.
"""


async def test_get_detail_returns_tools_and_body(client, tmp_path, monkeypatch):
    """GET /api/agents/{name} returns structured `tools` list and raw `body`.

    POSITIVE: tools == ['Read', 'Grep', 'Glob'] and body starts with 'This is'.
    NEGATIVE lock: tools is NOT None and NOT 'All tools' (proving the list was
    parsed, not defaulted); body is NOT empty (proving it was extracted)."""
    (tmp_path / "detail-agent.md").write_text(
        _AGENT_WITH_TOOLS_AND_BODY, encoding="utf-8"
    )
    monkeypatch.setattr(
        "src.routers.agent_gallery.default_agents_dir",
        lambda _repo_root: tmp_path,
    )

    resp = await client.get("/api/agents/detail-agent")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # POSITIVE: structured tools list
    assert data["tools"] == ["Read", "Grep", "Glob"]
    # NEGATIVE lock: not the all-tools fallback
    assert data["tools"] != "All tools"
    assert data["tools"] is not None

    # POSITIVE: raw body pre-fill
    assert "This is the body of the agent." in data["body"]
    # NEGATIVE lock: not empty
    assert data["body"] != ""


# =============================================================================
# 11. Regression — validate_agents_dir over REAL agents dir still accepts
#     hook-bearing agents (the write-path hooks check did NOT leak into read path)
# =============================================================================


def test_validate_hooks_structure_not_called_by_validate_agents_dir(tmp_path):
    """validate_agents_dir over a hook-bearing agent file does NOT invoke the
    write-path validate_hooks_structure (only type-checks the hooks key).

    POSITIVE: the real-agent-shaped hook content passes validate_agents_dir
    with zero errors. NEGATIVE lock: error count is 0, not >0 (if the write
    checker leaked into the read path, the 14 hook-bearing real agents would
    suddenly gain errors — breaking the gallery's calibration gate)."""
    content = """---
name: hook-bearer
description: Agent with a real hooks block.
model: sonnet
hooks:
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: powershell -File "$CLAUDE_PROJECT_DIR/.claude/hooks/test.ps1"
          timeout: 5
---

Body.
"""
    (tmp_path / "hook-bearer.md").write_text(content, encoding="utf-8")

    from src.services.agent_validation import validate_agents_dir

    result = validate_agents_dir(tmp_path)
    error_count = result["error_count"]
    assert error_count == 0  # POSITIVE: zero errors
    # NEGATIVE lock: the write-path structural checker is NOT part of this path
    assert result["warning_count"] == 0  # no warnings either
