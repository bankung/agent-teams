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

import re
from pathlib import Path

from src.schemas.agent_metadata import KNOWN_TOOLS
from src.services.tool_risk import (
    ALL_TOOLS_CHIP,
    RISK_CLASSES,
    TOOL_RISK_TABLE,
    build_tool_chips,
    classify_tool,
)

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
# 1b. Lockstep guard — TOOL_RISK_TABLE names must all be KNOWN_TOOLS
# (Kanban #1021 dev-reviewer fix: the two vocabularies had diverged — a name
# the risk classifier confidently classifies must never ALSO trip the #1016
# validator's "unrecognized tool" WARNING. No prod import between the two
# modules; this test is the lockstep guard.)
# =============================================================================


def test_risk_table_names_are_known_tools():
    missing = set(TOOL_RISK_TABLE) - KNOWN_TOOLS
    # POSITIVE + NEGATIVE in one: every TOOL_RISK_TABLE key is a KNOWN_TOOLS
    # member (empty diff). A non-empty diff names exactly which tool(s)
    # would spuriously WARN in the #1016 validator despite the risk
    # classifier already knowing them.
    assert missing == set(), (
        f"TOOL_RISK_TABLE names missing from KNOWN_TOOLS: {sorted(missing)} — "
        f"add them to agent_metadata.KNOWN_TOOLS or the #1016 validator will "
        f"spuriously WARN 'unrecognized tool' for a name tool_risk.py already "
        f"classifies confidently."
    )


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


# =============================================================================
# 4. Lockstep guard — FE RISK_ORDER (AgentBadges.tsx) vs BE RISK_CLASSES
# (Kanban #2789 AC2: the two risk-class taxonomies were kept in sync by a
# COMMENT ONLY on each side — this section replaces that with a fail-loud
# guard so an add/remove on either side breaks the suite instead of drifting
# silently. Mirrors the `_find_agents_dir()` walk-up pattern in
# test_spawn_check.py so the FE path isn't hardcoded to a parents[N] depth.)
# =============================================================================

_RISK_ORDER_RE = re.compile(
    r"RISK_ORDER:\s*ToolChipRiskClass\[\]\s*=\s*\[(.*?)\]", re.DOTALL
)
_QUOTED_STRING_RE = re.compile(r'"([^"]+)"')


def _find_repo_root() -> Path:
    """Walk up from this test file until the repo root is found (marked by
    the presence of `web/components/AgentBadges.tsx`).
    """
    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        fe_file = candidate / "web" / "components" / "AgentBadges.tsx"
        if fe_file.is_file():
            return candidate
    raise RuntimeError(
        f"Could not locate web/components/AgentBadges.tsx walking up from {here}"
    )


def _parse_fe_risk_order() -> list[str]:
    fe_path = _find_repo_root() / "web" / "components" / "AgentBadges.tsx"
    assert fe_path.is_file(), f"expected FE file at {fe_path}"
    text = fe_path.read_text(encoding="utf-8")
    match = _RISK_ORDER_RE.search(text)
    assert match is not None, (
        "RISK_ORDER array literal not found in AgentBadges.tsx — FE source "
        "shape changed; update _RISK_ORDER_RE."
    )
    order = _QUOTED_STRING_RE.findall(match.group(1))
    assert order, "RISK_ORDER parsed to an empty list — regex likely mismatched"
    return order


def test_fe_risk_order_matches_be_risk_classes():
    fe_order = _parse_fe_risk_order()
    # Sanity-check the parse itself before trusting it as a NEGATIVE lock:
    # must be the 5 expected classes, not an accidental partial/empty match.
    assert len(fe_order) == 5, fe_order
    # POSITIVE + NEGATIVE in one (mirrors test_risk_table_names_are_known_tools
    # above): membership must match exactly in both directions — a class
    # added/removed on either side shows up as a non-empty symmetric diff.
    assert set(fe_order) == set(RISK_CLASSES), (
        f"FE RISK_ORDER {sorted(fe_order)} != BE RISK_CLASSES "
        f"{sorted(RISK_CLASSES)} — a risk class was added/removed on one "
        f"side without the other. Update web/components/AgentBadges.tsx "
        f"RISK_ORDER and src/services/tool_risk.py RISK_CLASSES together."
    )
    # Nice-to-have: severity ORDER also matches, not just membership.
    assert fe_order == list(RISK_CLASSES), (
        f"FE RISK_ORDER order {fe_order} != BE RISK_CLASSES order "
        f"{list(RISK_CLASSES)} (same classes, different severity ranking)."
    )


def test_be_risk_classes_are_self_consistent():
    # Every value TOOL_RISK_TABLE can produce is a member of the canonical set.
    assert set(TOOL_RISK_TABLE.values()) <= set(RISK_CLASSES)
    # NEGATIVE lock companion: an empty TOOL_RISK_TABLE would vacuously pass
    # the subset check above, so confirm it's actually populated.
    assert TOOL_RISK_TABLE, "TOOL_RISK_TABLE is empty — subset check above is vacuous"
    # The All-tools pseudo-entry's risk_class is also a member.
    assert ALL_TOOLS_CHIP["risk_class"] in RISK_CLASSES
