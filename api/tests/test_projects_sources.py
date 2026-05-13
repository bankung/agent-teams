"""Kanban #778 — `projects.sources` JSONB list wire-up.

Migration `0020_projects_sources` adds the column + CHECK `jsonb_array_length(sources) <= 20`.
This file pins the API-layer contract added by the dev-backend follow-up:
- `SourceEntry` Pydantic model (extra=forbid, url-shape validator, label/kind optional)
- `ProjectCreate.sources` + `ProjectUpdate.sources` accept `list[SourceEntry] | None`,
  `max_length=20`
- `ProjectRead.sources` is ALWAYS a list at the wire boundary (NULL coerced to `[]`)
- POST + PATCH wire-up; PATCH-null normalizes to `[]` (parity with agent_overrides)
- DB CHECK is defense-in-depth — proves it fires when Pydantic is bypassed

Cleanup uses scaffold_cleanup + DELETE /api/projects/{id} on the way out so the
live-DB row-count invariant in conftest stays happy.
"""

from __future__ import annotations

import uuid

import pytest


# ---- helpers ---------------------------------------------------------------


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str, *, team: str = "dev") -> dict:
    return {
        "name": name,
        "description": f"k778 sources fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": team,
    }


def _twenty_one_valid_sources() -> list[dict]:
    return [{"url": f"https://example.com/{i}"} for i in range(21)]


# ---- 1. Pydantic boundary: 21 entries → 422 --------------------------------


@pytest.mark.asyncio
async def test_create_project_rejects_21_sources(client, scaffold_cleanup) -> None:
    """POST with sources of length 21 → 422 (Pydantic max_length=20).

    The Pydantic boundary fires FIRST — caller never reaches the DB CHECK. The
    422 loc points at body.sources with type=too_long.
    """
    name = scaffold_cleanup(_unique_name("k778-21-create"))
    payload = _project_create_payload(name)
    payload["sources"] = _twenty_one_valid_sources()

    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert "detail" in body and isinstance(body["detail"], list)
    matches = [err for err in body["detail"] if err["loc"][:2] == ["body", "sources"]]
    assert matches, f"expected loc=['body','sources',...] in 422 detail; got {body}"
    assert matches[0]["type"] == "too_long", (
        f"expected type='too_long'; got {matches[0]['type']!r}"
    )


@pytest.mark.asyncio
async def test_patch_project_rejects_21_sources(client, scaffold_cleanup) -> None:
    """PATCH with sources of length 21 → 422 (Pydantic max_length=20)."""
    name = scaffold_cleanup(_unique_name("k778-21-patch"))
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    try:
        patch = await client.patch(
            f"/api/projects/{project_id}",
            json={"sources": _twenty_one_valid_sources()},
        )
        assert patch.status_code == 422, patch.text
        matches = [
            err for err in patch.json()["detail"]
            if err["loc"][:2] == ["body", "sources"]
        ]
        assert matches and matches[0]["type"] == "too_long", patch.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 2. Invalid URL shape → 422 --------------------------------------------


@pytest.mark.asyncio
async def test_create_project_rejects_invalid_url_shape(
    client, scaffold_cleanup
) -> None:
    """POST with sources=[{"url": "not-a-url"}] → 422 (no scheme, no abs path)."""
    name = scaffold_cleanup(_unique_name("k778-badurl"))
    payload = _project_create_payload(name)
    payload["sources"] = [{"url": "not-a-url"}]

    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 422, resp.text
    body = resp.json()
    # Pydantic places the validator error at body.sources.<idx>.url with
    # type='value_error' (custom ValueError from _url_shape).
    matches = [
        err for err in body["detail"]
        if err["loc"][:2] == ["body", "sources"] and err["loc"][-1] == "url"
    ]
    assert matches, f"expected url validator error in detail; got {body}"
    assert matches[0]["type"] == "value_error", (
        f"expected type='value_error'; got {matches[0]['type']!r}"
    )


# ---- 3. Invalid `kind` literal → 422 ---------------------------------------


@pytest.mark.asyncio
async def test_create_project_rejects_invalid_kind_enum(
    client, scaffold_cleanup
) -> None:
    """POST with sources=[{"url":"https://x","kind":"lolwut"}] → 422.

    kind is Literal['doc','spec','repo','dashboard','other'] | None — 'lolwut'
    fails with type=literal_error at body.sources.0.kind.
    """
    name = scaffold_cleanup(_unique_name("k778-badkind"))
    payload = _project_create_payload(name)
    payload["sources"] = [{"url": "https://x", "kind": "lolwut"}]

    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 422, resp.text
    body = resp.json()
    matches = [
        err for err in body["detail"]
        if err["loc"][:2] == ["body", "sources"] and err["loc"][-1] == "kind"
    ]
    assert matches, f"expected kind validator error in detail; got {body}"
    assert matches[0]["type"] == "literal_error", (
        f"expected type='literal_error'; got {matches[0]['type']!r}"
    )


