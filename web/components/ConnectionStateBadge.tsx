"use client";

// #783 — SSE connection state badge; mounts in Board header

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
      className="inline-flex items-center"
      title={tooltip}
      data-connection-state={state}
      data-connection-badge
    >
      <span
        aria-label={LABEL[state]}
        className={`inline-block h-2 w-2 rounded-full ${DOT_CLASS[state]}`}
      />
    </span>
  );
}
