"use client";

// ModalShell — shared modal chrome (Kanban #1682 Phase 1 E1).
//
// Collapses the duplicated backdrop + centered panel pattern used by ~10
// ad-hoc modals. Key fixes over the inline copies:
//
//   a11y: role="dialog" + aria-modal="true" are placed on the INNER PANEL
//         (not the backdrop div), so screen readers announce the panel only
//         and not the full viewport.
//
//   stale-closure ESC: the ESC listener is registered with a fresh ref on
//         every render cycle, so `onClose` is never stale.
//
// Panel chrome (backdrop + panel dimensions) mirrors every existing modal's
// Tailwind classes verbatim so behavior + visuals are identical.
//
// Children receive the inner panel — callers put their <form> or <div>
// content directly inside. The panel itself is a <div>; callers that need a
// <form> wrap their own <form> inside `children`.

import { useCallback, useEffect, useRef } from "react";

// Tailwind max-width tokens for the sm:max-w-* panel constraint.
// Callers pass the token; ModalShell maps it to the full class so Tailwind's
// static analyser can see all class strings at build time.
const MAX_WIDTH_CLASS: Record<string, string> = {
  sm: "sm:max-w-sm",
  md: "sm:max-w-md",
  lg: "sm:max-w-lg",
};

type Props = {
  open: boolean;
  // Called on ESC + backdrop-mousedown. Callers must guard against closing
  // while submitting (pass a no-op or check inside their handler).
  onClose: () => void;
  // aria-labelledby value — must match the id on the heading inside children.
  labelledBy: string;
  // Controls sm:max-w-* on the panel. Defaults to 'md' (matches existing
  // migrated modals). Use 'lg' for denser forms (EditProjectModal,
  // PlatformSettingsModal) and 'sm' for compact confirmations.
  maxWidth?: "sm" | "md" | "lg";
  // Optional: appended to the panel className for one-off overrides (e.g.
  // PlatformSettingsModal adds sm:max-h-[85vh]).
  panelExtraClassName?: string;
  // Optional: forwarded to the outer backdrop for data-* test attributes.
  backdropProps?: Record<string, unknown>;
  children: React.ReactNode;
};

export function ModalShell({
  open,
  onClose,
  labelledBy,
  maxWidth = "md",
  panelExtraClassName,
  backdropProps,
  children,
}: Props) {
  // Keep a stable ref so the ESC listener always calls the freshest onClose
  // without needing to re-register every render.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  const handleEsc = useCallback((e: KeyboardEvent) => {
    if (e.key === "Escape") onCloseRef.current();
  }, []);

  useEffect(() => {
    if (!open) return;
    document.addEventListener("keydown", handleEsc);
    return () => document.removeEventListener("keydown", handleEsc);
  }, [open, handleEsc]);

  if (!open) return null;

  const panelMaxW = MAX_WIDTH_CLASS[maxWidth] ?? MAX_WIDTH_CLASS.md;

  return (
    // Backdrop — no role/aria-modal here (a11y fix: those go on the panel below)
    <div
      className="fixed inset-0 z-50 flex items-stretch justify-center bg-zinc-900/40 dark:bg-zinc-950/70 sm:items-center sm:px-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      {...backdropProps}
    >
      {/* Panel — role="dialog" + aria-modal live here, not on the backdrop */}
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        className={`flex w-full max-w-none flex-col overflow-y-auto rounded-none border-0 bg-white p-4 dark:bg-zinc-900 sm:h-auto ${panelMaxW} sm:overflow-visible sm:rounded sm:border sm:border-zinc-200 sm:dark:border-zinc-800${panelExtraClassName ? ` ${panelExtraClassName}` : ""}`}
      >
        {children}
      </div>
    </div>
  );
}
