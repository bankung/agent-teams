"""Zero-LLM lexical search over a project's `shared/` memory corpus — Kanban #1678.

A hand-rolled BM25 recall tool: given a project's `shared/*.md` corpus
(decisions.md, incidents/, design/, runbooks/, stories/, code-map*, …), return
the most-relevant markdown chunks for a query in well under a second, with NO
LLM and NO token cost. This is LEXICAL search (BM25), deliberately NOT
embeddings — embeddings are a separate experiment (#975). stdlib only; ZERO
external dependency.

Design mirrors `services/next_action_ranker.py`: module-level constants +
dataclasses + pure functions split from I/O so the BM25 math and chunker are
unit-testable without a FastAPI/Postgres fixture.

Three responsibilities, layered:

  1. Corpus I/O      — resolve the per-project root, walk `*.md` recursively,
                       read each file (path, mtime, text).
  2. Chunking        — split each file on markdown headings (`#`/`##`/`###` …);
                       a decisions.md `## <date> — <title>` entry becomes one
                       chunk (heading line kept WITH its body).
  3. BM25 ranking    — tokenize, build per-chunk term freqs + corpus doc freqs,
                       score chunks for a query with standard BM25
                       (k1=1.5, b=0.75).

Security
--------
The `scroll` / `browse` modes accept a caller-supplied `file` (relative path).
`safe_resolve_in_root()` is the path-traversal guard — it rejects absolute
paths + `..` segments and asserts the resolved real path stays INSIDE the
corpus root (mirrors `services/resource_storage._confine`, the
security-reviewed precedent). Read-only throughout: this module NEVER opens a
file for write and NEVER mutates `shared/`.
"""

from __future__ import annotations

import math
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# --- BM25 hyperparameters (module-level so tuning/tests live in one place) ----
# Standard Okapi BM25 defaults. k1 controls term-frequency saturation; b
# controls length normalization (0 = none, 1 = full).
BM25_K1: float = 1.5
BM25_B: float = 0.75

# Snippet rendering: chars of context to show around the strongest term hit in
# a discovery result. shortcut: fixed-width window, fine for recall snippets;
# upgrade: sentence-boundary aware excerpting if snippets read awkwardly.
SNIPPET_RADIUS: int = 160

# Markdown ATX heading: 1–6 leading '#', a space, then the heading text. We cap
# at 6 '#' per the CommonMark spec (7+ is not a heading).
_HEADING_RE = re.compile(r"^(#{1,6})\s+(\S(?:.*\S)?)\s*$")

# Tokenizer: lowercase, split on any run of non-alphanumerics, drop empties.
_TOKEN_RE = re.compile(r"[a-z0-9]+")


# =============================================================================
# Data shapes
# =============================================================================


@dataclass(frozen=True)
class CorpusFile:
    """One markdown file in the corpus."""

    rel_path: str          # POSIX-style path relative to the corpus root
    mtime: float           # st_mtime — feeds the cache key
    text: str              # full file text (UTF-8, errors replaced)


@dataclass(frozen=True)
class Chunk:
    """One heading-delimited slice of a file (the unit BM25 scores).

    `heading` is the heading text WITHOUT the leading '#'s (empty string for a
    file's pre-heading preamble). `line_start`/`line_end` are 1-based, inclusive
    — they index lines in the source file so `scroll` can jump straight there.
    """

    file: str              # rel_path of the owning file
    heading: str
    level: int             # heading depth 1..6; 0 = preamble (no heading)
    line_start: int        # 1-based, inclusive
    line_end: int          # 1-based, inclusive
    text: str              # the chunk body INCLUDING its heading line


@dataclass(frozen=True)
class ScoredChunk:
    """A chunk plus its BM25 score for a particular query."""

    chunk: Chunk
    score: float


