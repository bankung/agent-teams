"use client";

// ConnectionStateBadge — small colored dot + label reflecting the live SSE
// subscription state. Mounts in the Board header (next to ProjectSwitcher).
//
// Kanban #783.

import type { ConnectionState } from "@/lib/useRowChangedEvents";

type Props = {
  state: ConnectionState;
  lastEventAt: Date | null;
};

const DOT_CLASS: Record<ConnectionState, string> = {
  connecting: "bg-zinc-400 dark:bg-zinc-500",
  open: "bg-green-500",
  reconnecting: "bg-yellow-500",
  offline: "bg-red-400",
};

const LABEL: Record<ConnectionState, string> = {
  connecting: "connecting",
  open: "live",
  reconnecting: "reconnecting",
  offline: "offline",
};

export function ConnectionStateBadge({ state, lastEventAt }: Props) {
  const tooltip =
    `state: ${state}` +
    (lastEventAt
      ? ` — last event: ${lastEventAt.toLocaleTimeString()}`
      : " — no events yet");

  return (
    <span
      className="inline-flex items-center gap-1.5 text-xs text-zinc-600 dark:text-zinc-400"
      title={tooltip}
      data-connection-state={state}
      data-connection-badge
    >
      <span
        aria-hidden
        className={`inline-block h-2 w-2 rounded-full ${DOT_CLASS[state]}`}
      />
      <span className="tabular-nums">{LABEL[state]}</span>
    </span>
  );
}
