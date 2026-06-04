"""Resource verify-and-tag pipeline (Kanban #1309).

Given a stored file (or a link URL) this module derives the METADATA that lands
in `project_resources.tags` (a JSON object — see schemas/project_resource.py for
the #1309 list->dict shape decision). Pure-stdlib: `csv`, `json`, `mimetypes`,
`hashlib` only — NO third-party parsers are imported here.

Design — PLUGGABLE per-format parser registry
----------------------------------------------
`_FORMAT_PARSERS: dict[str, ParserFn]` maps a detected format ("csv", "tsv",
"json") to a function that returns a `ParseResult`. FULL support today:

  - csv / tsv  -> header + row_count + col_count + first-N-row preview.
  - json       -> top-level type + (for a list-of-objects) row_count / col_count
                  / schema + preview; for an object, the key list.

GRACEFUL DEGRADE (parsers NOT installed): xlsx + pdf are DETECTED (so the UI can
show the format) but no parser runs — the metadata carries
`parser_unavailable=true` + a note, `preview=None`, and the pipeline does NOT
crash. Adding openpyxl/pdfplumber later is a one-line registry entry (#1309
follow-up — see report). Every other / unknown format is treated the same
graceful-degrade way.

est_cost approach (#1309)
-------------------------
"What would it cost to feed this whole file to an LLM once?" We approximate token
count as `bytes / _BYTES_PER_TOKEN` (≈4 bytes/token, the standard rough rule for
English text) and price it with `src.pricing.lookup_price` at the configured
default model (`_EST_COST_MODEL`, an Anthropic Opus-tier input-price snapshot).
This is a planning hint, NOT billing — it is documented as an approximation in
the tags payload (`est_cost_basis`).

The pipeline NEVER raises on a malformed file: parse failures are caught and
recorded as `parse_error` in the metadata so a bad upload still produces a row.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import mimetypes
import os
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse

from src.pricing import lookup_price

logger = logging.getLogger(__name__)

# Number of leading data rows surfaced in the preview sample.
PREVIEW_ROWS: int = 10
# Bytes read for content-sniffing the format when the extension is ambiguous.
_SNIFF_BYTES: int = 4096
# Rough English-text bytes-per-token heuristic for est_cost.
_BYTES_PER_TOKEN: float = 4.0
# Default model used to price est_cost_if_full (input direction). Bare name ->
# anthropic vendor in pricing.lookup_price. Opus-tier worst-case planning hint.
_EST_COST_MODEL: str = "opus"
# Number of leading bytes hashed -> sha256 (first 16 hex chars) for a cheap
# content fingerprint. We hash the WHOLE file (small files) but only surface a
# short prefix; #1309 spec says "sha256(first16)".
_HASH_HEX_LEN: int = 16


# ---------------------------------------------------------------------------
# Parser result type + registry
# ---------------------------------------------------------------------------


@dataclass
class ParseResult:
    """Structured output of a per-format parser.

    row_count / col_count / schema_detected / preview are None when the parser
    could not derive them (e.g. a JSON scalar, or a degrade-only format).
    `parser_unavailable` flags the graceful-degrade case (format known, no
    parser installed). `notes` collects free-form annotations.
    """

    format_detected: str
    row_count: int | None = None
    col_count: int | None = None
    schema_detected: list[str] | None = None
    preview: Any = None
    parser_unavailable: bool = False
    parse_error: str | None = None
    notes: list[str] = field(default_factory=list)


ParserFn = Callable[[bytes, "ParseContext"], ParseResult]


@dataclass
class ParseContext:
    """Side info handed to a parser (filename + sniffed delimiter, etc.)."""

    filename: str
    content_type: str | None


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


def detect_format(filename: str, head: bytes, content_type: str | None) -> str:
    """Best-effort format key from extension + content sniff + declared mime.

    Returns one of: "csv", "tsv", "json", "xlsx", "pdf", or "unknown". Extension
    wins when unambiguous; otherwise we sniff the leading bytes. NEVER raises.
    """
    ext = os.path.splitext(filename or "")[1].lower().lstrip(".")

    if ext in ("csv",):
        return "csv"
    if ext in ("tsv", "tab"):
        return "tsv"
    if ext in ("json",):
        return "json"
    if ext in ("xlsx", "xlsm"):
        return "xlsx"
    if ext in ("pdf",):
        return "pdf"

    # PDF magic bytes.
    if head[:5] == b"%PDF-":
        return "pdf"
    # XLSX is a ZIP container (PK\x03\x04). Distinguish from a generic zip only by
    # extension above; bare PK with no .xlsx ext we still treat as xlsx-degrade.
    if head[:4] == b"PK\x03\x04":
        return "xlsx"

    # Content sniff for text formats — decode leniently.
    try:
        sample = head.decode("utf-8", errors="replace").lstrip()
    except Exception:  # pragma: no cover - decode with replace shouldn't raise
        sample = ""
    if sample[:1] in ("{", "["):
        return "json"
    if "\t" in sample.splitlines()[0] if sample.splitlines() else False:
        return "tsv"
    if "," in (sample.splitlines()[0] if sample.splitlines() else ""):
        return "csv"

    # Fall back to the declared mime type when present.
    if content_type:
        if "json" in content_type:
            return "json"
        if "csv" in content_type:
            return "csv"
        if "tab-separated" in content_type:
            return "tsv"

    return "unknown"


def guess_content_type(filename: str, declared: str | None) -> str | None:
    """Resolve a content_type: prefer a non-generic declared value, else guess
    from the filename via stdlib `mimetypes`. Returns None when undeterminable.
    """
    if declared and declared not in ("application/octet-stream", ""):
        return declared
    guessed, _ = mimetypes.guess_type(filename or "")
    return guessed or declared or None


# ---------------------------------------------------------------------------
# Parsers (stdlib only)
# ---------------------------------------------------------------------------


def _parse_delimited(data: bytes, ctx: ParseContext, delimiter: str, fmt: str) -> ParseResult:
    """Parse CSV/TSV: header -> schema, count rows, first-N-row preview.

    Robust to a missing trailing newline + quoted fields (uses csv.reader).
    Decodes utf-8 with replacement so a stray byte never blows up the parse.
    row_count is the DATA row count (excludes the header line).
    """
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception as exc:  # pragma: no cover
        return ParseResult(format_detected=fmt, parse_error=f"decode failed: {exc}")

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration:
        # Empty file — zero rows, zero cols.
        return ParseResult(
            format_detected=fmt, row_count=0, col_count=0,
            schema_detected=[], preview=[],
        )

    schema = [h.strip() for h in header]
    col_count = len(schema)
    preview: list[dict[str, Any]] = []
    row_count = 0
    for row in reader:
        # Skip a wholly-empty trailing line (csv yields [] for blank lines).
        if not row:
            continue
        row_count += 1
        if len(preview) < PREVIEW_ROWS:
            # Zip header to row, tolerating ragged rows (pad/truncate).
            cells = (row + [None] * col_count)[:col_count]
            preview.append({schema[i]: cells[i] for i in range(col_count)})

    return ParseResult(
        format_detected=fmt,
        row_count=row_count,
        col_count=col_count,
        schema_detected=schema,
        preview=preview,
    )


def _parse_csv(data: bytes, ctx: ParseContext) -> ParseResult:
    return _parse_delimited(data, ctx, delimiter=",", fmt="csv")


def _parse_tsv(data: bytes, ctx: ParseContext) -> ParseResult:
    return _parse_delimited(data, ctx, delimiter="\t", fmt="tsv")


# JSON files larger than this threshold are skipped to avoid loading hundreds
# of MB into RAM (#1309 fix #7). Preview is set null + a tag note is added.
_JSON_MAX_INLINE_BYTES: int = 50 * 1024 * 1024  # 50 MB


def _parse_json(data: bytes, ctx: ParseContext) -> ParseResult:
    """Parse JSON: for a list-of-objects derive row/col/schema/preview; for a
    bare object surface its keys; for a scalar just record the type.

    Files > _JSON_MAX_INLINE_BYTES skip the full parse to avoid loading the
    whole document into RAM — tags carry too_large_for_inline_parse=true.
    """
    if len(data) > _JSON_MAX_INLINE_BYTES:
        return ParseResult(
            format_detected="json",
            parser_unavailable=False,
            notes=[
                f"json: file too large for inline parse "
                f"({len(data) // (1024 * 1024)} MB > "
                f"{_JSON_MAX_INLINE_BYTES // (1024 * 1024)} MB limit); "
                "too_large_for_inline_parse=true (#1309 fix #7)"
            ],
        )

    try:
        text = data.decode("utf-8", errors="replace")
        doc = json.loads(text)
    except (ValueError, UnicodeDecodeError) as exc:
        return ParseResult(format_detected="json", parse_error=f"invalid json: {exc}")

    if isinstance(doc, list):
        row_count = len(doc)
        # Union of keys across the first PREVIEW_ROWS object rows -> schema.
        schema_keys: list[str] = []
        seen: set[str] = set()
        for item in doc[:PREVIEW_ROWS]:
            if isinstance(item, dict):
                for k in item.keys():
                    if k not in seen:
                        seen.add(k)
                        schema_keys.append(k)
        preview = doc[:PREVIEW_ROWS]
        col_count = len(schema_keys) if schema_keys else None
        return ParseResult(
            format_detected="json",
            row_count=row_count,
            col_count=col_count,
            schema_detected=schema_keys or None,
            preview=preview,
            notes=["json: list root"],
        )

    if isinstance(doc, dict):
        keys = list(doc.keys())
        return ParseResult(
            format_detected="json",
            row_count=None,
            col_count=len(keys),
            schema_detected=keys,
            preview=doc,
            notes=["json: object root"],
        )

    # Scalar (string/number/bool/null).
    return ParseResult(
        format_detected="json",
        preview=doc,
        notes=[f"json: scalar root ({type(doc).__name__})"],
    )


def _parse_degrade(fmt: str, parser_name: str) -> ParserFn:
    """Build a graceful-degrade parser for a format whose lib isn't installed."""

    def _inner(data: bytes, ctx: ParseContext) -> ParseResult:
        return ParseResult(
            format_detected=fmt,
            parser_unavailable=True,
            notes=[
                f"{fmt}: parser '{parser_name}' not installed; metadata limited "
                "to format detection (see #1309 follow-up to add the dep)"
            ],
        )

    return _inner


