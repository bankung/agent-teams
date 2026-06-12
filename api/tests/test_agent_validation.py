"""Tests for the agent-frontmatter validator (Kanban #1016).

Coverage matrix:

1. Service (`services.agent_validation.validate_agents_dir`) — tmp_path-based,
   the real agents dir is NEVER touched (the dir path is injected per test).
   Invalid cases (≥5, each asserts severity + that a line is reported):
     - missing `name`
     - duplicate `name` across two files
     - bad `name` regex
     - bad `model` enum
     - malformed YAML (asserts the reported line number)
   Plus:
     - a fully valid file → zero diagnostics
     - unknown top-level key → WARNING (not error)
     - unknown tool name inside `tools` → WARNING (not error)
     - `tools: "All tools"` literal → accepted (no diagnostic)
     - missing frontmatter → ERROR
     - underscore-prefixed include (`_dev-shared.md`) → SKIPPED (not scanned)

2. Endpoint `GET /api/agents/validate`
     - response shape: files_scanned + diagnostics + error_count + warning_count
     - POST to the same URL → 405 (no POST handler)

3. CLI (`scripts.validate_agents.run`)
     - invoked as the function (NOT a subprocess); exit code 1 when an error
       exists, 0 when clean / warnings-only.

Every error/warning assertion pairs a POSITIVE check (the expected diagnostic
appears) with a NEGATIVE lock (a control that should NOT produce that diagnostic
stays clean), so a passing test cannot be satisfied by a validator that flags
everything (or nothing).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.schemas.agent_metadata import AgentMetadata
from src.services import agent_validation as svc

# A minimal, fully-valid frontmatter block reused across fixtures. Every
# required key present + a valid model + a known-tool list, so it produces ZERO
# diagnostics — the clean baseline every negative-lock leans on.
_VALID = """---
name: {name}
description: A valid test agent.
model: sonnet
tools: [Read, Grep, Glob]
---

