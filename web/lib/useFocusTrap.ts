"use client";

// useFocusTrap — Kanban #1315 deferred-review FOCUS-1. Shared initial-focus +
// Tab/Shift+Tab trap + focus-restore for the two overlay surfaces that manage
// their own DOM (ModalShell's portal panel + ResourcePreviewDrawer, which is
// NOT built on ModalShell). No dependency — web/package.json carries no
// headless-ui/radix primitive that already does this (checked: dnd-kit,
// driver.js, react-markdown only), so this hand-rolls the minimum:
//
//   * on open: focus the first focusable descendant (fallback: the panel
//     itself, via tabIndex=-1) UNLESS a descendant already grabbed focus
//     first (lets NewTaskModal/EditProjectModal's own titleInputRef.focus()
//     effects keep winning — they still run, this is just a safety net for
//     the other ~20 ModalShell consumers that never wired their own focus).
//   * while open: Tab from the last focusable wraps to the first (and
//     Shift+Tab from the first wraps to the last), so focus never escapes to
//     the page behind the overlay.
//   * on close: restore focus to whatever had it before the overlay opened
//     (the opener button, typically).
//
// Deliberately excludes ESC handling — both callers already own a correct
// ESC listener (ModalShell's fresh-ref pattern / ResourcePreviewDrawer's own
// effect) and the brief says not to touch that.

import { useEffect, useRef } from "react";

// Elements considered part of the Tab cycle. `[tabindex]` intentionally
// excludes `tabindex="-1"` (programmatic-only focus targets, e.g. the panel
// fallback below) via the :not() clause.
const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

function getFocusable(container: HTMLElement): HTMLElement[] {
  // Visibility check: `.closest("[hidden]")` (bounded to `container` so an
  // ancestor OUTSIDE the panel — e.g. the panel itself being briefly
  // mid-transition — can't false-positive). Deliberately NOT `offsetParent`
  // (real-browser-only signal; jsdom's no-layout-engine test environment
  // always reports it null, which would make this trap untestable) — every
  // hidden surface in this codebase hides via the `hidden` attribute
  // (NIT-2's tabpanel pattern) or a full unmount (never queried at all), so
  // this check covers both without a layout dependency.
  return Array.from(
    container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
  ).filter((el) => {
    const hiddenAncestor = el.closest("[hidden]");
    return hiddenAncestor === null || !container.contains(hiddenAncestor);
  });
}

/**
 * @param containerRef  Ref to the panel element the trap operates within.
 * @param active        Whether the overlay is currently open.
 */
export function useFocusTrap(
  containerRef: React.RefObject<HTMLElement | null>,
  active: boolean,
) {
  // Element that had focus before the overlay opened — restored on close.
  const openerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!active) return;
    const container = containerRef.current;
    if (!container) return;

    openerRef.current = document.activeElement as HTMLElement | null;

    // Initial focus: skip if a descendant already claimed it (e.g. a
    // caller's own titleInputRef.focus() effect, which runs before this one
    // since child effects commit first). Deferred one frame so a caller's
    // synchronous-in-effect focus (no rAF) still wins the race cleanly.
    const raf = requestAnimationFrame(() => {
      if (!container.contains(document.activeElement)) {
        const first = getFocusable(container)[0];
        if (first) {
          first.focus();
        } else {
          // No focusable descendant (rare — e.g. a link-only preview) —
          // fall back to the panel itself so Tab/ESC still work sanely.
          container.focus();
        }
      }
    });

    return () => cancelAnimationFrame(raf);
  }, [active, containerRef]);

  useEffect(() => {
    if (!active) return;
    const container = containerRef.current;

    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== "Tab" || !container) return;
      const focusable = getFocusable(container);
      if (focusable.length === 0) {
        // Nothing tabbable — keep focus pinned to the panel.
        e.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const current = document.activeElement;

      if (e.shiftKey) {
        if (current === first || !container.contains(current)) {
          e.preventDefault();
          last.focus();
        }
      } else if (current === last || !container.contains(current)) {
        e.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [active, containerRef]);

  // Restore focus to the opener on close (active flips true -> false, or the
  // component unmounts while open).
  useEffect(() => {
    if (active) return;
    const opener = openerRef.current;
    if (opener && document.contains(opener)) opener.focus();
  }, [active]);
  useEffect(() => {
    return () => {
      const opener = openerRef.current;
      if (opener && document.contains(opener)) opener.focus();
    };
  }, []);
}