# Registry — FULL parsers for csv/tsv/json (stdlib); degrade stubs for xlsx/pdf.
# Adding a real xlsx/pdf parser later = swap the registry value for a function
# that imports openpyxl / pdfplumber and returns a populated ParseResult.
_FORMAT_PARSERS: dict[str, ParserFn] = {
    "csv": _parse_csv,
    "tsv": _parse_tsv,
    "json": _parse_json,
    "xlsx": _parse_degrade("xlsx", "openpyxl"),
    "pdf": _parse_degrade("pdf", "pdfplumber"),
}


# ---------------------------------------------------------------------------
# est_cost + hash helpers
# ---------------------------------------------------------------------------


def estimate_cost_if_full(size_bytes: int, model: str = _EST_COST_MODEL) -> dict[str, Any]:
    """Approximate the one-shot USD cost of feeding the whole file to an LLM.

    tokens ≈ size_bytes / _BYTES_PER_TOKEN; price via pricing.lookup_price(input).
    Returns a dict carrying the basis so the number is never mistaken for billing.
    `usd` is None when the model price is unknown.
    """
    approx_tokens = int(size_bytes / _BYTES_PER_TOKEN) if size_bytes > 0 else 0
    price_per_m = lookup_price(model, "input")
    usd = round(approx_tokens / 1_000_000 * price_per_m, 6) if price_per_m else None
    return {
        "usd": usd,
        "approx_tokens": approx_tokens,
        "model": model,
        "basis": f"bytes/{_BYTES_PER_TOKEN:g} tokens @ {model} input price; planning estimate, not billing",
    }


