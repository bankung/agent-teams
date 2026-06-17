"""Kanban #1678 — zero-LLM 3-mode BM25 search over a project's shared/ corpus.

Endpoint: GET /api/projects/{project_id}/shared/search?mode=discovery|scroll|browse

Two layers of coverage:

  A. PURE units (no HTTP, no DB) over `services/shared_search.py`:
       * chunking splits decisions.md into per-`##` entries (heading kept with body)
       * BM25 ranks the right decisions.md entry to the TOP for two real queries
         (AC4: "weekly release cadence" -> #1646; "Integrations settings popup" -> #1655)
       * path-traversal inputs are rejected (../../../etc/passwd, ..%2f.., absolute)
       * scroll returns the right line window + prev/next cursors
       * browse returns the heading tree
       * latency: discovery over the REAL agent-teams corpus is well under 1s (AC3)

  B. HTTP contract-smoke (the endpoint is wired): discovery happy path against the
     live `agent-teams` project (id resolved by name), plus the 422/400 guards.

The pure layer reads the in-repo corpus directly via the service (the corpus is a
committed fixture), so the AC4/AC3 assertions don't need the API container.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.services import shared_search as svc
from src.settings import get_settings


# =============================================================================
# Corpus fixture — the real agent-teams shared/ tree (committed, in-repo)
# =============================================================================


def _agent_teams_shared_root() -> Path:
    """Resolve the in-repo agent-teams shared/ corpus root via REPO_ROOT.

    Mirrors the router's null-working_path fallback branch. REPO_ROOT is /repo in
    the container; for a host uvicorn run it points at the repo checkout.
    """
    repo_root = get_settings().repo_root
    return Path(repo_root) / "context" / "projects" / "agent-teams" / "shared"


@pytest.fixture()
def corpus_root() -> Path:
    root = _agent_teams_shared_root()
    if not root.is_dir():
        pytest.skip(f"agent-teams shared corpus not found at {root}")
    return root


@pytest.fixture(autouse=True)
def _clear_corpus_cache():
    """Drop the module cache before each test so cache state never leaks."""
    svc.clear_cache()
    yield
    svc.clear_cache()


# =============================================================================
# A. Pure units — chunking
# =============================================================================


def test_chunk_file_splits_on_headings_keeping_heading_with_body():
    text = (
        "# Title\n"
        "preamble under title\n"
        "## 2026-05-29 — First entry — Kanban #1646\n"
        "body line one\n"
        "body line two\n"
        "## 2026-05-29 — Second entry — Kanban #1655\n"
        "second body\n"
    )
    chunks = svc.chunk_file("decisions.md", text)

    headings = [c.heading for c in chunks]
    assert "2026-05-29 — First entry — Kanban #1646" in headings
    assert "2026-05-29 — Second entry — Kanban #1655" in headings

    # The `##` entry chunk keeps its heading line as the first body line.
    first = next(c for c in chunks if "First entry" in c.heading)
    assert first.text.startswith("## 2026-05-29 — First entry")
    assert "body line one" in first.text
    # …and does NOT bleed into the next entry.
    assert "second body" not in first.text
    # Line numbers are 1-based inclusive.
    assert first.line_start >= 1
    assert first.line_end >= first.line_start


def test_chunk_preamble_emitted_only_when_nonempty():
    # File that opens straight into a heading -> no level-0 preamble chunk.
    chunks = svc.chunk_file("x.md", "## only heading\nbody\n")
    assert all(c.level != 0 for c in chunks)

    # File with leading prose before any heading -> one preamble chunk.
    chunks2 = svc.chunk_file("y.md", "intro prose\nmore prose\n## heading\nbody\n")
    assert chunks2[0].level == 0
    assert "intro prose" in chunks2[0].text


def test_real_decisions_md_chunks_into_per_entry_blocks(corpus_root):
    """The live decisions.md splits into many per-`##` entries."""
    decisions = corpus_root / "decisions.md"
    if not decisions.is_file():
        pytest.skip("decisions.md absent")
    text = decisions.read_text(encoding="utf-8", errors="replace")
    chunks = svc.chunk_file("decisions.md", text)
    level2 = [c for c in chunks if c.level == 2]
    # decisions.md is a long log of `##` dated entries — expect many.
    assert len(level2) >= 10, f"only {len(level2)} level-2 chunks"
    # The two AC4 entries are present as their own chunks.
    assert any("#1646" in c.heading for c in level2), "missing #1646 entry"
    assert any("#1655" in c.heading for c in level2), "missing #1655 entry"


# =============================================================================
# A. Pure units — BM25 ranking (AC4)
# =============================================================================


def test_bm25_ranks_weekly_release_cadence_to_1646(corpus_root):
    """AC4: 'weekly release cadence' surfaces the #1646 decisions.md entry at top."""
    corpus = svc.get_corpus(corpus_root)
    ranked = svc.score_chunks(corpus, "weekly release cadence", limit=5)
    assert ranked, "no results for 'weekly release cadence'"
    top = ranked[0].chunk
    assert top.file == "decisions.md", f"top hit in {top.file}, not decisions.md"
    assert "#1646" in top.heading, f"top heading was {top.heading!r}"


