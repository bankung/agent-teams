// Kanban #1582 — First-time product tour (driver.js).
//
// This module is the shared, SSR-safe substrate for the two tour components:
//   - ProductTour.tsx          → dashboard phase (runs on /dashboard)
//   - ProductTourBoardResume.tsx → board phase (resumes on /p/<name>)
//
// It holds ONLY pure values + tiny pure helpers. No top-level `window` /
// `localStorage` access so it imports cleanly during SSR; every accessor is
// guarded for the client. driver.js itself is imported lazily inside the
// components (client-only) — never here — so this module stays render-safe.

// ── localStorage keys ──────────────────────────────────────────────────────
// Completion flag — written once the user finishes OR skips the tour. Presence
// (=== "true") suppresses the auto-fire forever (per browser) until cleared.
export const TOUR_COMPLETED_KEY = "at_tour_completed_v1";
// Cross-route phase baton. When the dashboard phase hands off to a project
// board, it writes "board" here + navigates. The board-resume component reads
// this on mount, runs the board phase, then clears it. Absent = no resume.
export const TOUR_PHASE_KEY = "at_tour_phase_v1";

export type TourPhase = "board";

// Stable sample project the dashboard phase navigates to for the board +
// task-drawer steps. Seeded by the platform (#1573 wave) with 3 sample tasks;
// referenced by DashboardWelcomeBanner too. If it is ever absent the board
// phase degrades to a no-op (every step guards its anchor).
export const TOUR_DEMO_PROJECT = "demo-tour";

// ── SSR-safe localStorage helpers ───────────────────────────────────────────
// All wrapped in try/catch for private-mode / quota / disabled-storage. Reads
// return a safe default; writes silently no-op on failure (the tour is a
// progressive enhancement, never load-bearing).

export function isTourCompleted(): boolean {
  if (typeof window === "undefined") return true; // never auto-fire on the server
  try {
    return window.localStorage.getItem(TOUR_COMPLETED_KEY) === "true";
  } catch {
    return true; // storage blocked → treat as completed so we never nag
  }
}

export function markTourCompleted(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(TOUR_COMPLETED_KEY, "true");
  } catch {
    /* private mode / quota — in-memory only, no persistence */
  }
}

export function setTourPhase(phase: TourPhase): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(TOUR_PHASE_KEY, phase);
  } catch {
    /* ignore */
  }
}

export function readTourPhase(): TourPhase | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOUR_PHASE_KEY) === "board"
      ? "board"
      : null;
  } catch {
    return null;
  }
}

export function clearTourCompleted(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(TOUR_COMPLETED_KEY);
    clearTourPhase();
  } catch {
    /* ignore */
  }
}

export function clearTourPhase(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(TOUR_PHASE_KEY);
  } catch {
    /* ignore */
  }
}

// ── driver.js shared config ─────────────────────────────────────────────────
// Theming is handled in globals.css via `popoverClass: "at-tour-popover"` +
// `.dark .at-tour-popover` overrides. The overlay color must differ per theme:
// a dark scrim on light bg, a deeper scrim on dark bg. driver.js paints the
// overlay as an inline SVG fill, so theme-aware CSS can't reach it — we pass
// the color explicitly from the live `dark` class on <html>.
export const TOUR_POPOVER_CLASS = "at-tour-popover";

export function isDarkMode(): boolean {
  if (typeof document === "undefined") return false;
  return document.documentElement.classList.contains("dark");
}

// Overlay color tuned per theme (rgba string driver.js feeds to SVG fill).
export function overlayColorForTheme(): string {
  // light: zinc-900 @ 55%; dark: near-black @ 72% — matches the modal scrims
  // used by TaskDetail (bg-zinc-900/40) / ModalShell but a touch stronger so
  // the spotlight reads clearly.
  return isDarkMode() ? "rgba(9, 9, 11, 0.72)" : "rgba(24, 24, 27, 0.55)";
}

// Shared driver.js Config fragment (button labels + chrome). Steps + theme
// color are merged per-phase by the components.
export const TOUR_BASE_CONFIG = {
  showProgress: true,
  popoverClass: TOUR_POPOVER_CLASS,
  stagePadding: 6,
  stageRadius: 8,
  allowClose: true,
  nextBtnText: "Next →",
  prevBtnText: "← Back",
  doneBtnText: "Done",
  progressText: "{{current}} of {{total}}",
} as const;