@dataclass
class Corpus:
    """A loaded + chunked corpus, ready to query.

    Built by `build_corpus()`. Holds the per-chunk token bags + corpus-wide doc
    frequencies so repeat `score_chunks` calls don't re-tokenize. Mutable so the
    module-level cache can stash it keyed by (root, max_mtime).
    """

    root: str                                  # resolved corpus root (abs, POSIX)
    files: list[CorpusFile]
    chunks: list[Chunk]
    max_mtime: float
    # Precomputed BM25 index over `chunks` (parallel lists, index-aligned).
    _doc_tokens: list[Counter] = field(default_factory=list)   # term -> tf per chunk
    _doc_len: list[int] = field(default_factory=list)          # token count per chunk
    _doc_freq: Counter = field(default_factory=Counter)        # term -> #chunks containing it
    _avg_len: float = 0.0


# =============================================================================
# Path-traversal guard (security-critical — mirrors resource_storage._confine)
# =============================================================================


def safe_resolve_in_root(root: Path, rel: str) -> Path:
    """Resolve `rel` against `root`, asserting the result stays INSIDE `root`.

    The single chokepoint for the caller-supplied `file` param (scroll/browse).
    Rejects, with ValueError:
      * absolute inputs (`/etc/passwd`, `C:\\...`),
      * any path containing a `..` segment (before resolution — cheap reject),
      * anything that, once resolved (symlinks included), escapes `root`.

    Returns the resolved absolute Path on success. The router maps the
    ValueError to HTTP 400. NEVER opens or writes the path — resolution only.
    """
    if rel is None:
        raise ValueError("shared_search: file path is required")

    # Normalize separators so a Windows-style '\' in the input is treated as a
    # path separator on POSIX too (defense-in-depth — the API runs on Linux).
    candidate_str = rel.replace("\\", "/").strip()
    if not candidate_str:
        raise ValueError("shared_search: file path is empty")

    pure = Path(candidate_str)
    if pure.is_absolute():
        raise ValueError(f"shared_search: absolute paths are not allowed: {rel!r}")
    # Reject any explicit parent-dir segment up front — a resolved path can also
    # catch this, but an early lexical reject is clearer + cheaper.
    if ".." in pure.parts:
        raise ValueError(f"shared_search: path traversal ('..') is not allowed: {rel!r}")

    root_resolved = root.resolve()
    target = (root_resolved / pure).resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(
            f"shared_search: path {rel!r} escapes the corpus root; refusing"
        ) from exc
    return target


# =============================================================================
# Corpus I/O
# =============================================================================


def load_corpus(root: Path) -> list[CorpusFile]:
    """Walk `*.md` under `root` recursively; return (rel_path, mtime, text) per file.

    Files that vanish or fail to read between the walk and the open are skipped
    (best-effort — a recall tool must not 500 on a transient FS race). Symlinked
    `.md` files that resolve OUTSIDE the root are skipped (defense-in-depth: the
    corpus only ever contains files physically under the project's shared/).

    Perf note: per-file syscalls are minimized — `rglob('*.md')` already yields
    file entries, so we skip a redundant `is_file()`, and the (expensive on a
    slow bind mount) symlink-escape `realpath` check runs ONLY for actual
    symlinks. On Docker-Desktop/Windows bind mounts each syscall is a VM
    round-trip, so this matters; on a native/page-cached FS it's noise.
    """
    root_resolved = root.resolve()
    root_str = str(root_resolved)
    out: list[CorpusFile] = []
    for path in sorted(root_resolved.rglob("*.md")):
        try:
            # Only pay the realpath round-trip when the entry is a symlink;
            # reject one that escapes the root.
            if path.is_symlink():
                real = os.path.realpath(path)
                if not (real == root_str or real.startswith(root_str + os.sep)):
                    continue
            text = path.read_text(encoding="utf-8", errors="replace")
            mtime = path.stat().st_mtime
        except OSError:
            # Transient race / permission blip / not a regular file — skip it.
            continue
        rel = path.relative_to(root_resolved).as_posix()
        out.append(CorpusFile(rel_path=rel, mtime=mtime, text=text))
    return out


