"""Kanban #979 — permission gate decision-table coverage.

The gate is a pure function (`check_permission(tools_config, tool) ->
PermissionDecision`); these tests are the canonical decision-table proof for
the locked design:

  - 4 tiers (read/write/network/destructive) x 3 outcomes (auto_allow / halt
    / reject) = 12 base cases (parameterized).
  - Master kill switch (`tools_enabled=false`) rejects EVERY tier regardless
    of `auto_allow_tiers` contents.
  - `tools_config=None` is treated as kill-switch-on (legacy / pre-migration
    rows must not bypass the gate).
  - Unknown / missing tiers default to REJECT (defensive over-block; covers
    the future-Tier-enum-addition forward-compat case).
  - The `ToolTier` Pydantic Literal stays in lockstep with the `Tier` enum
    in `tools/base.py` — a guard test pins the mapping.
"""

from __future__ import annotations

import pytest

from tools import (
    PermissionDecision,
    Tier,
    Tool,
    ToolInput,
    ToolResult,
    check_permission,
)


# ---------------------------------------------------------------------------
# Tiny stub tools — one per Tier value. Real registered tools work too (and
# we sanity-check that path in `test_real_tool_from_registry` below), but
# stubs keep the cases self-contained and resilient to future tier
# reassignments on the production tools.
# ---------------------------------------------------------------------------


class _StubInput(ToolInput):
    pass


def _make_stub_tool(tier_value: Tier) -> Tool:
    """Build a fresh stub Tool subclass with the requested tier."""

    class _Stub(Tool):
        name = f"stub_{tier_value.value}"
        description = f"stub tool at tier {tier_value.value}"
        tier = tier_value
        input_schema = _StubInput

        async def _run(self, input_obj, context):  # pragma: no cover
            return ToolResult(success=True)

    return _Stub()


@pytest.fixture
def stub_tools() -> dict[Tier, Tool]:
    return {t: _make_stub_tool(t) for t in Tier}


# ---------------------------------------------------------------------------
# Helpers — config builders matching the locked Q2-B default + variants.
# ---------------------------------------------------------------------------


def _enabled_config(
    *,
    auto_allow: list[str],
    halt: list[str],
    hosts: list[str] | None = None,
) -> dict:
    return {
        "tools_enabled": True,
        "auto_allow_tiers": auto_allow,
        "halt_tiers": halt,
        "http_hosts": hosts or [],
    }


def _locked_default() -> dict:
    """The locked Q2-B default (tools_enabled=False)."""
    return {
        "tools_enabled": False,
        "auto_allow_tiers": ["read"],
        "halt_tiers": ["write", "network", "destructive"],
        "http_hosts": [],
    }


# ---------------------------------------------------------------------------
# 12-case decision table: 4 tiers x 3 outcomes.
# Each case crafts a config that should produce the expected verdict on the
# stub at that tier.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tier", "auto_allow", "halt", "expected"),
    [
        # AUTO_ALLOW for every tier (when explicitly listed)
        (Tier.READ, ["read"], ["write", "network", "destructive"], PermissionDecision.AUTO_ALLOW),
        (Tier.WRITE, ["write"], ["read", "network", "destructive"], PermissionDecision.AUTO_ALLOW),
        (Tier.NETWORK, ["network"], ["read", "write", "destructive"], PermissionDecision.AUTO_ALLOW),
        (Tier.DESTRUCTIVE, ["destructive"], ["read", "write", "network"], PermissionDecision.AUTO_ALLOW),
        # HALT for every tier (when in halt_tiers and NOT in auto_allow)
        (Tier.READ, [], ["read"], PermissionDecision.HALT),
        (Tier.WRITE, [], ["write"], PermissionDecision.HALT),
        (Tier.NETWORK, [], ["network"], PermissionDecision.HALT),
        (Tier.DESTRUCTIVE, [], ["destructive"], PermissionDecision.HALT),
        # REJECT for every tier (when in NEITHER list — defensive default)
        (Tier.READ, [], [], PermissionDecision.REJECT),
        (Tier.WRITE, [], [], PermissionDecision.REJECT),
        (Tier.NETWORK, [], [], PermissionDecision.REJECT),
        (Tier.DESTRUCTIVE, [], [], PermissionDecision.REJECT),
    ],
)
def test_decision_table(stub_tools, tier, auto_allow, halt, expected):
    cfg = _enabled_config(auto_allow=auto_allow, halt=halt)
    assert check_permission(cfg, stub_tools[tier]) is expected


# ---------------------------------------------------------------------------
# Master kill switch — tools_enabled=False rejects EVERY tier, even when the
# tier is explicitly in auto_allow_tiers. This is the load-bearing safety
# invariant (Q2-B locked default ships with tools_enabled=False).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier", list(Tier))
def test_tools_enabled_false_kills_all_tiers(stub_tools, tier):
    """Every tier rejects when tools_enabled=False, regardless of allow-list."""
    cfg = {
        "tools_enabled": False,
        # Even with the tier ALLOWED, the kill switch overrides.
        "auto_allow_tiers": [tier.value],
        "halt_tiers": [],
        "http_hosts": [],
    }
    assert check_permission(cfg, stub_tools[tier]) is PermissionDecision.REJECT


