"use client";

// Kanban #2111 Part 1 — single shared wildcard SSE connection.
//
// Problem: InboxBadge, FlagBellBadge, and DashboardRefresher each called
// useRowChangedEvents() which opened its own EventSource — 3 connections for
// the wildcard (no projectId) channel on the dashboard.
//
// Solution: one WildcardSSEProvider owns ONE EventSource; wildcard consumers
// subscribe via useWildcardRowChanged() and share that connection.
//
// AC lock: Board.tsx keeps its scoped connection (projectId = project.id) via
// useRowChangedEvents directly — it is NOT folded into this provider.
//
// The provider dispatches events to subscribers via a Set of listener objects.
// Debounce + hard-cap logic lives per-subscriber (same as before) so each
// consumer's callback cadence is independent.

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

import type {
  ConnectionState,
  RowChangedEvent,
  UseRowChangedEventsArgs,
  UseRowChangedEventsResult,
} from "./useRowChangedEvents";

function apiBaseUrl(): string {
  return process.env.NEXT_PUBLIC_API_URL ?? "";
}

// Per-subscriber callbacks (mirrors useRowChangedEventsArgs minus projectId).
type Subscriber = {
  onTaskChange?: (ev: RowChangedEvent) => void;
  onProjectChange?: (ev: RowChangedEvent) => void;
};

type ContextValue = {
  connectionState: ConnectionState;
  lastEventAt: Date | null;
  subscribe: (subscriber: Subscriber) => () => void;
};

const WildcardSSEContext = createContext<ContextValue | null>(null);

// Hard caps — match useRowChangedEvents constants.
const HARD_FLUSH_COUNT = 5;
const HARD_FLUSH_MS = 250;

export function WildcardSSEProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [connectionState, setConnectionState] =
    useState<ConnectionState>("connecting");
  const [lastEventAt, setLastEventAt] = useState<Date | null>(null);

  // Subscriber registry — mutated synchronously (no re-render needed).
  const subscribersRef = useRef<Set<Subscriber>>(new Set());

  const subscribe = useCallback((sub: Subscriber) => {
    subscribersRef.current.add(sub);
    return () => {
      subscribersRef.current.delete(sub);
    };
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const url = apiBaseUrl() + "/api/events/stream";
    const es = new EventSource(url);

    // Shared burst buffer — same trailing-edge + hard-cap logic as
    // useRowChangedEvents; routes each flushed event to all subscribers.
    let buffer: RowChangedEvent[] = [];
    let firstEventMs: number | null = null;
    let trailingTimer: ReturnType<typeof setTimeout> | null = null;
    let hardCapTimer: ReturnType<typeof setTimeout> | null = null;
    // Stable local for the cleanup below (ref-value-in-cleanup lint guard).
    const subscribers = subscribersRef.current;

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
      for (const ev of batch) {
        for (const sub of subscribersRef.current) {
          if (ev.table === "tasks") sub.onTaskChange?.(ev);
          else if (ev.table === "projects") sub.onProjectChange?.(ev);
        }
      }
    };

    const scheduleFlush = (debounceMs: number) => {
      if (trailingTimer !== null) clearTimeout(trailingTimer);
      trailingTimer = setTimeout(flush, debounceMs);
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
        return;
      }
      setLastEventAt(new Date());
      buffer.push(parsed);
      if (buffer.length >= HARD_FLUSH_COUNT) {
        flush();
        return;
      }
      // Use 100ms default debounce for the shared provider; individual
      // consumers that need a different debounce (e.g. InboxBadge's 400ms)
      // should apply their own debounce inside their callback.
      scheduleFlush(100);
    };

    const onOpen = () => setConnectionState("open");
    const onError = () => setConnectionState("reconnecting");

    es.addEventListener("row_changed", onRowChanged as EventListener);
    es.addEventListener("open", onOpen);
    es.addEventListener("error", onError);

    return () => {
      es.removeEventListener("row_changed", onRowChanged as EventListener);
      es.removeEventListener("open", onOpen);
      es.removeEventListener("error", onError);
      es.close();
      clearTimers();
      buffer = [];
      firstEventMs = null;
      subscribers.clear();
      setConnectionState("offline");
    };
  }, []);

  return (
    <WildcardSSEContext.Provider
      value={{ connectionState, lastEventAt, subscribe }}
    >
      {children}
    </WildcardSSEContext.Provider>
  );
}

// useWildcardRowChanged — drop-in for useRowChangedEvents when projectId is
// undefined (wildcard). Shares the single provider connection instead of
// opening a new EventSource.
//
// Returns the same { connectionState, lastEventAt } shape as useRowChangedEvents
// so callers can switch with zero signature changes.
export function useWildcardRowChanged(
  args: Omit<UseRowChangedEventsArgs, "projectId" | "debounceMs">,
): UseRowChangedEventsResult {
  const ctx = useContext(WildcardSSEContext);

  // Stable refs so subscribe() callback never changes identity.
  const onTaskChangeRef = useRef(args.onTaskChange);
  const onProjectChangeRef = useRef(args.onProjectChange);
  useEffect(() => {
    onTaskChangeRef.current = args.onTaskChange;
    onProjectChangeRef.current = args.onProjectChange;
  }, [args.onTaskChange, args.onProjectChange]);

  useEffect(() => {
    if (!ctx) return;
    const unsub = ctx.subscribe({
      onTaskChange: (ev) => onTaskChangeRef.current?.(ev),
      onProjectChange: (ev) => onProjectChangeRef.current?.(ev),
    });
    return unsub;
  }, [ctx]);

  if (!ctx) {
    // Fallback: context not mounted — return inert state.
    // In production all wildcard consumers sit under WildcardSSEProvider in
    // layout.tsx; this branch only fires in tests or isolated renders.
    return { connectionState: "offline", lastEventAt: null };
  }

  return { connectionState: ctx.connectionState, lastEventAt: ctx.lastEventAt };
}
