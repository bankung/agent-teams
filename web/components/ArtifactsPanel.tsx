"use client";

// ArtifactsPanel — Kanban #2558 (FE phase). Project-scoped list of every
// task-result file, backed by GET /api/projects/{id}/outputs (cross-task
// aggregate; sorted mtime DESC server-side).
//
// Shape mirrors SessionCostPanel's fetch-page-1 + "Load more" append pattern
// (offset pagination, hasMore = returned rows === limit), NOT ResourcesPanel's
// lazy-collapsed-footer pattern — this is a full-page view (always visible),
// so it fetches on mount via useAsyncData rather than gating on an expand click.
//
// Row = filename + kind/mime chip (ResourcesPanel/TaskOutputs chip idiom) +
// mtime + owning-task link (`/p/<name>?task=<id>` — the SAME cross-page
// deep-link href GanttView/AgentDetail already use; Board.tsx's #1001
// follow-up deep-link handler reads `?task=` on load and scrolls+highlights
// the card) + a Download link resolved against the API origin (download_url
// is BE-relative; a bare relative href would navigate against the WEB origin
// instead — see apiOrigin() in lib/api.ts).

import Link from "next/link";
import { useCallback, useState } from "react";

import {
  apiOrigin,
  listProjectOutputs,
  type ProjectOutputItem,
  type ProjectOutputsResponse,
} from "@/lib/api";
import { extractErrorMessage } from "@/lib/errors";
import { useAsyncData } from "@/lib/useAsyncData";
import { formatBytes } from "@/components/ResourceUploadModal";

type Props = {
  projectId: number;
  projectName: string;
};

// PAGE_SIZE mirrors the BE default (routers/task_outputs.py Query(default=50))
// and the ResourcesPanel Load-more convention (#2125): a returned page shorter
// than PAGE_SIZE signals "no more rows."
const PAGE_SIZE = 50;

// Full ISO timestamp → "Jun 25, 2026, 14:03" (local tz). Same format + reasoning
// as SessionCostPanel.fmtTimestamp — mtime is a real instant, not a date-only
// billing boundary, so local-tz rendering is correct. No shared house-wide date
// helper exists (grep confirmed); each component defines its own local fmt*.
function fmtMtime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function ArtifactRow({
  item,
  projectName,
}: {
  item: ProjectOutputItem;
  projectName: string;
}) {
  const taskHref = `/p/${encodeURIComponent(projectName)}?task=${item.task_id}`;
  const downloadHref = `${apiOrigin()}${item.download_url}`;

  return (
    <li
      className="flex flex-col gap-1.5 rounded border border-zinc-100 bg-white p-2.5 dark:border-zinc-800 dark:bg-zinc-900/40"
      data-artifact-row={`${item.task_id}-${item.filename}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span
          className="min-w-0 flex-1 truncate font-mono text-xs text-zinc-800 dark:text-zinc-200"
          title={item.filename}
        >
          {item.filename}
        </span>
        <span className="shrink-0 rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
          {item.kind}
        </span>
        <span className="shrink-0 text-[11px] text-zinc-500 dark:text-zinc-400">
          {formatBytes(item.size)}
        </span>
        <a
          href={downloadHref}
          className="shrink-0 rounded border border-zinc-200 bg-white px-2 py-0.5 text-xs font-medium text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-600"
          data-artifact-download
        >
          Download
        </a>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-zinc-500 dark:text-zinc-400">
        <span
          className="tabular-nums"
          title={item.mtime}
          data-artifact-mtime
        >
          {fmtMtime(item.mtime)}
        </span>
        <span aria-hidden>·</span>
        {item.task_title !== null ? (
          <Link
            href={taskHref}
            className="truncate text-zinc-600 hover:underline dark:text-zinc-300"
            data-artifact-task-link
          >
            #{item.task_id} {item.task_title}
          </Link>
        ) : (
          <span className="truncate" data-artifact-task-deleted>
            #{item.task_id} (deleted task)
          </span>
        )}
        {item.role !== null && (
          <>
            <span aria-hidden>·</span>
            <span className="truncate">{item.role}</span>
          </>
        )}
      </div>
    </li>
  );
}

export function ArtifactsPanel({ projectId, projectName }: Props) {
  // #2492 — initial fetch-on-mount goes through the shared useAsyncData hook
  // (the codebase's ONE centralized, audited fetch-in-effect site) rather than
  // a hand-rolled effect; a direct `useEffect(() => { void refresh() }, [...])`
  // trips the eslint react-hooks/set-state-in-effect ERROR tier. `setData` is
  // reused below for the Load-more append (an event-handler mutation, not an
  // effect, so it needs no disable).
  const {
    data: page,
    loading,
    error,
    setData,
  } = useAsyncData<ProjectOutputsResponse>(
    () => listProjectOutputs(projectId, { limit: PAGE_SIZE }),
    [projectId],
    { errorFallback: "Could not load artifacts" },
  );
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);

  const items = page?.items ?? [];
  const total = page?.total ?? 0;
  // hasMore driven by items.length vs total (AC-specified), not the
  // returned-page-length heuristic other panels use — the BE returns an exact
  // `total` up front, so items.length < total is the precise signal.
  const hasMore = page !== null && items.length < total;

  const handleLoadMore = useCallback(async () => {
    if (!hasMore || loadingMore) return;
    setLoadingMore(true);
    setLoadMoreError(null);
    try {
      const next = await listProjectOutputs(projectId, {
        limit: PAGE_SIZE,
        offset: items.length,
      });
      setData((prev) => {
        const prevItems = prev?.items ?? [];
        // De-dup guard (mirrors ResourcesPanel.handleLoadMore): a row can
        // recur across pages if mtimes tie and a new file lands between
        // fetches, shifting the offset window.
        const seen = new Set(prevItems.map((r) => `${r.task_id}:${r.filename}`));
        const novel = next.items.filter(
          (r) => !seen.has(`${r.task_id}:${r.filename}`),
        );
        return { items: [...prevItems, ...novel], total: next.total };
      });
    } catch (err: unknown) {
      setLoadMoreError(extractErrorMessage(err, "Could not load more artifacts"));
    } finally {
      setLoadingMore(false);
    }
  }, [hasMore, loadingMore, projectId, items.length, setData]);

  return (
    <section data-artifacts-panel>
      {loading && items.length === 0 && error === null && (
        <p className="py-6 text-center text-xs text-zinc-400 dark:text-zinc-500">
          Loading artifacts…
        </p>
      )}

      {(error !== null || loadMoreError !== null) && (
        <p
          role="alert"
          className="mb-2 text-xs text-red-700 dark:text-red-300"
          data-artifacts-error
        >
          {error ?? loadMoreError}
        </p>
      )}

      {!loading && error === null && items.length === 0 && (
        <p
          className="py-6 text-center text-xs italic text-zinc-400 dark:text-zinc-500"
          data-artifacts-empty
        >
          No task outputs yet
        </p>
      )}

      {items.length > 0 && (
        <>
          <ul className="flex flex-col gap-2">
            {items.map((item) => (
              <ArtifactRow
                key={`${item.task_id}:${item.filename}`}
                item={item}
                projectName={projectName}
              />
            ))}
          </ul>
          {hasMore && (
            <div className="mt-3 flex justify-center">
              <button
                type="button"
                onClick={() => void handleLoadMore()}
                disabled={loadingMore}
                className="rounded border border-zinc-200 bg-white px-3 py-1.5 text-[11px] font-medium text-zinc-600 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
                data-artifacts-load-more
              >
                {loadingMore ? "Loading…" : "Load more"}
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