# =============================================================================
# Chunking — split a file on markdown headings
# =============================================================================


def chunk_file(rel_path: str, text: str) -> list[Chunk]:
    """Split one file into heading-delimited chunks.

    Rules:
      * A heading line (`#`..`######` + text) STARTS a new chunk and is kept as
        that chunk's first line (so a decisions.md `## <date> — <title>` entry
        is one chunk: heading + body together).
      * Lines before the first heading form a level-0 "preamble" chunk (only
        emitted if it contains non-whitespace — a file that opens straight into
        a heading has no preamble).
      * line_start / line_end are 1-based inclusive into the source file.

    Nesting is intentionally FLAT: a `###` under a `##` opens its own sibling
    chunk rather than nesting inside the parent. For a recall tool, finer-
    grained chunks rank more precisely; `scroll` reassembles surrounding context
    on demand. shortcut: flat split, no hierarchy rollup; fine for BM25 recall —
    upgrade path is a parent-heading breadcrumb on each chunk if callers want it.
    """
    lines = text.splitlines() or [""]   # floor: treat truly-empty file as one empty line
    chunks: list[Chunk] = []

    # State for the chunk currently being accumulated.
    cur_heading = ""
    cur_level = 0
    cur_start = 1               # 1-based line index where the current chunk began
    cur_lines: list[str] = []

    def _flush(end_line: int) -> None:
        # Emit the accumulated chunk unless it's an empty preamble (level 0 with
        # no non-whitespace content).
        body = "\n".join(cur_lines)
        if cur_level == 0 and not body.strip():
            return
        chunks.append(
            Chunk(
                file=rel_path,
                heading=cur_heading,
                level=cur_level,
                line_start=cur_start,
                line_end=end_line,
                text=body,
            )
        )

    for idx, line in enumerate(lines, start=1):
        m = _HEADING_RE.match(line)
        if m:
            # Close the in-progress chunk at the line BEFORE this heading.
            _flush(idx - 1)
            cur_level = len(m.group(1))
            cur_heading = m.group(2).strip()
            cur_start = idx
            cur_lines = [line]
        else:
            cur_lines.append(line)

    # Flush the trailing chunk.
    _flush(len(lines))
    return chunks


# =============================================================================
# Tokenization + BM25
# =============================================================================


def tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumeric runs, drop empties."""
    return _TOKEN_RE.findall(text.lower())


def _build_index(corpus: Corpus) -> None:
    """Populate the BM25 index fields on `corpus` in place.

    Computes, over `corpus.chunks`: per-chunk term-frequency Counters, per-chunk
    token lengths, the corpus-wide document frequency per term, and the average
    chunk length. Called once at corpus-build time; `score_chunks` reads these.
    """
    doc_tokens: list[Counter] = []
    doc_len: list[int] = []
    doc_freq: Counter = Counter()

    for chunk in corpus.chunks:
        # Index the heading text + body together (the heading carries the
        # strongest signal — e.g. a decisions.md date+title line).
        toks = tokenize(chunk.text)
        tf = Counter(toks)
        doc_tokens.append(tf)
        doc_len.append(len(toks))
        for term in tf:
            doc_freq[term] += 1

    corpus._doc_tokens = doc_tokens
    corpus._doc_len = doc_len
    corpus._doc_freq = doc_freq
    corpus._avg_len = (sum(doc_len) / len(doc_len)) if doc_len else 0.0


def _idf(doc_freq: int, n_docs: int) -> float:
    """BM25 (Robertson/Sparck-Jones) IDF with the +0.5 smoothing.

    idf = ln( 1 + (N - n + 0.5) / (n + 0.5) )

    The leading `1 +` keeps IDF non-negative even for a term that appears in
    more than half the chunks (the classic unsmoothed form can go negative and
    let a near-ubiquitous term DROP a chunk's score, which is wrong for recall).
    """
    return math.log(1.0 + (n_docs - doc_freq + 0.5) / (doc_freq + 0.5))


def score_chunks(corpus: Corpus, query: str, *, limit: int = 10) -> list[ScoredChunk]:
    """Score every chunk against `query` with BM25; return the top `limit`.

    Pure read over the precomputed index. Chunks with a score <= 0 (no query
    term present) are dropped. Stable ordering: score DESC, then (file,
    line_start) ASC so ties are deterministic.
    """
    q_terms = tokenize(query)
    if not q_terms or not corpus.chunks:
        return []

    n_docs = len(corpus.chunks)
    avg_len = corpus._avg_len or 1.0
    # Pre-compute IDF per unique query term once.
    idf_cache: dict[str, float] = {}
    for term in set(q_terms):
        df = corpus._doc_freq.get(term, 0)
        idf_cache[term] = _idf(df, n_docs) if df > 0 else 0.0

    scored: list[ScoredChunk] = []
    for i, chunk in enumerate(corpus.chunks):
        tf_counter = corpus._doc_tokens[i]
        dl = corpus._doc_len[i]
        denom_norm = BM25_K1 * (1.0 - BM25_B + BM25_B * (dl / avg_len))
        score = 0.0
        for term in idf_cache:
            tf = tf_counter.get(term, 0)
            if tf == 0:
                continue
            idf = idf_cache[term]
            if idf <= 0.0:
                continue
            score += idf * (tf * (BM25_K1 + 1.0)) / (tf + denom_norm)
        if score > 0.0:
            scored.append(ScoredChunk(chunk=chunk, score=score))

    scored.sort(key=lambda sc: (-sc.score, sc.chunk.file, sc.chunk.line_start))
    return scored[:limit]


# =============================================================================
# Corpus build + module-level cache
# =============================================================================


def build_corpus(root: Path) -> Corpus:
    """Load + chunk + index the corpus rooted at `root` (no caching)."""
    files = load_corpus(root)
    chunks: list[Chunk] = []
    for f in files:
        chunks.extend(chunk_file(f.rel_path, f.text))
    max_mtime = max((f.mtime for f in files), default=0.0)
    corpus = Corpus(
        root=root.resolve().as_posix(),
        files=files,
        chunks=chunks,
        max_mtime=max_mtime,
    )
    _build_index(corpus)
    return corpus


def build_corpus_from_text(rel_path: str, text: str) -> Corpus:
    """Build a one-file in-memory corpus from raw text (no FS) — test/util hook.

    Lets unit tests exercise chunking + BM25 on a controlled document without
    writing to disk. The synthetic file gets mtime=0.0 and a sentinel root.
    """
    files = [CorpusFile(rel_path=rel_path, mtime=0.0, text=text)]
    chunks = chunk_file(rel_path, text)
    corpus = Corpus(root="<memory>", files=files, chunks=chunks, max_mtime=0.0)
    _build_index(corpus)
    return corpus


# shortcut: a per-request rebuild is acceptable for the agent-teams corpus
# (<100 files; ~70ms of actual chunk+index work). This tiny cache keyed by the
# resolved root skips the re-read+re-chunk when nothing on disk changed.
# Upgrade path: if a corpus ever grows past a few thousand files, replace this
# with a persisted inverted index (e.g. SQLite FTS) refreshed on a file-watch.
#
# Two-tier freshness so the WARM path is fast even on a slow bind mount:
#   * Within FRESHNESS_TTL_S of the last freshness check, trust the cache with
#     NO filesystem walk at all (the mtime probe is itself a full rglob+stat,
#     which costs ~300ms on Docker-Desktop/Windows mounts — paying it on every
#     query would defeat the cache).
#   * After the TTL, re-probe the newest *.md mtime; rebuild only if it moved.
# So an edit is reflected within at most FRESHNESS_TTL_S; back-to-back queries
# in a burst pay the FS walk at most once.
FRESHNESS_TTL_S: float = 5.0


@dataclass
class _CacheEntry:
    corpus: Corpus
    checked_at: float          # monotonic time of the last freshness validation
    probe: tuple[int, float]   # (file_count, max_mtime) at last build/check


_CORPUS_CACHE: dict[str, _CacheEntry] = {}


def _probe_freshness(root: Path) -> tuple[int, float]:
    """Freshness probe: (count, newest_mtime) of *.md files under root.

    Returns both the file count and the newest mtime so the cache detects ANY
    of: an edit (mtime moves), a new file added (count up), or a file deleted
    (count down — mtime-only would miss a deletion of a non-newest file).
    Walks but only stats — does NOT read file bodies.
    """
    count = 0
    newest = 0.0
    root_resolved = root.resolve()
    for path in root_resolved.rglob("*.md"):
        try:
            mt = path.stat().st_mtime
        except OSError:
            continue
        count += 1
        if mt > newest:
            newest = mt
    return count, newest


def get_corpus(root: Path) -> Corpus:
    """Return a built corpus for `root`, served from the TTL+mtime cache.

    Fast path: a cache entry validated within FRESHNESS_TTL_S is returned with
    no FS access. Slow path: re-probe (file_count, newest_mtime); rebuild only
    if EITHER changed — detecting edits (mtime moves), new files (count up), or
    deletions of non-newest files (count down, missed by mtime-only).
    Any add/edit/delete is reflected within at most FRESHNESS_TTL_S.
    """
    root_resolved = root.resolve()
    key_root = root_resolved.as_posix()
    now = time.monotonic()

    entry = _CORPUS_CACHE.get(key_root)
    if entry is not None and (now - entry.checked_at) < FRESHNESS_TTL_S:
        return entry.corpus

    probe = _probe_freshness(root_resolved)
    if entry is not None and entry.probe == probe:
        # Unchanged on disk — keep the built corpus, just reset the freshness clock.
        entry.checked_at = now
        entry.probe = probe
        return entry.corpus

    corpus = build_corpus(root_resolved)
    _CORPUS_CACHE[key_root] = _CacheEntry(corpus=corpus, checked_at=now, probe=probe)
    return corpus


def clear_cache() -> None:
    """Drop the module-level corpus cache (test hook)."""
    _CORPUS_CACHE.clear()


# =============================================================================
# Snippet rendering (discovery mode)
# =============================================================================


def make_snippet(chunk_text: str, query: str, *, radius: int = SNIPPET_RADIUS) -> str:
    """Excerpt a short window around the strongest query-term hit in the chunk.

    Finds the earliest position of any query term (case-insensitive) and returns
    `radius` chars on each side, collapsing internal whitespace to single spaces
    and adding ellipses when truncated. Falls back to the chunk head when no
    term matches (shouldn't happen for a scored result, but keeps it total).
    """
    flat = re.sub(r"\s+", " ", chunk_text).strip()
    low = flat.lower()
    q_terms = tokenize(query)
    hit = -1
    for term in q_terms:
        pos = low.find(term)
        if pos != -1 and (hit == -1 or pos < hit):
            hit = pos
    if hit == -1:
        excerpt = flat[: radius * 2]
        return excerpt + ("…" if len(flat) > radius * 2 else "")

    start = max(0, hit - radius)
    end = min(len(flat), hit + radius)
    excerpt = flat[start:end]
    if start > 0:
        excerpt = "…" + excerpt
    if end < len(flat):
        excerpt = excerpt + "…"
    return excerpt


# =============================================================================
# Mode helpers (thin orchestration the router calls — kept here so the router
# stays I/O-glue only and the logic is unit-testable)
# =============================================================================


def run_discovery(root: Path, query: str, *, limit: int = 10) -> dict:
    """`discovery` mode: top-`limit` BM25 chunks for `query`.

    Returns a plain dict matching the discovery response schema (the router
    wraps it). Includes `elapsed_ms` measured around the score (corpus build is
    cache-amortized) and a `corpus` size summary.
    """
    start = time.perf_counter()
    corpus = get_corpus(root)
    ranked = score_chunks(corpus, query, limit=limit)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    results = [
        {
            "file": sc.chunk.file,
            "heading": sc.chunk.heading,
            "line_start": sc.chunk.line_start,
            "line_end": sc.chunk.line_end,
            "snippet": make_snippet(sc.chunk.text, query),
            "score": round(sc.score, 4),
        }
        for sc in ranked
    ]
    return {
        "mode": "discovery",
        "query": query,
        "results": results,
        "elapsed_ms": round(elapsed_ms, 2),
        "corpus": {"files": len(corpus.files), "chunks": len(corpus.chunks)},
    }


def run_scroll(root: Path, file: str, *, line: int = 1, window: int = 40) -> dict:
    """`scroll` mode: a window of `window` lines starting at `line` within `file`.

    Path-traversal-guarded via `safe_resolve_in_root` (ValueError -> router 400).
    Raises FileNotFoundError when the (in-root) file doesn't exist -> router 404.
    `prev_line`/`next_line` are pagination cursors (None at the ends); `eof` is
    True when the window reached the last line.
    """
    target = safe_resolve_in_root(root, file)
    if not target.is_file():
        raise FileNotFoundError(file)

    text = target.read_text(encoding="utf-8", errors="replace")
    all_lines = text.splitlines() or [""]   # floor: treat truly-empty file as one empty line
    total = len(all_lines)

    start = max(1, line)
    if start > total:
        start = total
    end = min(total, start + window - 1)

    # Slice is 1-based inclusive -> 0-based for Python.
    window_lines = all_lines[start - 1 : end]

    prev_line = max(1, start - window) if start > 1 else None
    next_line = end + 1 if end < total else None

    return {
        "mode": "scroll",
        "file": safe_rel(root, target),
        "line_start": start,
        "line_end": end,
        "lines": window_lines,
        "prev_line": prev_line,
        "next_line": next_line,
        "eof": end >= total,
    }


def file_headings(text: str) -> list[dict]:
    """Heading tree for one file: [{level, text, line}] in document order."""
    out: list[dict] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        m = _HEADING_RE.match(line)
        if m:
            out.append({"level": len(m.group(1)), "text": m.group(2).strip(), "line": idx})
    return out


def run_browse(root: Path, file: str | None = None) -> dict:
    """`browse` mode: heading tree for one `file`, or the whole-corpus structure.

    With `file` -> just that file's headings (path-guarded; 404 if missing).
    Without -> every file's `{path, bytes, headings}` plus corpus totals.
    """
    corpus = get_corpus(root)

    if file is not None:
        target = safe_resolve_in_root(root, file)
        if not target.is_file():
            raise FileNotFoundError(file)
        rel = safe_rel(root, target)
        # Prefer the already-loaded corpus text; fall back to disk on a miss.
        corpus_file = next((f for f in corpus.files if f.rel_path == rel), None)
        text = corpus_file.text if corpus_file is not None else target.read_text(
            encoding="utf-8", errors="replace"
        )
        return {
            "mode": "browse",
            "files": [
                {
                    "path": rel,
                    "bytes": len(text.encode("utf-8")),
                    "headings": file_headings(text),
                }
            ],
            "totals": {"files": 1, "chunks": sum(1 for c in corpus.chunks if c.file == rel)},
        }

    files_out: list[dict] = []
    for f in corpus.files:
        files_out.append(
            {
                "path": f.rel_path,
                "bytes": len(f.text.encode("utf-8")),
                "headings": file_headings(f.text),
            }
        )
    return {
        "mode": "browse",
        "files": files_out,
        "totals": {"files": len(corpus.files), "chunks": len(corpus.chunks)},
    }


def safe_rel(root: Path, target: Path) -> str:
    """POSIX rel-path of an already-confined `target` under `root`."""
    return target.resolve().relative_to(root.resolve()).as_posix()