def test_bm25_ranks_integrations_settings_popup_to_1655(corpus_root):
    """AC4: 'Integrations settings popup' surfaces the #1655 decisions.md entry at top."""
    corpus = svc.get_corpus(corpus_root)
    ranked = svc.score_chunks(corpus, "Integrations settings popup", limit=5)
    assert ranked, "no results for 'Integrations settings popup'"
    top = ranked[0].chunk
    assert top.file == "decisions.md", f"top hit in {top.file}, not decisions.md"
    assert "#1655" in top.heading, f"top heading was {top.heading!r}"


def test_bm25_empty_query_returns_no_results(corpus_root):
    corpus = svc.get_corpus(corpus_root)
    assert svc.score_chunks(corpus, "", limit=5) == []
    assert svc.score_chunks(corpus, "   ", limit=5) == []


def test_bm25_synthetic_ranking_is_deterministic():
    """A controlled 3-chunk corpus: the chunk with the query term ranks first;
    ties break on (file, line_start)."""
    text = (
        "## alpha entry\n"
        "the quick brown fox jumps\n"
        "## beta entry\n"
        "unrelated content about cats\n"
        "## gamma entry\n"
        "the quick brown fox again here\n"
    )
    corpus = svc.build_corpus_from_text("synthetic.md", text)
    ranked = svc.score_chunks(corpus, "quick brown fox", limit=10)
    # Only the two fox chunks score > 0.
    assert len(ranked) == 2
    headings = [r.chunk.heading for r in ranked]
    assert "alpha entry" in headings and "gamma entry" in headings
    # Deterministic order on score tie -> earlier line_start first.
    assert ranked[0].chunk.line_start < ranked[1].chunk.line_start


# =============================================================================
# A. Pure units — path-traversal guard (SECURITY)
# =============================================================================


@pytest.mark.parametrize(
    "bad",
    [
        "../../../etc/passwd",
        "../secret.md",
        "subdir/../../escape.md",
        "/etc/passwd",
        "/absolute/path.md",
        "..\\..\\windows.md",        # backslash traversal
        "decisions/../../../x.md",
    ],
)
def test_path_guard_rejects_traversal(corpus_root, bad):
    with pytest.raises(ValueError):
        svc.safe_resolve_in_root(corpus_root, bad)


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_path_guard_rejects_empty(corpus_root, empty):
    with pytest.raises(ValueError):
        svc.safe_resolve_in_root(corpus_root, empty)


def test_path_guard_accepts_in_root_file(corpus_root):
    # decisions.md is at the corpus root.
    resolved = svc.safe_resolve_in_root(corpus_root, "decisions.md")
    assert resolved.name == "decisions.md"
    # And a nested file (incidents/<file>) resolves too.
    nested = svc.safe_resolve_in_root(corpus_root, "stories/_template.md")
    assert nested.as_posix().endswith("stories/_template.md")


def test_load_corpus_skips_symlink_escaping_root(tmp_path):
    """A symlinked .md pointing OUTSIDE the root is not ingested."""
    root = tmp_path / "shared"
    root.mkdir()
    (root / "real.md").write_text("# real\nbody\n", encoding="utf-8")

    outside = tmp_path / "outside.md"
    outside.write_text("# secret\nshould not be read\n", encoding="utf-8")
    link = root / "link.md"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform/user")

    files = svc.load_corpus(root)
    rels = {f.rel_path for f in files}
    assert "real.md" in rels
    assert "link.md" not in rels  # escaping symlink dropped


