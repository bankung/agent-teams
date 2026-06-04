"""Kanban #1309 — resource verify-and-tag + storage UNIT tests (stdlib only).

These run NOW (no server / no python-multipart needed). They lock the pure
pipeline + storage-confinement logic:
  - CSV schema detect + row_count=1000 + col_count=8 + preview=first 10 rows.
  - TSV + JSON parsing.
  - filename sanitization (path-traversal attempts rejected).
  - path-confinement guard (escape -> ValueError).
  - 520 MB cap logic via a monkeypatched small limit (NO 520 MB file created).
  - est_cost approximation (positive value + basis annotation).
  - XLSX/PDF graceful-degrade (format detected, parser_unavailable=True, no crash).

The integration suite (multipart upload, 413 over the wire, link kind, list
filters, preview endpoint, delete-to-trash, same-project task_id 422,
operator-gate 403) lives in test_resources_integration.py and needs
python-multipart (passes only AFTER the container rebuild). Edge/negative/
regression rigor is dev-tester's domain.
"""

from __future__ import annotations

import io

import pytest

from src.services import resource_storage as rs
from src.services import resource_verify as rv


# ---------------------------------------------------------------------------
# CSV / TSV / JSON parsing
# ---------------------------------------------------------------------------


def _make_csv_bytes(n_rows: int, n_cols: int) -> bytes:
    """Build a CSV with `n_cols` named columns + `n_rows` data rows."""
    buf = io.StringIO()
    header = ",".join(f"col{i}" for i in range(n_cols))
    buf.write(header + "\n")
    for r in range(n_rows):
        buf.write(",".join(f"v{r}_{c}" for c in range(n_cols)) + "\n")
    return buf.getvalue().encode("utf-8")


def test_csv_schema_rowcount_colcount_preview() -> None:
    data = _make_csv_bytes(1000, 8)
    tags = rv.verify_and_tag_file(data, "sample_sales.csv", "text/csv", len(data))

    assert tags["format_detected"] == "csv"
    # POSITIVE: 1000 data rows (header excluded), 8 columns.
    assert tags["row_count"] == 1000, tags["row_count"]
    assert tags["col_count"] == 8, tags["col_count"]
    assert tags["schema_detected"] == [f"col{i}" for i in range(8)]
    # NEGATIVE (the lock): preview is the FIRST 10 rows only, not all 1000.
    assert len(tags["preview"]) == 10, len(tags["preview"])
    # First preview row maps header -> first data row values.
    assert tags["preview"][0]["col0"] == "v0_0"
    assert tags["preview"][0]["col7"] == "v0_7"
    assert not tags["parser_unavailable"]


def test_csv_empty_file_zero_rows() -> None:
    tags = rv.verify_and_tag_file(b"", "empty.csv", "text/csv", 0)
    assert tags["format_detected"] == "csv"
    assert tags["row_count"] == 0
    assert tags["col_count"] == 0
    assert tags["preview"] == []


def test_tsv_parsing() -> None:
    data = b"a\tb\tc\n1\t2\t3\n4\t5\t6\n"
    tags = rv.verify_and_tag_file(data, "x.tsv", None, len(data))
    assert tags["format_detected"] == "tsv"
    assert tags["row_count"] == 2
    assert tags["col_count"] == 3
    assert tags["schema_detected"] == ["a", "b", "c"]
    assert tags["preview"][0] == {"a": "1", "b": "2", "c": "3"}


def test_json_list_of_objects() -> None:
    data = b'[{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]'
    tags = rv.verify_and_tag_file(data, "rows.json", "application/json", len(data))
    assert tags["format_detected"] == "json"
    assert tags["row_count"] == 2
    assert tags["col_count"] == 2
    assert set(tags["schema_detected"]) == {"id", "name"}
    assert len(tags["preview"]) == 2


def test_json_object_root() -> None:
    data = b'{"a": 1, "b": 2, "c": 3}'
    tags = rv.verify_and_tag_file(data, "obj.json", "application/json", len(data))
    assert tags["format_detected"] == "json"
    assert tags["schema_detected"] == ["a", "b", "c"]
    assert tags["col_count"] == 3


def test_malformed_json_records_error_no_crash() -> None:
    data = b"{not valid json"
    tags = rv.verify_and_tag_file(data, "bad.json", "application/json", len(data))
    # NEGATIVE (the lock): a bad file does NOT crash — it records parse_error and
    # still yields a tags object so a row can be created.
    assert tags["format_detected"] == "json"
    assert "parse_error" in tags
    assert tags["preview"] is None


# ---------------------------------------------------------------------------
# XLSX / PDF graceful-degrade
# ---------------------------------------------------------------------------


def test_xlsx_graceful_degrade() -> None:
    # PK zip magic -> xlsx detection even without an .xlsx ext.
    data = b"PK\x03\x04" + b"\x00" * 100
    tags = rv.verify_and_tag_file(data, "book.xlsx", None, len(data))
    assert tags["format_detected"] == "xlsx"
    # POSITIVE: degrade path flagged, no parser ran, no crash.
    assert tags["parser_unavailable"] is True
    assert tags["preview"] is None
    assert any("openpyxl" in n for n in tags.get("notes", []))


