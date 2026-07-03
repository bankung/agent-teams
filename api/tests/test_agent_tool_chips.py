"""Contract-smoke tests for tool-scope risk chips (Kanban #1021).

``services/tool_risk.py`` classifies every tool name a `.claude/agents/*.md`
frontmatter can carry into one of five risk classes, and
``services/agent_validation.py`` threads the result into a new additive
``tool_chips`` field on BOTH ``AgentSummary`` (``GET /api/agents``) and
``AgentDetail`` (``GET /api/agents/{name}``) — the existing ``tools`` /
``tools_summary`` / ``tool_count`` fields are untouched.

Classification unit cases (one per class + the mcp__ prefix rule + the
fail-closed unknown-tool default + the "All tools" pseudo-entry), plus two
live-route cases against the tmp-dir pattern already used by
``test_agent_gallery.py`` (same ``_patch_agents_dir``-style fixture, kept
local here so this file has no import-order dependency on that one).

Every assertion pairs a POSITIVE check with the NEGATIVE lock it is guarding.
"""

from __future__ import annotations

from pathlib import Path

from src.services.tool_risk import build_tool_chips, classify_tool

# =============================================================================
# 1. Unit — classify_tool: one case per class
# =============================================================================


def test_classify_read_only_tools():
    for name in ("Read", "Grep", "Glob", "WebFetch", "WebSearch"):
        assert classify_tool(name) == "read-only"
    # NEGATIVE lock: a write tool must NOT land in read-only.
    assert classify_tool("Write") != "read-only"


def test_classify_write_edit_tools():
    for name in ("Write", "Edit", "NotebookEdit"):
        assert classify_tool(name) == "write-edit"
    # NEGATIVE lock: a read-only tool must NOT land in write-edit.
    assert classify_tool("Read") != "write-edit"


def test_classify_shell_or_destructive_tools():
    for name in ("Bash", "PowerShell"):
        assert classify_tool(name) == "shell-or-destructive"
    # NEGATIVE lock: a read-only tool must NOT be flagged shell-or-destructive.
    assert classify_tool("Read") != "shell-or-destructive"


def test_classify_always_safe_tool():
    assert classify_tool("TodoWrite") == "always-safe"
    # NEGATIVE lock: always-safe is NOT the default for an arbitrary name.
    assert classify_tool("SomeRandomTool") != "always-safe"


def test_classify_external_mcp_prefix():
    # Both documented prefixes.
    assert classify_tool("mcp__firecrawl-scrape") == "external"
    assert classify_tool("mcp__ccd_session__dismiss_task") == "external"
    assert classify_tool("MCP_SOMETHING") == "external"
    # NEGATIVE lock: a non-mcp name is NOT external even if it contains "mcp"
    # as a substring (prefix check, not a substring check).
    assert classify_tool("SomeMcpHelper") != "external"


def test_classify_unknown_tool_fails_closed():
    # A name never seen before (not in TOOL_RISK_TABLE, no mcp__/MCP_ prefix)
    # must default to the HIGHEST-risk bucket, never a safe one.
    assert classify_tool("TotallyMadeUpTool") == "shell-or-destructive"
    # NEGATIVE lock: it must NOT silently resolve to always-safe or read-only
    # (the two classes that would under-report risk).
    assert classify_tool("TotallyMadeUpTool") not in ("always-safe", "read-only")


# =============================================================================
# 2. Unit — build_tool_chips: list / All-tools / None / malformed shapes
# =============================================================================


def test_build_tool_chips_preserves_frontmatter_order():
    # Deliberately NOT alphabetical input (Write, Bash, Read) so the negative
    # lock below is meaningful — an already-sorted input would let a stray
    # `sorted(...)` pass silently.
    chips = build_tool_chips(["Write", "Bash", "Read"])
    # POSITIVE: order preserved exactly as given (NOT alphabetized).
    assert [c["name"] for c in chips] == ["Write", "Bash", "Read"]
    assert [c["risk_class"] for c in chips] == [
        "write-edit",
        "shell-or-destructive",
        "read-only",
    ]
    # NEGATIVE lock: sorted() would reorder this to [Bash, Read, Write] —
    # confirm the actual output does NOT match the sorted order (guards
    # against a future accidental `sorted(...)` creeping in).
    assert [c["name"] for c in chips] != sorted(["Write", "Bash", "Read"])


