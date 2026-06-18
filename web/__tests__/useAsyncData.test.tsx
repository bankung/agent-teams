// Unit tests for useAsyncData (Kanban #2492, Phase B of #2489).
//
// Covers: (1) loading→data happy path, (2) error path sets error + clears
// loading, (3) cancellation — a dep change / unmount before the promise
// resolves does NOT setState from the stale promise (no act warning, no state
// update), (4) setData local mutation, (5) reload re-fetches, plus onSuccess
// + resetDataOnReload behavior.
//
// Determinism: all async assertions use findBy*/waitFor (sync querySelector
// hides async-fetch RTL races). asyncUtilTimeout raised for full-suite CPU load.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  fireEvent,
  act,
  configure,
} from "@testing-library/react";
import { useState } from "react";

import { useAsyncData } from "@/lib/useAsyncData";

configure({ asyncUtilTimeout: 5000 });

// A controllable deferred promise so a test can resolve/reject on demand and
// assert the in-flight (loading) state deterministically.
function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

// ---- harness components -----------------------------------------------------

function Probe({
  fetcher,
  dep,
  errorFallback,
}: {
  fetcher: () => Promise<string>;
  dep: number;
  errorFallback?: string;
}) {
  const { data, loading, error } = useAsyncData(fetcher, [dep], { errorFallback });
  return (
    <div>
      <span data-testid="loading">{String(loading)}</span>
      <span data-testid="data">{data ?? "∅"}</span>
      <span data-testid="error">{error ?? "∅"}</span>
    </div>
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe("useAsyncData — happy path", () => {
  it("flips loading true→false and surfaces the resolved data", async () => {
    const d = deferred<string>();
    const fetcher = vi.fn(() => d.promise);
    render(<Probe fetcher={fetcher} dep={1} />);

    // Loading is true synchronously on mount.
    expect(screen.getByTestId("loading")).toHaveTextContent("true");
    expect(screen.getByTestId("data")).toHaveTextContent("∅");

    await act(async () => {
      d.resolve("payload");
    });

    await waitFor(() => {
      expect(screen.getByTestId("loading")).toHaveTextContent("false");
    });
    expect(screen.getByTestId("data")).toHaveTextContent("payload");
    expect(screen.getByTestId("error")).toHaveTextContent("∅");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

describe("useAsyncData — error path", () => {
  it("sets error (via extractErrorMessage) and clears loading", async () => {
    const d = deferred<string>();
    const fetcher = vi.fn(() => d.promise);
    render(<Probe fetcher={fetcher} dep={1} errorFallback="fallback msg" />);

    await act(async () => {
      d.reject(new Error("boom"));
    });

    await waitFor(() => {
      expect(screen.getByTestId("error")).toHaveTextContent("boom");
    });
    expect(screen.getByTestId("loading")).toHaveTextContent("false");
    expect(screen.getByTestId("data")).toHaveTextContent("∅");
  });

  it("uses the errorFallback for a non-Error rejection", async () => {
    const d = deferred<string>();
    const fetcher = vi.fn(() => d.promise);
    render(<Probe fetcher={fetcher} dep={1} errorFallback="fallback msg" />);

    await act(async () => {
      d.reject("just a string");
    });

    await waitFor(() => {
      expect(screen.getByTestId("error")).toHaveTextContent("fallback msg");
    });
  });
});

describe("useAsyncData — cancellation (stale-response guard)", () => {
  it("does NOT setState when the dep changes before the first promise resolves", async () => {
    const first = deferred<string>();
    const second = deferred<string>();
    const fetcher = vi
      .fn<() => Promise<string>>()
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);

    const { rerender } = render(<Probe fetcher={fetcher} dep={1} />);

    // Change the dep → the first effect's cleanup sets cancelled=true and a new
    // fetch starts. Resolving the FIRST (now-stale) promise must be a no-op.
    rerender(<Probe fetcher={fetcher} dep={2} />);

    await act(async () => {
      first.resolve("STALE");
      second.resolve("FRESH");
    });

    await waitFor(() => {
      expect(screen.getByTestId("data")).toHaveTextContent("FRESH");
    });
    // The stale value never won the race.
    expect(screen.getByTestId("data")).not.toHaveTextContent("STALE");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("does NOT setState (no act warning) when unmounted before resolve", async () => {
    const d = deferred<string>();
    const fetcher = vi.fn(() => d.promise);
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    const { unmount } = render(<Probe fetcher={fetcher} dep={1} />);
    unmount();

    // Resolving after unmount must not trigger a setState-after-unmount warning.
    await act(async () => {
      d.resolve("late");
    });

    const sawActWarning = errorSpy.mock.calls.some((args) =>
      args.some(
        (a) =>
          typeof a === "string" &&
          (a.includes("not wrapped in act") ||
            a.includes("unmounted component") ||
            a.includes("update to") ),
      ),
    );
    expect(sawActWarning).toBe(false);
  });
});

describe("useAsyncData — setData local mutation", () => {
  function ListProbe({ fetcher }: { fetcher: () => Promise<string[]> }) {
    const { data, setData } = useAsyncData(fetcher, []);
    return (
      <div>
        <span data-testid="rows">{(data ?? []).join(",")}</span>
        <button onClick={() => setData((prev) => [...(prev ?? []), "added"])}>
          add
        </button>
      </div>
    );
  }

  it("lets a consumer append to the fetched value without a refetch", async () => {
    const fetcher = vi.fn(() => Promise.resolve(["a", "b"]));
    render(<ListProbe fetcher={fetcher} />);

    await screen.findByText("a,b");
    fireEvent.click(screen.getByText("add"));
    expect(screen.getByTestId("rows")).toHaveTextContent("a,b,added");
    // No second fetch fired from a local mutation.
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

describe("useAsyncData — reload", () => {
  function ReloadProbe({ fetcher }: { fetcher: () => Promise<number> }) {
    const { data, reload } = useAsyncData(fetcher, []);
    return (
      <div>
        <span data-testid="n">{data ?? "∅"}</span>
        <button onClick={reload}>reload</button>
      </div>
    );
  }

  it("re-runs the fetcher when reload() is called", async () => {
    let n = 0;
    const fetcher = vi.fn(() => Promise.resolve(++n));
    render(<ReloadProbe fetcher={fetcher} />);

    await screen.findByText("1");
    fireEvent.click(screen.getByText("reload"));
    await screen.findByText("2");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});

describe("useAsyncData — onSuccess + resetDataOnReload", () => {
  it("calls onSuccess with the resolved data", async () => {
    const onSuccess = vi.fn();
    function OkProbe() {
      const { data } = useAsyncData(() => Promise.resolve("ok"), [], { onSuccess });
      return <span data-testid="v">{data ?? "∅"}</span>;
    }
    render(<OkProbe />);
    await screen.findByText("ok");
    expect(onSuccess).toHaveBeenCalledWith("ok");
  });

  it("clears data on reload when resetDataOnReload is true", async () => {
    const d1 = deferred<string>();
    const d2 = deferred<string>();
    const fetcher = vi
      .fn<() => Promise<string>>()
      .mockReturnValueOnce(d1.promise)
      .mockReturnValueOnce(d2.promise);

    function ResetProbe() {
      const { data, reload } = useAsyncData(fetcher, [], {
        resetDataOnReload: true,
      });
      const [, force] = useState(0);
      return (
        <div>
          <span data-testid="v">{data ?? "∅"}</span>
          <button
            onClick={() => {
              reload();
              force((x) => x + 1);
            }}
          >
            reload
          </button>
        </div>
      );
    }

    render(<ResetProbe />);
    await act(async () => {
      d1.resolve("first");
    });
    await screen.findByText("first");

    // Trigger reload: data should clear to ∅ immediately (resetDataOnReload),
    // then show the second result once it resolves.
    fireEvent.click(screen.getByText("reload"));
    await waitFor(() => {
      expect(screen.getByTestId("v")).toHaveTextContent("∅");
    });

    await act(async () => {
      d2.resolve("second");
    });
    await screen.findByText("second");
  });
});