# ---- 4. Extra-forbid on element → 422 --------------------------------------


@pytest.mark.asyncio
async def test_create_project_rejects_extra_key_in_source(
    client, scaffold_cleanup
) -> None:
    """SourceEntry has `extra='forbid'` — unknown key 'frobozz' → 422."""
    name = scaffold_cleanup(_unique_name("k778-extra"))
    payload = _project_create_payload(name)
    payload["sources"] = [{"url": "https://x", "frobozz": True}]

    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 422, resp.text
    body = resp.json()
    matches = [
        err for err in body["detail"]
        if err["loc"][:2] == ["body", "sources"] and err["type"] == "extra_forbidden"
    ]
    assert matches, f"expected extra_forbidden error in detail; got {body}"


# ---- 5. Happy CRUD round-trip ----------------------------------------------


@pytest.mark.asyncio
async def test_sources_happy_crud_round_trip(client, scaffold_cleanup) -> None:
    """Full happy path: POST → GET → PATCH → GET, plus a key-absent PATCH that
    must leave sources unchanged. Also sanity-checks /stats still returns the
    project (no breakage of the parallel lane).
    """
    name = scaffold_cleanup(_unique_name("k778-happy"))
    initial_sources = [
        {"url": "https://docs.example.com", "label": "docs", "kind": "doc"},
        {"url": "/local/path"},
    ]
    payload = _project_create_payload(name)
    payload["sources"] = initial_sources

    # POST.
    create = await client.post("/api/projects", json=payload)
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    try:
        body = create.json()
        assert isinstance(body["sources"], list), body
        assert len(body["sources"]) == 2, body
        # First entry: all three keys present.
        e0 = body["sources"][0]
        assert e0["url"] == "https://docs.example.com"
        assert e0["label"] == "docs"
        assert e0["kind"] == "doc"
        # Second entry: only url present (label/kind dropped via exclude_none).
        e1 = body["sources"][1]
        assert e1["url"] == "/local/path"
        assert "label" not in e1, f"label should be dropped when None; got {e1}"
        assert "kind" not in e1, f"kind should be dropped when None; got {e1}"

        # PATCH with new sources.
        new_sources = [
            {"url": "https://a.example.com/spec", "label": "spec", "kind": "spec"},
            {"url": "https://b.example.com/repo", "kind": "repo"},
            {"url": "ref://internal/dashboard", "label": "dash", "kind": "dashboard"},
        ]
        patch = await client.patch(
            f"/api/projects/{project_id}", json={"sources": new_sources}
        )
        assert patch.status_code == 200, patch.text
        pbody = patch.json()
        assert len(pbody["sources"]) == 3, pbody
        assert pbody["sources"][0] == {
            "url": "https://a.example.com/spec",
            "label": "spec",
            "kind": "spec",
        }
        assert pbody["sources"][1] == {
            "url": "https://b.example.com/repo",
            "kind": "repo",
        }
        assert pbody["sources"][2] == {
            "url": "ref://internal/dashboard",
            "label": "dash",
            "kind": "dashboard",
        }

        # PATCH with sources OMITTED → sources unchanged (exclude_unset=True).
        bump = await client.patch(
            f"/api/projects/{project_id}", json={"description": "bumped"}
        )
        assert bump.status_code == 200, bump.text
        assert bump.json()["description"] == "bumped"
        assert bump.json()["sources"] == pbody["sources"], (
            "omitted sources field must not be overwritten"
        )

        # GET by id matches the last PATCH.
        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["sources"] == pbody["sources"]

        # /stats still returns the project (sanity check — parallel lane intact).
        stats_resp = await client.get("/api/projects/stats")
        assert stats_resp.status_code == 200, stats_resp.text
        stats_ids = [e["id"] for e in stats_resp.json()]
        assert project_id in stats_ids, "project missing from /stats after sources wire-up"
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 5b. PATCH-null normalizes to [] (parity with agent_overrides) ---------


