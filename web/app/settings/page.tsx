// Settings — Kanban #955.C. New top-level surface for operator preferences.
// Server Component; mounts the PushNotificationsPanel client child.
//
// Layout mirrors the dashboard header pattern (compact header + main panel
// body). For now this page holds only the Push panel; future operator
// settings (theme persistence, language, notification routing) can slot in
// as sibling <section>s.

import Link from "next/link";

import { PushNotificationsPanel } from "@/components/PushNotificationsPanel";
import { ThemePicker } from "@/components/ThemePicker";

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
        <span className="ml-auto flex w-full items-center justify-end gap-2 sm:w-auto">
          <ThemePicker />
        </span>
      </header>

      <div className="mx-auto w-full max-w-2xl">
        <PushNotificationsPanel />
      </div>
    </main>
  );
}