def hash_prefix(data: bytes) -> str:
    """sha256 of the bytes, first _HASH_HEX_LEN hex chars (#1309 fingerprint)."""
    return hashlib.sha256(data).hexdigest()[:_HASH_HEX_LEN]


# ---------------------------------------------------------------------------
# Public entrypoints
# ---------------------------------------------------------------------------


def verify_and_tag_file(
    data: bytes,
    filename: str,
    content_type: str | None,
    size_bytes: int,
) -> dict[str, Any]:
    """Run the FILE verify-and-tag pipeline -> the `tags` metadata object.

    Steps: resolve content_type -> detect format -> dispatch to the per-format
    parser -> assemble {format_detected, row_count, col_count, schema_detected,
    preview, hash, est_cost_if_full, parser_unavailable, ...}. NEVER raises — a
    parser failure is recorded as `parse_error` so a row is still created.
    """
    head = data[:_SNIFF_BYTES]
    resolved_ct = guess_content_type(filename, content_type)
    fmt = detect_format(filename, head, resolved_ct)

    parser = _FORMAT_PARSERS.get(fmt)
    if parser is None:
        result = ParseResult(
            format_detected=fmt,
            parser_unavailable=True,
            notes=[f"{fmt}: no parser registered; format detection only"],
        )
    else:
        try:
            ctx = ParseContext(filename=filename, content_type=resolved_ct)
            result = parser(data, ctx)
        except Exception as exc:  # defensive — a parser bug must not 500 the upload
            logger.warning("resource_verify: parser for %s raised: %s", fmt, exc)
            result = ParseResult(format_detected=fmt, parse_error=str(exc))

    tags: dict[str, Any] = {
        "format_detected": result.format_detected,
        "row_count": result.row_count,
        "col_count": result.col_count,
        "schema_detected": result.schema_detected,
        "preview": result.preview,
        "preview_rows": PREVIEW_ROWS,
        "hash": hash_prefix(data),
        "est_cost_if_full": estimate_cost_if_full(size_bytes),
        "parser_unavailable": result.parser_unavailable,
        "content_type_resolved": resolved_ct,
    }
    if result.parse_error is not None:
        tags["parse_error"] = result.parse_error
    if result.notes:
        tags["notes"] = result.notes
    return tags


def verify_and_tag_link(url: str, head_status: int | None = None, title: str | None = None) -> dict[str, Any]:
    """Run the LINK verify-and-tag pipeline -> the `tags` metadata object.

    URL-syntax validation only here (scheme + netloc must be present); the
    best-effort HEAD probe (status + title) is performed by the router (it needs
    async I/O + a timeout) and passed in. Raises ValueError on a syntactically
    invalid URL so the router can 422.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(
            f"url must be an absolute http(s) URL; got {url!r}"
        )
    return {
        "url_scheme": parsed.scheme,
        "url_host": parsed.netloc,
        "head_status": head_status,
        "title": title,
    }
