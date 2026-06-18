// Unit tests for usePersistentState (Kanban #2491, Phase A of #2489).
//
// Covers: default-on-empty, read-existing, setValue-persists, cross-instance
// same-tab sync (synthetic storage event), corrupt-value fallback, and a
// render-count bound that proves the stable-snapshot cache prevents the
// infinite-loop footgun documented in the hook.

import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { useEffect, useRef } from "react";

import { usePersistentState, useIsHydrated } from "@/lib/usePersistentState";

beforeEach(() => {
  window.localStorage.clear();
});

// ---- harness components -----------------------------------------------------

function StringProbe({ k, def }: { k: string; def: string }) {
  const [value, setValue] = usePersistentState<string>(k, def, {
    serialize: (v) => v,
    deserialize: (raw) => raw,
  });
  return (
    <div>
      <span data-testid="value">{value}</span>
      <button onClick={() => setValue("written")}>set</button>
      <button onClick={() => setValue((p) => p + "!")}>append</button>
    </div>
  );
}

function ObjectProbe({ k }: { k: string }) {
  // Commit counter (incremented in an effect — the lint-clean way; a ref write
  // in render violates react-hooks/refs). An infinite render loop inflates the
  // commit count AND makes React throw "Maximum update depth exceeded", so a
  // clean render with a tiny count proves the stable-snapshot cache works.
  const commits = useRef(0);
  const el = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    commits.current += 1;
    if (el.current) el.current.textContent = String(commits.current);
  });
  const [value, setValue] = usePersistentState<{ n: number }>(k, { n: 0 });
  return (
    <div>
      <span data-testid="n">{value.n}</span>
      <span data-testid="commits" ref={el} />
      <button onClick={() => setValue({ n: value.n + 1 })}>inc</button>
    </div>
  );
}

function BoolProbe({ k }: { k: string }) {
  // Mirrors the collapse-panel migration: default expanded; stored `false`
  // means collapsed.
  const [expanded] = usePersistentState<boolean>(k, true, {
    deserialize: (raw) => JSON.parse(raw) !== false,
  });
  return <span data-testid="expanded">{String(expanded)}</span>;
}

function HydratedProbe() {
  return <span data-testid="hydrated">{String(useIsHydrated())}</span>;
}

// ---- tests ------------------------------------------------------------------

