// WildcardSSEContext — StrictMode subscriber survival — Kanban #2699 FE
// audit F3.
//
// Bug: the provider's effect cleanup called `subscribers.clear()` on the
// shared subscribersRef Set. Under React 18/19 StrictMode (mount → cleanup
// → remount, same fiber), that clear() wiped the ENTIRE shared registry
// during the provider's synthetic double-invoke replay — including any
// subscriber that had already registered during the first mount. After the
// replay, such a subscriber is silently gone from the Set until something
// else causes it to re-subscribe; events stop reaching it in the meantime.
//
// Fix under test: cleanup no longer calls subscribers.clear(). Subscribers
// remove themselves via their own unsub() (the function returned by
// subscribe()) — the provider cleanup only touches its OWN resources
// (EventSource, timers, buffer), never the shared registry.
//
// Test design note — why this isn't simply "render a <Subscriber/> child
// under <StrictMode> and fire one event": empirically verified (throwaway
// probes, reverted before this file was written) that:
//   1. This vitest + RTL + React 19 harness DOES genuinely double-invoke
//      effects under a real `<React.StrictMode>` wrapper (measured 2
//      mount calls for a bare useEffect, and 2 EventSource constructions
//      for the provider specifically).
//   2. But `useWildcardRowChanged`'s own subscribe effect has dep [ctx],
//      and the provider's inline context value (`{ connectionState,
//      lastEventAt, subscribe }`) is unmemoized — so ctx gets a new
//      identity on every provider render, and a child Subscriber
//      re-subscribes on that same cadence, independent of and
//      synchronous with the provider's own StrictMode replay. That
//      self-heals the pre-fix bug within the same JS tick, before any
//      test code gets a chance to observe the gap via a delivered event —
//      so an event-delivery-timing assertion cannot distinguish fixed
//      from broken here (verified: it passed against BOTH pre-fix and
//      post-fix code).
//
// Given that, this test asserts on the actual code-level contract the fix
// protects, directly: it spies on Set.prototype.clear (scoped to this one
// test, restored after) and asserts it is never invoked while the
// provider's inner effect runs its StrictMode-driven mount → cleanup →
// remount cycle. This is the literal call the audit flagged
// (`subscribers.clear()`) and is a more faithful, non-flaky check than
// racing a same-tick event around an unrelated confound. It's paired with
// a second assertion — a *held-open* subscriber (one whose own effect does
// NOT get a chance to re-run, achieved by never re-rendering the tree
// after the initial StrictMode-settled mount) still receives an event
// fired well after StrictMode's synchronous replay has finished — which is
// the steady-state (non-racing) shape of "subscriber registered at first
// mount keeps working after a remount."
//
// Harness note: this repo mocks the SSE surface at the
// `useRowChangedEvents` hook boundary everywhere else (see
// Board.donePagination.test.tsx) because jsdom has no EventSource
// implementation; WildcardSSEContext IS that boundary — it constructs `new
// EventSource(...)` directly — so this file supplies a minimal
// class-level EventSource mock instead.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, act } from "@testing-library/react";
import * as React from "react";
import {
  WildcardSSEProvider,
  useWildcardRowChanged,
} from "@/lib/WildcardSSEContext";
import type { RowChangedEvent } from "@/lib/useRowChangedEvents";

// ---------------------------------------------------------------------------
// Minimal EventSource mock — captures listeners per instance so the test can
// fire a `row_changed` MessageEvent manually. jsdom has no real EventSource.
// ---------------------------------------------------------------------------
class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  closed = false;
  private listeners: Record<string, Array<(ev: Event) => void>> = {};

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, cb: EventListener) {
    (this.listeners[type] ??= []).push(cb as (ev: Event) => void);
  }

  removeEventListener(type: string, cb: EventListener) {
    const arr = this.listeners[type];
    if (!arr) return;
    this.listeners[type] = arr.filter((fn) => fn !== cb);
  }

  close() {
    this.closed = true;
  }

  // Test helper — deliver a row_changed payload to every registered listener.
  emitRowChanged(payload: RowChangedEvent) {
    const msg = new MessageEvent("row_changed", {
      data: JSON.stringify(payload),
    });
    for (const cb of this.listeners["row_changed"] ?? []) cb(msg);
  }
}