# =============================================================================
# A. Pure units — scroll mode
# =============================================================================


def test_run_scroll_returns_window_and_cursors(tmp_path):
    root = tmp_path / "shared"
    root.mkdir()
    body = "\n".join(f"line {i}" for i in range(1, 101))  # 100 lines
    (root / "doc.md").write_text(body, encoding="utf-8")

    out = svc.run_scroll(root, "doc.md", line=10, window=5)
    assert out["mode"] == "scroll"
    assert out["file"] == "doc.md"
    assert out["line_start"] == 10
    assert out["line_end"] == 14
    assert out["lines"] == ["line 10", "line 11", "line 12", "line 13", "line 14"]
    assert out["prev_line"] == 5          # 10 - window
    assert out["next_line"] == 15
    assert out["eof"] is False


def test_run_scroll_eof_and_top_cursors(tmp_path):
    root = tmp_path / "shared"
    root.mkdir()
    (root / "doc.md").write_text("a\nb\nc\n", encoding="utf-8")  # splitlines -> 3 lines (no trailing '')

    # From the top.
    top = svc.run_scroll(root, "doc.md", line=1, window=2)
    assert top["prev_line"] is None
    assert top["line_start"] == 1

    # A window reaching the end sets eof + next_line None.
    end = svc.run_scroll(root, "doc.md", line=1, window=1000)
    assert end["eof"] is True
    assert end["next_line"] is None


def test_run_scroll_missing_file_raises(tmp_path):
    root = tmp_path / "shared"
    root.mkdir()
    with pytest.raises(FileNotFoundError):
        svc.run_scroll(root, "nope.md", line=1, window=10)


def test_run_scroll_traversal_raises(tmp_path):
    root = tmp_path / "shared"
    root.mkdir()
    (tmp_path / "secret.md").write_text("secret", encoding="utf-8")
    with pytest.raises(ValueError):
        svc.run_scroll(root, "../secret.md", line=1, window=10)


# =============================================================================
# A. Pure units — browse mode
# =============================================================================


def test_run_browse_whole_corpus_lists_files_and_headings(tmp_path):
    root = tmp_path / "shared"
    (root / "sub").mkdir(parents=True)
    (root / "a.md").write_text("# A title\ntext\n## A sub\nmore\n", encoding="utf-8")
    (root / "sub" / "b.md").write_text("# B title\n", encoding="utf-8")

    out = svc.run_browse(root)
    assert out["mode"] == "browse"
    paths = {f["path"] for f in out["files"]}
    assert paths == {"a.md", "sub/b.md"}
    a = next(f for f in out["files"] if f["path"] == "a.md")
    levels = [(h["level"], h["text"]) for h in a["headings"]]
    assert (1, "A title") in levels and (2, "A sub") in levels
    assert out["totals"]["files"] == 2


def test_run_browse_single_file_scopes_tree(tmp_path):
    root = tmp_path / "shared"
    root.mkdir()
    (root / "a.md").write_text("# only A\n## A two\n", encoding="utf-8")
    (root / "b.md").write_text("# only B\n", encoding="utf-8")

    out = svc.run_browse(root, "a.md")
    assert len(out["files"]) == 1
    assert out["files"][0]["path"] == "a.md"
    assert out["totals"]["files"] == 1


def test_run_browse_traversal_raises(tmp_path):
    root = tmp_path / "shared"
    root.mkdir()
    with pytest.raises(ValueError):
        svc.run_browse(root, "../../etc/passwd")


# =============================================================================
# A. Pure units — discovery wrapper + snippet
# =============================================================================


def test_run_discovery_shape_and_snippet(corpus_root):
    out = svc.run_discovery(corpus_root, "weekly release cadence", limit=3)
    assert out["mode"] == "discovery"
    assert out["query"] == "weekly release cadence"
    assert 1 <= len(out["results"]) <= 3
    top = out["results"][0]
    assert set(top.keys()) == {"file", "heading", "line_start", "line_end", "snippet", "score"}
    assert top["score"] > 0.0
    assert len(top["snippet"]) > 0
    assert out["corpus"]["files"] >= 1 and out["corpus"]["chunks"] >= 1


# =============================================================================
# A. Pure units — latency (AC3)
# =============================================================================


