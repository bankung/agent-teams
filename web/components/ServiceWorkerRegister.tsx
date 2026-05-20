"use client";

// ServiceWorkerRegister — Kanban #955.C.
//
// Tiny client component mounted from app/layout.tsx. Registers /sw.js once
// on first hydration so the Web Push service worker is active by the time
// the operator opens the settings panel. Registration is idempotent
// browser-side (re-calling register() is a no-op when /sw.js is already
// controlling the page); we still gate the call on first mount to avoid
// repeated register() noise in dev hot-reload.
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

export function ServiceWorkerRegister() {
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!("serviceWorker" in navigator)) return;
    // Defer to next idle tick — service-worker registration shouldn't compete
    // with initial paint or hydration on slow devices.
    const handle = window.setTimeout(() => {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
        // Silent — push.ts surfaces the actionable error from the settings
        // panel when the operator clicks the master toggle.
      });
    }, 0);
    return () => window.clearTimeout(handle);
  }, []);

  return null;
}
