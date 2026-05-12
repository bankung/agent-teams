"use client";

// useRowChangedEvents — subscribes to GET /api/events/stream (SSE) for the
// session-bound project. Routes parsed `row_changed` payloads to caller-supplied
// callbacks; surfaces a `connectionState` + `lastEventAt` for header badge UX.
//
// Payload is a HINT — always refetch via REST after the callback. Server
// guarantees no payload-as-canonical-state (per Kanban #782 design). The hook
// is a thin browser-EventSource wrapper; it does NOT cache or coalesce data,
// only event arrival timing (debounce buffer flushes a SINGLE notification per
// burst to the caller).
//
// Kanban #783 — frontend half of real-time push.

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

// API base for the EventSource URL. Mirrors the browser/SSR split in
// web/lib/api.ts but EventSource is browser-only — we never read this server-
// side (guarded by typeof window check below). The cookie-style absolute URL
// keeps fetch + EventSource pointing at the same origin.
function apiBaseUrl(): string {
  return process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8456";
}

export function useRowChangedEvents(
  args: UseRowChangedEventsArgs,
): UseRowChangedEventsResult {
  const { projectId, onTaskChange, onProjectChange, debounceMs = 100 } = args;

  const [connectionState, setConnectionState] =
    useState<ConnectionState>("connecting");
  const [lastEventAt, setLastEventAt] = useState<Date | null>(null);

  // Stable refs over the latest callback values + debounce knob so the
  // EventSource effect doesn't tear down on every parent re-render. Caller
  // can pass inline arrow functions safely.
  const onTaskChangeRef = useRef(onTaskChange);
  const onProjectChangeRef = useRef(onProjectChange);
  const debounceMsRef = useRef(debounceMs);
  useEffect(() => {
    onTaskChangeRef.current = onTaskChange;
    onProjectChangeRef.current = onProjectChange;
    debounceMsRef.current = debounceMs;
  }, [onTaskChange, onProjectChange, debounceMs]);

  useEffect(() => {
    // EventSource is a browser-only API. SSR / Node test env: no-op.
    if (typeof window === "undefined") return;

    const url =
      apiBaseUrl() +
      "/api/events/stream" +
      (projectId !== undefined ? `?project_id=${projectId}` : "");
    const es = new EventSource(url);

    // Per-burst debounce buffer. Trailing-edge flush via setTimeout; reset on
    // every new event until either (a) trailing timer fires, (b) buffer hits
    // HARD_FLUSH_COUNT, or (c) HARD_FLUSH_MS elapsed since first event.
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
      // Fire one callback per event — caller decides how to coalesce on its
      // side (typical: a single router.refresh() regardless of count, which
      // Next 14 dedupes anyway). We don't merge here because tasks vs projects
      // events route to different callbacks.
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
        // Independent hard-cap timer; survives debounce-reset.
        if (hardCapTimer !== null) clearTimeout(hardCapTimer);
        hardCapTimer = setTimeout(flush, HARD_FLUSH_MS);
      }
    };

    const onRowChanged = (msg: MessageEvent<string>) => {
      let parsed: RowChangedEvent;
      try {
        parsed = JSON.parse(msg.data) as RowChangedEvent;
      } catch {
        // Malformed payload — drop silently. (Server is the only writer; if
        // this fires, it's a contract drift and the next REST refetch will
        // still reconcile state.)
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
      // Browser auto-reconnects on transient drop. We surface "reconnecting"
      // for visibility but do not close — closing would defeat the built-in
      // reconnect. EventSource has no public "is it actually retrying?" flag,
      // so this state is a best-effort hint.
      setConnectionState("reconnecting");
    };

    es.addEventListener("row_changed", onRowChanged as EventListener);
    es.addEventListener("open", onOpen);
    es.addEventListener("error", onError);

    return () => {
      // Strict mode-safe: idempotent close + clear timers.
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
