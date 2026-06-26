"use client";

// SettingsNav — Kanban #2716. Left-hand category nav for the two-pane settings
// layout. Mirrors the prop-driven segmented-control pattern from ViewSwitcher:
// the server page resolves the active section + the visible category list (which
// already reflects project scope) and passes them in; each item is a <Link> to
// ?section=<id> that preserves ?project= when present. The active item carries
// aria-current.
//
// Routing model: section state lives entirely in the ?section= query param
// (deep-linkable, back/forward works). This component never owns state — it just
// renders links — so it stays a thin, testable presentation layer.
//
// Responsive: a vertical sidebar on >=sm; on <sm it collapses to a horizontal
// scroll / chip row (same item styling, no separate component).

import Link from "next/link";

import type { SettingsCategory, SettingsSectionId } from "@/lib/settingsCategories";

type Props = {
  categories: SettingsCategory[];
  active: SettingsSectionId;
  // Present only when ?project= resolved — preserved on every nav link so
  // switching categories doesn't drop the project scope.
  projectName?: string;
};

// Build the href for a section, preserving ?project= when in scope.
function sectionHref(id: SettingsSectionId, projectName?: string): string {
  const params = new URLSearchParams();
  if (projectName) params.set("project", projectName);
  params.set("section", id);
  return `/settings?${params.toString()}`;
}

function itemCls(isActive: boolean): string {
  const base =
    "block whitespace-nowrap rounded-md px-3 py-2 text-sm transition-colors min-h-[44px] sm:min-h-0 sm:py-1.5";
  return `${base} ${
    isActive
      ? "bg-zinc-900 font-semibold text-white dark:bg-zinc-100 dark:text-zinc-900"
      : "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800"
  }`;
}

export function SettingsNav({ categories, active, projectName }: Props) {
  return (
    <nav
      aria-label="Settings categories"
      data-settings-nav
      // <sm: horizontal scroll row; >=sm: vertical sidebar.
      className="flex flex-row gap-1 overflow-x-auto border-b border-zinc-200 pb-2 sm:w-48 sm:shrink-0 sm:flex-col sm:gap-0.5 sm:border-b-0 sm:border-r sm:pr-3 sm:pb-0 dark:border-zinc-800"
    >
      {categories.map((c) => {
        const isActive = c.id === active;
        return (
          <Link
            key={c.id}
            href={sectionHref(c.id, projectName)}
            aria-current={isActive ? "page" : undefined}
            data-settings-nav-item={c.id}
            data-active={isActive ? "true" : undefined}
            className={itemCls(isActive)}
          >
            {c.label}
          </Link>
        );
      })}
    </nav>
  );
}
