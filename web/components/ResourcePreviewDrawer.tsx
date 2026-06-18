"use client";

// ResourcePreviewDrawer — Kanban #1315. Right-side drawer that lazily fetches
// GET /api/resources/{id}/preview and renders:
//   * CSV/TSV  → the first-N rows as a table (preview = list of row-objects).
//   * JSON     → the parsed value pretty-printed.
//   * PDF/other→ whatever `preview` snippet the parser produced (string/blob).
//   * link     → the link metadata (scheme / host / head_status / title) read
//                straight off the already-loaded Resource (no preview call —
//                a link's preview endpoint returns only the file-stat nulls).
//
// Built as a drawer (not a modal) so it doesn't fight the upload modal's z
// layering and reads like a detail panel. ESC + backdrop close. The preview is
// fetched fresh each open (cheap; reads off stored tags, never re-reads files).

import { useEffect } from "react";

import {
  getResourcePreview,
  HttpError,
  type Resource,
  type ResourcePreview,
} from "@/lib/api";
import { useAsyncData } from "@/lib/useAsyncData";

type Props = {
  resource: Resource;
  onClose: () => void;
};

export function ResourcePreviewDrawer({ resource, onClose }: Props) {
  const isLink = resource.kind === "link";

  // ESC closes the drawer (mirror ModalShell's fresh-ref pattern minimally).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  // #2492 — preview fetch + cancel-guard via useAsyncData. Links never call the
  // preview endpoint (their metadata renders straight off the Resource), so the
  // fetcher returns null for a link; the render's `isLink` branch ignores it.
  // The 404 → custom-copy mapping is preserved by rethrowing a plain Error from
  // the fetcher so extractErrorMessage surfaces it verbatim.
  const { data: preview, loading, error } = useAsyncData<ResourcePreview | null>(
    async () => {
      if (isLink) return null;
      try {
        return await getResourcePreview(resource.id);
      } catch (err: unknown) {
        if (err instanceof HttpError && err.status === 404) {
          throw new Error("Preview not available (resource not found).");
        }
        throw err;
      }
    },
    [resource.id, isLink],
    { errorFallback: "Could not load preview" },
  );

  const title = resource.filename ?? resource.url ?? `Resource #${resource.id}`;

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-zinc-900/40 dark:bg-zinc-950/70"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      data-resource-preview-backdrop
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Preview of ${title}`}
        className="flex h-full w-full max-w-xl flex-col overflow-y-auto border-l border-zinc-200 bg-white p-4 shadow-xl dark:border-zinc-800 dark:bg-zinc-900"
        data-resource-preview-drawer
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold text-zinc-900 dark:text-zinc-100">
              {title}
            </h2>
            <p className="mt-0.5 text-[11px] uppercase tracking-wide text-zinc-400 dark:text-zinc-500">
              {resource.kind}
              {resource.content_type ? ` · ${resource.content_type}` : ""}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close preview"
            className="shrink-0 rounded border border-zinc-200 bg-white px-2 py-1 text-xs font-medium text-zinc-600 hover:border-zinc-300 hover:text-zinc-900 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
            data-resource-preview-close
          >
            Close
          </button>
        </div>

        <div className="mt-4 flex-1">
          {isLink ? (
            <LinkPreview resource={resource} />
          ) : loading ? (
            <p className="text-xs text-zinc-500 dark:text-zinc-400">
              Loading preview…
            </p>
          ) : error !== null ? (
            <p
              role="alert"
              className="text-xs text-red-700 dark:text-red-300"
              data-resource-preview-error
            >
              {error}
            </p>
          ) : preview ? (
            <FilePreview preview={preview} />
          ) : null}
        </div>
      </div>
    </div>
  );
}

function safeHref(u: string | null | undefined): string {
  return u && /^https?:\/\//i.test(u) ? u : "#";
}

function LinkPreview({ resource }: { resource: Resource }) {
  const t = resource.tags ?? {};
  const rows: Array<[string, string]> = [
    ["URL", resource.url ?? "—"],
    ["Scheme", typeof t.url_scheme === "string" ? t.url_scheme : "—"],
    ["Host", typeof t.url_host === "string" ? t.url_host : "—"],
    [
      "HEAD status",
      t.head_status == null ? "not probed" : String(t.head_status),
    ],
    ["Title", typeof t.title === "string" && t.title ? t.title : "—"],
    ["Label", resource.label ?? "—"],
  ];
  return (
    <dl className="flex flex-col gap-2 text-xs" data-resource-link-preview>
      {rows.map(([k, v]) => (
        <div key={k} className="flex flex-col gap-0.5">
          <dt className="font-medium uppercase tracking-wide text-zinc-400 dark:text-zinc-500">
            {k}
          </dt>
          <dd className="break-all text-zinc-700 dark:text-zinc-300">
            {k === "URL" && resource.url ? (
              <a
                href={safeHref(resource.url)}
                target="_blank"
                rel="noopener noreferrer"
                className="text-emerald-700 underline hover:text-emerald-800 dark:text-emerald-400 dark:hover:text-emerald-300"
              >
                {v}
              </a>
            ) : (
              v
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function FilePreview({ preview }: { preview: ResourcePreview }) {
  // Stats strip.
  const stats: Array<[string, string]> = [];
  if (preview.format_detected)
    stats.push(["format", preview.format_detected]);
  if (preview.row_count != null) stats.push(["rows", String(preview.row_count)]);
  if (preview.col_count != null) stats.push(["cols", String(preview.col_count)]);

  return (
    <div className="flex flex-col gap-3" data-resource-file-preview>
      {stats.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {stats.map(([k, v]) => (
            <span
              key={k}
              className="inline-flex items-center gap-1 rounded border border-zinc-200 bg-zinc-50 px-1.5 py-0.5 text-[10px] font-medium text-zinc-600 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-400"
            >
              <span className="uppercase tracking-wide text-zinc-400 dark:text-zinc-500">
                {k}
              </span>
              <span className="tabular-nums text-zinc-700 dark:text-zinc-300">
                {v}
              </span>
            </span>
          ))}
        </div>
      )}

      {preview.parser_unavailable ? (
        <p className="text-xs italic text-zinc-500 dark:text-zinc-500">
          No parser available for this file type — stored without a preview.
        </p>
      ) : (
        <PreviewBody preview={preview.preview} schema={preview.schema_detected} />
      )}
    </div>
  );
}

// PreviewBody — renders the `preview` value by shape:
//   * array of row-objects → an HTML table (CSV/TSV first rows).
//   * any other value      → pretty-printed JSON / text in a <pre>.
function PreviewBody({
  preview,
  schema,
}: {
  preview: unknown;
  schema: string[] | null;
}) {
  if (preview == null) {
    return (
      <p className="text-xs italic text-zinc-500 dark:text-zinc-500">
        (no preview rows captured)
      </p>
    );
  }

  const isRowArray =
    Array.isArray(preview) &&
    preview.length > 0 &&
    preview.every(
      (r) => r != null && typeof r === "object" && !Array.isArray(r),
    );

  if (isRowArray) {
    const rows = preview as Array<Record<string, unknown>>;
    // Column order: schema_detected when present, else union of row keys.
    const cols =
      schema && schema.length > 0
        ? schema
        : Array.from(
            rows.reduce<Set<string>>((acc, r) => {
              Object.keys(r).forEach((k) => acc.add(k));
              return acc;
            }, new Set<string>()),
          );
    return (
      <div className="overflow-x-auto rounded border border-zinc-200 dark:border-zinc-800">
        <table className="w-full border-collapse text-[11px]">
          <thead>
            <tr className="bg-zinc-50 dark:bg-zinc-950">
              {cols.map((c) => (
                <th
                  key={c}
                  className="whitespace-nowrap border-b border-zinc-200 px-2 py-1 text-left font-medium text-zinc-600 dark:border-zinc-800 dark:text-zinc-400"
                >
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr
                key={i}
                className="even:bg-zinc-50/50 dark:even:bg-zinc-950/40"
              >
                {cols.map((c) => (
                  <td
                    key={c}
                    className="max-w-[18rem] truncate border-b border-zinc-100 px-2 py-1 text-zinc-700 dark:border-zinc-900 dark:text-zinc-300"
                    title={cellText(r[c])}
                  >
                    {cellText(r[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  // Fallback: JSON / text snippet.
  let body: string;
  if (typeof preview === "string") {
    body = preview;
  } else {
    try {
      body = JSON.stringify(preview, null, 2);
    } catch {
      body = String(preview);
    }
  }
  return (
    <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-all rounded border border-zinc-200 bg-zinc-50 p-2 font-mono text-[10px] leading-tight text-zinc-700 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-300">
      {body}
    </pre>
  );
}

function cellText(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}
