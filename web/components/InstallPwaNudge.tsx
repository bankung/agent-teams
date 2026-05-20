"use client";

// iOS PWA install nudge — Kanban #955.C (AC6).
//
// Shown only when:
//   1. Running on iOS (iPhone / iPad / iPod user-agent), AND
//   2. NOT already installed as a PWA (`navigator.standalone !== true`).
//
// iOS Safari 16.4+ supports Web Push, but ONLY when the page is opened from
// the home-screen-installed shortcut. In normal Safari tabs PushManager is
// missing entirely (feature-detect returns false). Operators on iOS need
// to install-to-home-screen first; this nudge walks them through the
// Share → "Add to Home Screen" flow.
//
// Auto-dismiss: the component disappears as soon as the user installs the
// PWA and re-opens the app (standalone === true on next mount). No
// localStorage flag — re-render is enough.

import { useEffect, useState } from "react";

import { isIosNonStandalone } from "@/lib/push";

export function InstallPwaNudge() {
  // Default false (SSR safety): the iOS / standalone checks both depend on
  // `window.navigator`, which is server-undefined. Hydrate the actual value
  // on mount; first paint omits the nudge (acceptable — the nudge is an
  // optional secondary surface, not a blocking gate).
  const [show, setShow] = useState(false);

  useEffect(() => {
    setShow(isIosNonStandalone());
  }, []);

  if (!show) return null;

  return (
    <aside
      role="note"
      aria-label="Install agent-teams as a PWA for push notifications"
      data-install-pwa-nudge
      className="rounded-md border border-amber-200 bg-amber-50 p-3 text-[13px] text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/40 dark:text-amber-100"
    >
      <p className="font-semibold mb-1">
        Install agent-teams to enable push on iOS
      </p>
      <p className="text-[12px] leading-5 text-amber-800 dark:text-amber-200">
        iOS Safari only delivers push notifications when this app is
        installed to the home screen. Tap the{" "}
        <span aria-hidden className="font-mono">
          Share
        </span>{" "}
        icon in Safari, then choose{" "}
        <span className="font-semibold">Add to Home Screen</span>. Re-open
        from the home-screen icon and the Enable-push toggle will work.
      </p>
    </aside>
  );
}
