"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { listTasks, type TaskRead } from "@/lib/api";
import { TaskStatus, type TaskStatusValue } from "@/lib/constants";
import { extractErrorMessage } from "@/lib/errors";
import { normalizeDateOnly } from "@/lib/calendarDates";
import { ModalShell } from "./ModalShell";

// CalendarTaskPicker — Wave E (#11). A small searchable task picker invoked from
// a Calendar day cell's "Add existing task to this date" context-menu action.
//
// Flow: opens a modal listing the project's active tasks (CANCELLED excluded),
// filters live by id / title substring, and on pick calls `onPick(task)`. The
// PARENT (CalendarView) owns the PATCH (due_date = the target day) + optimistic
// update + revert, so this component is purely the selection surface.
//
// a11y: ModalShell provides role="dialog" + ESC + backdrop-close; the search
// input autofocuses; results are a keyboard-navigable list (Up/Down/Enter).

const STATUS_LABEL: Record<TaskStatusValue, string> = {
  [TaskStatus.TODO]: "todo",
  [TaskStatus.IN_PROGRESS]: "in progress",
  [TaskStatus.REVIEW]: "review",
  [TaskStatus.BLOCKED]: "blocked",
  [TaskStatus.DONE]: "done",
  [TaskStatus.CANCELLED]: "cancelled",
  [TaskStatus.HALTED_PENDING_USER]: "halted",
};

type Props = {
  projectId: number;
  dayKey: string; // "YYYY-MM-DD" target day
  onPick: (task: TaskRead) => void;
  onClose: () => void;
};

export function CalendarTaskPicker({
  projectId,
  dayKey,
  onPick,
  onClose,
}: Props) {
  const [query, setQuery] = useState("");
  const [tasks, setTasks] = useState<TaskRead[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeIdx, setActiveIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLUListElement | null>(null);

  // Fetch the project's tasks once on open. CANCELLED rows are excluded by the
  // BE default; we keep DONE rows (operator may want to re-date a finished task).
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    listTasks(projectId, { limit: 500 })
      .then((rows) => {
        if (cancelled) return;
        setTasks(rows);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setTasks([]);
        setError(extractErrorMessage(err, "Failed to load tasks"));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const results = useMemo(() => {
    const all = tasks ?? [];
    const q = query.trim().toLowerCase();
    const filtered =
      q === ""
        ? all
        : all.filter(
            (t) =>
              t.title.toLowerCase().includes(q) || String(t.id).includes(q),
          );
    // Stable order: process_status then id (mirrors the rest of the calendar).
    return [...filtered]
      .sort((a, b) => a.process_status - b.process_status || a.id - b.id)
      .slice(0, 50);
  }, [tasks, query]);

  // Keep the active index in range as the result set shrinks.
  useEffect(() => {
    setActiveIdx((i) => Math.min(i, Math.max(0, results.length - 1)));
  }, [results.length]);

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIdx((i) => Math.min(i + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const t = results[activeIdx];
      if (t) onPick(t);
    }
  }

  return (
    <ModalShell
      open
      onClose={onClose}
      labelledBy="calendar-task-picker-title"
      maxWidth="md"
      scrollable
      backdropProps={{ "data-calendar-task-picker": true }}
    >
      <div onKeyDown={onKeyDown}>
        <h2
          id="calendar-task-picker-title"
          className="text-sm font-semibold uppercase tracking-wide text-zinc-900 dark:text-zinc-100"
        >
          Add task to {dayKey}
        </h2>
        <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
          Pick a task to set its due date to{" "}
          <span className="font-mono">{dayKey}</span>.
        </p>

        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by id or title…"
          autoComplete="off"
          aria-label="Search tasks"
          className="mt-3 block w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500"
          data-calendar-picker-search
        />

        <div className="mt-3 max-h-72 overflow-y-auto rounded border border-zinc-100 dark:border-zinc-800">
          {loading ? (
            <p
              className="px-3 py-4 text-xs italic text-zinc-400 dark:text-zinc-500"
              role="status"
            >
              Loading tasks…
            </p>
          ) : error !== null ? (
            <p
              className="px-3 py-4 text-xs text-red-700 dark:text-red-300"
              role="alert"
            >
              {error}
            </p>
          ) : results.length === 0 ? (
            <p className="px-3 py-4 text-xs italic text-zinc-500 dark:text-zinc-400">
              No matching tasks.
            </p>
          ) : (
            <ul ref={listRef} role="listbox" aria-label="Tasks">
              {results.map((t, i) => {
                const currentDue = normalizeDateOnly(t.due_date);
                const isActive = i === activeIdx;
                return (
                  <li key={t.id} role="presentation">
                    <button
                      type="button"
                      role="option"
                      aria-selected={isActive}
                      onMouseEnter={() => setActiveIdx(i)}
                      onClick={() => onPick(t)}
                      className={`flex w-full items-center gap-2 px-3 py-2 text-left text-xs ${
                        isActive
                          ? "bg-zinc-100 dark:bg-zinc-800"
                          : "hover:bg-zinc-50 dark:hover:bg-zinc-800/50"
                      }`}
                      data-calendar-picker-option={t.id}
                    >
                      <span className="font-mono text-zinc-500 dark:text-zinc-400">
                        #{t.id}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-zinc-800 dark:text-zinc-200">
                        {t.title}
                      </span>
                      <span className="shrink-0 font-mono text-[10px] uppercase text-zinc-400 dark:text-zinc-500">
                        {STATUS_LABEL[t.process_status] ??
                          `ps${t.process_status}`}
                      </span>
                      {currentDue && (
                        <span className="shrink-0 font-mono text-[10px] text-zinc-400 dark:text-zinc-500">
                          {currentDue}
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div className="mt-4 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
            data-calendar-picker-cancel
          >
            Cancel
          </button>
        </div>
      </div>
    </ModalShell>
  );
}
