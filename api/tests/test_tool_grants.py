"""Unit tests for the Mode-A tool-governance gate (Kanban #1799 P0).

Covers `services/tool_grants.check_grant` (all 5 enforcement cases + audit-row
written for ALLOW and DENY), the static `services/tool_registry`, and the
`config.tool_grants` Pydantic validator on ProjectCreate / ProjectUpdate.

The gate is a PURE function (no DB). It writes a JSONL audit row to
`TOOL_GRANTS_AUDIT_PATH`; tests point that env at a per-test tmp file so the
allow/deny rows can be read back without touching the real _scratch trail.
"""

from __future__ import annotations

import json

import pytest

from src.schemas.project import ProjectCreate, ProjectUpdate
from src.services import tool_grants
from src.services.tool_grants import GrantDecision, check_grant
from src.services.tool_registry import TOOL_REGISTRY, get_tool, is_known_tool


# ---------------------------------------------------------------------------
# Audit-path redirect: route check_grant's JSONL writes to a tmp file per test
# so we can assert the row was written without polluting the real trail.
# ---------------------------------------------------------------------------


@pytest.fixture
def audit_path(tmp_path, monkeypatch):
    """Point tool_grants._AUDIT_PATH at a fresh tmp file for this test."""
    p = tmp_path / "tool-grants-audit.jsonl"
    monkeypatch.setattr(tool_grants, "_AUDIT_PATH", p)
    return p