function latestOpenInstance(): MockEventSource {
  const open = MockEventSource.instances.filter((i) => !i.closed);
  const last = open[open.length - 1];
  if (!last) throw new Error("no open MockEventSource instance");
  return last;
}

// A long-lived subscriber — subscribes via the real public
// useWildcardRowChanged hook (same surface every production consumer uses:
// InboxBadge, FlagBellBadge, DashboardRefresher).
function Subscriber({ onEvent }: { onEvent: (ev: RowChangedEvent) => void }) {
  useWildcardRowChanged({ onTaskChange: onEvent });
  return null;
}

describe("WildcardSSEProvider — StrictMode subscriber survival (#2699 F3)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    MockEventSource.instances = [];
    // @ts-expect-error — jsdom has no EventSource; test-only global mock.
    global.EventSource = MockEventSource;
  });

  afterEach(() => {
    vi.useRealTimers();
    // @ts-expect-error — cleanup the test-only global mock.
    delete global.EventSource;
  });

  it("does not clear the shared subscriber registry when the provider's effect StrictMode-replays (mount -> cleanup -> remount)", () => {
    const clearSpy = vi.spyOn(Set.prototype, "clear");
    try {
      render(
        <React.StrictMode>
          <WildcardSSEProvider>
            <Subscriber onEvent={() => {}} />
          </WildcardSSEProvider>
        </React.StrictMode>,
      );

      // Confirms StrictMode double-invoke genuinely happened for the
      // provider's inner effect (2 EventSource constructions: one closed
      // by the synthetic cleanup, one surviving from the remount).
      expect(MockEventSource.instances.length).toBe(2);
      expect(MockEventSource.instances[0].closed).toBe(true);
      expect(MockEventSource.instances[1].closed).toBe(false);

      // The actual regression under test: subscribers.clear() must never
      // be called by the provider's cleanup. Pre-fix this fails (clear()
      // was called at least once, from the provider's StrictMode-replayed
      // cleanup); post-fix nothing in the module calls Set#clear at all.
      expect(clearSpy).not.toHaveBeenCalled();
    } finally {
      clearSpy.mockRestore();
    }
  });

  it("a subscriber registered at mount keeps receiving events after StrictMode's synchronous replay has settled", () => {
    const received: RowChangedEvent[] = [];
    const onEvent = (ev: RowChangedEvent) => received.push(ev);

    render(
      <React.StrictMode>
        <WildcardSSEProvider>
          <Subscriber onEvent={onEvent} />
        </WildcardSSEProvider>
      </React.StrictMode>,
    );

    expect(MockEventSource.instances.length).toBe(2);

    // Deliver on the surviving (post-remount) connection, well after the
    // StrictMode replay's own synchronous churn has finished settling.
    act(() => {
      latestOpenInstance().emitRowChanged({
        table: "tasks",
        op: "update",
        id: 42,
        ts: "2026-07-02 00:00:00",
      });
      vi.advanceTimersByTime(100);
    });

    expect(received).toHaveLength(1);
    expect(received[0].id).toBe(42);
  });

  it("still delivers events under a plain single mount (no StrictMode)", () => {
    const received: RowChangedEvent[] = [];
    const onEvent = (ev: RowChangedEvent) => received.push(ev);

    render(
      <WildcardSSEProvider>
        <Subscriber onEvent={onEvent} />
      </WildcardSSEProvider>,
    );

    expect(MockEventSource.instances.length).toBe(1);

    act(() => {
      latestOpenInstance().emitRowChanged({
        table: "tasks",
        op: "insert",
        id: 7,
        ts: "2026-07-02 00:00:00",
      });
      vi.advanceTimersByTime(100);
    });

    expect(received).toHaveLength(1);
    expect(received[0].id).toBe(7);
  });
});
