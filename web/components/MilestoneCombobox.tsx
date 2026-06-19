"use client";

// MilestoneCombobox — searchable single-select for milestone assignment
// (Wave C #8). Replaces the plain milestone <select> in NewTaskModal,
// AiTaskModal, and TaskDetail with a type-to-filter dropdown.
//
// VALUE CONTRACT (unchanged from the <select> it replaces):
//   value: number | null  — milestone_id, or null for "None"/unassigned.
//   onChange(number | null) — fires with the chosen milestone_id, or null.
// Callers keep their existing milestoneId state + the same body-shaping
// (`milestone_id` omitted/null when empty); this component only swaps the
// control, never the contract.
//
// The caller owns loading `milestones` exactly as the old picker did
// (listMilestones(projectId)) and passes them in — keeping this component
// self-contained and fetch-free, mirroring ModelTierSelect.
//
// Interaction (mirrors NewTaskDropdown's conventions):
//   - text input filters by title (case-insensitive substring) as you type.
//   - dropdown lists a "None" option + matching milestones.
//   - ↑/↓ move the highlight, Enter selects it, Esc closes the popover.
//   - pointerdown outside the wrapper closes the popover.
//   - dark-mode styled; chrome matches the project's input + menu classes.
//
// Defensive: if the assigned milestone isn't in `milestones` (filtered-out
// status / fetch failure), the closed-state input still shows "#<id>" so the
// assignment stays visible — same posture as the old TaskDetail <select>.

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import type { MilestoneRead } from "@/lib/api";

type Props = {
  // Current milestone_id, or null for unassigned.
  value: number | null;
  // Fires with the new milestone_id (or null for "None").
  onChange: (milestoneId: number | null) => void;
  // The project's milestones — caller-loaded (same source as the old picker).
  milestones: MilestoneRead[];
  disabled?: boolean;
  // Forwarded to the underlying text input for data-* test selectors / id.
  // The index signature admits arbitrary `data-*` keys (a closed
  // InputHTMLAttributes object literal would reject them — TS2353).
  inputProps?: React.InputHTMLAttributes<HTMLInputElement> & {
    [dataAttr: `data-${string}`]: unknown;
  };
};

const NONE_LABEL = "None";

export function MilestoneCombobox({
  value,
  onChange,
  milestones,
  disabled = false,
  inputProps,
}: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [highlight, setHighlight] = useState(0);
  const listboxId = useId();

  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Label shown in the closed input. Resolve the selected milestone's title;
  // fall back to "#<id>" if it's not in the list (filtered/failed fetch), and
  // empty string for None so the placeholder shows.
  const selectedLabel = useMemo(() => {
    if (value === null) return "";
    const found = milestones.find((m) => m.id === value);
    return found ? found.title : `#${value}`;
  }, [value, milestones]);

  // While open, the input mirrors the live `query`. While closed, it shows the
  // selected label. This lets the user type to filter without destroying the
  // committed selection until they pick (or clear) something.
  const inputValue = open ? query : selectedLabel;

  // Filtered matches (case-insensitive substring on title). Empty query → all.
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (q === "") return milestones;
    return milestones.filter((m) => m.title.toLowerCase().includes(q));
  }, [query, milestones]);

  // Option rows = a leading "None" row (index 0) + each match. Keeping None in
  // the same roving-focus list means ↑/↓/Enter can select it too.
  const optionCount = matches.length + 1;

  const closePopover = useCallback(() => {
    setOpen(false);
    setQuery("");
  }, []);

  function openPopover() {
    if (disabled) return;
    setQuery("");
    setHighlight(0);
    setOpen(true);
  }

  function commit(index: number) {
    if (index <= 0) {
      onChange(null);
    } else {
      const m = matches[index - 1];
      if (m) onChange(m.id);
    }
    closePopover();
    inputRef.current?.blur();
  }

  // Outside-pointerdown closes (mirrors NewTaskDropdown). Registered only open.
  useEffect(() => {
    if (!open) return;
    function onPointerDown(e: PointerEvent) {
      const t = e.target as Node;
      if (wrapperRef.current?.contains(t)) return;
      closePopover();
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open, closePopover]);

  // Clamp the highlight within range during render — no effect needed.
  const safeHighlight = Math.min(highlight, optionCount - 1);

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!open) {
        openPopover();
        return;
      }
      setHighlight((h) => (h + 1) % optionCount);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (!open) {
        openPopover();
        return;
      }
      setHighlight((h) => (h - 1 + optionCount) % optionCount);
    } else if (e.key === "Enter") {
      if (open) {
        e.preventDefault();
        commit(safeHighlight);
      }
    } else if (e.key === "Escape") {
      if (open) {
        e.preventDefault();
        e.stopPropagation();
        closePopover();
      }
    }
  }

  return (
    <div className="relative mt-1" ref={wrapperRef} data-milestone-combobox>
      <input
        {...inputProps}
        ref={inputRef}
        type="text"
        role="combobox"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-autocomplete="list"
        autoComplete="off"
        value={inputValue}
        placeholder={NONE_LABEL}
        disabled={disabled}
        onFocus={openPopover}
        onClick={openPopover}
        onChange={(e) => {
          if (!open) setOpen(true);
          setQuery(e.target.value);
          setHighlight(0);
        }}
        onKeyDown={onKeyDown}
        className="block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
      />

      {open && (
        <ul
          id={listboxId}
          role="listbox"
          className="absolute left-0 right-0 z-50 mt-1 max-h-56 overflow-y-auto rounded-md border border-zinc-200 bg-white py-1 shadow-lg dark:border-zinc-700 dark:bg-zinc-900"
          data-milestone-combobox-list
        >
          <li
            role="option"
            aria-selected={value === null}
            onMouseDown={(e) => {
              // mousedown (not click) so it fires before the input's blur.
              e.preventDefault();
              commit(0);
            }}
            onMouseEnter={() => setHighlight(0)}
            className={`cursor-pointer px-3 py-1.5 text-sm ${
              safeHighlight === 0
                ? "bg-zinc-100 dark:bg-zinc-800"
                : ""
            } text-zinc-500 dark:text-zinc-400`}
            data-milestone-combobox-none
          >
            {NONE_LABEL}
          </li>
          {matches.length === 0 ? (
            <li className="px-3 py-1.5 text-sm text-zinc-400 dark:text-zinc-500">
              No matches
            </li>
          ) : (
            matches.map((m, i) => {
              const idx = i + 1;
              return (
                <li
                  key={m.id}
                  role="option"
                  aria-selected={value === m.id}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    commit(idx);
                  }}
                  onMouseEnter={() => setHighlight(idx)}
                  className={`cursor-pointer px-3 py-1.5 text-sm text-zinc-700 dark:text-zinc-200 ${
                    safeHighlight === idx
                      ? "bg-zinc-100 dark:bg-zinc-800"
                      : ""
                  } ${value === m.id ? "font-medium" : ""}`}
                  data-milestone-combobox-option
                  data-milestone-id={m.id}
                >
                  {m.title}
                </li>
              );
            })
          )}
        </ul>
      )}
    </div>
  );
}
