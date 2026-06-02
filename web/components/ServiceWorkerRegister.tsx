"use client";

// ServiceWorkerRegister — Kanban #955.C + #1769 (SW-lifecycle guard).
//
// Tiny client component mounted from app/layout.tsx. Registers /sw.js once
// on first hydration so the Web Push service worker is active by the time
// the operator opens the settings panel. Registration is idempotent
// browser-side (re-calling register() is a no-op when /sw.js is already
// controlling the page); we still gate the call on first mount to avoid
// repeated register() noise in dev hot-reload.
//
// Kanban #1769 — lifecycle guard additions:
//   - After register() resolves, call registration.update() to detect a new
//     SW version immediately (instead of waiting for the browser's 24-hour
//     check cycle).
//   - Listen for `controllerchange` on navigator.serviceWorker. When the new
//     SW calls skipWaiting+clients.claim (see sw.js), this event fires on all
//     open clients. We reload the page exactly once (guarded by a module-level
//     boolean) so the client adopts the new worker without a manual unregister
//     step and without entering a reload loop.
//
// Why a dedicated component instead of an inline <script> in layout.tsx:
//   - Service worker registration must run AFTER the page is interactive
//     (Lighthouse warns otherwise); useEffect runs post-hydration.
//   - Inline scripts in <head> bypass React's lifecycle and would fire on
//     every navigation in App Router.
//   - Keeps layout.tsx as a Server Component (mixing inline-script SW
//     registration would force the layout client-side).
//
// Failure modes (all silent, by design — the settings panel surfaces a
// user-actionable error if push setup ultimately fails):
//   - Browser lacks serviceWorker (older Safari, IE) → no-op.
//   - Browser blocks /sw.js (Firefox private mode) → register() rejects,
//     we swallow. The settings panel will report 'unsupported' instead.
//
// Note: the SW activates immediately on first register, but Push API
// `subscribe()` still requires an active permission grant — registration
// alone never opens a notification prompt. D7 (no auto-prompt) is preserved.

import { useEffect } from "react";

// Module-level guard: ensures we reload at most once per page lifetime even
// if `controllerchange` fires more than once (e.g. rapid successive deploys).
let _reloadedForControllerChange = false;

export function ServiceWorkerRegister() {
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!("serviceWorker" in navigator)) return;

    // `controllerchange` fires when a new SW takes control (skipWaiting +
    // clients.claim in sw.js). Reload once so the page runs under the new
    // worker. The module-level flag prevents a reload loop.
    const handleControllerChange = () => {
      if (_reloadedForControllerChange) return;
      _reloadedForControllerChange = true;
      window.location.reload();
    };
    navigator.serviceWorker.addEventListener(
      "controllerchange",
      handleControllerChange,
    );

    // Defer to next idle tick — service-worker registration shouldn't compete
    // with initial paint or hydration on slow devices.
    const handle = window.setTimeout(() => {
      navigator.serviceWorker
        .register("/sw.js", { scope: "/" })
        .then((registration) => {
          // Kanban #1769 — trigger an immediate update check so a newly
          // deployed sw.js is detected without waiting for the browser's
          // default 24-hour stale-check window.
          registration.update().catch(() => {
            // update() can reject in private mode or when the network is
            // unavailable — treat as non-fatal.
          });
        })
        .catch(() => {
          // Silent — push.ts surfaces the actionable error from the settings
          // panel when the operator clicks the master toggle.
        });
    }, 0);

    return () => {
      window.clearTimeout(handle);
      navigator.serviceWorker.removeEventListener(
        "controllerchange",
        handleControllerChange,
      );
    };
  }, []);

  return null;
}