@pytest.mark.asyncio
async def test_patch_sources_null_normalizes_to_empty_list(
    client, scaffold_cleanup
) -> None:
    """PATCH {"sources": null} → response is exactly [] (not None).

    Mirrors test_777_edge_patch_agent_overrides_null_clears_to_empty_dict —
    keeps the "always a list at the response boundary" wire contract intact
    across explicit-null PATCH.
    """
    name = scaffold_cleanup(_unique_name("k778-null"))
    payload = _project_create_payload(name)
    payload["sources"] = [{"url": "https://x", "kind": "doc"}]
    create = await client.post("/api/projects", json=payload)
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    try:
        patch = await client.patch(
            f"/api/projects/{project_id}", json={"sources": None}
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["sources"] == [], (
            f"sources after null-PATCH must be []; got {patch.json()['sources']!r}"
        )
        # Round-trip via GET.
        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["sources"] == []
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 5c. Default-empty on POST without sources -----------------------------


@pytest.mark.asyncio
async def test_create_project_without_sources_defaults_to_empty(
    client, scaffold_cleanup
) -> None:
    """POST with no `sources` key → response carries `sources=[]` (DB
    server_default `'[]'::jsonb` fires; ORM default=list is the Python-side
    safety net).
    """
    name = scaffold_cleanup(_unique_name("k778-defaults"))
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    try:
        assert create.json()["sources"] == []
        # GET by-name also returns [].
        get_resp = await client.get(f"/api/projects/by-name/{name}")
        assert get_resp.status_code == 200
        assert get_resp.json()["sources"] == []
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 6. DB CHECK defense-in-depth: bypass Pydantic, expect IntegrityError --


@pytest.mark.asyncio
async def test_db_check_rejects_21_entries_when_pydantic_bypassed(
    client, scaffold_cleanup
) -> None:
    """When the API layer is bypassed (direct ORM session write), the DB CHECK
    `ck_projects_sources_length` MUST reject a 21-element array. This proves
    defense-in-depth — Pydantic max_length=20 is the first wall (covered above);
    the DB CHECK is the second wall in case the wire validator is ever loosened
    or a non-API writer slips in.
    """
    import sqlalchemy.exc
    from src.constants import ProjectTeam
    from src.db import SessionLocal
    from src.models.project import Project

    # Create a project via the API first (so scaffold_cleanup still owns the
    # filesystem folder), then mutate sources via ORM to trigger the DB CHECK.
    name = scaffold_cleanup(_unique_name("k778-dbcheck"))
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]

    try:
        async with SessionLocal() as session:
            row = await session.get(Project, project_id)
            assert row is not None
            row.sources = [{"url": f"https://example.com/{i}"} for i in range(21)]
            with pytest.raises(sqlalchemy.exc.IntegrityError) as exc_info:
                await session.commit()
            # The error message should reference the CHECK constraint name.
            assert "ck_projects_sources_length" in str(exc_info.value), (
                f"expected ck_projects_sources_length in IntegrityError; "
                f"got: {exc_info.value}"
            )
            await session.rollback()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 7. Blank URL rejected -------------------------------------------------


@pytest.mark.asyncio
async def test_create_project_rejects_blank_url(client, scaffold_cleanup) -> None:
    """URL of pure whitespace (which would survive min_length=1 if not for the
    strip-then-check) → 422. Locks the _url_shape validator's blank-rejection.
    """
    name = scaffold_cleanup(_unique_name("k778-blank"))
    payload = _project_create_payload(name)
    payload["sources"] = [{"url": "   "}]  # pure whitespace
    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 422, resp.text
    matches = [
        err for err in resp.json()["detail"]
        if err["loc"][:2] == ["body", "sources"] and err["loc"][-1] == "url"
    ]
    assert matches, f"expected url validator error in detail; got {resp.json()}"


# ---- 8-11. Scheme allowlist — reject code-execution / non-allowlisted ------
# Kanban #778 BLOCKER-1 (dev-reviewer 2026-05-13): the prior `"://" in s` gate
# admitted `javascript://%0aalert(1)//` (canonical AngularJS-sanitizer-bypass
# payload — the FE renders the entry as an external `<a href>` and the browser
# executes JS in-origin on click). The new `^(http|https|ref|file)://` allowlist
# closes the class. These four cases lock the rejection.


def _assert_url_value_error(resp) -> None:
    """Helper — assert 422 with a value_error at body.sources.0.url."""
    assert resp.status_code == 422, resp.text
    matches = [
        err for err in resp.json()["detail"]
        if err["loc"][:2] == ["body", "sources"] and err["loc"][-1] == "url"
    ]
    assert matches, f"expected url validator error in detail; got {resp.json()}"
    assert matches[0]["type"] == "value_error", (
        f"expected type='value_error'; got {matches[0]['type']!r}"
    )