def test_locked_default_rejects_read(stub_tools):
    """The Q2-B locked default ships tools_enabled=False — even read rejects.

    Pins the on-ship behavior: a fresh project's `tools_config` cannot
    silently auto-execute ANY tool until the user explicitly enables.
    """
    assert check_permission(_locked_default(), stub_tools[Tier.READ]) is PermissionDecision.REJECT


# ---------------------------------------------------------------------------
# Forward-compat / defensive defaults
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier", list(Tier))
def test_none_config_treated_as_disabled(stub_tools, tier):
    """`tools_config=None` (legacy row, pre-migration 0027) rejects all tiers.

    Mirrors `tools_enabled=False`. Over-block beats under-block on a
    NULL row — the alternative (treat None as "no policy, allow everything")
    would be a silent backdoor.
    """
    assert check_permission(None, stub_tools[tier]) is PermissionDecision.REJECT


def test_empty_dict_treated_as_disabled(stub_tools):
    """Empty dict has no `tools_enabled` key → falsy → reject.

    Defensive: a malformed / partially-migrated row must NOT auto-allow.
    """
    assert check_permission({}, stub_tools[Tier.READ]) is PermissionDecision.REJECT


def test_unknown_tier_defaults_to_reject(stub_tools):
    """A tier in neither auto_allow nor halt list rejects (defensive default).

    Already covered in the 12-case parameterized table, but spelled out
    here for clarity — the SAME case is the forward-compat protection
    when a new Tier enum value is added but the project's config hasn't
    been migrated to include it.
    """
    cfg = _enabled_config(auto_allow=[], halt=[])
    assert check_permission(cfg, stub_tools[Tier.DESTRUCTIVE]) is PermissionDecision.REJECT


def test_auto_allow_wins_over_halt_if_overlap_present(stub_tools):
    """Defensive: a hand-edited DB row with overlapping lists picks auto_allow.

    The Pydantic `ToolsConfig` validator at the API boundary REJECTS overlap
    (422), so the only way an overlapping config reaches the gate is via a
    raw-SQL hand edit. The gate must still be deterministic; the locked
    lookup order is auto_allow → halt → reject.
    """
    cfg = _enabled_config(auto_allow=["read"], halt=["read"])
    assert check_permission(cfg, stub_tools[Tier.READ]) is PermissionDecision.AUTO_ALLOW


def test_malformed_list_type_treated_as_empty(stub_tools):
    """If `auto_allow_tiers` / `halt_tiers` isn't a list (hand-edited drift),
    the gate treats it as empty — falls through to REJECT for any tier."""
    cfg = {
        "tools_enabled": True,
        "auto_allow_tiers": "read",  # malformed — string, not list
        "halt_tiers": None,  # malformed — None, not list
        "http_hosts": [],
    }
    assert check_permission(cfg, stub_tools[Tier.READ]) is PermissionDecision.REJECT


# ---------------------------------------------------------------------------
# Real-registry sanity — verify the gate works against an actual registered
# tool (not just stubs). file_edit is Tier.WRITE; the locked default puts
# `write` in halt_tiers, so under tools_enabled=True it should HALT.
# ---------------------------------------------------------------------------


def test_real_tool_from_registry_halts_on_write():
    from tools import GLOBAL_REGISTRY

    file_edit = GLOBAL_REGISTRY.get("file_edit")
    assert file_edit.tier is Tier.WRITE
    cfg = {
        "tools_enabled": True,
        "auto_allow_tiers": ["read"],
        "halt_tiers": ["write", "network", "destructive"],
        "http_hosts": [],
    }
    assert check_permission(cfg, file_edit) is PermissionDecision.HALT


def test_real_tool_from_registry_auto_allows_read():
    from tools import GLOBAL_REGISTRY

    git_status = GLOBAL_REGISTRY.get("git_status")
    assert git_status.tier is Tier.READ
    cfg = {
        "tools_enabled": True,
        "auto_allow_tiers": ["read"],
        "halt_tiers": ["write", "network", "destructive"],
        "http_hosts": [],
    }
    assert check_permission(cfg, git_status) is PermissionDecision.AUTO_ALLOW


# ---------------------------------------------------------------------------
# Tier enum vs Pydantic Literal lockstep
# ---------------------------------------------------------------------------


def test_permission_decision_serializes_to_str():
    """`PermissionDecision` inherits `str, Enum` so it serializes cleanly
    into the future audit log (#980) and into halt_reason metadata
    without explicit `.value` access at call sites."""
    assert PermissionDecision.AUTO_ALLOW == "auto_allow"
    assert PermissionDecision.HALT == "halt"
    assert PermissionDecision.REJECT == "reject"


def test_tier_enum_values_align_with_known_strings():
    """The four Tier values are the canonical tier strings the API
    contract uses. A drift here fires immediately in CI."""
    assert {t.value for t in Tier} == {"read", "write", "network", "destructive"}
