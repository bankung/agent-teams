"use client";

// ResourcesPanel — Kanban #1315. Collapsible footer on the project board page
// surfacing attached files + links from /api/projects/{id}/resources.
//
// Default COLLAPSED; the chevron toggles. Expand/collapse persists per-user via
// usePersistentState (#2491) under a stable per-project key.
//
// Open state:
//   * Lists resources newest-first (the BE orders created_at DESC).
//   * Filter chips: All / Files / Links.
//   * Sort: uploaded (DESC, default) / name / size.
//   * Key tags rendered inline as chips (size, row_count for CSV, mime).
//   * [+ Add] opens ResourceUploadModal (2 tabs: file / link).
//   * Preview per row → ResourcePreviewDrawer.
//   * Delete per row (operator-gated BE; soft-delete).
//   * Empty state with a CTA.
//
// Data flow: the list is fetched client-side on first expand (lazy — no fetch
// while collapsed) and refreshed on demand. Created rows are PREPENDED locally
// (+ a short flash) so the new resource appears without a round-trip.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  deleteResource,
  listResources,
  HttpError,
  type Resource,
  type ResourceKindValue,
} from "@/lib/api";
import { extractErrorMessage } from "@/lib/errors";
import { usePersistentState, useIsHydrated } from "@/lib/usePersistentState";
import { Icon } from "./Icon";
import { ResourcePreviewDrawer } from "./ResourcePreviewDrawer";
import { ResourceUploadModal, formatBytes } from "./ResourceUploadModal";

type KindFilter = "all" | ResourceKindValue;
type SortKey = "uploaded" | "name" | "size";

type Props = {
  projectId: number;
  // Default-collapsed footer; the storage key is derived from projectId so the
  // preference is stable per project per user.
  defaultCollapsed?: boolean;
};

const FLASH_MS = 1500;

