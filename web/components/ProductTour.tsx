"use client";

// Kanban #1582 — First-time product tour, DASHBOARD phase.
//
// Mounted once on /dashboard. Two responsibilities:
//   1. Auto-fire the tour ONCE for first-time users (localStorage gate).
//   2. Render the always-available "Take the tour" replay button (AC #5) so
//      returning users can revisit it from the dashboard header.
//
// The tour spans two routes. This component owns the dashboard half:
//   dashboard overview → project grid → open New Project modal → team field
//   (with its ? tooltip) → working-path field → close modal → hand off.
// On the final "Continue on a project board →" step it writes the phase baton
// (TOUR_PHASE_KEY="board") and navigates to /p/demo-tour, where
// ProductTourBoardResume picks up the board + task-drawer steps.
//
// driver.js is imported dynamically (client-only) so it never enters the SSR
// bundle. Its CSS is imported statically (tree-shaken into the client chunk).
//
// SSR safety: `mounted` gates the first render; the auto-fire effect reads
// localStorage only after mount. First server paint renders nothing tour-ish
// except the static replay button (no storage access in render).

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import "driver.js/dist/driver.css";

import {
  TOUR_BASE_CONFIG,
  TOUR_DEMO_PROJECT,
  clearTourPhase,
  isTourCompleted,
  markTourCompleted,
  overlayColorForTheme,
  setTourPhase,
} from "@/lib/tour";

// Open/close the New Project modal by clicking its real trigger / backdrop —
// the modal owns its own open state, so we drive it the way a user would.
// Both are best-effort: a missing element just means the step shows without
// the modal (the step still has a fallback element to highlight).
function openNewProjectModal() {
  const trigger = document.querySelector<HTMLButtonElement>(
    "[data-new-project-trigger]",
  );
  // Only click if the modal isn't already open (avoid toggling it shut).
  const alreadyOpen = document.querySelector("[data-new-project-modal]");
  if (trigger && !alreadyOpen) trigger.click();
}

function closeNewProjectModal() {
  const cancel = document.querySelector<HTMLButtonElement>(
    "[data-new-project-cancel]",
  );
  if (cancel) cancel.click();
}

