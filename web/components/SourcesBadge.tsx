"use client";

import { useEffect, useRef, useState } from "react";

import type { Source } from "@/lib/api";

type Props = { sources: Source[] };

// #778 — allowlist-only clickable URLs; mirrors api SourceEntry._url_shape; excludes file://
const ALLOWED_SCHEMES = ["http", "https", "ref"] as const;
const SCHEME_RE = new RegExp(`^(?:${ALLOWED_SCHEMES.join("|")})://`, "i");

function isExternal(url: string): boolean {
  return SCHEME_RE.test(url);
}

// #778 — curated source list popover; empty → renders null
export function SourcesBadge({ sources }: Props) {
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (!wrapperRef.current) return;
      if (!wrapperRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (sources.length === 0) return null;

  return (
    <div ref={wrapperRef} className="relative inline-block" data-sources-badge>
      <button
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1 rounded border border-zinc-200 bg-white px-2 py-0.5 text-xs font-medium text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
      >
        <span>Sources</span>
        <span className="tabular-nums text-zinc-500 dark:text-zinc-400">
          {sources.length}
        </span>
        <span aria-hidden className="text-zinc-400 dark:text-zinc-500">
          ▾
        </span>
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="Project sources"
          className="absolute left-0 top-full z-20 mt-1 w-80 max-w-[calc(100vw-2rem)] rounded border border-zinc-200 bg-white py-1 dark:border-zinc-800 dark:bg-zinc-900"
          data-sources-panel
        >
          {sources.map((s, i) => {
            const text = s.label && s.label.length > 0 ? s.label : s.url;
            const external = isExternal(s.url);
            return (
              <div
                key={`${s.url}-${i}`}
                className="flex items-center justify-between gap-2 px-3 py-1.5 text-sm hover:bg-zinc-50 dark:hover:bg-zinc-800/50"
              >
                {external ? (
                  <a
                    href={s.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="min-w-0 flex-1 truncate text-zinc-700 hover:text-zinc-900 hover:underline dark:text-zinc-300 dark:hover:text-zinc-100"
                    title={s.url}
                  >
                    {text}
                  </a>
                ) : (
                  <span
                    className="min-w-0 flex-1 truncate text-zinc-500 dark:text-zinc-400"
                    title={s.url}
                  >
                    {text}
                  </span>
                )}
                {s.kind && (
                  <span className="inline-flex shrink-0 items-center rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
                    {s.kind}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