export function ResourcesPanel({ projectId, defaultCollapsed = true }: Props) {
  const storageKey = `resources-panel:${projectId}`;

  // SSR-safe: server snapshot = !defaultCollapsed; client reads localStorage.
  const [expanded, setExpanded] = usePersistentState<boolean>(
    storageKey,
    !defaultCollapsed,
    { deserialize: (raw) => JSON.parse(raw) !== false },
  );
  // Mount gate for the chevron glyph (avoids an SSR/client glyph mismatch).
  const hydrated = useIsHydrated();

  const [resources, setResources] = useState<Resource[]>([]);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [kindFilter, setKindFilter] = useState<KindFilter>("all");
  const [sortKey, setSortKey] = useState<SortKey>("uploaded");

  const [addOpen, setAddOpen] = useState(false);
  const [previewResource, setPreviewResource] = useState<Resource | null>(null);
  const [flashId, setFlashId] = useState<number | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // cancelled ref — set to true on unmount so in-flight fetch doesn't update
  // state after the component is gone (mirrors ResourcePreviewDrawer pattern).
  const cancelledRef = useRef(false);
  useEffect(() => {
    cancelledRef.current = false;
    return () => {
      cancelledRef.current = true;
    };
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const rows = await listResources(projectId);
      if (!cancelledRef.current) {
        setResources(rows);
        setLoaded(true);
      }
    } catch (err: unknown) {
      if (!cancelledRef.current)
        setError(extractErrorMessage(err, "Could not load resources"));
    } finally {
      if (!cancelledRef.current) setLoading(false);
    }
  }, [projectId]);

  // Lazy fetch: only when expanded AND not yet loaded. Avoids a fetch while
  // the panel sits collapsed at the bottom of every board.
  //
  // #2492 — deliberately NOT migrated to useAsyncData. This is a LAZY,
  // fetch-once-then-latch machine (the `loaded` flag stops a re-expand from
  // refetching) with local list mutations (onCreated prepend, onDelete filter)
  // and its own cancelledRef. useAsyncData(fetcher, deps) fetches eagerly on
  // mount + on every dep change, which would (a) fetch while the panel is
  // collapsed and (b) re-fetch on every expand/collapse — both behavior
  // regressions. `refresh` already guards stale responses via cancelledRef, so
  // the warning is a false-positive here; disabled with rationale rather than
  // contorting the lazy-latch design to fit the hook.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (expanded && !loaded && !loading) void refresh();
  }, [expanded, loaded, loading, refresh]);

  useEffect(() => {
    return () => {
      if (flashTimer.current) clearTimeout(flashTimer.current);
    };
  }, []);

  function toggle() {
    setExpanded(!expanded);
  }

  const onCreated = useCallback((created: Resource) => {
    // Prepend + flash. De-dup by id in case a refresh raced ahead.
    setResources((prev) => [created, ...prev.filter((r) => r.id !== created.id)]);
    setLoaded(true);
    setFlashId(created.id);
    if (flashTimer.current) clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setFlashId(null), FLASH_MS);
  }, []);

  const onDelete = useCallback(async (resource: Resource) => {
    setDeletingId(resource.id);
    try {
      await deleteResource(resource.id);
      setResources((prev) => prev.filter((r) => r.id !== resource.id));
    } catch (err: unknown) {
      // 403 = operator gate active. Surface the BE message inline.
      const msg =
        err instanceof HttpError
          ? err.message
          : extractErrorMessage(err, "Delete failed");
      setError(msg);
    } finally {
      setDeletingId(null);
    }
  }, []);

  const counts = useMemo(() => {
    let files = 0;
    let links = 0;
    for (const r of resources) {
      if (r.kind === "file") files += 1;
      else links += 1;
    }
    return { files, links, total: resources.length };
  }, [resources]);

  const visible = useMemo(() => {
    const filtered =
      kindFilter === "all"
        ? resources
        : resources.filter((r) => r.kind === kindFilter);
    const sorted = [...filtered];
    if (sortKey === "name") {
      sorted.sort((a, b) =>
        rowName(a).localeCompare(rowName(b), undefined, { sensitivity: "base" }),
      );
    } else if (sortKey === "size") {
      sorted.sort((a, b) => (b.size_bytes ?? -1) - (a.size_bytes ?? -1));
    } else {
      // uploaded DESC — created_at ISO strings sort lexicographically.
      sorted.sort((a, b) => {
        if (a.created_at === b.created_at) return b.id - a.id;
        return a.created_at < b.created_at ? 1 : -1;
      });
    }
    return sorted;
  }, [resources, kindFilter, sortKey]);

  return (
    <section
      className="mt-3 rounded border border-zinc-200 bg-white text-sm text-zinc-700 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300"
      data-resources-panel
    >
      <button
        type="button"
        onClick={toggle}
        className="flex w-full items-center justify-between px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-zinc-600 hover:bg-zinc-50 dark:text-zinc-400 dark:hover:bg-zinc-800"
        aria-expanded={expanded}
        data-resources-toggle
      >
        <span>Resources</span>
        <span className="inline-flex items-center gap-2">
          {loaded && (
            <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] tabular-nums text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
              {counts.total}
            </span>
          )}
          <span aria-hidden className="text-zinc-400 dark:text-zinc-500">
            {/* Only reflect open/closed after hydration to avoid SSR mismatch. */}
            {hydrated && expanded ? "▾" : "▸"}
          </span>
        </span>
      </button>

      {expanded && (
        <div
          className="border-t border-zinc-200 px-3 py-3 dark:border-zinc-800"
          data-resources-body
        >
          {/* Controls row: filter + sort (left), [+ Add] (right). */}
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-1" role="group" aria-label="Filter by kind">
              <FilterChip
                active={kindFilter === "all"}
                onClick={() => setKindFilter("all")}
                label={`All (${counts.total})`}
                dataAttr="all"
              />
              <FilterChip
                active={kindFilter === "file"}
                onClick={() => setKindFilter("file")}
                label={`Files (${counts.files})`}
                dataAttr="file"
              />
              <FilterChip
                active={kindFilter === "link"}
                onClick={() => setKindFilter("link")}
                label={`Links (${counts.links})`}
                dataAttr="link"
              />
            </div>
            <label className="inline-flex items-center gap-1 text-[11px] text-zinc-500 dark:text-zinc-400">
              <span className="sr-only sm:not-sr-only">Sort</span>
              <select
                value={sortKey}
                onChange={(e) => setSortKey(e.target.value as SortKey)}
                className="rounded border border-zinc-200 bg-transparent px-1.5 py-1 text-[11px] text-zinc-600 focus:border-zinc-400 focus:outline-none min-h-[36px] sm:min-h-0 dark:border-zinc-700 dark:text-zinc-300"
                data-resources-sort
              >
                <option value="uploaded">Newest</option>
                <option value="name">Name</option>
                <option value="size">Size</option>
              </select>
            </label>
            <span className="ml-auto">
              <button
                type="button"
                onClick={() => setAddOpen(true)}
                className="inline-flex items-center gap-1.5 rounded border border-emerald-600 bg-emerald-600 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-emerald-700 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-emerald-500 dark:bg-emerald-500 dark:hover:bg-emerald-600"
                data-resources-add
              >
                <Icon name="plus" size={13} aria-hidden />
                <span>Add</span>
              </button>
            </span>
          </div>

          {error !== null && (
            <p
              role="alert"
              className="mb-2 text-xs text-red-700 dark:text-red-300"
              data-resources-error
            >
              {error}
            </p>
          )}

          {loading && !loaded ? (
            <p className="py-2 text-center text-xs text-zinc-400 dark:text-zinc-500">
              Loading resources…
            </p>
          ) : visible.length === 0 ? (
            <div
              className="flex flex-col items-center gap-2 py-6 text-center"
              data-resources-empty
            >
              <p className="text-xs text-zinc-400 dark:text-zinc-500">
                {resources.length === 0
                  ? "No resources yet — drop a file or paste a link."
                  : "No resources match this filter."}
              </p>
              {resources.length === 0 && (
                <button
                  type="button"
                  onClick={() => setAddOpen(true)}
                  className="inline-flex items-center gap-1.5 rounded border border-emerald-600 bg-emerald-600 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-emerald-700 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-emerald-500 dark:bg-emerald-500 dark:hover:bg-emerald-600"
                  data-resources-empty-add
                >
                  <Icon name="plus" size={13} aria-hidden />
                  <span>Add a resource</span>
                </button>
              )}
            </div>
          ) : (
            <ul className="flex flex-col divide-y divide-zinc-100 dark:divide-zinc-800">
              {visible.map((r) => (
                <ResourceRow
                  key={r.id}
                  resource={r}
                  flash={flashId === r.id}
                  deleting={deletingId === r.id}
                  onPreview={() => setPreviewResource(r)}
                  onDelete={() => onDelete(r)}
                />
              ))}
            </ul>
          )}
        </div>
      )}

      <ResourceUploadModal
        projectId={projectId}
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onCreated={onCreated}
      />
      {previewResource && (
        <ResourcePreviewDrawer
          resource={previewResource}
          onClose={() => setPreviewResource(null)}
        />
      )}
    </section>
  );
}