Body text for {name}.
"""


def _write(dir_: Path, filename: str, content: str) -> Path:
    p = dir_ / filename
    p.write_text(content, encoding="utf-8")
    return p


def _diags_for(dir_: Path) -> list[dict]:
    return svc.validate_agents_dir(dir_)["diagnostics"]


def _diags_for_file(dir_: Path, filename: str) -> list[dict]:
    return [d for d in _diags_for(dir_) if d["file"] == filename]


# =============================================================================
# 0. The AgentMetadata Pydantic schema itself (the contract declaration)
# =============================================================================


def test_agent_metadata_accepts_valid_minimal():
    m = AgentMetadata(name="dev-backend", description="x")
    assert m.name == "dev-backend"
    assert m.model is None  # absent = inherit (not an error)


def test_agent_metadata_accepts_all_tools_literal_and_extra_keys():
    m = AgentMetadata(
        name="secretary",
        description="x",
        tools="All tools",
        email_actions="enabled",  # custom key tolerated (extra="allow")
    )
    assert m.tools == "All tools"


def test_agent_metadata_rejects_bad_name_and_model():
    with pytest.raises(ValueError):
        AgentMetadata(name="Dev Backend", description="x")  # spaces, caps
    with pytest.raises(ValueError):
        AgentMetadata(name="dev-backend", description="x", model="gpt5")


# =============================================================================
# 1. Service — valid baseline
# =============================================================================


def test_valid_file_has_zero_diagnostics(tmp_path):
    _write(tmp_path, "dev-backend.md", _VALID.format(name="dev-backend"))
    result = svc.validate_agents_dir(tmp_path)
    assert result["files_scanned"] == 1
    assert result["diagnostics"] == []
    assert result["error_count"] == 0
    assert result["warning_count"] == 0


def test_folded_block_scalar_description_is_accepted(tmp_path):
    # netops-monitoring-reader.md shape: ``description: >`` folded block scalar
    # spanning indented continuation lines, AND containing mid-sentence colons.
    # Strict YAML handles this, but the line parser must too. (calibration)
    _write(
        tmp_path,
        "folded.md",
        "---\n"
        "name: folded-agent\n"
        "description: >\n"
        "  Read-only agent. Pulls data: events, history. Never writes config.\n"
        "  Proposes manual fixes only.\n"
        "tools: [Read, Grep]\n"
        "---\nbody\n",
    )
    # POSITIVE: zero diagnostics — the folded description is valid.
    assert _diags_for_file(tmp_path, "folded.md") == []


def test_description_with_inline_colons_is_accepted(tmp_path):
    # Plain (non-folded) description with mid-sentence colons — the exact shape
    # that made strict-YAML emit 8 false "mapping values" errors. (calibration)
    _write(
        tmp_path,
        "colons.md",
        "---\n"
        "name: colon-agent\n"
        "description: SQL optimizer. Read-only: never executes DML. "
        "Success metric: every rewrite ships with an EXPLAIN delta.\n"
        "model: sonnet\n"
        "---\nbody\n",
    )
    assert _diags_for_file(tmp_path, "colons.md") == []


def test_model_absent_is_not_an_error(tmp_path):
    # netops-monitoring-reader.md shape: real file with NO model key.
    _write(
        tmp_path,
        "netops.md",
        "---\nname: netops-monitoring-reader\n"
        "description: read-only.\ntools: [Read, Grep]\n---\nbody\n",
    )
    assert _diags_for(tmp_path) == []


# =============================================================================
# 1. Service — invalid cases (≥5)
# =============================================================================


def test_missing_name_is_error(tmp_path):
    _write(
        tmp_path,
        "noname.md",
        "---\ndescription: missing the name key.\nmodel: sonnet\n---\nbody\n",
    )
    # control: a sibling valid file must stay clean (negative lock).
    _write(tmp_path, "ok.md", _VALID.format(name="ok-agent"))

    diags = _diags_for_file(tmp_path, "noname.md")
    assert any(
        d["field"] == "name"
        and d["severity"] == "error"
        and "required" in d["message"]
        for d in diags
    )
    assert _diags_for_file(tmp_path, "ok.md") == []


def test_duplicate_name_across_files_is_error(tmp_path):
    # alphabetical order: a.md registers the name first, b.md is the duplicate.
    _write(tmp_path, "a-first.md", _VALID.format(name="shared-name"))
    _write(tmp_path, "b-second.md", _VALID.format(name="shared-name"))
    _write(tmp_path, "c-unique.md", _VALID.format(name="unique-name"))

    a_diags = _diags_for_file(tmp_path, "a-first.md")
    b_diags = _diags_for_file(tmp_path, "b-second.md")
    c_diags = _diags_for_file(tmp_path, "c-unique.md")

    # The FIRST declarer is clean; the LATER file carries the duplicate error.
    assert a_diags == []
    assert any(
        d["field"] == "name"
        and d["severity"] == "error"
        and "duplicate" in d["message"]
        and "a-first.md" in d["message"]
        for d in b_diags
    )
    # negative lock: a distinct name does NOT trip the duplicate check.
    assert c_diags == []


def test_bad_name_regex_is_error(tmp_path):
    _write(
        tmp_path,
        "badname.md",
        "---\nname: Dev_Backend\ndescription: ok.\n---\nbody\n",
    )
    _write(tmp_path, "ok.md", _VALID.format(name="ok-agent"))

    diags = _diags_for_file(tmp_path, "badname.md")
    assert any(
        d["field"] == "name"
        and d["severity"] == "error"
        and "must match" in d["message"]
        for d in diags
    )
    assert _diags_for_file(tmp_path, "ok.md") == []


def test_bad_model_enum_is_error(tmp_path):
    _write(
        tmp_path,
        "badmodel.md",
        "---\nname: bad-model\ndescription: ok.\nmodel: gpt5\n---\nbody\n",
    )
    _write(tmp_path, "ok.md", _VALID.format(name="ok-agent"))

    diags = _diags_for_file(tmp_path, "badmodel.md")
    assert any(
        d["field"] == "model"
        and d["severity"] == "error"
        and "gpt5" in d["message"]
        for d in diags
    )
    # negative lock: a valid model produces no model diagnostic.
    assert not any(d["field"] == "model" for d in _diags_for_file(tmp_path, "ok.md"))


def test_malformed_yaml_is_error_with_line_number(tmp_path):
    # ``model: [unterminated`` is invalid YAML on body line 3 → source line 4
    # (1 fence + 3 body lines). Assert a usable line number is reported.
    _write(
        tmp_path,
        "broken.md",
        "---\nname: broken\ndescription: ok\nmodel: [unterminated\n---\nbody\n",
    )
    _write(tmp_path, "ok.md", _VALID.format(name="ok-agent"))

    diags = _diags_for_file(tmp_path, "broken.md")
    yaml_diags = [d for d in diags if d["field"] == "yaml"]
    assert yaml_diags, "expected a yaml parse diagnostic"
    d = yaml_diags[0]
    assert d["severity"] == "error"
    assert "malformed YAML" in d["message"]
    # Line number is present and points into the file (not the fallback 1, and
    # within the file's line span).
    assert d["line"] >= 1
    assert d["line"] == 4
    # negative lock: the clean sibling has no yaml diagnostic.
    assert not any(x["field"] == "yaml" for x in _diags_for_file(tmp_path, "ok.md"))


# =============================================================================
# 1. Service — warnings (never errors)
# =============================================================================


def test_unknown_key_is_warning_not_error(tmp_path):
    _write(
        tmp_path,
        "custom.md",
        "---\nname: custom-agent\ndescription: ok.\n"
        "model: sonnet\nemail_actions: enabled\n---\nbody\n",
    )
    diags = _diags_for_file(tmp_path, "custom.md")
    warns = [d for d in diags if d["field"] == "email_actions"]
    assert warns and warns[0]["severity"] == "warning"
    # negative lock: an unknown key must NOT produce any error-severity diag.
    assert all(d["severity"] != "error" for d in diags)


def test_unknown_tool_is_warning_not_error(tmp_path):
    _write(
        tmp_path,
        "weirdtool.md",
        "---\nname: weird-tool\ndescription: ok.\n"
        "tools: [Read, Telekinesis]\n---\nbody\n",
    )
    diags = _diags_for_file(tmp_path, "weirdtool.md")
    tool_warns = [
        d
        for d in diags
        if d["field"].startswith("tools[") and "Telekinesis" in d["message"]
    ]
    assert tool_warns and tool_warns[0]["severity"] == "warning"
    # negative lock: the KNOWN tool (Read) produces no diagnostic at all.
    assert not any(
        "Read" in d["message"] for d in diags if d["field"].startswith("tools[")
    )
    # and no error-severity diagnostics from an unknown tool name.
    assert all(d["severity"] != "error" for d in diags)


def test_all_tools_literal_is_accepted(tmp_path):
    _write(
        tmp_path,
        "alltools.md",
        '---\nname: all-tools\ndescription: ok.\ntools: "All tools"\n---\nbody\n',
    )
    # POSITIVE: zero diagnostics for the literal.
    assert _diags_for_file(tmp_path, "alltools.md") == []
    # NEGATIVE lock: a DIFFERENT bare string IS rejected (so the accept above is
    # specific to the literal, not "any string passes").
    _write(
        tmp_path,
        "badstr.md",
        "---\nname: bad-str\ndescription: ok.\ntools: everything\n---\nbody\n",
    )
    bad = _diags_for_file(tmp_path, "badstr.md")
    assert any(d["field"] == "tools" and d["severity"] == "error" for d in bad)


def test_missing_frontmatter_is_error(tmp_path):
    _write(tmp_path, "noframe.md", "# Just a heading, no frontmatter\n\nbody\n")
    diags = _diags_for_file(tmp_path, "noframe.md")
    assert any(
        d["field"] == "frontmatter"
        and d["severity"] == "error"
        and "missing frontmatter" in d["message"]
        for d in diags
    )


def test_underscore_prefixed_include_is_skipped(tmp_path):
    # _dev-shared.md has no frontmatter; it must be SKIPPED, not errored.
    _write(tmp_path, "_dev-shared.md", "# shared substrate\n\nno frontmatter here\n")
    _write(tmp_path, "real.md", _VALID.format(name="real-agent"))
    result = svc.validate_agents_dir(tmp_path)
    # Only the real agent file is scanned; the include is invisible.
    assert result["files_scanned"] == 1
    assert result["diagnostics"] == []
    assert all(d["file"] != "_dev-shared.md" for d in result["diagnostics"])


# =============================================================================
# 1. Service — aggregate counts + ordering
# =============================================================================


def test_counts_and_ordering(tmp_path):
    _write(tmp_path, "z-ok.md", _VALID.format(name="z-ok"))
    _write(tmp_path, "a-bad.md", "---\ndescription: no name.\n---\nbody\n")
    result = svc.validate_agents_dir(tmp_path)
    assert result["files_scanned"] == 2
    assert result["error_count"] >= 1
    # diagnostics are sorted by (file, line) → a-bad.md before z-ok.md.
    files = [d["file"] for d in result["diagnostics"]]
    assert files == sorted(files)


# =============================================================================
# 2. Endpoint  GET /api/agents/validate
# =============================================================================


@pytest.fixture
def _patch_agents_dir(tmp_path, monkeypatch):
    """Point the endpoint's agents dir at a tmp dir with one valid + one invalid
    file, so the response shape assertions are deterministic and non-vacuous.
    """
    _write(tmp_path, "good.md", _VALID.format(name="good-agent"))
    _write(tmp_path, "bad.md", "---\ndescription: no name.\nmodel: gpt5\n---\nb\n")
    # The router calls default_agents_dir(repo_root); override it to ignore the
    # repo root and return our tmp dir.
    monkeypatch.setattr(
        "src.routers.agent_validation.default_agents_dir",
        lambda _repo_root: tmp_path,
    )
    return tmp_path


async def test_endpoint_response_shape(client, _patch_agents_dir):
    resp = await client.get("/api/agents/validate")
    assert resp.status_code == 200
    body = resp.json()
    # Shape: the four contract keys present and well-typed.
    assert set(body) == {
        "files_scanned",
        "diagnostics",
        "error_count",
        "warning_count",
    }
    assert body["files_scanned"] == 2
    assert body["error_count"] >= 1  # bad.md (missing name + bad model)
    assert isinstance(body["diagnostics"], list)
    sample = body["diagnostics"][0]
    assert set(sample) == {"file", "line", "field", "message", "severity"}
    # file is a basename only — never an absolute path.
    assert "/" not in sample["file"] and "\\" not in sample["file"]
    # bad.md has missing name + bad model → at least one diagnostic for "name".
    assert any(d["field"] == "name" for d in body["diagnostics"])


async def test_endpoint_rejects_post(client):
    # No POST handler → 405 Method Not Allowed (the dropped POST-body variant).
    resp = await client.post("/api/agents/validate", json={"path": "x"})
    assert resp.status_code == 405


# =============================================================================
# 3. CLI  scripts.validate_agents.run  (invoke the function, not a subprocess)
# =============================================================================


def test_cli_exit_code_error(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "bad.md", "---\ndescription: no name.\n---\nbody\n")
    monkeypatch.setattr(
        "scripts.validate_agents.default_agents_dir", lambda _r: tmp_path
    )
    from scripts import validate_agents

    code = validate_agents.run()
    assert code == 1
    out = capsys.readouterr().out
    assert "[error]" in out  # human-friendly line emitted


def test_cli_exit_code_clean(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "good.md", _VALID.format(name="good-agent"))
    monkeypatch.setattr(
        "scripts.validate_agents.default_agents_dir", lambda _r: tmp_path
    )
    from scripts import validate_agents

    code = validate_agents.run()
    assert code == 0


def test_cli_exit_code_zero_on_warnings_only(tmp_path, monkeypatch, capsys):
    # warnings-only (unknown key) must NOT fail the build.
    _write(
        tmp_path,
        "warn.md",
        "---\nname: warn-agent\ndescription: ok.\ncustom_key: x\n---\nbody\n",
    )
    monkeypatch.setattr(
        "scripts.validate_agents.default_agents_dir", lambda _r: tmp_path
    )
    from scripts import validate_agents

    code = validate_agents.run()
    assert code == 0
    out = capsys.readouterr().out
    assert "[warning]" in out
    assert "[error]" not in out


# =============================================================================
# NIT-3: BOM-prefixed file is accepted cleanly
# =============================================================================


def test_bom_prefixed_valid_file_has_zero_diagnostics(tmp_path):
    # utf-8-sig encoding strips the BOM on read; the file must validate clean.
    content = _VALID.format(name="bom-agent")
    p = tmp_path / "bom.md"
    p.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))
    assert _diags_for_file(tmp_path, "bom.md") == []


# =============================================================================
# NIT-2: missing `description` key → error
# =============================================================================


def test_missing_description_is_error(tmp_path):
    _write(
        tmp_path,
        "nodesc.md",
        "---\nname: nodesc-agent\nmodel: sonnet\n---\nbody\n",
    )
    _write(tmp_path, "ok.md", _VALID.format(name="ok-agent"))

    diags = _diags_for_file(tmp_path, "nodesc.md")
    assert any(
        d["field"] == "description"
        and d["severity"] == "error"
        and "required" in d["message"]
        for d in diags
    )
    # negative lock: valid sibling stays clean.
    assert _diags_for_file(tmp_path, "ok.md") == []
