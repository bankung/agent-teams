"use client";

// TaskActionButtons — Kanban #1001. Quick-action button row for the task focus
// page. Renders 4 buttons: Approve, Reject, Halt, Open full.
//
// Layout discipline:
//   - Sticky-bottom toolbar on mobile (<sm). Pinned to the viewport so the
//     buttons stay reachable as the user scrolls the task body.
//   - Inline row on desktop (sm+). Falls into the normal vertical stack;
//     the focus page's max-w-2xl wrapper keeps the row tight.
//
// `actionHint` from the URL (?action_hint=approve|reject) sets the
// `autoFocus` boolean on the corresponding button so a Tab cycle starts in
// the right place — without changing the visible chrome (no special border /
// background just for the focused button; the browser's native focus ring
// handles the cue).
//
// `approveDisabled` is set by the parent for cases where Approve does not
// have a meaningful target — typically a multi-option decision task (the
// option chooser is rendered inline above; the single Approve button is
// hidden). The button is rendered greyed out with a hint title.

import Link from "next/link";

type Props = {
  // Task state — drives label / disabled hints.
  approveLabel: string;
  approveDisabled: boolean;
  approveDisabledReason?: string;
  submitting: boolean;
  actionHint: "approve" | "reject" | null;
  // Kanban #1451 — hide Reject for HITL question/decision tasks that have
  // 0 or 1 option (no meaningful "second choice" to map Reject to). Halt is
  // still surfaced as the meta-escape regardless.
  hideReject?: boolean;

  // "Open full" target (resolved by the parent — typically /p/<project_name>).
  openFullHref: string;

  // Callbacks.
  onApprove: () => void;
  onRejectClick: () => void;
  onHaltClick: () => void;
};

export function TaskActionButtons({
  approveLabel,
  approveDisabled,
  approveDisabledReason,
  submitting,
  actionHint,
  hideReject = false,
  openFullHref,
  onApprove,
  onRejectClick,
  onHaltClick,
}: Props) {
  return (
    <div
      data-task-action-buttons
      data-action-hint={actionHint ?? "none"}
      // Mobile: fixed bottom toolbar with a backdrop blur to separate from the
      // task body. Desktop: relative inline row inside the normal stack.
      className="fixed inset-x-0 bottom-0 z-30 flex w-full items-center gap-2 border-t border-zinc-200 bg-white/95 px-4 py-3 backdrop-blur supports-[backdrop-filter]:bg-white/80 dark:border-zinc-800 dark:bg-zinc-950/95 dark:supports-[backdrop-filter]:bg-zinc-950/80 sm:relative sm:inset-auto sm:bottom-auto sm:w-auto sm:border-0 sm:bg-transparent sm:px-0 sm:py-0 sm:backdrop-blur-0 dark:sm:bg-transparent"
    >
      <button
        type="button"
        onClick={onApprove}
        disabled={submitting || approveDisabled}
        // eslint-disable-next-line jsx-a11y/no-autofocus
        autoFocus={actionHint === "approve"}
        title={approveDisabled ? approveDisabledReason : undefined}
        data-task-action="approve"
        className="flex-1 rounded border border-emerald-600 bg-emerald-600 px-3 py-2 text-xs font-semibold uppercase tracking-wide text-white hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed min-h-[44px] sm:flex-none sm:min-h-0 sm:px-3 sm:py-1.5 dark:border-emerald-500 dark:bg-emerald-600 dark:hover:bg-emerald-700"
      >
        {submitting ? "…" : approveLabel}
      </button>
      {!hideReject && (
        <button
          type="button"
          onClick={onRejectClick}
          disabled={submitting}
          // eslint-disable-next-line jsx-a11y/no-autofocus
          autoFocus={actionHint === "reject"}
          data-task-action="reject"
          className="flex-1 rounded border border-red-500 bg-white px-3 py-2 text-xs font-semibold uppercase tracking-wide text-red-700 hover:bg-red-50 disabled:opacity-50 disabled:cursor-not-allowed min-h-[44px] sm:flex-none sm:min-h-0 sm:px-3 sm:py-1.5 dark:border-red-700 dark:bg-zinc-900 dark:text-red-300 dark:hover:bg-red-950/40"
        >
          Reject
        </button>
      )}
      <button
        type="button"
        onClick={onHaltClick}
        disabled={submitting}
        data-task-action="halt"
        className="flex-1 rounded border border-amber-500 bg-white px-3 py-2 text-xs font-semibold uppercase tracking-wide text-amber-700 hover:bg-amber-50 disabled:opacity-50 disabled:cursor-not-allowed min-h-[44px] sm:flex-none sm:min-h-0 sm:px-3 sm:py-1.5 dark:border-amber-600 dark:bg-zinc-900 dark:text-amber-300 dark:hover:bg-amber-950/40"
      >
        Halt
      </button>
      <Link
        href={openFullHref}
        data-task-action="open-full"
        className="flex flex-1 items-center justify-center rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-semibold uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 min-h-[44px] sm:flex-none sm:min-h-0 sm:px-3 sm:py-1.5 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
      >
        Open full
      </Link>
    </div>
  );
}