function rowName(r: Resource): string {
  return r.label ?? r.filename ?? r.url ?? `Resource #${r.id}`;
}

function FilterChip({
  active,
  onClick,
  label,
  dataAttr,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  dataAttr: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`rounded border px-2 py-1 text-[11px] font-medium transition-colors min-h-[36px] sm:min-h-0 ${
        active
          ? "border-zinc-400 bg-zinc-100 text-zinc-900 dark:border-zinc-500 dark:bg-zinc-800 dark:text-zinc-100"
          : "border-zinc-200 bg-transparent text-zinc-500 hover:text-zinc-800 dark:border-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200"
      }`}
      data-resources-filter={dataAttr}
    >
      {label}
    </button>
  );
}

function ResourceRow({
  resource,
  flash,
  deleting,
  onPreview,
  onDelete,
}: {
  resource: Resource;
  flash: boolean;
  deleting: boolean;
  onPreview: () => void;
  onDelete: () => void;
}) {
  const isLink = resource.kind === "link";
  const tags = resource.tags ?? {};
  const name = resource.label ?? resource.filename ?? resource.url ?? `#${resource.id}`;
  const safeHref = (u: string | null | undefined) =>
    u && /^https?:\/\//i.test(u) ? u : "#";

  // Inline tag chips: size (file) / mime / row_count (CSV).
  const chips: Array<{ key: string; text: string }> = [];
  if (!isLink && resource.size_bytes != null)
    chips.push({ key: "size", text: formatBytes(resource.size_bytes) });
  if (!isLink && typeof tags.format_detected === "string")
    chips.push({ key: "fmt", text: tags.format_detected });
  if (!isLink && typeof tags.row_count === "number")
    chips.push({ key: "rows", text: `${tags.row_count} rows` });
  if (!isLink && resource.content_type)
    chips.push({ key: "mime", text: resource.content_type });
  if (isLink && typeof tags.url_host === "string")
    chips.push({ key: "host", text: tags.url_host });

  // Full tag JSON revealed on demand (title attr + an expandable details).
  // Depend on resource.tags (the stable source object) — `tags` above is a
  // per-render coalesced local, which would change identity every render.
  const fullTags = useMemo(() => {
    try {
      return JSON.stringify(resource.tags ?? {}, null, 2);
    } catch {
      return "(tags could not be serialized)";
    }
  }, [resource.tags]);
  const [showTags, setShowTags] = useState(false);

  return (
    <li
      className={`py-2 transition-colors ${
        flash ? "bg-emerald-50 dark:bg-emerald-950/30" : ""
      }`}
      data-resources-row={resource.id}
    >
      <div className="flex items-start gap-2">
        <span
          aria-hidden
          className="mt-0.5 shrink-0 text-zinc-400 dark:text-zinc-500"
          title={resource.kind}
        >
          {isLink ? "🔗" : "📄"}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            {isLink && resource.url ? (
              <a
                href={safeHref(resource.url)}
                target="_blank"
                rel="noopener noreferrer"
                className="truncate text-xs font-medium text-emerald-700 hover:underline dark:text-emerald-400"
                title={resource.url}
              >
                {name}
              </a>
            ) : (
              <span
                className="truncate text-xs font-medium text-zinc-800 dark:text-zinc-200"
                title={name}
              >
                {name}
              </span>
            )}
          </div>
          {chips.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-1">
              {chips.map((c) => (
                <span
                  key={c.key}
                  className="inline-flex items-center rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-medium tabular-nums text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400"
                  data-resources-chip={c.key}
                >
                  {c.text}
                </span>
              ))}
              <button
                type="button"
                onClick={() => setShowTags((v) => !v)}
                className="rounded px-1 text-[10px] text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200"
                aria-expanded={showTags}
                data-resources-tags-toggle
              >
                {showTags ? "hide tags" : "tags"}
              </button>
            </div>
          )}
          {showTags && (
            <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded border border-zinc-200 bg-zinc-50 p-2 font-mono text-[10px] leading-tight text-zinc-600 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-400">
              {fullTags}
            </pre>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={onPreview}
            className="rounded border border-zinc-200 bg-white px-2 py-1 text-[11px] font-medium text-zinc-600 hover:border-zinc-300 hover:text-zinc-900 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
            data-resources-preview
          >
            Preview
          </button>
          <button
            type="button"
            onClick={() => {
              if (window.confirm(`Delete "${name}"? This cannot be undone.`)) {
                onDelete();
              }
            }}
            disabled={deleting}
            className="rounded border border-zinc-200 bg-white px-2 py-1 text-[11px] font-medium text-zinc-500 hover:border-red-300 hover:text-red-700 disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400 dark:hover:border-red-800 dark:hover:text-red-300"
            data-resources-delete
            aria-label={`Delete ${name}`}
          >
            {deleting ? "…" : "Delete"}
          </button>
        </div>
      </div>
    </li>
  );
}
