"use client";

// DatePicker — calendar-popover date input (Wave C #9). Replaces native
// <input type="date"> in NewTaskModal / AiTaskModal / TaskDetail (due_date)
// and MilestoneFormModal (start_date + target_date).
//
// VALUE CONTRACT (unchanged from the native input it replaces):
//   value: "YYYY-MM-DD" | null  — the chosen civil date, null/"" = unset.
//   onChange("YYYY-MM-DD" | null) — fires with the picked key, or null on Clear.
// Callers keep their existing dueDate/startDate/targetDate state + body shaping
// (omit/null when empty). To minimise churn at call sites that hold a string
// state initialised to "" (NewTaskModal/AiTaskModal/MilestoneFormModal), this
// accepts value as `string | null` and emits "" for the empty case when
// `emitEmptyString` is set, so the existing `dueDate !== ""` guards keep working
// verbatim. TaskDetail (number|null-style null handling) sets it false to get
// real null. Either way the wire value is identical.
//
// Reuses calendarDates.ts for ALL date math (buildMonthGrid / parse / labels /
// today) — no new date logic, no UTC-instant serialization (see that file's
// header for the civil-date rationale).
//
// Interaction (mirrors NewTaskDropdown conventions):
//   - readonly text input shows the chosen date; click/focus opens the popover.
//   - month grid (Sun-started) with prev/next month chevrons.
//   - click a day → commit + close. "Clear" affordance empties the value.
//   - Esc closes; pointerdown outside closes. dark-mode styled.

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import {
  addMonths,
  buildMonthGrid,
  currentYearMonth,
  monthLabel,
  normalizeDateOnly,
  parseMonthParam,
  todayKey,
  WEEKDAY_LABELS,
  type YearMonth,
} from "@/lib/calendarDates";

type Props = {
  // "YYYY-MM-DD" or null/"" when unset.
  value: string | null;
  // Fires with the picked key, or the empty value (see emitEmptyString).
  onChange: (value: string | null) => void;
  disabled?: boolean;
  // When true (default), the cleared/empty value is emitted as "" (string) so
  // call sites whose state is a `string` keep their `!== ""` guards. When
  // false, emits real `null` (for call sites that model the field as nullable).
  emitEmptyString?: boolean;
  // Forwarded to the underlying display input for data-* / id selectors.
  // The index signature admits arbitrary `data-*` keys (a closed
  // InputHTMLAttributes object literal would reject them — TS2353).
  inputProps?: React.InputHTMLAttributes<HTMLInputElement> & {
    [dataAttr: `data-${string}`]: unknown;
  };
};

// "YYYY-MM-DD" → the {year, month0} that should open in the grid. Falls back to
// the operator's current month when value is empty/malformed.
function monthForValue(value: string | null): YearMonth {
  const key = normalizeDateOnly(value);
  if (key) {
    // key is "YYYY-MM-DD"; parseMonthParam wants "YYYY-MM".
    const ym = parseMonthParam(key.slice(0, 7));
    if (ym) return ym;
  }
  return currentYearMonth();
}