def test_pdf_graceful_degrade() -> None:
    data = b"%PDF-1.7\n" + b"binary junk \x00\x01\x02"
    tags = rv.verify_and_tag_file(data, "doc.pdf", "application/pdf", len(data))
    assert tags["format_detected"] == "pdf"
    assert tags["parser_unavailable"] is True
    assert tags["preview"] is None
    assert any("pdfplumber" in n for n in tags.get("notes", []))


# ---------------------------------------------------------------------------
# est_cost + hash
# ---------------------------------------------------------------------------


def test_est_cost_positive_with_basis() -> None:
    data = _make_csv_bytes(100, 4)
    tags = rv.verify_and_tag_file(data, "s.csv", "text/csv", len(data))
    est = tags["est_cost_if_full"]
    # POSITIVE: a non-trivial file yields a positive token estimate + USD.
    assert est["approx_tokens"] > 0
    assert est["usd"] is not None and est["usd"] > 0
    # NEGATIVE (the lock): the number is annotated as a planning estimate, not
    # silent billing.
    assert "estimate, not billing" in est["basis"]


def test_hash_is_stable_16_hex() -> None:
    h1 = rv.hash_prefix(b"hello world")
    h2 = rv.hash_prefix(b"hello world")
    assert h1 == h2
    assert len(h1) == 16
    assert h1 != rv.hash_prefix(b"different")


# ---------------------------------------------------------------------------
# Link URL validation
# ---------------------------------------------------------------------------


def test_link_valid_url() -> None:
    tags = rv.verify_and_tag_link("https://example.com/spec.pdf")
    assert tags["url_scheme"] == "https"
    assert tags["url_host"] == "example.com"


def test_link_invalid_url_raises() -> None:
    with pytest.raises(ValueError):
        rv.verify_and_tag_link("not-a-url")
    with pytest.raises(ValueError):
        rv.verify_and_tag_link("ftp://example.com/x")


# ---------------------------------------------------------------------------
# Filename sanitization (security)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,must_not_contain",
    [
        ("../../etc/passwd", ".."),
        ("..\\..\\windows\\system32", ".."),
        ("/abs/path/file.csv", "/"),
        ("C:\\Users\\x\\file.csv", "\\"),
        ("normal/sub/dir/data.csv", "/"),
    ],
)
def test_sanitize_strips_path_components(raw: str, must_not_contain: str) -> None:
    out = rs.sanitize_filename(raw)
    # NEGATIVE (the lock): no separators or traversal survive sanitization.
    assert must_not_contain not in out, out
    assert "/" not in out and "\\" not in out and ".." not in out
    # POSITIVE: the real basename / extension is preserved when present.
    if raw.endswith(".csv"):
        assert out.endswith(".csv"), out


def test_sanitize_leading_dots_and_nul() -> None:
    assert not rs.sanitize_filename("...hidden").startswith(".")
    assert "\x00" not in rs.sanitize_filename("a\x00b.csv")
    # Empty / all-junk falls back to a generic safe name.
    assert rs.sanitize_filename("") == rs._FALLBACK_NAME
    assert rs.sanitize_filename("..") == rs._FALLBACK_NAME


def test_sanitize_keeps_safe_name() -> None:
    assert rs.sanitize_filename("sample_sales-2026.csv") == "sample_sales-2026.csv"


# ---------------------------------------------------------------------------
# Path confinement
# ---------------------------------------------------------------------------


def test_build_target_path_confined(tmp_path) -> None:
    base = tmp_path / "proj"
    target = rs.build_target_path(base, 42, "data.csv")
    # POSITIVE: target lands under <base>/data/raw/.
    assert target == (base / "data" / "raw" / "42-data.csv").resolve()

    # NEGATIVE (the lock): even a name carrying `..` segments resolves to a path
    # that STAYS inside `base` — the mandatory `<id>-` prefix neutralizes a
    # leading `..` (it becomes a `<id>-..` segment), so no traversal escapes the
    # storage subtree at this layer (and sanitize_filename strips `..` upstream).
    confined = rs.build_target_path(base, 1, "../../../escape.csv")
    confined.relative_to(base.resolve())  # raises if it escaped — it must not


def test_confine_guard_raises_on_escape(tmp_path) -> None:
    """The low-level _confine guard rejects a target outside the root."""
    base = tmp_path / "proj"
    outside = tmp_path / "elsewhere" / "x.csv"
    # NEGATIVE (the lock): a target resolving outside `base` is refused.
    with pytest.raises(ValueError):
        rs._confine(outside, base)
    # POSITIVE: an inside target is accepted (returns the resolved path).
    inside = base / "data" / "raw" / "ok.csv"
    assert rs._confine(inside, base) == inside.resolve()


