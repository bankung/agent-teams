"use client";

// Kanban #2376 (R7) — Settings-page control that re-launches the product tour.
// Because tour steps anchor to dashboard elements, we clear the completed flag
// and navigate to /dashboard so the auto-fire re-runs the tour naturally.

import { useRouter } from "next/navigation";
import { clearTourCompleted } from "@/lib/tour";

export function TourReplayButton() {
  const router = useRouter();

  function handleReplay() {
    clearTourCompleted();
    router.push("/dashboard");
  }

  return (
    <button
      type="button"
      data-tour-replay-settings
      onClick={handleReplay}
      className="inline-flex items-center gap-2 rounded-md border border-zinc-200 bg-white px-4 py-2 text-sm font-medium text-zinc-700 hover:border-zinc-400 hover:text-zinc-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-600 dark:hover:text-zinc-100"
    >
      Replay product tour
    </button>
  );
}
