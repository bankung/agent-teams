"use client";

// NewTaskDropdown — Kanban #1781 (FE). Single "+ New ▾" button replacing the
// former side-by-side AI-task + manual-task triggers in the board header.
//
// Clicking the button opens a small menu with two items — AI Task / Manual
// Task — each of which opens the EXISTING modal (AiTaskModal / NewTaskModal)
// via their externalOpen / onExternalClose props. No flow changes: the modals
// keep the same props (projectId, enabledRoles, project, onPushToast).
//
// a11y / interaction:
//   - role="menu" + role="menuitem" with aria-haspopup / aria-expanded.
//   - Esc closes the menu and returns focus to the trigger.
//   - Outside-click (mousedown) closes the menu.
//   - On open, focus moves to the first menu item; ArrowDown/ArrowUp cycle.
//
// While a modal is open, the menu is closed (the modal's own ModalShell traps
// focus + handles Esc); reopening the dropdown after the modal closes is a
// fresh trigger click.

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import type { MilestoneRead, ProjectRead } from "@/lib/api";
import { readEnabledRoles } from "@/lib/enabledRoles";
import { AiTaskModal } from "./AiTaskModal";
import { Icon } from "./Icon";
import { MilestoneFormModal } from "./MilestoneFormModal";
import { NewTaskModal } from "./NewTaskModal";

type Props = {
  project: ProjectRead;
  onPushToast: (text: string) => void;
};

type OpenModal = "ai" | "manual" | "milestone" | null;

export function NewTaskDropdown({ project, onPushToast }: Props) {
  const router = useRouter();
  const [menuOpen, setMenuOpen] = useState(false);
  const [openModal, setOpenModal] = useState<OpenModal>(null);
  const menuId = useId();

  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const firstItemRef = useRef<HTMLButtonElement | null>(null);
  const enabledRoles = readEnabledRoles(project.config);

  const closeMenu = useCallback((returnFocus = false) => {
    setMenuOpen(false);
    if (returnFocus) triggerRef.current?.focus();
  }, []);

  // Outside-click (mousedown) + Esc close the menu. Registered only while open.
  useEffect(() => {
    if (!menuOpen) return;

    function onPointerDown(e: PointerEvent) {
      const t = e.target as Node;
      if (
        menuRef.current?.contains(t) ||
        triggerRef.current?.contains(t)
      ) {
        return;
      }
      setMenuOpen(false);
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        closeMenu(true);
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [menuOpen, closeMenu]);

  // On open, focus the first item so keyboard users land in the menu.
  useEffect(() => {
    if (menuOpen) {
      requestAnimationFrame(() => firstItemRef.current?.focus());
    }
  }, [menuOpen]);

  function pick(which: "ai" | "manual" | "milestone") {
    setMenuOpen(false);
    setOpenModal(which);
  }

  // Roving focus between the two menu items via Arrow keys.
  function onMenuKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const items = Array.from(
      menuRef.current?.querySelectorAll<HTMLButtonElement>(
        '[role="menuitem"]',
      ) ?? [],
    );
    if (items.length === 0) return;
    const idx = items.indexOf(document.activeElement as HTMLButtonElement);
    const next =
      e.key === "ArrowDown"
        ? (idx + 1) % items.length
        : (idx - 1 + items.length) % items.length;
    items[next]?.focus();
  }

  return (
    <div className="relative" data-new-task-dropdown>
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        aria-controls={menuId}
        onClick={() => setMenuOpen((v) => !v)}
        className="inline-flex items-center gap-1 rounded border border-emerald-300 bg-white px-2.5 py-1.5 text-xs font-medium uppercase tracking-wide text-emerald-700 hover:border-emerald-400 hover:text-emerald-900 min-h-[44px] sm:min-h-0 dark:border-emerald-700 dark:bg-zinc-900 dark:text-emerald-300 dark:hover:border-emerald-500 dark:hover:text-emerald-100"
        data-new-task-dropdown-trigger
      >
        <Icon name="plus" size={14} aria-hidden />
        <span>New</span>
        <Icon name="chevron-down" size={12} aria-hidden />
      </button>

      {menuOpen && (
        <div
          ref={menuRef}
          id={menuId}
          role="menu"
          aria-label="Create task"
          onKeyDown={onMenuKeyDown}
          className="absolute right-0 z-50 mt-1 w-44 overflow-hidden rounded-md border border-zinc-200 bg-white shadow-lg dark:border-zinc-700 dark:bg-zinc-900"
          data-new-task-dropdown-menu
        >
          <button
            ref={firstItemRef}
            type="button"
            role="menuitem"
            onClick={() => pick("ai")}
            className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-xs font-medium text-zinc-700 hover:bg-violet-50 hover:text-violet-900 focus:bg-violet-50 focus:text-violet-900 focus:outline-none dark:text-zinc-200 dark:hover:bg-violet-950/40 dark:hover:text-violet-100 dark:focus:bg-violet-950/40"
            data-new-task-dropdown-ai
          >
            <Icon name="ai-agent" size={14} aria-hidden />
            <span>AI Task</span>
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => pick("manual")}
            className="flex w-full items-center gap-2 border-t border-zinc-100 px-3 py-2.5 text-left text-xs font-medium text-zinc-700 hover:bg-zinc-50 hover:text-zinc-900 focus:bg-zinc-50 focus:text-zinc-900 focus:outline-none dark:border-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-800 dark:hover:text-zinc-100 dark:focus:bg-zinc-800"
            data-new-task-dropdown-manual
          >
            <Icon name="add-task" size={14} aria-hidden />
            <span>Manual Task</span>
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => pick("milestone")}
            className="flex w-full items-center gap-2 border-t border-zinc-100 px-3 py-2.5 text-left text-xs font-medium text-zinc-700 hover:bg-amber-50 hover:text-amber-900 focus:bg-amber-50 focus:text-amber-900 focus:outline-none dark:border-zinc-800 dark:text-zinc-200 dark:hover:bg-amber-950/40 dark:hover:text-amber-100 dark:focus:bg-amber-950/40"
            data-new-task-dropdown-milestone
          >
            <Icon name="sprint" size={14} aria-hidden />
            <span>New Milestone</span>
          </button>
        </div>
      )}

      {/* Existing modals, driven via externalOpen. No internal triggers render
          because externalOpen is always defined here. Same props/flow as before. */}
      <AiTaskModal
        projectId={project.id}
        enabledRoles={enabledRoles}
        project={project}
        onPushToast={onPushToast}
        externalOpen={openModal === "ai"}
        onExternalClose={() => setOpenModal(null)}
      />
      <NewTaskModal
        projectId={project.id}
        enabledRoles={enabledRoles}
        project={project}
        onPushToast={onPushToast}
        externalOpen={openModal === "manual"}
        onExternalClose={() => setOpenModal(null)}
      />
      {/* Wave B (#3b) — New Milestone shortcut. MilestoneFormModal in create
          mode. On success router.refresh() re-fetches the board + milestone
          filter dropdown (same pattern as MilestonesView). */}
      <MilestoneFormModal
        projectId={project.id}
        open={openModal === "milestone"}
        onClose={() => setOpenModal(null)}
        onSaved={(_saved: MilestoneRead) => {
          setOpenModal(null);
          router.refresh();
        }}
      />
    </div>
  );
}
