"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { listProjects, type ProjectRead } from "@/lib/api";
import { extractErrorMessage } from "@/lib/errors";

type Props = { current: string };

export function ProjectSwitcher({ current }: Props) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [projects, setProjects] = useState<ProjectRead[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const wrapperRef = useRef<HTMLDivElement | null>(null);

  // Lazy fetch on first open — avoid a network hit on every board mount.
  useEffect(() => {
    if (!open || projects.length > 0 || loadError !== null) return;
    let cancelled = false;
    listProjects({ status: 1 })
      .then((rows) => {
        if (!cancelled) setProjects(rows);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setLoadError(extractErrorMessage(err, "Failed to load"));
      });
    return () => {
      cancelled = true;
    };
  }, [open, projects.length, loadError]);

  // Close on outside click + Escape.
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

  const onSelect = (name: string) => {
    setOpen(false);
    if (name === current) return;
    router.push(`/p/${name}`);
  };

  // Clear loadError on every (re)open — without this, a single failed fetch
  // latches the dropdown into the error state until full page reload (#760).
  // The `projects.length > 0` guard in the lazy-fetch effect preserves the
  // happy-path no-refetch behavior; only the error latch is removed.
  const onToggle = () => {
    setLoadError(null);
    setOpen((v) => !v);
  };

  return (
    <div ref={wrapperRef} className="relative inline-block" data-project-switcher>
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={onToggle}
        className="inline-flex items-center gap-1.5 rounded border border-zinc-200 bg-white px-2 py-1 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
      >
        <span className="text-zinc-900 dark:text-zinc-100 normal-case tracking-normal text-sm font-semibold">
          {current}
        </span>
        <span aria-hidden className="text-zinc-400 dark:text-zinc-500">
          ▾
        </span>
      </button>
      {open && (
        <div
          role="listbox"
          className="absolute left-0 top-full z-20 mt-1 w-64 rounded border border-zinc-200 bg-white py-1 dark:border-zinc-800 dark:bg-zinc-900"
          data-project-switcher-panel
        >
          {loadError !== null && (
            <div className="px-3 py-2 text-xs text-red-700 dark:text-red-300">{loadError}</div>
          )}
          {loadError === null && projects.length === 0 && (
            <div className="px-3 py-2 text-xs uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              Loading…
            </div>
          )}
          {projects.map((p) => {
            const isCurrent = p.name === current;
            return (
              <button
                key={p.id}
                type="button"
                role="option"
                aria-selected={isCurrent}
                onClick={() => onSelect(p.name)}
                className={`flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left text-sm hover:bg-zinc-50 dark:hover:bg-zinc-800/50 ${
                  isCurrent
                    ? "text-zinc-900 dark:text-zinc-100 font-medium"
                    : "text-zinc-600 dark:text-zinc-400"
                }`}
                data-project-name={p.name}
              >
                <span className="truncate">{p.name}</span>
                <span className="inline-flex shrink-0 items-center rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
                  {p.team}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