def _read_audit(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


# ===========================================================================
# Registry
# ===========================================================================


def test_registry_seeds_both_trash_tools_as_destructive() -> None:
    assert is_known_tool("gmail.trash")
    assert is_known_tool("outlook.trash")
    assert TOOL_REGISTRY["gmail.trash"]["tier"] == "destructive"
    assert TOOL_REGISTRY["outlook.trash"]["tier"] == "destructive"
    # Forward-compat metadata present, NOT consumed by the gate.
    assert TOOL_REGISTRY["gmail.trash"]["version"] == "v1"
    # No cost_units key — costs stay in the router constants (design doc).
    assert "cost_units" not in TOOL_REGISTRY["gmail.trash"]


def test_registry_unknown_tool() -> None:
    assert not is_known_tool("gmail.nuke")
    assert get_tool("gmail.nuke") is None


# ===========================================================================
# check_grant — the 5 enforcement cases
# ===========================================================================


def test_grant_absent_tool_grants_key_allows(audit_path) -> None:
    """Case 1: config has no tool_grants key -> ALLOW (unrestricted default)."""
    cfg = {"enabled_roles": [1, 2]}  # other config present, no tool_grants
    assert check_grant(cfg, "secretary", "gmail.trash") is GrantDecision.ALLOW


def test_grant_role_not_a_key_allows(audit_path) -> None:
    """Case 2: tool_grants present but role not listed -> ALLOW (opt-in)."""
    cfg = {"tool_grants": {"dev-backend": ["gmail.trash"]}}
    # secretary is NOT a key -> unrestricted.
    assert check_grant(cfg, "secretary", "gmail.trash") is GrantDecision.ALLOW


def test_grant_role_listed_tool_in_list_allows(audit_path) -> None:
    """Case 3: role IS a key AND tool in its list -> ALLOW."""
    cfg = {"tool_grants": {"secretary": ["gmail.trash", "outlook.trash"]}}
    assert check_grant(cfg, "secretary", "gmail.trash") is GrantDecision.ALLOW
    assert check_grant(cfg, "secretary", "outlook.trash") is GrantDecision.ALLOW


def test_grant_role_listed_tool_not_in_list_denies(audit_path) -> None:
    """Case 4: role IS a key but tool NOT in its list -> DENY (caller 403s)."""
    cfg = {"tool_grants": {"secretary": ["gmail.trash"]}}
    # outlook.trash is not granted to secretary.
    assert check_grant(cfg, "secretary", "outlook.trash") is GrantDecision.DENY


def test_grant_role_empty_list_denies_everything(audit_path) -> None:
    """Case 5: role key with empty list -> DENY every tool (explicit lockout)."""
    cfg = {"tool_grants": {"secretary": []}}
    assert check_grant(cfg, "secretary", "gmail.trash") is GrantDecision.DENY
    assert check_grant(cfg, "secretary", "outlook.trash") is GrantDecision.DENY


def test_grant_none_config_allows(audit_path) -> None:
    """A None config (legacy / unset) -> ALLOW (unrestricted)."""
    assert check_grant(None, "secretary", "gmail.trash") is GrantDecision.ALLOW


def test_grant_none_role_allows(audit_path) -> None:
    """No X-Agent-Role header (role=None) -> ALLOW even when tool_grants exist."""
    cfg = {"tool_grants": {"secretary": []}}
    assert check_grant(cfg, None, "gmail.trash") is GrantDecision.ALLOW


def test_grant_malformed_tool_grants_allows(audit_path) -> None:
    """A hand-edited tool_grants that is not a dict -> ALLOW (opt-in: do not
    silently lock out roles the operator never meant to restrict)."""
    cfg = {"tool_grants": ["secretary"]}  # bogus shape (list, not dict)
    assert check_grant(cfg, "secretary", "gmail.trash") is GrantDecision.ALLOW


def test_grant_malformed_role_value_denies(audit_path) -> None:
    """A role key present but its value is not a list -> DENY (over-block once a
    role is explicitly listed)."""
    cfg = {"tool_grants": {"secretary": "gmail.trash"}}  # str, not list
    assert check_grant(cfg, "secretary", "gmail.trash") is GrantDecision.DENY


# ===========================================================================
# Audit row written for BOTH allow and deny
# ===========================================================================


def test_audit_row_written_on_allow(audit_path) -> None:
    cfg = {"tool_grants": {"secretary": ["gmail.trash"]}}
    check_grant(cfg, "secretary", "gmail.trash", project_id=42)
    rows = _read_audit(audit_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["decision"] == "allow"
    assert row["role"] == "secretary"
    assert row["tool"] == "gmail.trash"
    assert row["project_id"] == 42
    assert row["ts"].endswith("Z")


def test_audit_row_written_on_deny(audit_path) -> None:
    cfg = {"tool_grants": {"secretary": ["gmail.trash"]}}
    check_grant(cfg, "secretary", "outlook.trash", project_id=42)
    rows = _read_audit(audit_path)
    assert len(rows) == 1
    row = rows[0]
    # POSITIVE: a deny row IS written (the trail covers refusals).
    assert row["decision"] == "deny"
    assert row["tool"] == "outlook.trash"
    # NEGATIVE: it is NOT recorded as an allow.
    assert row["decision"] != "allow"


def test_audit_appends_both_decisions_in_sequence(audit_path) -> None:
    """Two calls (one allow, one deny) append two distinct rows."""
    cfg = {"tool_grants": {"secretary": ["gmail.trash"]}}
    check_grant(cfg, "secretary", "gmail.trash", project_id=1)
    check_grant(cfg, "secretary", "outlook.trash", project_id=1)
    rows = _read_audit(audit_path)
    decisions = [r["decision"] for r in rows]
    assert decisions == ["allow", "deny"]


# ===========================================================================
# config.tool_grants validator — ProjectCreate / ProjectUpdate (422 / accept)
# ===========================================================================


def _create_payload(config: dict) -> dict:
    """Minimal valid ProjectCreate kwargs with the given config."""
    return {
        "name": "grant-validator-test",
        "paths": {"web": "/w", "api": "/a", "db": "/d"},
        "team": "dev",
        "config": config,
    }


def test_validator_accepts_valid_grant() -> None:
    """A valid {role: [registry-known tool]} passes ProjectCreate validation."""
    pc = ProjectCreate(**_create_payload({"tool_grants": {"secretary": ["gmail.trash"]}}))
    assert pc.config["tool_grants"]["secretary"] == ["gmail.trash"]


def test_validator_accepts_empty_grant_list() -> None:
    """An explicit empty allow-list (deny-all lockout) is a VALID config."""
    pc = ProjectCreate(**_create_payload({"tool_grants": {"secretary": []}}))
    assert pc.config["tool_grants"]["secretary"] == []


def test_validator_rejects_non_list_value() -> None:
    """A role value that is not a list -> ValidationError (422 at the boundary)."""
    with pytest.raises(Exception) as exc:
        ProjectCreate(**_create_payload({"tool_grants": {"secretary": "gmail.trash"}}))
    assert "tool_grants" in str(exc.value)


def test_validator_rejects_unknown_tool_name() -> None:
    """A tool name absent from the registry -> ValidationError."""
    with pytest.raises(Exception) as exc:
        ProjectCreate(**_create_payload({"tool_grants": {"secretary": ["gmail.nuke"]}}))
    assert "not a registered tool" in str(exc.value)


def test_validator_rejects_non_dict_tool_grants() -> None:
    """tool_grants itself must be an object (dict), not a list."""
    with pytest.raises(Exception) as exc:
        ProjectCreate(**_create_payload({"tool_grants": ["secretary"]}))
    assert "tool_grants" in str(exc.value)


def test_validator_rejects_bad_role_key_shape() -> None:
    """A role key with illegal chars (path/space) is rejected."""
    with pytest.raises(Exception) as exc:
        ProjectCreate(**_create_payload({"tool_grants": {"../etc": ["gmail.trash"]}}))
    assert "tool_grants" in str(exc.value)


def test_validator_on_update_path() -> None:
    """The PATCH (ProjectUpdate) config validator enforces the same rules."""
    # valid
    pu = ProjectUpdate(config={"tool_grants": {"dev-backend": ["outlook.trash"]}})
    assert pu.config["tool_grants"]["dev-backend"] == ["outlook.trash"]
    # invalid tool
    with pytest.raises(Exception) as exc:
        ProjectUpdate(config={"tool_grants": {"dev-backend": ["bogus.tool"]}})
    assert "not a registered tool" in str(exc.value)


def test_validator_ignores_config_without_tool_grants() -> None:
    """A config with no tool_grants key is untouched (and other subkeys still
    validate — enabled_roles here)."""
    pc = ProjectCreate(**_create_payload({"enabled_roles": [1, 2, 3]}))
    assert "tool_grants" not in pc.config
    assert pc.config["enabled_roles"] == [1, 2, 3]


# ===========================================================================
# NIT-2 (#1848): audit default path is outside _scratch (durable sink)
# ===========================================================================


def test_audit_default_path_is_not_scratch() -> None:
    """The module-level _AUDIT_PATH default must not point into _scratch/.

    _scratch is gitignored and excluded from the nightly backup tarball
    (backup.py::_TAR_SKIP_NAMES). Audit rows written there are lost on
    container rebuild if TOOL_GRANTS_AUDIT_PATH is unset. The default should
    resolve to a durable path (e.g. /repo/logs/) so the trail survives absent
    operator configuration.

    POSITIVE: the default contains '/logs/' (a durable directory).
    NEGATIVE: the default does NOT contain '_scratch'.
    """
    import os

    from src.services import tool_grants

    # Read the compiled default from the env-lookup expression (the module
    # stores the resolved Path in _AUDIT_PATH at import time). If the env var
    # is set in the test runner the value will reflect that; this test asserts
    # the DEFAULT (env var unset).
    env_override = os.environ.get("TOOL_GRANTS_AUDIT_PATH", "")
    if env_override:
        # Env is set — can only assert it's not _scratch (operator configured).
        assert "_scratch" not in env_override, (
            "TOOL_GRANTS_AUDIT_PATH points into _scratch — override to a "
            "durable path (e.g. /repo/logs/tool-grants-audit.jsonl)"
        )
    else:
        # No override — the compiled default in the module must not be _scratch.
        default_str = str(tool_grants._AUDIT_PATH)
        assert "_scratch" not in default_str, (
            f"Default audit path {default_str!r} is inside _scratch (not durable)"
        )
        # POSITIVE: the path is in the expected durable location.
        assert "logs" in default_str, (
            f"Default audit path {default_str!r} should be in a 'logs' directory"
        )