export function DatePicker({
  value,
  onChange,
  disabled = false,
  emitEmptyString = true,
  inputProps,
}: Props) {
  const [open, setOpen] = useState(false);
  // The month currently displayed in the grid (seeded from `value`).
  const [viewMonth, setViewMonth] = useState<YearMonth>(() =>
    monthForValue(value),
  );
  const popoverId = useId();

  const wrapperRef = useRef<HTMLDivElement | null>(null);

  // Normalised committed key for highlight comparisons + display.
  const selectedKey = normalizeDateOnly(value);
  const today = todayKey();

  const grid = useMemo(() => buildMonthGrid(viewMonth), [viewMonth]);

  const emptyValue = emitEmptyString ? "" : null;

  const closePopover = useCallback(() => setOpen(false), []);

  function openPopover() {
    if (disabled) return;
    // Re-seed the visible month from the current value each open so reopening
    // after an external value change lands on the right month.
    setViewMonth(monthForValue(value));
    setOpen(true);
  }

  function pickDay(key: string) {
    onChange(key);
    closePopover();
  }

  function clear() {
    onChange(emptyValue);
    closePopover();
  }

  // Outside-pointerdown closes (mirrors NewTaskDropdown). Registered only open.
  useEffect(() => {
    if (!open) return;
    function onPointerDown(e: PointerEvent) {
      const t = e.target as Node;
      if (wrapperRef.current?.contains(t)) return;
      closePopover();
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        closePopover();
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, closePopover]);

  return (
    <div className="relative mt-1" ref={wrapperRef} data-date-picker>
      <input
        {...inputProps}
        type="text"
        readOnly
        role="combobox"
        aria-expanded={open}
        aria-controls={popoverId}
        autoComplete="off"
        value={selectedKey ?? ""}
        placeholder="YYYY-MM-DD"
        disabled={disabled}
        onFocus={openPopover}
        onClick={openPopover}
        onKeyDown={(e) => {
          if ((e.key === "Enter" || e.key === " " || e.key === "ArrowDown") && !open) {
            e.preventDefault();
            openPopover();
          }
        }}
        className="block w-full cursor-pointer rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
      />

      {open && (
        <div
          id={popoverId}
          role="dialog"
          aria-label="Choose date"
          className="absolute left-0 z-50 mt-1 w-64 rounded-md border border-zinc-200 bg-white p-2 shadow-lg dark:border-zinc-700 dark:bg-zinc-900"
          data-date-picker-popover
        >
          {/* Month header: prev / label / next */}
          <div className="flex items-center justify-between px-1">
            <button
              type="button"
              onClick={() => setViewMonth((m) => addMonths(m, -1))}
              aria-label="Previous month"
              className="rounded p-1 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 focus:outline-none focus:ring-1 focus:ring-zinc-400 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
              data-date-picker-prev
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
                <path d="M15 18l-6-6 6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
            <span
              className="text-xs font-semibold text-zinc-900 dark:text-zinc-100"
              data-date-picker-month-label
            >
              {monthLabel(viewMonth)}
            </span>
            <button
              type="button"
              onClick={() => setViewMonth((m) => addMonths(m, 1))}
              aria-label="Next month"
              className="rounded p-1 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 focus:outline-none focus:ring-1 focus:ring-zinc-400 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
              data-date-picker-next
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
                <path d="M9 18l6-6-6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          </div>

          {/* Weekday header row */}
          <div className="mt-2 grid grid-cols-7 gap-0.5">
            {WEEKDAY_LABELS.map((w) => (
              <div
                key={w}
                className="py-1 text-center text-[10px] font-medium uppercase tracking-wide text-zinc-400 dark:text-zinc-500"
              >
                {w}
              </div>
            ))}
          </div>

          {/* Day grid */}
          <div className="grid grid-cols-7 gap-0.5">
            {grid.flat().map((cell) => {
              const isSelected = selectedKey === cell.key;
              const isToday = today === cell.key;
              return (
                <button
                  key={cell.key}
                  type="button"
                  onClick={() => pickDay(cell.key)}
                  aria-label={cell.key}
                  aria-current={isToday ? "date" : undefined}
                  aria-pressed={isSelected}
                  data-date-picker-day={cell.key}
                  className={[
                    "h-7 rounded text-xs focus:outline-none focus:ring-1 focus:ring-zinc-400",
                    cell.inMonth
                      ? "text-zinc-700 dark:text-zinc-200"
                      : "text-zinc-300 dark:text-zinc-600",
                    isSelected
                      ? "bg-emerald-600 font-semibold text-white hover:bg-emerald-700 dark:bg-emerald-500 dark:hover:bg-emerald-600"
                      : "hover:bg-zinc-100 dark:hover:bg-zinc-800",
                    !isSelected && isToday
                      ? "ring-1 ring-inset ring-emerald-400 dark:ring-emerald-500"
                      : "",
                  ].join(" ")}
                >
                  {cell.day}
                </button>
              );
            })}
          </div>

          {/* Footer: Clear */}
          <div className="mt-2 flex items-center justify-between border-t border-zinc-100 pt-2 dark:border-zinc-800">
            <button
              type="button"
              onClick={clear}
              className="rounded px-2 py-1 text-xs font-medium text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 focus:outline-none dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
              data-date-picker-clear
            >
              Clear
            </button>
            <button
              type="button"
              onClick={() => pickDay(today)}
              className="rounded px-2 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-50 focus:outline-none dark:text-emerald-400 dark:hover:bg-emerald-950/40"
              data-date-picker-today
            >
              Today
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
