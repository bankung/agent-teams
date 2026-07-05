// AgentsChangedBanner — Kanban #1019 (FE).
//
// Drives the banner through the REAL WildcardSSEProvider (not a mocked
// useWildcardRowChanged) using the same MockEventSource harness as
// WildcardSSEContext.strictMode.test.tsx — this is the established pattern
// for this exact SSE boundary in this repo (jsdom has no EventSource; the
// provider constructs `new EventSource(...)` directly, so it's the mock
// point, not the hook). Doubles as the "assertion that an agents event
// routes to onAgentsChange" called out in the task's deliverable #4.
//
// Coverage:
//   - banner hidden on initial render (no event yet)
//   - a `row_changed` event with table:"agents" makes it visible
//   - a `tasks`/`projects` event does NOT show it (routing is table-scoped)
//   - dismiss (X button) hides it
//   - a fresh onAgentsChange after dismiss re-shows it (spec: "re-appears")
//   - "How to restart" expands/collapses instructional text

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import * as React from "react";

import {
  WildcardSSEProvider,
  useWildcardRowChanged,
} from "@/lib/WildcardSSEContext";
import type { RowChangedEvent } from "@/lib/useRowChangedEvents";
import { AgentsChangedBanner } from "@/components/AgentsChangedBanner";

// ---------------------------------------------------------------------------
// Minimal EventSource mock — same shape as WildcardSSEContext.strictMode.test.tsx.
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

const AGENTS_EVENT: RowChangedEvent = {
  table: "agents",
  op: "changed",
  ts: "2026-07-05 00:00:00",
};

describe("AgentsChangedBanner (#1019)", () => {
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

  it("is hidden on initial render", () => {
    render(
      <WildcardSSEProvider>
        <AgentsChangedBanner />
      </WildcardSSEProvider>,
    );
    expect(
      document.querySelector("[data-agents-changed-banner]"),
    ).toBeNull();
  });

  it("appears when an agents-table row_changed event is delivered", () => {
    render(
      <WildcardSSEProvider>
        <AgentsChangedBanner />
      </WildcardSSEProvider>,
    );

    act(() => {
      latestOpenInstance().emitRowChanged(AGENTS_EVENT);
      vi.advanceTimersByTime(100);
    });

    const banner = document.querySelector("[data-agents-changed-banner]");
    expect(banner).not.toBeNull();
    expect(screen.getByText("New or changed agent detected")).toBeTruthy();
  });

  it("does NOT appear for a tasks or projects row_changed event", () => {
    render(
      <WildcardSSEProvider>
        <AgentsChangedBanner />
      </WildcardSSEProvider>,
    );

    act(() => {
      latestOpenInstance().emitRowChanged({
        table: "tasks",
        op: "update",
        id: 1,
        ts: "2026-07-05 00:00:00",
      });
      vi.advanceTimersByTime(100);
    });

    expect(
      document.querySelector("[data-agents-changed-banner]"),
    ).toBeNull();
  });

  it("dismiss (X button) hides the banner", () => {
    render(
      <WildcardSSEProvider>
        <AgentsChangedBanner />
      </WildcardSSEProvider>,
    );

    act(() => {
      latestOpenInstance().emitRowChanged(AGENTS_EVENT);
      vi.advanceTimersByTime(100);
    });
    expect(
      document.querySelector("[data-agents-changed-banner]"),
    ).not.toBeNull();

    fireEvent.click(
      screen.getByRole("button", { name: "Dismiss agent-changed notice" }),
    );

    expect(document.querySelector("[data-agents-changed-banner]")).toBeNull();
  });

  it("re-appears on a fresh onAgentsChange after a prior dismiss", () => {
    render(
      <WildcardSSEProvider>
        <AgentsChangedBanner />
      </WildcardSSEProvider>,
    );

    act(() => {
      latestOpenInstance().emitRowChanged(AGENTS_EVENT);
      vi.advanceTimersByTime(100);
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Dismiss agent-changed notice" }),
    );
    expect(document.querySelector("[data-agents-changed-banner]")).toBeNull();

    act(() => {
      latestOpenInstance().emitRowChanged(AGENTS_EVENT);
      vi.advanceTimersByTime(100);
    });

    expect(
      document.querySelector("[data-agents-changed-banner]"),
    ).not.toBeNull();
  });

  it('"How to restart" expands instructional text and can collapse again', () => {
    render(
      <WildcardSSEProvider>
        <AgentsChangedBanner />
      </WildcardSSEProvider>,
    );

    act(() => {
      latestOpenInstance().emitRowChanged(AGENTS_EVENT);
      vi.advanceTimersByTime(100);
    });

    const toggle = screen.getByRole("button", { name: "How to restart" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText(/start a new one/i)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Hide" }));
    expect(
      screen.queryByText(/start a new one/i),
    ).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Deliverable #4 (optional) — small assertion directly against
// WildcardSSEContext: an "agents" event routes to onAgentsChange (and NOT to
// onTaskChange/onProjectChange).
// ---------------------------------------------------------------------------
describe("WildcardSSEContext — agents table routing (#1019)", () => {
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

  function Subscriber({
    onAgents,
    onTask,
    onProject,
  }: {
    onAgents: (ev: RowChangedEvent) => void;
    onTask: (ev: RowChangedEvent) => void;
    onProject: (ev: RowChangedEvent) => void;
  }) {
    useWildcardRowChanged({
      onAgentsChange: onAgents,
      onTaskChange: onTask,
      onProjectChange: onProject,
    });
    return null;
  }

  it("routes a table:'agents' event to onAgentsChange only", () => {
    const agentsEvents: RowChangedEvent[] = [];
    const taskEvents: RowChangedEvent[] = [];
    const projectEvents: RowChangedEvent[] = [];

    render(
      <WildcardSSEProvider>
        <Subscriber
          onAgents={(ev) => agentsEvents.push(ev)}
          onTask={(ev) => taskEvents.push(ev)}
          onProject={(ev) => projectEvents.push(ev)}
        />
      </WildcardSSEProvider>,
    );

    act(() => {
      latestOpenInstance().emitRowChanged(AGENTS_EVENT);
      vi.advanceTimersByTime(100);
    });

    expect(agentsEvents).toHaveLength(1);
    expect(agentsEvents[0].table).toBe("agents");
    expect(taskEvents).toHaveLength(0);
    expect(projectEvents).toHaveLength(0);
  });
});