def test_resolve_storage_base_fallback(tmp_path) -> None:
    # working_path set -> used verbatim.
    assert rs.resolve_storage_base("/custom/wp", 7, tmp_path) == \
        rs.Path("/custom/wp")
    # working_path null -> documented repo fallback.
    out = rs.resolve_storage_base(None, 7, tmp_path)
    assert out == tmp_path / "_data" / "projects" / "7"
    # Empty/whitespace working_path also falls back.
    assert rs.resolve_storage_base("   ", 7, tmp_path) == \
        tmp_path / "_data" / "projects" / "7"


# ---------------------------------------------------------------------------
# 520 MB streaming cap (monkeypatched small limit — NO giant file)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_cap_aborts_and_cleans_partial(tmp_path, monkeypatch) -> None:
    """With a tiny patched cap, a stream that exceeds it raises
    UploadTooLargeError AND deletes the partial file (no orphan)."""
    monkeypatch.setattr(rs, "MAX_UPLOAD_BYTES", 10)  # 10-byte cap

    async def _chunks():
        yield b"12345"  # 5 bytes
        yield b"67890ABC"  # +8 = 13 > 10 -> abort on this chunk

    base = tmp_path / "proj"
    with pytest.raises(rs.UploadTooLargeError):
        await rs.stream_to_disk(_chunks(), base, 99, "big.bin")

    # NEGATIVE (the lock): the partial file was removed.
    target = base / "data" / "raw" / "99-big.bin"
    assert not target.exists(), "partial file must be deleted on cap-exceed"


@pytest.mark.asyncio
async def test_streaming_under_cap_succeeds(tmp_path) -> None:
    async def _chunks():
        yield b"hello,"
        yield b"world"

    base = tmp_path / "proj"
    stored = await rs.stream_to_disk(_chunks(), base, 5, "ok.csv")
    # POSITIVE: file written, size = sum of chunks.
    assert stored.size_bytes == len(b"hello,world")
    assert stored.path.exists()
    assert stored.path.read_bytes() == b"hello,world"


# ---------------------------------------------------------------------------
# Trash move (soft-delete)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_move_to_trash(tmp_path) -> None:
    base = tmp_path / "proj"
    raw = base / "data" / "raw"
    raw.mkdir(parents=True)
    f = raw / "7-data.csv"
    f.write_bytes(b"x")

    moved = rs.move_to_trash(base, str(f))
    assert moved is True
    # POSITIVE: file now lives under .trash/, original gone.
    assert not f.exists()
    assert (base / ".trash" / "7-data.csv").exists()
    # NEGATIVE (idempotent): a second call (source gone) is a no-op.
    assert rs.move_to_trash(base, str(f)) is False
    # None path (e.g. link resource) -> no-op.
    assert rs.move_to_trash(base, None) is False


def test_move_to_trash_rejects_escaped_source(tmp_path) -> None:
    """#1309 fix #6b: stored_path pointing outside storage_base -> ValueError."""
    base = tmp_path / "proj"
    outside = tmp_path / "elsewhere" / "bad.csv"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"bad")
    # NEGATIVE (the lock): tampered stored_path escaping storage_base is refused.
    with pytest.raises(ValueError, match="escapes storage root"):
        rs.move_to_trash(base, str(outside))
    # POSITIVE: the outside file was NOT moved (still exists).
    assert outside.exists()


# ---------------------------------------------------------------------------
# JSON large-file guard (#1309 fix #7)
# ---------------------------------------------------------------------------


def test_json_large_file_skips_inline_parse(monkeypatch) -> None:
    """Files exceeding _JSON_MAX_INLINE_BYTES skip json.loads (#1309 fix #7)."""
    import src.services.resource_verify as rv_mod  # noqa: PLC0415

    # Patch the threshold to 10 bytes so we can test with a tiny payload.
    monkeypatch.setattr(rv_mod, "_JSON_MAX_INLINE_BYTES", 10)

    big_json = b'[{"a": 1}, {"b": 2}]'  # 20 bytes > 10
    tags = rv_mod.verify_and_tag_file(big_json, "big.json", "application/json", len(big_json))

    # POSITIVE: format still detected; pipeline doesn't crash.
    assert tags["format_detected"] == "json"
    # NEGATIVE (the lock): no inline parse happened — preview is None.
    assert tags["preview"] is None
    assert tags["row_count"] is None
    # A note signals the skip.
    assert any("too_large_for_inline_parse" in n for n in tags.get("notes", [])), tags


def test_json_small_file_parses_normally(monkeypatch) -> None:
    """Files under the threshold still parse fully."""
    import src.services.resource_verify as rv_mod  # noqa: PLC0415

    monkeypatch.setattr(rv_mod, "_JSON_MAX_INLINE_BYTES", 10 * 1024 * 1024)  # 10 MB

    data = b'[{"id": 1, "val": "x"}]'
    tags = rv_mod.verify_and_tag_file(data, "small.json", "application/json", len(data))
    # POSITIVE: parsed normally.
    assert tags["row_count"] == 1
    assert tags["preview"] is not None