@pytest.mark.asyncio
async def test_url_rejects_javascript_scheme(client, scaffold_cleanup) -> None:
    """BLOCKER-1 payload: `javascript://%0aalert(1)//` MUST 422.

    `//` after `javascript:` is a JS line-comment; if the FE rendered the
    string as an `<a href>` and the user clicked, the browser would execute
    `alert(1)` in-origin. The scheme allowlist rejects at the validator boundary.
    """
    name = scaffold_cleanup(_unique_name("k778-js-scheme"))
    payload = _project_create_payload(name)
    payload["sources"] = [{"url": "javascript://%0aalert(1)//"}]
    resp = await client.post("/api/projects", json=payload)
    _assert_url_value_error(resp)


@pytest.mark.asyncio
async def test_url_rejects_data_scheme(client, scaffold_cleanup) -> None:
    """`data://text/plain,foo` rejected — `data:` is a code-rendering scheme."""
    name = scaffold_cleanup(_unique_name("k778-data-scheme"))
    payload = _project_create_payload(name)
    payload["sources"] = [{"url": "data://text/plain,foo"}]
    resp = await client.post("/api/projects", json=payload)
    _assert_url_value_error(resp)


@pytest.mark.asyncio
async def test_url_rejects_vbscript_scheme(client, scaffold_cleanup) -> None:
    """`vbscript://alert` rejected — legacy IE code-execution scheme."""
    name = scaffold_cleanup(_unique_name("k778-vbs-scheme"))
    payload = _project_create_payload(name)
    payload["sources"] = [{"url": "vbscript://alert"}]
    resp = await client.post("/api/projects", json=payload)
    _assert_url_value_error(resp)


@pytest.mark.asyncio
async def test_url_rejects_gopher_scheme(client, scaffold_cleanup) -> None:
    """`gopher://example.com` rejected — not on the allowlist."""
    name = scaffold_cleanup(_unique_name("k778-gopher-scheme"))
    payload = _project_create_payload(name)
    payload["sources"] = [{"url": "gopher://example.com"}]
    resp = await client.post("/api/projects", json=payload)
    _assert_url_value_error(resp)


# ---- 12. Bare `://` separator rejected (closes dev-tester N1) --------------


@pytest.mark.asyncio
async def test_url_rejects_bare_scheme_separator(client, scaffold_cleanup) -> None:
    """`://` (no scheme letter) was accepted by the old substring gate
    (dev-tester #778 Probe C4). The new regex requires an allowlisted scheme
    letter before `://`, so the bare separator no longer matches.
    """
    name = scaffold_cleanup(_unique_name("k778-bare-sep"))
    payload = _project_create_payload(name)
    payload["sources"] = [{"url": "://"}]
    resp = await client.post("/api/projects", json=payload)
    _assert_url_value_error(resp)


# ---- 13-15. Bonus: case-insensitive + file:// + ref:// accepted ------------


@pytest.mark.asyncio
async def test_url_accepts_uppercase_scheme(client, scaffold_cleanup) -> None:
    """`HTTPS://example.com` accepted (allowlist match is case-insensitive).

    Locks the case-insensitive flag on `_SCHEME_RE` so a future maintainer
    can't silently tighten the regex to lowercase-only.
    """
    name = scaffold_cleanup(_unique_name("k778-upper"))
    payload = _project_create_payload(name)
    payload["sources"] = [{"url": "HTTPS://example.com"}]
    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    try:
        # Stored value is NOT lowercased — strip is the only mutation.
        assert resp.json()["sources"][0]["url"] == "HTTPS://example.com"
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_url_accepts_file_scheme(client, scaffold_cleanup) -> None:
    """`file:///etc/hosts` accepted (file:// is on the allowlist)."""
    name = scaffold_cleanup(_unique_name("k778-file"))
    payload = _project_create_payload(name)
    payload["sources"] = [{"url": "file:///etc/hosts"}]
    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    try:
        assert resp.json()["sources"][0]["url"] == "file:///etc/hosts"
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_url_accepts_ref_scheme(client, scaffold_cleanup) -> None:
    """`ref://internal/doc` accepted (ref:// is on the allowlist; used by
    dev-researcher for internal references — see dev-tester probe).
    """
    name = scaffold_cleanup(_unique_name("k778-ref"))
    payload = _project_create_payload(name)
    payload["sources"] = [{"url": "ref://internal/doc"}]
    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    try:
        assert resp.json()["sources"][0]["url"] == "ref://internal/doc"
    finally:
        await client.delete(f"/api/projects/{project_id}")
