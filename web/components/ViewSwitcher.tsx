"use client";

// ViewSwitcher — Wave A (#1). Unified segmented control for the four per-project
// views: Board · List · Calendar · Gantt.
//
// Routing model:
//   - Board    → /p/<name>            (kanban; no view param)
//   - List     → /p/<name>?view=list  (the SAME board route in list mode)
//   - Calendar → /p/<name>/calendar
//   - Gantt    → /p/<name>/gantt
//
// Why List is a query param, not a route: list mode is a render variant of the
// board page (it shares the board's task state, filters, deep-link + SSE
// refresh). It has never been a route and making it one would duplicate the
// board's data fetch. The `?view=list` href lets the switcher link to list mode
// from OTHER pages (calendar/gantt) with a shareable URL; the board reads the
// param on mount and seeds its view state from it (URL wins over localStorage
// on first paint). See web/components/Board.tsx.
//
// Two modes of operation, by whether `onSelect` is passed:
//   - In-board (onSelect given): Board / List render as <button>s that flip the
//     board's local `view` state in place (no navigation → preserves SSE,
//     filters, deep-link scroll, localStorage persistence). Calendar / Gantt
//     are always real <Link> navigations (separate routes).
//   - Off-board (no onSelect, e.g. calendar / gantt pages): ALL four items are
//     <Link>s. Board → /p/<name>, List → /p/<name>?view=list.
//
// Active state is PROP-DRIVEN (`active`) rather than derived from the pathname:
// on the board page the live board/list distinction is owned by Board's view
// state (it can change without navigation), so Board passes its current value.
// Calendar / Gantt pages pass a static literal.

import Link from "next/link";

import { Icon } from "@/components/Icon";

export type ViewKey = "board" | "list" | "calendar" | "gantt";

type ViewDef = {
  key: ViewKey;
  label: string;
  icon: string;
  href: (name: string) => string;
  // Board + List can be flipped in place when onSelect is supplied; Calendar +
  // Gantt always navigate.
  inPlace: boolean;
};

const VIEWS: ViewDef[] = [
  {
    key: "board",
    label: "Board",
    icon: "view-board",
    href: (n) => `/p/${encodeURIComponent(n)}`,
    inPlace: true,
  },
  {
    key: "list",
    label: "List",
    icon: "view-list",
    href: (n) => `/p/${encodeURIComponent(n)}?view=list`,
    inPlace: true,
  },
  {
    key: "calendar",
    label: "Calendar",
    icon: "clock",
    href: (n) => `/p/${encodeURIComponent(n)}/calendar`,
    inPlace: false,
  },
  {
    key: "gantt",
    label: "Gantt",
    icon: "sprint",
    href: (n) => `/p/${encodeURIComponent(n)}/gantt`,
    inPlace: false,
  },
];

const baseItemCls =
  "inline-flex items-center gap-1.5 px-3 py-2 min-h-[44px] sm:min-h-0 sm:px-2.5 sm:py-1 transition-colors";

function itemCls(isActive: boolean): string {
  return `${baseItemCls} ${
    isActive
      ? "bg-zinc-900 font-semibold text-white dark:bg-zinc-100 dark:text-zinc-900"
      : "bg-transparent text-zinc-500 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800"
  }`;
}

type Props = {
  projectName: string;
  active: ViewKey;
  // Supplied only on the board page → Board/List flip in place instead of
  // navigating. "board" | "list" are the only values it ever receives.
  onSelect?: (view: "board" | "list") => void;
};

export function ViewSwitcher({ projectName, active, onSelect }: Props) {
  return (
    <nav
      aria-label="View"
      data-view-switcher
      className="inline-flex items-center overflow-hidden rounded-md border border-zinc-200 text-xs dark:border-zinc-700"
    >
      {VIEWS.map((v) => {
        const isActive = v.key === active;
        const common = {
          "data-view-switcher-item": v.key,
          "data-active": isActive ? "true" : undefined,
          className: itemCls(isActive),
        } as const;
        const inner = (
          <>
            <Icon name={v.icon} size={14} aria-hidden />
            <span>{v.label}</span>
          </>
        );

        // In-place flip (board page only) for Board / List.
        if (onSelect && v.inPlace) {
          return (
            <button
              key={v.key}
              type="button"
              onClick={() => onSelect(v.key as "board" | "list")}
              aria-pressed={isActive}
              {...common}
            >
              {inner}
            </button>
          );
        }

        // Navigation link (Calendar / Gantt always; Board / List when off-board).
        return (
          <Link
            key={v.key}
            href={v.href(projectName)}
            aria-current={isActive ? "page" : undefined}
            {...common}
          >
            {inner}
          </Link>
        );
      })}
    </nav>
  );
}