describe("usePersistentState", () => {
  it("returns the default when storage is empty", () => {
    render(<StringProbe k="probe.string" def="fallback" />);
    expect(screen.getByTestId("value")).toHaveTextContent("fallback");
  });

  it("reads an existing stored value (deserialized)", () => {
    window.localStorage.setItem("probe.string", "stored-val");
    render(<StringProbe k="probe.string" def="fallback" />);
    expect(screen.getByTestId("value")).toHaveTextContent("stored-val");
  });

  it("setValue persists to localStorage and updates the returned value", () => {
    render(<StringProbe k="probe.string" def="fallback" />);
    fireEvent.click(screen.getByText("set"));
    expect(screen.getByTestId("value")).toHaveTextContent("written");
    expect(window.localStorage.getItem("probe.string")).toBe("written");
  });

  it("supports a functional updater against the live stored value", () => {
    window.localStorage.setItem("probe.string", "a");
    render(<StringProbe k="probe.string" def="fallback" />);
    fireEvent.click(screen.getByText("append"));
    expect(screen.getByTestId("value")).toHaveTextContent("a!");
    expect(window.localStorage.getItem("probe.string")).toBe("a!");
  });

  it("a second hook instance syncs when the first writes (same-tab synthetic event)", () => {
    // Two independent components sharing the same key. The writer dispatches a
    // synthetic StorageEvent; the reader's subscribe() re-reads.
    render(
      <>
        <div data-testid="writer">
          <StringProbe k="shared.key" def="init" />
        </div>
        <div data-testid="reader">
          <BoolProbe k="shared.key" />
        </div>
      </>,
    );
    // BoolProbe deserializes JSON.parse(raw) !== false; "init" is not "false"
    // (and not valid JSON) — but it never parses because the writer hasn't
    // written yet, so it shows the default `true`.
    expect(screen.getByTestId("expanded")).toHaveTextContent("true");

    // Write `false` via a raw localStorage + synthetic event to mimic a sibling
    // writer (the boolean collapse case stores JSON).
    act(() => {
      window.localStorage.setItem("shared.key", "false");
      window.dispatchEvent(
        new StorageEvent("storage", {
          key: "shared.key",
          newValue: "false",
          storageArea: localStorage,
        }),
      );
    });
    expect(screen.getByTestId("expanded")).toHaveTextContent("false");
  });

  it("two instances of the SAME hook stay in sync on write", () => {
    function Pair() {
      return (
        <>
          <StringProbe k="pair.key" def="d" />
          <span data-testid="mirror">
            <Mirror k="pair.key" />
          </span>
        </>
      );
    }
    function Mirror({ k }: { k: string }) {
      const [v] = usePersistentState<string>(k, "d", {
        serialize: (x) => x,
        deserialize: (raw) => raw,
      });
      return <span data-testid="mirror-val">{v}</span>;
    }
    render(<Pair />);
    expect(screen.getByTestId("mirror-val")).toHaveTextContent("d");
    fireEvent.click(screen.getByText("set"));
    // The writer's synthetic StorageEvent propagates to the mirror instance.
    expect(screen.getByTestId("mirror-val")).toHaveTextContent("written");
  });

  it("falls back to the default when the stored value is corrupt / unparseable", () => {
    // ObjectProbe uses the default JSON.parse deserializer; a non-JSON raw
    // string throws → hook returns defaultValue ({ n: 0 }).
    window.localStorage.setItem("probe.object", "{not valid json");
    render(<ObjectProbe k="probe.object" />);
    expect(screen.getByTestId("n")).toHaveTextContent("0");
  });

  it("object-valued default on EMPTY storage stays bounded (frozen default ref)", () => {
    // The other danger case: an object default passed as a fresh literal each
    // render. If getSnapshot returned that fresh ref on empty storage, Object.is
    // would always fail → infinite loop. The frozen-default ref prevents it.
    render(<ObjectProbe k="probe.empty.object" />);
    expect(screen.getByTestId("n")).toHaveTextContent("0");
    const commits = Number(screen.getByTestId("commits").textContent);
    expect(commits).toBeGreaterThan(0);
    expect(commits).toBeLessThanOrEqual(5);
  });

  it("keeps render count bounded — stable snapshot cache prevents an infinite loop", () => {
    // Object-valued key: the danger case. If getSnapshot returned a fresh parse
    // each render, Object.is(prev,next) would always be false → infinite loop.
    window.localStorage.setItem("probe.object", JSON.stringify({ n: 7 }));
    render(<ObjectProbe k="probe.object" />);
    expect(screen.getByTestId("n")).toHaveTextContent("7");
    // A handful of commits is fine (mount + StrictMode double-invoke + the
    // useSyncExternalStore client-snapshot settle). An unbounded loop would be
    // hundreds/thousands (and React would throw). Assert a small ceiling.
    const commits = Number(screen.getByTestId("commits").textContent);
    expect(commits).toBeGreaterThan(0);
    expect(commits).toBeLessThanOrEqual(5);
  });

  it("does not loop after a write either (cache stays hot)", () => {
    window.localStorage.setItem("probe.object", JSON.stringify({ n: 0 }));
    render(<ObjectProbe k="probe.object" />);
    fireEvent.click(screen.getByText("inc"));
    expect(screen.getByTestId("n")).toHaveTextContent("1");
    const commits = Number(screen.getByTestId("commits").textContent);
    // mount + one update settle; still tiny.
    expect(commits).toBeLessThanOrEqual(8);
  });
});

describe("useIsHydrated", () => {
  it("returns true after mount in the client test environment", () => {
    render(<HydratedProbe />);
    // jsdom render flushes effects; client snapshot = true.
    expect(screen.getByTestId("hydrated")).toHaveTextContent("true");
  });
});
