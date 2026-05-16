"use client";

// #783 — SSE hook for row_changed events; debounce 100ms + 5-event hard cap; HINT only — always refetch via REST

import { useEffect, useRef, useState } from "react";

export type RowChangedEvent = {
  table: "tasks" | "projects";
  op: "insert" | "update" | "delete";
  id: number;
  // project_id is omitted on `projects`-table events (per backend contract).
  project_id?: number;
  // `ts` is opaque — Postgres `now()::text`, NOT ISO-8601. Treat as a hint
  // string. Parsing to Date is a follow-up if needed (see open question in
  // dev-frontend session note).
  ts: string;
};

export type ConnectionState =
  | "connecting"
  | "open"
  | "reconnecting"
  | "offline";

export type UseRowChangedEventsArgs = {
  projectId?: number;
  onTaskChange?: (ev: RowChangedEvent) => void;
  onProjectChange?: (ev: RowChangedEvent) => void;
  debounceMs?: number;
};

export type UseRowChangedEventsResult = {
  connectionState: ConnectionState;
  lastEventAt: Date | null;
};

// Hard ceiling: flush after this many buffered events even if the trailing
// timer would push out further. Guarantees forward progress under bursty load.
const HARD_FLUSH_COUNT = 5;
// Hard ceiling: flush after this many ms since first buffered event in the
// current window. Independent of debounceMs; whichever fires first wins.
const HARD_FLUSH_MS = 250;

// EventSource URL; mirrors browser split in api.ts but browser-only
function apiBaseUrl(): string {
  return process.env.NEXT_PUBLIC_API_URL ?? "";
}

export function useRowChangedEvents(
  args: UseRowChangedEventsArgs,
): UseRowChangedEventsResult {
  const { projectId, onTaskChange, onProjectChange, debounceMs = 100 } = args;

  const [connectionState, setConnectionState] =
    useState<ConnectionState>("connecting");
  const [lastEventAt, setLastEventAt] = useState<Date | null>(null);

  // Stable refs for callbacks + debounce knob — safe for inline arrow functions
  const onTaskChangeRef = useRef(onTaskChange);
  const onProjectChangeRef = useRef(onProjectChange);
  const debounceMsRef = useRef(debounceMs);
  useEffect(() => {
    onTaskChangeRef.current = onTaskChange;
    onProjectChangeRef.current = onProjectChange;
    debounceMsRef.current = debounceMs;
  }, [onTaskChange, onProjectChange, debounceMs]);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const url =
      apiBaseUrl() +
      "/api/events/stream" +
      (projectId !== undefined ? `?project_id=${projectId}` : "");
    const es = new EventSource(url);

    // Per-burst buffer: trailing-edge flush; hard cap at HARD_FLUSH_COUNT / HARD_FLUSH_MS
    let buffer: RowChangedEvent[] = [];
    let firstEventMs: number | null = null;
    let trailingTimer: ReturnType<typeof setTimeout> | null = null;
    let hardCapTimer: ReturnType<typeof setTimeout> | null = null;

    const clearTimers = () => {
      if (trailingTimer !== null) {
        clearTimeout(trailingTimer);
        trailingTimer = null;
      }
      if (hardCapTimer !== null) {
        clearTimeout(hardCapTimer);
        hardCapTimer = null;
      }
    };

    const flush = () => {
      clearTimers();
      if (buffer.length === 0) {
        firstEventMs = null;
        return;
      }
      const batch = buffer;
      buffer = [];
      firstEventMs = null;
      // Route events to task/project callbacks; caller coalesces (e.g. router.refresh())
      for (const ev of batch) {
        if (ev.table === "tasks") {
          onTaskChangeRef.current?.(ev);
        } else if (ev.table === "projects") {
          onProjectChangeRef.current?.(ev);
        }
      }
    };

    const scheduleFlush = () => {
      if (trailingTimer !== null) clearTimeout(trailingTimer);
      trailingTimer = setTimeout(flush, debounceMsRef.current);
      if (firstEventMs === null) {
        firstEventMs = Date.now();
        if (hardCapTimer !== null) clearTimeout(hardCapTimer);
        hardCapTimer = setTimeout(flush, HARD_FLUSH_MS);
      }
    };

    const onRowChanged = (msg: MessageEvent<string>) => {
      let parsed: RowChangedEvent;
      try {
        parsed = JSON.parse(msg.data) as RowChangedEvent;
      } catch {
        // Malformed payload: drop silently; REST refetch will reconcile
        return;
      }
      setLastEventAt(new Date());
      buffer.push(parsed);
      if (buffer.length >= HARD_FLUSH_COUNT) {
        flush();
        return;
      }
      scheduleFlush();
    };

    const onOpen = () => {
      setConnectionState("open");
    };

    const onError = () => {
      // Browser auto-reconnects; we surface 'reconnecting' as a hint but don't close
      setConnectionState("reconnecting");
    };

    es.addEventListener("row_changed", onRowChanged as EventListener);
    es.addEventListener("open", onOpen);
    es.addEventListener("error", onError);

    return () => {
      // Cleanup: close EventSource + clear timers + reset state
      es.removeEventListener("row_changed", onRowChanged as EventListener);
      es.removeEventListener("open", onOpen);
      es.removeEventListener("error", onError);
      es.close();
      clearTimers();
      buffer = [];
      firstEventMs = null;
      setConnectionState("offline");
    };
  }, [projectId]);

  return { connectionState, lastEventAt };
}
