"""Tests for task outputs — resolver service + the two GET endpoints
(Kanban #1305).

Coverage matrix:

1. Resolver service (`services.task_outputs`) — unit, tmp_path-based
   - kind mapping by extension (every contract branch + unknown → text).
   - is_safe_filename rejects /, \\, .., null byte, empty.
   - working_path (general)      → <wp>/outputs/<id>/
   - working_path (data-analytics) → <wp>/analysis/outputs/<id>/
   - working_path null           → role-folder scan: task-<id>-* glob + <id>/ subdir
   - dot-files skipped; >50 MB skipped; recursion below the stated level skipped.
   - Windows-absolute working_path on Linux falls back to the null-branch scan.

2. GET /api/tasks/{id}/outputs endpoint
   - 400 missing X-Project-Id / 404 unknown task / 410 soft-deleted / 400 cross-project.
   - 200 + listing on the happy path (working_path project).
   - empty folder → 200 + [].

3. GET /api/tasks/{id}/outputs/{filename} endpoint
   - inline by default (Content-Disposition: inline) + nosniff present.
   - ?download=1 → Content-Disposition: attachment.
   - traversal filenames (.., %2f-decoded /, backslash, absolute, null byte) → 404.
   - symlink that escapes the outputs root is NOT served (containment) → 404.
   - same gate chain (400/404/410) before filename handling.

All filesystem fixtures use tmp_path (the project's working_path is pointed at a
tmp dir) — tests NEVER write into real role-state folders. The null-branch role
scan is exercised against a fabricated Project + tmp repo_root at the service
layer, not the live filesystem.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.services import task_outputs as svc


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(
    name: str, *, team: str = "dev", working_path: str | None = None
) -> dict:
    payload = {
        "name": name,
        "description": f"test fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": team,
    }
    if working_path is not None:
        payload["working_path"] = working_path
    return payload


# =============================================================================
# 1. Resolver service — kind mapping + safe filename (pure functions)
# =============================================================================


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("chart.png", "chart"),
        ("diagram.svg", "chart"),
        ("report.html", "chart"),
        ("notes.md", "doc"),
        ("data.csv", "export"),
        ("payload.json", "export"),
        ("run.txt", "text"),
        ("debug.log", "text"),
        ("archive.zip", "text"),  # unknown ext → text
        ("noext", "text"),
        ("UPPER.PNG", "chart"),  # case-insensitive
    ],
)
def test_kind_for_filename(filename: str, expected: str) -> None:
    assert svc.kind_for_filename(filename) == expected


@pytest.mark.parametrize(
    ("filename", "safe"),
    [
        ("ok.png", True),
        ("a-b_c.123.csv", True),
        ("../etc/passwd", False),
        ("a/b", False),
        ("a\\b", False),
        ("..", False),
        (".", False),
        ("", False),
        ("x\x00y", False),
        ("foo..bar", False),  # contains the traversal token
    ],
)
def test_is_safe_filename(filename: str, safe: bool) -> None:
    assert svc.is_safe_filename(filename) is safe


# =============================================================================
# 1b. Resolver service — folder resolution (tmp_path)
# =============================================================================


def _fake_project(name: str, *, team: str, working_path: str | None):
    """A duck-typed Project for the pure resolver (only reads name/team/working_path)."""
    return SimpleNamespace(name=name, team=team, working_path=working_path)


def test_resolver_working_path_general(tmp_path: Path) -> None:
    """team != data-analytics → <wp>/outputs/<id>/, direct files only."""
    task_id = 4242
    outdir = tmp_path / "outputs" / str(task_id)
    outdir.mkdir(parents=True)
    (outdir / "chart.png").write_bytes(b"\x89PNG fake")
    (outdir / "summary.md").write_text("# hi")
    (outdir / ".hidden").write_text("nope")  # dot-file skipped
    # recursion guard: a nested file must NOT appear.
    nested = outdir / "sub"
    nested.mkdir()
    (nested / "deep.txt").write_text("deep")

    proj = _fake_project("p", team="dev", working_path=str(tmp_path))
    listing = svc.list_task_outputs(proj, task_id, repo_root=Path("/repo"))
    names = [e["filename"] for e in listing]
    assert names == ["chart.png", "summary.md"]  # sorted, dot+nested excluded
    by_name = {e["filename"]: e for e in listing}
    assert by_name["chart.png"]["kind"] == "chart"
    assert by_name["summary.md"]["kind"] == "doc"
    assert by_name["chart.png"]["size"] == len(b"\x89PNG fake")


def test_resolver_working_path_data_analytics(tmp_path: Path) -> None:
    """team == data-analytics → <wp>/analysis/outputs/<id>/."""
    task_id = 77
    outdir = tmp_path / "analysis" / "outputs" / str(task_id)
    outdir.mkdir(parents=True)
    (outdir / "result.csv").write_text("a,b\n1,2\n")
    # The general-convention dir must be IGNORED for a data-analytics project.
    wrong = tmp_path / "outputs" / str(task_id)
    wrong.mkdir(parents=True)
    (wrong / "ignored.txt").write_text("should not appear")

    proj = _fake_project("p", team="data-analytics", working_path=str(tmp_path))
    listing = svc.list_task_outputs(proj, task_id, repo_root=Path("/repo"))
    assert [e["filename"] for e in listing] == ["result.csv"]


def test_resolver_empty_folder_returns_empty(tmp_path: Path) -> None:
    """No output folder at all → [] (not an error)."""
    proj = _fake_project("p", team="dev", working_path=str(tmp_path))
    assert svc.list_task_outputs(proj, 999, repo_root=Path("/repo")) == []


def test_resolver_skips_oversize_file(tmp_path: Path, monkeypatch) -> None:
    """Files > MAX_FILE_BYTES are skipped from the listing."""
    monkeypatch.setattr(svc, "MAX_FILE_BYTES", 10)
    task_id = 5
    outdir = tmp_path / "outputs" / str(task_id)
    outdir.mkdir(parents=True)
    (outdir / "small.txt").write_text("123")  # 3 bytes — kept
    (outdir / "big.txt").write_text("X" * 50)  # 50 bytes — skipped

    proj = _fake_project("p", team="dev", working_path=str(tmp_path))
    listing = svc.list_task_outputs(proj, task_id, repo_root=Path("/repo"))
    assert [e["filename"] for e in listing] == ["small.txt"]


def test_resolver_null_working_path_role_folder_scan(tmp_path: Path) -> None:
    """working_path null → scan role folders for task-<id>-* + <id>/ subdir."""
    task_id = 1305
    name = "proj-null"
    proj_dir = tmp_path / "context" / "projects" / name
    role_a = proj_dir / "dev-backend"
    role_b = proj_dir / "dev-frontend"
    role_a.mkdir(parents=True)
    role_b.mkdir(parents=True)
    # (a) glob match task-<id>-* directly in a role folder.
    (role_a / f"task-{task_id}-design.md").write_text("# design")
    # (b) <id>/ subdir with direct files in another role folder.
    sub = role_b / str(task_id)
    sub.mkdir()
    (sub / "screenshot.png").write_bytes(b"img")
    # noise that must NOT match: wrong task id, non-prefixed file, dot-file.
    (role_a / "task-9999-other.md").write_text("nope")
    (role_a / "random.txt").write_text("nope")
    (role_a / ".hidden").write_text("nope")

    proj = _fake_project(name, team="dev", working_path=None)
    listing = svc.list_task_outputs(proj, task_id, repo_root=tmp_path)
    names = sorted(e["filename"] for e in listing)
    assert names == ["screenshot.png", f"task-{task_id}-design.md"]


def test_resolver_windows_working_path_falls_back(tmp_path: Path) -> None:
    """A Windows-absolute working_path on Linux is not usable → null-branch scan.

    On the Linux container `Path('C:\\\\...').is_absolute()` is False, so the
    resolver must fall back to the repo_root role-folder scan rather than
    resolving the bogus path CWD-relative.
    """
    if os.name == "nt":  # pragma: no cover - container is Linux
        pytest.skip("Windows-path-on-Linux guard is only meaningful on POSIX")
    task_id = 314
    name = "proj-winfallback"
    role = tmp_path / "context" / "projects" / name / "dev-backend"
    role.mkdir(parents=True)
    (role / f"task-{task_id}-note.md").write_text("# note")

    proj = _fake_project(name, team="dev", working_path="C:\\Users\\bob\\proj")
    listing = svc.list_task_outputs(proj, task_id, repo_root=tmp_path)
    assert [e["filename"] for e in listing] == [f"task-{task_id}-note.md"]


def test_resolver_symlink_escape_not_listed(tmp_path: Path) -> None:
    """A symlink inside the outputs dir pointing OUTSIDE the root is excluded."""
    task_id = 8
    outdir = tmp_path / "outputs" / str(task_id)
    outdir.mkdir(parents=True)
    (outdir / "legit.txt").write_text("ok")
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    link = outdir / "escape.txt"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):  # pragma: no cover
        pytest.skip("symlinks not supported in this environment")

    proj = _fake_project("p", team="dev", working_path=str(tmp_path))
    listing = svc.list_task_outputs(proj, task_id, repo_root=Path("/repo"))
    # The escaping symlink must NOT appear; the legit file must.
    assert [e["filename"] for e in listing] == ["legit.txt"]
    # And it must NOT be resolvable for serving either.
    assert svc.resolve_output_file(proj, task_id, "escape.txt", Path("/repo")) is None


# =============================================================================
# 2. GET /api/tasks/{id}/outputs endpoint — gates + happy path
# =============================================================================


@pytest.mark.asyncio
async def test_list_outputs_400_when_header_missing(client) -> None:
    resp = await client.get("/api/tasks/1/outputs")
    assert resp.status_code == 400
    assert resp.json() == {
        "detail": "X-Project-Id header is required for task endpoints"
    }


@pytest.mark.asyncio
async def test_list_outputs_404_on_unknown_task(client) -> None:
    resp = await client.get(
        "/api/tasks/999999999/outputs", headers={"X-Project-Id": "1"}
    )
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Task id=999999999 not found"}


@pytest.mark.asyncio
async def test_list_outputs_410_on_soft_deleted_task(client) -> None:
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k1305-410"},
        headers=headers,
    )
    task_id = create.json()["id"]
    delete = await client.delete(f"/api/tasks/{task_id}", headers=headers)
    assert delete.status_code == 204

    resp = await client.get(f"/api/tasks/{task_id}/outputs", headers=headers)
    assert resp.status_code == 410, resp.text
    assert resp.json()["detail"].startswith(f"Task id={task_id} is deleted")


@pytest.mark.asyncio
async def test_list_outputs_400_on_cross_project_header(
    client, scaffold_cleanup
) -> None:
    active = await client.get("/api/projects/by-name/agent-teams")
    project_a_id = active.json()["id"]
    name_b = scaffold_cleanup(_unique_name("k1305-crossproj"))
    proj_b = await client.post("/api/projects", json=_project_create_payload(name_b))
    project_b_id = proj_b.json()["id"]
    headers_a = {"X-Project-Id": str(project_a_id)}
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_a_id, "title": "k1305-crossproj-task"},
        headers=headers_a,
    )
    task_id = create.json()["id"]
    try:
        resp = await client.get(
            f"/api/tasks/{task_id}/outputs",
            headers={"X-Project-Id": str(project_b_id)},
        )
        assert resp.status_code == 400, resp.text
        assert "does not belong to" in resp.json()["detail"]
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers_a)


@pytest.mark.asyncio
async def test_list_outputs_empty_returns_empty_list(
    client, scaffold_cleanup, tmp_path
) -> None:
    """A project whose working_path has no outputs folder → 200 + []."""
    name = scaffold_cleanup(_unique_name("k1305-empty"))
    proj = await client.post(
        "/api/projects",
        json=_project_create_payload(name, working_path=str(tmp_path)),
    )
    project_id = proj.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k1305-empty-task"},
        headers=headers,
    )
    task_id = create.json()["id"]
    try:
        resp = await client.get(f"/api/tasks/{task_id}/outputs", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json() == []
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
async def test_list_outputs_happy_path(client, scaffold_cleanup, tmp_path) -> None:
    """working_path project with sample files → 200 + sorted listing."""
    name = scaffold_cleanup(_unique_name("k1305-happy"))
    proj = await client.post(
        "/api/projects",
        json=_project_create_payload(name, working_path=str(tmp_path)),
    )
    project_id = proj.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k1305-happy-task"},
        headers=headers,
    )
    task_id = create.json()["id"]
    outdir = tmp_path / "outputs" / str(task_id)
    outdir.mkdir(parents=True)
    (outdir / "chart.png").write_bytes(b"\x89PNG payload")
    (outdir / "data.csv").write_text("a,b\n1,2\n")
    try:
        resp = await client.get(f"/api/tasks/{task_id}/outputs", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert [e["filename"] for e in body] == ["chart.png", "data.csv"]
        by_name = {e["filename"]: e for e in body}
        assert by_name["chart.png"]["kind"] == "chart"
        assert by_name["data.csv"]["kind"] == "export"
        for e in body:
            assert set(e.keys()) == {"filename", "mime", "size", "kind"}
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


# =============================================================================
# 3. GET /api/tasks/{id}/outputs/{filename} endpoint — serving + security
# =============================================================================


@pytest.mark.asyncio
async def test_get_output_file_inline_default(
    client, scaffold_cleanup, tmp_path
) -> None:
    """Default serve of a passive type = inline + nosniff present + correct bytes.

    NOTE: active-content suffixes (.html, .htm, .svg, .xml) are forced to
    attachment regardless of ?download — see test_active_content_forced_attachment.
    This test uses .txt to verify the inline path is still wired for passive types.
    """
    name = scaffold_cleanup(_unique_name("k1305-inline"))
    proj = await client.post(
        "/api/projects",
        json=_project_create_payload(name, working_path=str(tmp_path)),
    )
    project_id = proj.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k1305-inline-task"},
        headers=headers,
    )
    task_id = create.json()["id"]
    outdir = tmp_path / "outputs" / str(task_id)
    outdir.mkdir(parents=True)
    (outdir / "report.txt").write_text("hello plain")
    try:
        resp = await client.get(
            f"/api/tasks/{task_id}/outputs/report.txt", headers=headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-disposition"].startswith("inline")
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.text == "hello plain"
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
async def test_get_output_file_download_attachment(
    client, scaffold_cleanup, tmp_path
) -> None:
    """?download=1 → Content-Disposition: attachment; filename="...".

    Pairs the POSITIVE (download=1 ⇒ attachment) with the NEGATIVE lock
    (no flag ⇒ inline, NOT attachment) so the flag is proven load-bearing.
    """
    name = scaffold_cleanup(_unique_name("k1305-dl"))
    proj = await client.post(
        "/api/projects",
        json=_project_create_payload(name, working_path=str(tmp_path)),
    )
    project_id = proj.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k1305-dl-task"},
        headers=headers,
    )
    task_id = create.json()["id"]
    outdir = tmp_path / "outputs" / str(task_id)
    outdir.mkdir(parents=True)
    (outdir / "data.csv").write_text("a,b\n1,2\n")
    try:
        dl = await client.get(
            f"/api/tasks/{task_id}/outputs/data.csv?download=1", headers=headers
        )
        assert dl.status_code == 200, dl.text
        cd = dl.headers["content-disposition"]
        assert cd.startswith("attachment")
        assert 'filename="data.csv"' in cd
        assert dl.headers["x-content-type-options"] == "nosniff"
        # NEGATIVE lock: without the flag it is inline, not attachment.
        inline = await client.get(
            f"/api/tasks/{task_id}/outputs/data.csv", headers=headers
        )
        assert inline.headers["content-disposition"].startswith("inline")
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "..%2f..%2fmain.py",  # url-encoded ../../ — decodes to a path with /
        "..\\..\\secret",     # backslash traversal
        "....//etc",          # doubled traversal
    ],
)
async def test_get_output_file_traversal_rejected(
    client, scaffold_cleanup, tmp_path, bad
) -> None:
    """Traversal filenames are rejected with 404 and do NOT leak files."""
    name = scaffold_cleanup(_unique_name("k1305-trav"))
    proj = await client.post(
        "/api/projects",
        json=_project_create_payload(name, working_path=str(tmp_path)),
    )
    project_id = proj.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k1305-trav-task"},
        headers=headers,
    )
    task_id = create.json()["id"]
    # A real file exists in the outputs dir; traversal must still 404.
    outdir = tmp_path / "outputs" / str(task_id)
    outdir.mkdir(parents=True)
    (outdir / "legit.txt").write_text("ok")
    try:
        resp = await client.get(
            f"/api/tasks/{task_id}/outputs/{bad}", headers=headers
        )
        assert resp.status_code == 404, resp.text
        # Containment: the legit file IS still reachable (proves 404 is the
        # rejection, not a broken route).
        ok = await client.get(
            f"/api/tasks/{task_id}/outputs/legit.txt", headers=headers
        )
        assert ok.status_code == 200, ok.text
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
async def test_get_output_file_not_in_listing_404(
    client, scaffold_cleanup, tmp_path
) -> None:
    """A clean filename that is not in the listing → 404."""
    name = scaffold_cleanup(_unique_name("k1305-missing"))
    proj = await client.post(
        "/api/projects",
        json=_project_create_payload(name, working_path=str(tmp_path)),
    )
    project_id = proj.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k1305-missing-task"},
        headers=headers,
    )
    task_id = create.json()["id"]
    outdir = tmp_path / "outputs" / str(task_id)
    outdir.mkdir(parents=True)
    (outdir / "present.txt").write_text("ok")
    try:
        resp = await client.get(
            f"/api/tasks/{task_id}/outputs/absent.txt", headers=headers
        )
        assert resp.status_code == 404, resp.text
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
async def test_get_output_file_400_when_header_missing(client) -> None:
    """The gate chain runs before filename handling — missing header → 400."""
    resp = await client.get("/api/tasks/1/outputs/anything.txt")
    assert resp.status_code == 400
    assert resp.json() == {
        "detail": "X-Project-Id header is required for task endpoints"
    }


# =============================================================================
# 4. is_safe_filename — header injection (WARN-1/BLOCKER-1 additions, #1305)
# =============================================================================


@pytest.mark.parametrize(
    "filename",
    [
        'a"b.csv',      # double-quote breaks Content-Disposition quoted-string
        "a\rb.txt",     # CR — HTTP header injection
        "a\nb.txt",     # LF — HTTP header injection
    ],
)
def test_is_safe_filename_rejects_injection_chars(filename: str) -> None:
    assert svc.is_safe_filename(filename) is False


# =============================================================================
# 5. _scan_dir_direct_files skips on-disk evil names (WARN-1/BLOCKER-1, #1305)
# =============================================================================


def test_scan_dir_skips_unsafe_on_disk_name(tmp_path: Path) -> None:
    """A file on disk with a double-quote in its name is skipped from the listing.

    POSIX allows double-quote in filenames; on Linux we can create one.
    The scan must skip it so it never flows into the listing or FE markup.
    """
    task_id = 7777
    outdir = tmp_path / "outputs" / str(task_id)
    outdir.mkdir(parents=True)
    (outdir / "safe.txt").write_text("ok")
    evil_path = outdir / 'evil"name.txt'
    try:
        evil_path.write_text("injection")
    except (OSError, ValueError):
        pytest.skip("platform does not allow double-quote in filenames")

    proj = _fake_project("p", team="dev", working_path=str(tmp_path))
    listing = svc.list_task_outputs(proj, task_id, repo_root=Path("/repo"))
    names = [e["filename"] for e in listing]
    assert 'evil"name.txt' not in names
    assert "safe.txt" in names


# =============================================================================
# 6. FORCE_ATTACHMENT for active content (WARN-2, #1305)
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fname", "content", "expect_attachment"),
    [
        ("page.html", b"<h1>hi</h1>", True),   # active — forced attachment
        ("notes.txt",  b"plain text",  False),  # passive — remains inline
    ],
)
async def test_active_content_forced_attachment(
    client, scaffold_cleanup, tmp_path, fname, content, expect_attachment
) -> None:
    """.html (and other active suffixes) → attachment even without ?download."""
    name = scaffold_cleanup(_unique_name("k1305-force-att"))
    proj = await client.post(
        "/api/projects",
        json=_project_create_payload(name, working_path=str(tmp_path)),
    )
    project_id = proj.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k1305-force-att-task"},
        headers=headers,
    )
    task_id = create.json()["id"]
    outdir = tmp_path / "outputs" / str(task_id)
    outdir.mkdir(parents=True)
    (outdir / fname).write_bytes(content)
    try:
        resp = await client.get(
            f"/api/tasks/{task_id}/outputs/{fname}", headers=headers
        )
        assert resp.status_code == 200, resp.text
        cd = resp.headers["content-disposition"]
        if expect_attachment:
            assert cd.startswith("attachment"), f"expected attachment, got: {cd}"
        else:
            assert cd.startswith("inline"), f"expected inline, got: {cd}"
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


# =============================================================================
# 7. Listing cap at MAX_OUTPUT_FILES=50 (NIT-1, #1305)
# =============================================================================


def test_list_task_outputs_capped_at_50(tmp_path: Path, monkeypatch) -> None:
    """A directory with 55 files → only 50 returned (first 50 sorted names)."""
    task_id = 6060
    outdir = tmp_path / "outputs" / str(task_id)
    outdir.mkdir(parents=True)
    # Create 55 uniquely named files (zero-padded so sort order is deterministic).
    for i in range(55):
        (outdir / f"file{i:03d}.txt").write_text(f"content {i}")

    proj = _fake_project("p", team="dev", working_path=str(tmp_path))
    listing = svc.list_task_outputs(proj, task_id, repo_root=Path("/repo"))
    assert len(listing) == svc.MAX_OUTPUT_FILES
    # The returned names must be the first 50 in sorted order.
    all_names = sorted(f"file{i:03d}.txt" for i in range(55))
    assert [e["filename"] for e in listing] == all_names[:50]


# =============================================================================
# 8. Content-Disposition param injection — Security-W1/N1 (#2350)
# =============================================================================


@pytest.mark.parametrize(
    "filename",
    [
        "report.csv; filename=pwned.exe",  # semicolon — CD param injector
        "report.csv;filename=pwned.exe",   # semicolon without space
        "it's-a-trap.csv",                 # single-quote — CD param injector
        "file\x0cname.txt",                # form-feed — control char
        "file\x0bname.txt",                # vertical-tab — control char
        "file\x01name.txt",                # SOH — generic control char
    ],
)
def test_is_safe_filename_rejects_cd_param_injection(filename: str) -> None:
    """Semicolons, single-quotes, and control chars are rejected by is_safe_filename."""
    assert svc.is_safe_filename(filename) is False


def test_cd_param_injection_file_excluded_from_listing(tmp_path: Path) -> None:
    """An on-disk file named with a semicolon is excluded from the listing.

    LISTING path: the injected filename must NOT appear in list_task_outputs.
    A clean file in the same dir MUST still appear (proves the skip is selective).
    """
    task_id = 2350
    outdir = tmp_path / "outputs" / str(task_id)
    outdir.mkdir(parents=True)
    (outdir / "clean.csv").write_text("a,b\n1,2\n")
    evil_path = outdir / "report.csv; filename=pwned.exe"
    try:
        evil_path.write_text("injected")
    except (OSError, ValueError):
        pytest.skip("platform does not allow semicolon in filenames")

    proj = _fake_project("p", team="dev", working_path=str(tmp_path))
    listing = svc.list_task_outputs(proj, task_id, repo_root=Path("/repo"))
    names = [e["filename"] for e in listing]
    # NEGATIVE: injected name must not appear
    assert "report.csv; filename=pwned.exe" not in names
    # POSITIVE: clean file still listed
    assert "clean.csv" in names


@pytest.mark.asyncio
async def test_cd_param_injection_serve_path_404(
    client, scaffold_cleanup, tmp_path
) -> None:
    """SERVE path: requesting a filename with ';' returns 404 (never 200).

    Proves the injection name is blocked at the router gate (is_safe_filename),
    not just at the listing level — defense-in-depth.
    """
    name = scaffold_cleanup(_unique_name("k2350-cd-inject"))
    proj = await client.post(
        "/api/projects",
        json=_project_create_payload(name, working_path=str(tmp_path)),
    )
    project_id = proj.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k2350-cd-inject-task"},
        headers=headers,
    )
    task_id = create.json()["id"]
    outdir = tmp_path / "outputs" / str(task_id)
    outdir.mkdir(parents=True)
    (outdir / "legit.txt").write_text("ok")
    try:
        # Semicolon in the URL path segment → 404 (router gate rejects before serve)
        resp = await client.get(
            f"/api/tasks/{task_id}/outputs/report.csv; filename=pwned.exe",
            headers=headers,
        )
        assert resp.status_code == 404, resp.text
        # NEGATIVE lock: a legit name is still serveable (route is not broken)
        ok = await client.get(
            f"/api/tasks/{task_id}/outputs/legit.txt", headers=headers
        )
        assert ok.status_code == 200, ok.text
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
async def test_control_char_filename_serve_path_404(
    client, scaffold_cleanup, tmp_path
) -> None:
    """SERVE path: a percent-encoded control character in the filename → 404.

    A literal \\x0c cannot be placed in an HTTP URL (httpx rejects it before
    send), so we use the percent-encoded form %0C.  FastAPI decodes the path
    param → the decoded string contains \\x0c → is_safe_filename returns False
    → 404. This confirms the control-char guard is wired end-to-end in the
    serving route.
    """
    name = scaffold_cleanup(_unique_name("k2350-ctrl-char"))
    proj = await client.post(
        "/api/projects",
        json=_project_create_payload(name, working_path=str(tmp_path)),
    )
    project_id = proj.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k2350-ctrl-char-task"},
        headers=headers,
    )
    task_id = create.json()["id"]
    outdir = tmp_path / "outputs" / str(task_id)
    outdir.mkdir(parents=True)
    (outdir / "clean.txt").write_text("ok")
    try:
        # %0C = form-feed (0x0C) — a control character; router gate must 404.
        resp = await client.get(
            f"/api/tasks/{task_id}/outputs/file%0Cname.txt",
            headers=headers,
        )
        assert resp.status_code == 404, resp.text
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)
