"""Pydantic response models for the shared-corpus search endpoint — Kanban #1678.

`GET /api/projects/{project_id}/shared/search?mode=...` returns one of three
shapes keyed on `mode`:

  * discovery -> DiscoveryResponse   (BM25-ranked chunks for a query)
  * scroll    -> ScrollResponse      (a line-window of one file, with cursors)
  * browse    -> BrowseResponse      (heading tree: one file or whole corpus)

Three typed models rather than a discriminated union on the wire: the router
dispatches on the `mode` query param and returns the matching model, so the
OpenAPI schema for each mode stays explicit + readable (this is a dev/recall
tool, not a light-tech consumer surface). `extra="forbid"` on every model
mirrors the `user_actions` schema posture.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# =============================================================================
# discovery
# =============================================================================


class DiscoveryHit(BaseModel):
    """One BM25-ranked chunk."""

    model_config = ConfigDict(extra="forbid")

    file: str = Field(description="Path relative to the corpus root (POSIX).")
    heading: str = Field(description="Chunk heading text (no leading '#'); '' for preamble.")
    line_start: int = Field(ge=1, description="1-based first line of the chunk in its file.")
    line_end: int = Field(ge=1, description="1-based last line of the chunk in its file.")
    snippet: str = Field(description="Short excerpt around the strongest term hit.")
    score: float = Field(ge=0.0, description="BM25 score (unbounded above; higher = better).")


class CorpusSummary(BaseModel):
    """Corpus size at query time."""

    model_config = ConfigDict(extra="forbid")

    files: int = Field(ge=0)
    chunks: int = Field(ge=0)


class DiscoveryResponse(BaseModel):
    """`mode=discovery` (default): top-`limit` chunks ranked by BM25."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["discovery"] = Field(default="discovery")
    query: str
    results: list[DiscoveryHit] = Field(default_factory=list)
    elapsed_ms: float = Field(ge=0.0, description="Search latency (cache-amortized corpus build).")
    corpus: CorpusSummary


# =============================================================================
# scroll
# =============================================================================


class ScrollResponse(BaseModel):
    """`mode=scroll`: a window of lines around a position for paginated reading."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["scroll"] = Field(default="scroll")
    file: str = Field(description="Path relative to the corpus root (POSIX).")
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    lines: list[str]
    prev_line: int | None = Field(default=None, description="Cursor for the previous window; null at top.")
    next_line: int | None = Field(default=None, description="Cursor for the next window; null at EOF.")
    eof: bool = Field(description="True when the window reached the last line of the file.")


# =============================================================================
# browse
# =============================================================================


class HeadingNode(BaseModel):
    """One heading in a file's tree."""

    model_config = ConfigDict(extra="forbid")

    level: int = Field(ge=1, le=6)
    text: str
    line: int = Field(ge=1)


class FileNode(BaseModel):
    """One file's structure entry."""

    model_config = ConfigDict(extra="forbid")

    path: str
    bytes: int = Field(ge=0)
    headings: list[HeadingNode] = Field(default_factory=list)


class BrowseTotals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: int = Field(ge=0)
    chunks: int = Field(ge=0)


class BrowseResponse(BaseModel):
    """`mode=browse`: heading tree for one file, or the whole-corpus structure."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["browse"] = Field(default="browse")
    files: list[FileNode] = Field(default_factory=list)
    totals: BrowseTotals