export function ProductTour() {
  const router = useRouter();
  const [mounted, setMounted] = useState(false);
  // Guard against double-fire under React Strict mode / fast refresh.
  const startedRef = useRef(false);
  // M-3 — hold the live driver so a replay (or any re-start) can tear down the
  // previous instance before building a new one. Without this a second drive()
  // orphans the first driver's overlay/popover in the DOM.
  const driverRef = useRef<import("driver.js").Driver | null>(null);

  useEffect(() => {
    setMounted(true);
  }, []);

  // startTour — builds + drives the dashboard phase. `auto` distinguishes the
  // first-visit auto-fire from a manual replay (manual replay re-runs even if
  // the completed flag is set; it does NOT clear the flag on its own).
  const startTour = useCallback(
    async (auto: boolean) => {
      if (startedRef.current) return;
      startedRef.current = true;

      // Persist the auto-fire gate immediately so a page refresh never re-triggers
      // the tour. Replay (auto=false) skips this — isTourCompleted() is not checked
      // on the replay path anyway (handleReplay resets startedRef and calls
      // startTour(false) directly). Also suppresses Strict Mode double-invoke: the
      // second call is blocked by startedRef, so markTourCompleted() runs exactly once.
      if (auto) markTourCompleted();

      // M-3 — tear down any prior driver before building a new one so a replay
      // never leaves a second overlay/popover mounted.
      driverRef.current?.destroy();
      driverRef.current = null;

      // Dynamic import keeps driver.js out of the SSR + initial bundle.
      const { driver } = await import("driver.js");

      // H-2 — navigate to the board phase ONLY on an affirmative "Continue →"
      // (the done button) click. ESC / backdrop / X must NOT hand off. driver.js
      // v1.4.0's onDestroyStarted hook (DriverHook: (element, step, opts) => void)
      // carries NO destroy-reason, so we cannot branch on done-vs-skip there.
      // Instead we set this flag only inside the final step's onNextClick (the
      // done-button click handler) below. Verified against
      // node_modules/driver.js/dist/driver.js.d.ts (Driver.destroy / onNextClick /
      // onDestroyStarted shapes).
      let handedOff = false;

      // SECURITY: driver.js renders title/description via innerHTML — static string literals ONLY; never interpolate DB/user values here.
      const d = driver({
        ...TOUR_BASE_CONFIG,
        overlayColor: overlayColorForTheme(),
        steps: [
          {
            element: "[data-aggregate-summary]",
            popover: {
              title: "Your portfolio at a glance",
              description:
                "Every project's tasks rolled up by status. This is your cross-project command center.",
              side: "bottom",
              align: "start",
            },
          },
          {
            element: "[data-project-grid]",
            popover: {
              title: "Your projects",
              description:
                "Each card is one project. Click a project name to open its Kanban board.",
              side: "top",
              align: "start",
            },
          },
          {
            // Highlight the trigger; opening happens on the NEXT step so the
            // modal is present when we point at its fields.
            element: "[data-new-project-trigger]",
            popover: {
              title: "Start a new project",
              description:
                "AI agents need a project to work in. Let's open the create form.",
              side: "bottom",
              align: "end",
            },
            onDeselected: () => {
              // Moving forward off this step → open the modal so the next two
              // steps can anchor to its fields. (driver fires onDeselected when
              // leaving a step in either direction; opening is idempotent.)
              openNewProjectModal();
            },
          },
          {
            element: "[data-new-project-team]",
            popover: {
              title: "Pick a team",
              description:
                "The team decides which AI specialists join — dev, novel, SEO, and more. Tap the ? for each field's full explanation.",
              side: "left",
              align: "start",
            },
            // Defensive: ensure the modal is open before highlighting (covers
            // a fast Back→Next bounce that may have closed it).
            onHighlightStarted: () => openNewProjectModal(),
            // L-1 — this is the FIRST modal-field step. Moving BACK off it (to
            // the trigger step) must close the modal, else it's left open behind
            // the overlay. driver fires onDeselected in both directions; the
            // destination index < this step's index means we went backward.
            onDeselected: (_el, _step, { driver: drv }) => {
              const next = drv.getActiveIndex();
              const here = 3; // index of this team step in the steps[] array
              if (next !== undefined && next < here) closeNewProjectModal();
            },
          },
          {
            element: "[data-new-project-working-path]",
            popover: {
              title: "Where files live",
              description:
                "Optional. Point agents at your repo folder, or leave blank to keep everything inside agent-teams.",
              side: "left",
              align: "start",
            },
            onHighlightStarted: () => openNewProjectModal(),
            onDeselected: () => {
              // Leaving the modal-field steps → close the modal so the final
              // hand-off step isn't obscured by it.
              closeNewProjectModal();
            },
          },
          {
            // Hand-off step. No element → driver centers the popover.
            popover: {
              title: "See agents in action",
              description:
                "Next we'll open a real project board so you can see tasks, columns, and how to run an agent.",
              side: "over",
              align: "center",
              doneBtnText: "Continue →",
              // H-2 — affirmative-only hand-off. The "Continue →" (done) button
              // fires onNextClick. Setting handedOff here (NOT in
              // onHighlightStarted) means ESC / backdrop / X leave handedOff=false
              // and never navigate. onNextClick overrides driver's default
              // advance, so we must destroy() ourselves to fall into
              // onDestroyStarted's hand-off branch below.
              onNextClick: () => {
                handedOff = true;
                d.destroy();
              },
            },
          },
        ],
        // Skip / close (X, ESC, or backdrop) → mark completed, ensure the modal
        // is closed, and do NOT navigate.
        onDestroyStarted: () => {
          closeNewProjectModal();
          markTourCompleted();
          // Always tear down the dashboard-phase overlay first; navigation (if
          // any) happens afterward on a clean DOM.
          d.destroy();
          if (!handedOff) return;

          // L-2 — the demo-tour sample project may have been soft-deleted /
          // killed since the dashboard rendered. Verify it exists (one
          // round-trip) BEFORE navigating, so we never strand the user on a
          // 404. If it's gone, end the tour cleanly with no navigation.
          void fetch(
            `/api/projects/by-name/${encodeURIComponent(TOUR_DEMO_PROJECT)}`,
          )
            .then((res) => {
              if (!res.ok) {
                // demo-tour absent → skip the board phase gracefully.
                clearTourPhase();
                markTourCompleted();
                return;
              }
              // Exists → set the baton FIRST so the board mounts mid-tour, then
              // navigate to the board phase.
              setTourPhase("board");
              router.push(`/p/${TOUR_DEMO_PROJECT}`);
            })
            .catch(() => {
              // Network error → fail safe: no navigation, tour ends complete.
              clearTourPhase();
              markTourCompleted();
            });
        },
      });

      // M-3 — register as the live instance so a subsequent replay can destroy it.
      driverRef.current = d;
      d.drive();
    },
    [router],
  );

  // Auto-fire on first visit. Runs after mount so localStorage is readable.
  useEffect(() => {
    if (!mounted) return;
    if (isTourCompleted()) return;
    // Defer one tick so the dashboard's data-anchored sections are in the DOM
    // (they're server-rendered, so this is belt-and-suspenders).
    const id = window.setTimeout(() => void startTour(true), 350);
    return () => window.clearTimeout(id);
  }, [mounted, startTour]);

  // Manual replay — resets the started guard so it can run again, then drives.
  const handleReplay = useCallback(() => {
    // M-3 — destroy any running driver before re-starting so Replay never
    // orphans a live overlay/popover. startTour() also guards this, but doing
    // it here makes the replay entry-point self-contained.
    driverRef.current?.destroy();
    driverRef.current = null;
    startedRef.current = false;
    void startTour(false);
  }, [startTour]);

  // SSR + first paint: render the static replay button only (no storage read).
  return (
    <button
      type="button"
      onClick={handleReplay}
      data-tour-replay
      title="Take the product tour"
      aria-label="Take the product tour"
      className="inline-flex items-center gap-1 rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-400 hover:text-zinc-900 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-600 dark:hover:text-zinc-100"
    >
      <svg
        aria-hidden
        viewBox="0 0 16 16"
        width="13"
        height="13"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <circle cx="8" cy="8" r="6.5" />
        <path d="M6.2 6.2a1.8 1.8 0 1 1 2.4 1.7c-.5.2-.8.6-.8 1.1v.3" />
        <path d="M8 11.6h.01" />
      </svg>
      <span className="hidden sm:inline">Tour</span>
    </button>
  );
}
