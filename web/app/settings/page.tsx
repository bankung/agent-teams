// Settings — Kanban #955.C / #2375 (R5 consolidation). Top-level surface for
// operator preferences.
//
// Server Component; mounts the client children (ThemePicker, IntegrationsPanel,
// PushNotificationsPanel).
//
// Layout mirrors the dashboard header pattern (compact header + main panel
// body). Body holds labelled <section>s: Theme (relocated out of the header —
// #2375 R5), Integrations (relocated from the former PlatformSettingsModal),
// and Push notifications.

import Link from "next/link";

import { IntegrationsPanel } from "@/components/IntegrationsPanel";
import { PushNotificationsPanel } from "@/components/PushNotificationsPanel";
import { ThemePicker } from "@/components/ThemePicker";
import { TourReplayButton } from "@/components/TourReplayButton";

export const dynamic = "force-dynamic";

export default function SettingsPage() {
  return (
    <main className="flex min-h-screen flex-col overflow-y-auto bg-white px-4 py-4 sm:px-6 sm:py-5 dark:bg-zinc-950">
      <header className="mb-4 flex flex-wrap items-center gap-2 text-sm">
        <Link
          href="/dashboard"
          className="text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
        >
          ← Dashboard
        </Link>
        <span aria-hidden className="text-zinc-300 dark:text-zinc-600">
          ·
        </span>
        <span className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
          Settings
        </span>
      </header>

      <div className="mx-auto flex w-full max-w-2xl flex-col gap-8">
        {/* Theme — #2375 R5: ThemePicker relocated from every route header into
            this labelled body section. ThemeProvider/useTheme unchanged. */}
        <section
          data-settings-theme
          aria-labelledby="settings-theme-heading"
          className="flex flex-col gap-3"
        >
          <header className="flex flex-col gap-1">
            <h2
              id="settings-theme-heading"
              className="text-base font-semibold text-zinc-900 dark:text-zinc-100"
            >
              Theme
            </h2>
            <p className="text-[12px] text-zinc-500 dark:text-zinc-400 leading-5">
              Light, dark, or follow the system preference. Applies to this
              browser.
            </p>
          </header>
          <div className="rounded-md border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900">
            <ThemePicker />
          </div>
        </section>

        {/* Integrations — #2375 R5: relocated from PlatformSettingsModal. */}
        <IntegrationsPanel />

        {/* Push notifications — original #955.C panel. */}
        <PushNotificationsPanel />

        {/* Product tour — #2376 R7: replay from settings. */}
        <section
          data-settings-tour
          aria-labelledby="settings-tour-heading"
          className="flex flex-col gap-3"
        >
          <header className="flex flex-col gap-1">
            <h2
              id="settings-tour-heading"
              className="text-base font-semibold text-zinc-900 dark:text-zinc-100"
            >
              Product tour
            </h2>
            <p className="text-[12px] text-zinc-500 dark:text-zinc-400 leading-5">
              Walk through the key features of agent-teams again.
            </p>
          </header>
          <TourReplayButton />
        </section>
      </div>
    </main>
  );
}
