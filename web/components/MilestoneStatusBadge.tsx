import type { MilestoneStatusValue } from "@/lib/api";

// MilestoneStatusBadge — small lifecycle-status chip (Kanban #1868 FE).
// Color hues mirror the board lane vocabulary used elsewhere:
//   planned   → zinc  (neutral / not started)
//   active    → amber (in flight; same hue as the "In progress" lane)
//   released  → emerald (done; same hue as the "Done" lane)
//   cancelled → red   (terminal-negative; same hue as the "Blocked" lane)
const BADGE: Record<MilestoneStatusValue, { className: string; label: string }> = {
  planned: {
    className:
      "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
    label: "planned",
  },
  active: {
    className:
      "bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300",
    label: "active",
  },
  released: {
    className:
      "bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300",
    label: "released",
  },
  cancelled: {
    className: "bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-300",
    label: "cancelled",
  },
};

export function MilestoneStatusBadge({
  status,
}: {
  status: MilestoneStatusValue;
}) {
  const badge = BADGE[status];
  return (
    <span
      data-milestone-status={status}
      className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${badge.className}`}
    >
      {badge.label}
    </span>
  );
}
