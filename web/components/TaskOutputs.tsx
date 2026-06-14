"use client";

// TaskOutputs — Kanban #1305.
// Renders the "Outputs" section inside TaskDetail. Fetches
// GET /api/tasks/{id}/outputs on mount; shows each file with a kind-specific
// renderer and a Download button. Empty → shows a muted empty-state message.
// Chart (png/svg) and html files support click-to-expand via ModalShell.

import { useEffect, useId, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";

import {
  fetchTaskOutputBytes,
  getTaskOutputs,
  type TaskOutputEntry,
} from "@/lib/api";
import { extractErrorMessage } from "@/lib/errors";
import { ModalShell } from "./ModalShell";

type Props = {
  projectId: number;
  taskId: number;
};

// Tailwind component map for react-markdown — no @tailwindcss/typography plugin.
const mdComponents: Components = {
  h1: ({ children }) => (
    <h1 className="mt-3 text-base font-bold text-zinc-900 dark:text-zinc-100">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 className="mt-2.5 text-sm font-semibold text-zinc-800 dark:text-zinc-200">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="mt-2 text-sm font-medium text-zinc-700 dark:text-zinc-300">
      {children}
    </h3>
  ),
  p: ({ children }) => (
    <p className="mt-1 text-xs text-zinc-800 dark:text-zinc-200">{children}</p>
  ),
  ul: ({ children }) => (
    <ul className="mt-1 list-disc pl-4 text-xs text-zinc-800 dark:text-zinc-200">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="mt-1 list-decimal pl-4 text-xs text-zinc-800 dark:text-zinc-200">
      {children}
    </ol>
  ),
  li: ({ children }) => <li className="mt-0.5">{children}</li>,
  code: ({ children, className }) => {
    const isBlock = Boolean(className);
    return isBlock ? (
      <code className="block overflow-x-auto rounded bg-zinc-100 px-2 py-1.5 font-mono text-[11px] text-zinc-800 dark:bg-zinc-900 dark:text-zinc-200">
        {children}
      </code>
    ) : (
      <code className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-[11px] text-zinc-800 dark:bg-zinc-900 dark:text-zinc-200">
        {children}
      </code>
    );
  },
  pre: ({ children }) => (
    <pre className="mt-1 overflow-x-auto rounded bg-zinc-100 p-2 font-mono text-[11px] dark:bg-zinc-900">
      {children}
    </pre>
  ),
  blockquote: ({ children }) => (
    <blockquote className="mt-1 border-l-2 border-zinc-300 pl-2 text-xs italic text-zinc-600 dark:border-zinc-700 dark:text-zinc-400">
      {children}
    </blockquote>
  ),
  table: ({ children }) => (
    <div className="mt-1 overflow-x-auto">
      <table className="w-full text-xs">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border-b border-zinc-200 bg-zinc-50 px-2 py-1 text-left font-semibold text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-400">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border-b border-zinc-100 px-2 py-1 text-zinc-800 dark:border-zinc-800 dark:text-zinc-200">
      {children}
    </td>
  ),
};

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

// parseCsvNaive — v1 naive CSV: split on newlines then commas.
// Comment: does NOT handle quoted commas / multi-line fields — v1 limitation.
function parseCsvNaive(raw: string): { headers: string[]; rows: string[][]; totalDataRows: number } {
  const lines = raw.trim().split(/\r?\n/).filter((l) => l.trim().length > 0);
  if (lines.length === 0) return { headers: [], rows: [], totalDataRows: 0 };
  const headers = lines[0].split(",").map((c) => c.replace(/\r/g, "").trim());
  const dataLines = lines.slice(1);
  const rows = dataLines.slice(0, 10).map((l) => l.split(",").map((c) => c.replace(/\r/g, "").trim()));
  return { headers, rows, totalDataRows: dataLines.length };
}

// OutputRow — one file row: header + kind-specific content + Download button.
function OutputRow({
  entry,
  projectId,
  taskId,
}: {
  entry: TaskOutputEntry;
  projectId: number;
  taskId: number;
}) {
  // Loaded state: blobUrl for img/download, text for text-based renderers.
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  const modalTitleId = useId();
  // Track the blob URL for cleanup (revoke on unmount to avoid memory leaks).
  const blobUrlRef = useRef<string | null>(null);

  // v1 limitation: every listed row fetches its bytes eagerly on mount with no
  // concurrency cap or lazy-load. Fan-out is bounded because the BE caps the
  // listing at 50 entries (MAX_OUTPUT_FILES), so at most 50 parallel fetches.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setFetchError(null);
    setBlobUrl(null);
    setText(null);

    fetchTaskOutputBytes(projectId, taskId, entry.filename)
      .then(async (blob) => {
        if (cancelled) return;
        // Create download blob URL (used by both download button and img/iframe).
        const url = URL.createObjectURL(blob);
        blobUrlRef.current = url;
        setBlobUrl(url);

        // For text-based kinds, also decode as text for the content renderer.
        if (
          entry.kind === "doc" ||
          entry.kind === "text" ||
          entry.kind === "export" ||
          (entry.kind === "chart" && entry.filename.toLowerCase().endsWith(".html"))
        ) {
          const t = await blob.text();
          if (!cancelled) setText(t);
        }
        if (!cancelled) setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setFetchError(extractErrorMessage(err, "Failed to load"));
        setLoading(false);
      });

    return () => {
      cancelled = true;
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current);
        blobUrlRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- entry.* fields are the logical deps; fetch helper is module-stable. #1305
  }, [projectId, taskId, entry.filename, entry.kind]);

  function renderContent() {
    if (loading) {
      return (
        <p className="text-xs italic text-zinc-400 dark:text-zinc-500">Loading…</p>
      );
    }
    if (fetchError) {
      return (
        <p className="text-xs text-red-600 dark:text-red-400">{fetchError}</p>
      );
    }

    // chart: png / svg — inline img preview + click to expand
    if (entry.kind === "chart" && !entry.filename.toLowerCase().endsWith(".html")) {
      return (
        <>
          {blobUrl && (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={blobUrl}
              alt={entry.filename}
              onClick={() => setModalOpen(true)}
              className="max-h-32 max-w-full cursor-pointer rounded border border-zinc-200 object-contain dark:border-zinc-700"
            />
          )}
          <ModalShell
            open={modalOpen}
            onClose={() => setModalOpen(false)}
            labelledBy={modalTitleId}
            maxWidth="lg"
            scrollable
          >
            <h2
              id={modalTitleId}
              className="mb-2 text-sm font-semibold text-zinc-900 dark:text-zinc-100"
            >
              {entry.filename}
            </h2>
            {blobUrl && (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={blobUrl}
                alt={entry.filename}
                className="max-h-[80vh] max-w-full rounded object-contain"
              />
            )}
          </ModalShell>
        </>
      );
    }

    // chart: html — Preview button opens modal with sandboxed iframe
    if (entry.kind === "chart" && entry.filename.toLowerCase().endsWith(".html")) {
      return (
        <>
          <button
            type="button"
            onClick={() => setModalOpen(true)}
            className="self-start rounded border border-zinc-200 bg-white px-2 py-0.5 text-xs font-medium text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-600"
          >
            Preview
          </button>
          <ModalShell
            open={modalOpen}
            onClose={() => setModalOpen(false)}
            labelledBy={modalTitleId}
            maxWidth="lg"
            scrollable
          >
            <h2
              id={modalTitleId}
              className="mb-2 text-sm font-semibold text-zinc-900 dark:text-zinc-100"
            >
              {entry.filename}
            </h2>
            {text !== null && (
              // NEVER allow-same-origin — opaque-origin isolation per research-1305.md §3
              <iframe
                sandbox="allow-scripts"
                srcDoc={text}
                className="h-[70vh] w-full rounded border border-zinc-200 dark:border-zinc-700"
                title={entry.filename}
              />
            )}
          </ModalShell>
        </>
      );
    }

    // doc: markdown — rendered via react-markdown with Tailwind components.
    // react-markdown renders raw HTML as ESCAPED text by default - never add rehype-raw here without rehype-sanitize (#1305 security review).
    if (entry.kind === "doc" && text !== null) {
      return (
        <div className="mt-1 rounded border border-zinc-100 bg-zinc-50 px-3 py-2 dark:border-zinc-800 dark:bg-zinc-950/40">
          <ReactMarkdown components={mdComponents}>{text}</ReactMarkdown>
        </div>
      );
    }

    // export: csv — naive table (first 10 rows)
    if (entry.kind === "export" && entry.filename.toLowerCase().endsWith(".csv") && text !== null) {
      const { headers, rows, totalDataRows } = parseCsvNaive(text);
      return (
        <div className="mt-1 overflow-x-auto rounded border border-zinc-100 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950/40">
          <table className="w-full text-xs">
            <thead>
              <tr>
                {headers.map((h, i) => (
                  <th
                    key={i}
                    className="border-b border-zinc-200 px-2 py-1 text-left font-semibold text-zinc-600 dark:border-zinc-700 dark:text-zinc-400"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, ri) => (
                <tr
                  key={ri}
                  className="odd:bg-white even:bg-zinc-50 dark:odd:bg-transparent dark:even:bg-zinc-950/20"
                >
                  {row.map((cell, ci) => (
                    <td
                      key={ci}
                      className="px-2 py-1 text-zinc-800 dark:text-zinc-200"
                    >
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {totalDataRows > 10 && (
            <p className="px-2 py-1 text-[11px] text-zinc-500 dark:text-zinc-400">
              Showing first 10 of {totalDataRows} rows
            </p>
          )}
        </div>
      );
    }

    // export: json — pretty-print in scrollable pre
    if (entry.kind === "export" && entry.filename.toLowerCase().endsWith(".json") && text !== null) {
      let pretty = text;
      try {
        pretty = JSON.stringify(JSON.parse(text), null, 2);
      } catch {
        // Leave as-is if parse fails.
      }
      return (
        <pre className="mt-1 max-h-48 overflow-auto rounded border border-zinc-100 bg-zinc-50 px-2 py-1.5 font-mono text-[11px] text-zinc-800 dark:border-zinc-800 dark:bg-zinc-950/40 dark:text-zinc-200">
          {pretty}
        </pre>
      );
    }

    // text — scrollable pre
    if (entry.kind === "text" && text !== null) {
      return (
        <pre className="mt-1 max-h-48 overflow-auto rounded border border-zinc-100 bg-zinc-50 px-2 py-1.5 font-mono text-[11px] text-zinc-800 dark:border-zinc-800 dark:bg-zinc-950/40 dark:text-zinc-200">
          {text}
        </pre>
      );
    }

    return null;
  }

  return (
    <div
      data-output-row
      data-output-kind={entry.kind}
      className="flex flex-col gap-1.5 rounded border border-zinc-100 bg-white p-2 dark:border-zinc-800 dark:bg-zinc-900/40"
    >
      {/* Row header: filename + size + kind chip + Download */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex-1 truncate font-mono text-xs text-zinc-800 dark:text-zinc-200">
          {entry.filename}
        </span>
        <span className="shrink-0 text-[11px] text-zinc-500 dark:text-zinc-400">
          {formatBytes(entry.size)}
        </span>
        <span className="shrink-0 rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
          {entry.kind}
        </span>
        {/* Download: blob URL + <a download> — avoids ?download=1 round-trip */}
        {blobUrl ? (
          <a
            href={blobUrl}
            download={entry.filename}
            className="shrink-0 rounded border border-zinc-200 bg-white px-2 py-0.5 text-xs font-medium text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-600"
          >
            Download
          </a>
        ) : (
          <span className="shrink-0 rounded border border-zinc-100 px-2 py-0.5 text-xs text-zinc-400 dark:border-zinc-800">
            Download
          </span>
        )}
      </div>
      {renderContent()}
    </div>
  );
}

// TaskOutputs — the section component mounted inside TaskDetail (#1305).
export function TaskOutputs({ projectId, taskId }: Props) {
  const [entries, setEntries] = useState<TaskOutputEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setEntries(null);
    setError(null);
    getTaskOutputs(projectId, taskId)
      .then((data) => {
        if (!cancelled) setEntries(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(extractErrorMessage(err, "Failed to load outputs"));
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, taskId]);

  return (
    <section className="flex flex-col gap-2" data-outputs-section>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        Outputs
      </h3>

      {entries === null && error === null && (
        <p className="text-xs italic text-zinc-400 dark:text-zinc-500">…</p>
      )}

      {error !== null && (
        <p className="text-xs text-red-600 dark:text-red-400">{error}</p>
      )}

      {entries !== null && entries.length === 0 && (
        <p className="text-xs italic text-zinc-500 dark:text-zinc-400">
          No outputs yet — task may still be running
        </p>
      )}

      {entries !== null && entries.length > 0 && (
        <div className="flex flex-col gap-2">
          {entries.map((entry) => (
            <OutputRow
              key={entry.filename}
              entry={entry}
              projectId={projectId}
              taskId={taskId}
            />
          ))}
        </div>
      )}
    </section>
  );
}