def test_discovery_latency_well_under_one_second(corpus_root):
    """AC3: discovery over the real corpus completes in well under 1s.

    Measures a COLD run (cache cleared by the autouse fixture) — i.e. corpus
    build + score, the worst case — then a warm run. Both must be < 1000 ms;
    we assert a generous 800 ms ceiling to leave CI headroom while still proving
    the sub-second goal.
    """
    svc.clear_cache()
    t0 = time.perf_counter()
    out_cold = svc.run_discovery(corpus_root, "weekly release cadence", limit=10)
    cold_ms = (time.perf_counter() - t0) * 1000.0

    t1 = time.perf_counter()
    svc.run_discovery(corpus_root, "Integrations settings popup", limit=10)
    warm_ms = (time.perf_counter() - t1) * 1000.0

    assert cold_ms < 800.0, f"cold discovery took {cold_ms:.1f} ms"
    assert warm_ms < 800.0, f"warm discovery took {warm_ms:.1f} ms"
    # The wrapper's self-reported elapsed_ms is also sane.
    assert out_cold["elapsed_ms"] < 800.0


# =============================================================================
# A. Pure units — corpus-root resolution (project scoping, AC2)
# =============================================================================


def test_resolve_corpus_root_uses_working_path_when_set():
    from src.routers.shared_search import resolve_corpus_root

    root = resolve_corpus_root("/srv/projects/foo", "foo", Path("/repo"))
    assert root == Path("/srv/projects/foo/shared")


def test_resolve_corpus_root_fallback_when_working_path_null():
    from src.routers.shared_search import resolve_corpus_root

    root = resolve_corpus_root(None, "agent-teams", Path("/repo"))
    assert root == Path("/repo/context/projects/agent-teams/shared")
    # Project scoping (AC2): the root is derived from the project NAME, so a
    # different project name can never resolve into agent-teams' tree.
    other = resolve_corpus_root(None, "secretary", Path("/repo"))
    assert other == Path("/repo/context/projects/secretary/shared")
    assert other != root


# =============================================================================
# B. HTTP contract-smoke — the endpoint is wired
# =============================================================================


async def _agent_teams_project_id(client) -> int:
    resp = await client.get("/api/projects/by-name/agent-teams")
    if resp.status_code != 200:
        pytest.skip("agent-teams project not seeded in test DB")
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_http_discovery_happy_path(client):
    """discovery over the live agent-teams corpus returns ranked results."""
    pid = await _agent_teams_project_id(client)
    # X-Project-Id header required: router enforces project_id == session_project_id.
    resp = await client.get(
        f"/api/projects/{pid}/shared/search",
        params={"mode": "discovery", "q": "weekly release cadence", "limit": 5},
        headers={"X-Project-Id": str(pid)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "discovery"
    assert body["results"], "expected at least one result"
    assert body["results"][0]["file"] == "decisions.md"
    assert "#1646" in body["results"][0]["heading"]


@pytest.mark.asyncio
async def test_http_discovery_missing_q_is_422(client):
    pid = await _agent_teams_project_id(client)
    # X-Project-Id header required: router enforces project_id == session_project_id.
    resp = await client.get(
        f"/api/projects/{pid}/shared/search",
        params={"mode": "discovery"},
        headers={"X-Project-Id": str(pid)},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_http_unknown_mode_is_422(client):
    pid = await _agent_teams_project_id(client)
    # X-Project-Id header required: router enforces project_id == session_project_id.
    resp = await client.get(
        f"/api/projects/{pid}/shared/search",
        params={"mode": "bogus", "q": "x"},
        headers={"X-Project-Id": str(pid)},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_http_scroll_traversal_is_400(client):
    pid = await _agent_teams_project_id(client)
    resp = await client.get(
        f"/api/projects/{pid}/shared/search",
        params={"mode": "scroll", "file": "../../../etc/passwd"},
    )
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_http_unknown_project_is_404(client):
    # X-Project-Id must match the path project_id so the session-binding check
    # passes before the router does the DB lookup (which then returns 404).
    resp = await client.get(
        "/api/projects/99999999/shared/search",
        params={"mode": "discovery", "q": "x"},
        headers={"X-Project-Id": "99999999"},
    )
    assert resp.status_code == 404, resp.text
