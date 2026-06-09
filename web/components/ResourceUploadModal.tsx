"use client";

// ResourceUploadModal — Kanban #1315. Add a project resource via two tabs:
//   1. "Upload file"  — native HTML5 drag-drop zone + file picker. While the
//      single multipart POST is in-flight, an OPTIMISTIC staged pipeline
//      indicator cycles ('Storing file… / Detecting schema… / Counting rows… /
//      Estimating cost…') on a timer. The BE runs verify-and-tag SYNCHRONOUSLY
//      inside the POST (there is NO progress stream); the indicator resolves
//      when the 201 returns the tagged Resource. It never fakes completion.
//   2. "Add link"     — URL input + optional title/note (label).
//
// Reuses ModalShell (shared modal chrome). Native drag events only — NO new
// dependency (no react-dropzone). On success the parent prepends the new row +
// flashes it (onCreated callback).

import { useEffect, useRef, useState } from "react";

import {
  createResourceFile,
  createResourceLink,
  HttpError,
  type Resource,
} from "@/lib/api";
import { extractErrorMessage } from "@/lib/errors";
import { ModalShell } from "./ModalShell";

type Tab = "file" | "link";

// Optimistic staged-pipeline copy. Shown while the single POST is in-flight.
// The order mirrors the BE pipeline (store → verify/parse → tag → insert) so
// the operator gets an honest sense of what's happening, but the timing is
// cosmetic — the indicator resolves only when the 201 actually returns.
const PIPELINE_STAGES = [
  "Storing file…",
  "Detecting schema…",
  "Counting rows…",
  "Estimating cost…",
] as const;
const STAGE_INTERVAL_MS = 700;

type Props = {
  projectId: number;
  open: boolean;
  onClose: () => void;
  // Called with the freshly-created Resource (file or link) so the panel can
  // prepend + flash it without a full re-fetch.
  onCreated: (resource: Resource) => void;
  // Optional: pin new resources to a task (panel may be task-scoped later).
  taskId?: number;
};