def test_build_tool_chips_all_tools_pseudo_entry():
    from_literal = build_tool_chips("All tools")
    from_none = build_tool_chips(None)
    for chips in (from_literal, from_none):
        # POSITIVE: exactly one chip, the All-tools pseudo-entry.
        assert chips == [{"name": "All tools", "risk_class": "shell-or-destructive"}]
    # NEGATIVE lock: the None case must NOT be an empty chip list (that would
    # read as "no tools", the opposite of the intended "all tools" meaning).
    assert from_none != []


def test_build_tool_chips_empty_list_stays_empty():
    # An explicit empty list is a real (if unusual) frontmatter value — it
    # means "this agent's tools list is empty", NOT "unset" (which is what
    # None/absent means). Must NOT be coerced into the All-tools pseudo-entry.
    assert build_tool_chips([]) == []


# =============================================================================
# 3. Live route — GET /api/agents and GET /api/agents/{name} carry tool_chips
# =============================================================================

_VALID = """---
name: {name}
description: {desc}
model: sonnet
tools: {tools}
---

Body for {name}.
"""


def _write(dir_: Path, filename: str, content: str) -> Path:
    p = dir_ / filename
    p.write_text(content, encoding="utf-8")
    return p


async def test_list_and_detail_endpoints_carry_tool_chips(client, tmp_path, monkeypatch):
    _write(
        tmp_path,
        "chips-agent.md",
        _VALID.format(
            name="chips-agent", desc="Chips fixture.", tools="[Bash, Read, mcp__firecrawl-scrape]"
        ),
    )
    _write(
        tmp_path,
        "alltools-agent.md",
        "---\nname: alltools-agent\ndescription: no tools key.\nmodel: sonnet\n---\nbody\n",
    )
    monkeypatch.setattr(
        "src.routers.agent_gallery.default_agents_dir",
        lambda _repo_root: tmp_path,
    )

    list_resp = await client.get("/api/agents")
    assert list_resp.status_code == 200
    rows = {r["name"]: r for r in list_resp.json()}

    chips_row = rows["chips-agent"]
    # POSITIVE: order preserved, each entry classified correctly.
    assert chips_row["tool_chips"] == [
        {"name": "Bash", "risk_class": "shell-or-destructive"},
        {"name": "Read", "risk_class": "read-only"},
        {"name": "mcp__firecrawl-scrape", "risk_class": "external"},
    ]
    # NEGATIVE lock: the pre-existing tools_summary/tool_count fields are
    # UNCHANGED by this additive field (still "N tools" + 3, not touched).
    assert chips_row["tools_summary"] == "3 tools"
    assert chips_row["tool_count"] == 3

    all_tools_row = rows["alltools-agent"]
    # POSITIVE: absent `tools:` key → the single All-tools pseudo-chip.
    assert all_tools_row["tool_chips"] == [
        {"name": "All tools", "risk_class": "shell-or-destructive"}
    ]

    detail_resp = await client.get("/api/agents/chips-agent")
    assert detail_resp.status_code == 200
    detail_body = detail_resp.json()
    # POSITIVE: the detail endpoint carries the SAME tool_chips as the list row.
    assert detail_body["tool_chips"] == chips_row["tool_chips"]
    # NEGATIVE lock: the detail-only `tools` field (structured, edit-prefill)
    # is STILL present and untouched — tool_chips is additive, not a replacement.
    assert detail_body["tools"] == ["Bash", "Read", "mcp__firecrawl-scrape"]
