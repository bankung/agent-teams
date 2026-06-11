"use client";

// Kanban #1582 — First-time product tour, BOARD phase (resume half).
//
// Mounted once inside the project board (Board.tsx). Does nothing unless the
// dashboard phase handed off by writing TOUR_PHASE_KEY="board". When it did,
// this runs the board + task-drawer steps, then marks the whole tour completed
// and clears the baton so it doesn't re-fire on the next board visit.
//
// H-1/M-1 — the baton is global (one localStorage key), but the board phase is
// ONLY ever meant to run on the demo-tour sample project. A stale baton (set on
// the dashboard but never consumed — e.g. the user navigated to a real project
// instead of demo-tour) would otherwise fire the tour on the WRONG board. We
// guard on `project.name`: anything other than TOUR_DEMO_PROJECT clears the
// baton and returns. We also clear the baton on unmount so a navigate-away
// mid-phase never leaves it dangling.
//
// Steps (each guards its own anchor — a missing anchor is skipped, never
// throws, so a board with zero tasks still completes the tour gracefully):
//   1. Board columns      → [data-board="dnd"]
//   2. + New              → [data-new-task-dropdown-trigger]
//   3. Open a task        → click the first [data-task-card-id], then
//   4. Acceptance criteria→ [data-acceptance-criteria] inside the drawer
//
// Opening the drawer (step 3→4) is async: the drawer mounts after a click +
// React state update, and the AC section is near its bottom. driver.js's
// lifecycle hooks are SYNC (DriverHook returns void; the library does not await
// it), so we must NOT block inside onHighlightStarted. Instead we click the
// card synchronously, then attach a MutationObserver that calls d.refresh()
// once [data-acceptance-criteria] mounts and disconnects itself (with a safety
// disconnect timeout).

import { useCallback, useEffect, useRef } from "react";

import {
  TOUR_BASE_CONFIG,
  TOUR_DEMO_PROJECT,
  clearTourPhase,
  markTourCompleted,
  overlayColorForTheme,
  readTourPhase,
} from "@/lib/tour";

function closeTaskDrawer() {
  const close = document.querySelector<HTMLButtonElement>(
    "[data-task-detail-close]",
  );
  if (close) close.click();
}

export function ProductTourBoardResume({
  projectName,
}: {
  // H-1/M-1 — the board this resume controller is mounted on. The board phase
  // must run ONLY on the demo-tour sample project; on any real project the
  // baton is cleared and the tour does not fire.
  projectName: string;
}) {
  const startedRef = useRef(false);
  // M-3 — hold the live driver so a re-entry can tear it down first.
  const driverRef = useRef<import("driver.js").Driver | null>(null);

  const finish = useCallback(() => {
    markTourCompleted();
    clearTourPhase();
  }, []);

  const run = useCallback(async () => {
    if (startedRef.current) return;
    startedRef.current = true;

    // M-3 — destroy any prior instance before building a new one.
    driverRef.current?.destroy();
    driverRef.current = null;

    // Dynamic imports keep driver.js + its CSS out of the initial board bundle.
    await import("driver.js/dist/driver.css");
    const { driver } = await import("driver.js");

    // SECURITY: driver.js renders title/description via innerHTML — static string literals ONLY; never interpolate DB/user values here.
    const d = driver({
      ...TOUR_BASE_CONFIG,
      overlayColor: overlayColorForTheme(),
      steps: [
        {
          element: '[data-board="dnd"]',
          popover: {
            title: "The Kanban board",
            description:
              "Tasks flow left to right — New, In progress, Review, Blocked, Done. Drag a card to move it.",
            side: "top",
            align: "center",
          },
        },
        {
          element: "[data-new-task-dropdown-trigger]",
          popover: {
            title: "Add work",
            description:
              "Create an AI task (an agent runs it) or a manual task (you track it yourself).",
            side: "left",
            align: "start",
          },
        },
        {
          // Highlight the first task card; clicking happens on leave so the
          // drawer is open when the AC step asks for it.
          element: "[data-task-card-id]",
          popover: {
            title: "Open a task",
            description:
              "Click any card to see its full detail. Let's peek inside one.",
            side: "right",
            align: "start",
          },
        },
        {
          // The drawer's AC section. onHighlightStarted opens the drawer (click
          // the first card) then a MutationObserver refreshes driver once the
          // AC anchor mounts — see M-2 note below.
          element: "[data-acceptance-criteria]",
          popover: {
            title: "Acceptance criteria",
            description:
              "Agents check each criterion before marking a task done — your definition of success. The Run button (on AI tasks) launches the agent.",
            side: "left",
            align: "start",
          },
          // M-2 — driver's lifecycle hooks are synchronous. An async/await body
          // here races the library's contract (driver does not await the
          // returned promise, so the highlight may land before the drawer +
          // anchor exist). Instead: open the drawer synchronously, then observe
          // the DOM and refresh driver once [data-acceptance-criteria] mounts.
          onHighlightStarted: () => {
            // Open the drawer if it isn't already.
            if (!document.querySelector("[data-task-detail-modal]")) {
              const card = document.querySelector<HTMLElement>(
                "[data-task-card-id]",
              );
              card?.click();
            }
            // If the anchor is already present (drawer was open), nothing to do.
            if (document.querySelector("[data-acceptance-criteria]")) return;

            const observer = new MutationObserver(() => {
              if (document.querySelector("[data-acceptance-criteria]")) {
                observer.disconnect();
                window.clearTimeout(safety);
                // Re-measure so the spotlight + popover snap onto the now-mounted
                // AC section.
                d.refresh();
              }
            });
            observer.observe(document.body, {
              childList: true,
              subtree: true,
            });
            // Safety disconnect — if the drawer never mounts the anchor, stop
            // observing so we don't leak the observer.
            const safety = window.setTimeout(() => observer.disconnect(), 1500);
          },
        },
      ],
      onDestroyStarted: () => {
        // Any exit (Done, Skip, ESC, backdrop) → close the drawer, mark the
        // tour complete, clear the baton.
        closeTaskDrawer();
        finish();
        d.destroy();
      },
    });

    // M-3 — register as the live instance.
    driverRef.current = d;
    d.drive();
  }, [finish]);

  useEffect(() => {
    if (readTourPhase() !== "board") return;

    // H-1/M-1 — a baton set on the dashboard but landing on a REAL project
    // board (user navigated somewhere other than demo-tour) must not fire the
    // board tour. Clear the stale baton and bail.
    if (projectName !== TOUR_DEMO_PROJECT) {
      clearTourPhase();
      return;
    }

    // Defer so the SSR board markup (columns, cards) is committed before we
    // query anchors. A small delay also lets the route transition settle.
    const id = window.setTimeout(() => void run(), 450);
    return () => window.clearTimeout(id);
  }, [run, projectName]);

  // M-1 — on unmount (navigate away mid-phase) tear down any live driver and
  // clear the baton so it can never resume on the wrong board later. Separate
  // effect (empty deps) so it runs exactly once on unmount.
  useEffect(() => {
    return () => {
      driverRef.current?.destroy();
      driverRef.current = null;
      clearTourPhase();
    };
  }, []);

  // Renders nothing — purely an effect-driven resume controller.
  return null;
}