export function ResourceUploadModal({
  projectId,
  open,
  onClose,
  onCreated,
  taskId,
}: Props) {
  const [tab, setTab] = useState<Tab>("file");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // File tab state.
  const [file, setFile] = useState<File | null>(null);
  const [fileLabel, setFileLabel] = useState("");
  const [dragActive, setDragActive] = useState(false);
  const [stageIndex, setStageIndex] = useState(0);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // Link tab state.
  const [url, setUrl] = useState("");
  const [linkLabel, setLinkLabel] = useState("");

  // Reset everything when the modal closes so a re-open is clean.
  useEffect(() => {
    if (open) return;
    setTab("file");
    setSubmitting(false);
    setError(null);
    setFile(null);
    setFileLabel("");
    setDragActive(false);
    setStageIndex(0);
    setUrl("");
    setLinkLabel("");
  }, [open]);

  // Cycle the optimistic pipeline stages while a file upload is in-flight.
  // Resets to 0 when submitting stops. The interval never advances past the
  // last stage (clamps) so a slow upload doesn't loop confusingly.
  useEffect(() => {
    if (!submitting || tab !== "file") {
      setStageIndex(0);
      return;
    }
    const id = setInterval(() => {
      setStageIndex((i) => Math.min(i + 1, PIPELINE_STAGES.length - 1));
    }, STAGE_INTERVAL_MS);
    return () => clearInterval(id);
  }, [submitting, tab]);

  function close() {
    if (submitting) return;
    onClose();
  }

  function pickFile(f: File | null) {
    setFile(f);
    if (error !== null) setError(null);
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragActive(false);
    if (submitting) return;
    const f = e.dataTransfer.files?.[0] ?? null;
    if (f) pickFile(f);
  }

  const urlTrimmed = url.trim();
  // Mirror the BE gate: absolute http(s) URL. A loose check before submit keeps
  // an obviously-bad URL off the network; the BE still 422s on edge cases.
  const urlValid = /^https?:\/\/.+/i.test(urlTrimmed);
  const canSubmit =
    !submitting && (tab === "file" ? file !== null : urlValid);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setError(null);
    setSubmitting(true);
    try {
      let created: Resource;
      if (tab === "file") {
        // file is non-null here (canSubmit guards it).
        created = await createResourceFile(projectId, file as File, {
          label: fileLabel.trim() || undefined,
          task_id: taskId,
        });
      } else {
        created = await createResourceLink(projectId, {
          url: urlTrimmed,
          label: linkLabel.trim() || undefined,
          task_id: taskId,
        });
      }
      onCreated(created);
      onClose();
    } catch (err: unknown) {
      if (err instanceof HttpError) {
        // 413 = over the upload cap; 403 = operator gate active. The BE detail
        // string already carries a human-readable message — surface it as-is.
        setError(err.message);
      } else {
        setError(extractErrorMessage(err, "Could not add resource"));
      }
    } finally {
      setSubmitting(false);
    }
  }

  const tabBtn = (id: Tab, text: string) => (
    <button
      type="button"
      role="tab"
      id={`resource-tab-${id}`}
      aria-selected={tab === id}
      aria-controls={`resource-panel-${id}`}
      onClick={() => {
        if (submitting) return;
        setTab(id);
        if (error !== null) setError(null);
      }}
      disabled={submitting}
      className={`flex-1 rounded px-3 py-2 text-xs font-medium uppercase tracking-wide transition-colors min-h-[44px] sm:min-h-0 disabled:opacity-50 ${
        tab === id
          ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900"
          : "bg-zinc-100 text-zinc-600 hover:text-zinc-900 dark:bg-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-200"
      }`}
      data-resource-tab={id}
    >
      {text}
    </button>
  );

  return (
    <ModalShell
      open={open}
      onClose={close}
      labelledBy="resource-upload-title"
      backdropProps={{ "data-resource-upload-modal": true }}
    >
      <form onSubmit={onSubmit}>
        <h2
          id="resource-upload-title"
          className="text-sm font-semibold uppercase tracking-wide text-zinc-900 dark:text-zinc-100"
        >
          Add resource
        </h2>
        <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
          Attach a file (CSV / JSON / PDF / …) or a link to this project.
        </p>

        {/* Tab switcher */}
        <div
          role="tablist"
          aria-label="Resource kind"
          className="mt-3 flex gap-2"
        >
          {tabBtn("file", "Upload file")}
          {tabBtn("link", "Add link")}
        </div>

        {tab === "file" ? (
          <div className="mt-3" role="tabpanel" id="resource-panel-file" aria-labelledby="resource-tab-file" data-resource-file-panel>
            {/* Native HTML5 drag-drop zone + click-to-pick. No dependency. */}
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              onDragOver={(e) => {
                e.preventDefault();
                if (!submitting) setDragActive(true);
              }}
              onDragLeave={() => setDragActive(false)}
              onDrop={onDrop}
              disabled={submitting}
              className={`flex w-full flex-col items-center justify-center gap-1 rounded border-2 border-dashed px-4 py-8 text-center text-xs transition-colors disabled:opacity-60 ${
                dragActive
                  ? "border-emerald-500 bg-emerald-50 text-emerald-700 dark:border-emerald-500 dark:bg-emerald-950/30 dark:text-emerald-300"
                  : "border-zinc-300 bg-zinc-50 text-zinc-500 hover:border-zinc-400 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-400 dark:hover:border-zinc-600"
              }`}
              data-resource-dropzone
            >
              {file ? (
                <>
                  <span className="font-medium text-zinc-800 dark:text-zinc-200">
                    {file.name}
                  </span>
                  <span className="text-[11px] text-zinc-500 dark:text-zinc-500">
                    {formatBytes(file.size)} · click to choose a different file
                  </span>
                </>
              ) : (
                <>
                  <span className="font-medium text-zinc-700 dark:text-zinc-300">
                    Drop a file here
                  </span>
                  <span className="text-[11px]">or click to browse</span>
                </>
              )}
            </button>
            <input
              ref={fileInputRef}
              type="file"
              className="sr-only"
              onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
              disabled={submitting}
              data-resource-file-input
            />

            <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
              Label{" "}
              <span className="font-normal text-zinc-400">(optional)</span>
              <input
                type="text"
                value={fileLabel}
                onChange={(e) => setFileLabel(e.target.value)}
                placeholder="e.g. Q3 sales export"
                autoComplete="off"
                disabled={submitting}
                className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500"
                data-resource-file-label
              />
            </label>

            {/* Optimistic staged pipeline indicator — only while uploading. */}
            {submitting && (
              <div
                className="mt-3 flex items-center gap-2 rounded border border-zinc-200 bg-zinc-50 px-3 py-2 text-xs text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300"
                role="status"
                aria-live="polite"
                data-resource-pipeline
              >
                <span
                  aria-hidden
                  className="h-3 w-3 animate-spin rounded-full border-2 border-zinc-300 border-t-emerald-600 dark:border-zinc-700 dark:border-t-emerald-400"
                />
                <span>{PIPELINE_STAGES[stageIndex]}</span>
              </div>
            )}
          </div>
        ) : (
          <div className="mt-3" role="tabpanel" id="resource-panel-link" aria-labelledby="resource-tab-link" data-resource-link-panel>
            <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300">
              URL <span className="text-red-600 dark:text-red-400">*</span>
              <input
                type="url"
                value={url}
                onChange={(e) => {
                  setUrl(e.target.value);
                  if (error !== null) setError(null);
                }}
                placeholder="https://example.com/doc"
                autoComplete="off"
                disabled={submitting}
                aria-invalid={url.length > 0 && !urlValid}
                className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500"
                data-resource-link-url
              />
            </label>
            <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
              Title / note{" "}
              <span className="font-normal text-zinc-400">(optional)</span>
              <input
                type="text"
                value={linkLabel}
                onChange={(e) => setLinkLabel(e.target.value)}
                placeholder="e.g. API reference"
                autoComplete="off"
                disabled={submitting}
                className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500"
                data-resource-link-label
              />
            </label>
          </div>
        )}

        {error !== null && (
          <p
            role="alert"
            className="mt-3 text-xs text-red-700 dark:text-red-300"
            data-resource-error
          >
            {error}
          </p>
        )}

        <div className="mt-4 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={close}
            disabled={submitting}
            className="rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
            data-resource-cancel
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!canSubmit}
            className="rounded border border-emerald-600 bg-emerald-600 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-emerald-700 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-emerald-500 dark:bg-emerald-500 dark:hover:bg-emerald-600"
            data-resource-submit
          >
            {submitting
              ? tab === "file"
                ? "Uploading…"
                : "Adding…"
              : tab === "file"
                ? "Upload"
                : "Add link"}
          </button>
        </div>
      </form>
    </ModalShell>
  );
}

// formatBytes — compact human-readable size. Shared with ResourcesPanel chip
// rendering via re-export so the two surfaces never drift.
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let val = bytes / 1024;
  let i = 0;
  while (val >= 1024 && i < units.length - 1) {
    val /= 1024;
    i += 1;
  }
  return `${val < 10 ? val.toFixed(1) : Math.round(val)} ${units[i]}`;
}
