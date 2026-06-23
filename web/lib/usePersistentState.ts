"use client";

// usePersistentState — Kanban #2491 (Phase A of the #2489 set-state-in-effect
// remediation). One shared hook over React's useSyncExternalStore for the
// "hydrate-from-localStorage" pattern that ~18 components previously open-coded
// as `useState(default) + useEffect(() => setState(readStorage()))` (the
// react-hooks/set-state-in-effect warning).
//
// Why useSyncExternalStore: localStorage IS an external store. It cannot be
// read during SSR, so every site used to start at a default via useState and
// then setState the stored value in an effect. useSyncExternalStore is React's
// sanctioned API for this — it returns the SERVER snapshot during SSR + the
// initial hydration render (so no hydration mismatch), then re-renders with the
// CLIENT snapshot WITHOUT a setState-in-effect.
//
// Behavior is byte-for-byte equivalent to the prior pattern + collapseState.ts:
//   * SSR / first paint = defaultValue (server snapshot).
//   * After hydration = stored value (deserialize), or defaultValue if absent /
//     unparseable / storage blocked.
//   * Cross-tab sync via the native "storage" event; same-tab sync via the
//     synthetic StorageEvent the writer dispatches (mirrors writeExpanded).

import { useRef, useSyncExternalStore } from "react";

export type PersistentStateOptions<T> = {
  serialize?: (v: T) => string;
  deserialize?: (raw: string) => T;
};

// Module-level subscribe: identity is constant across all renders + call sites
// (it closes over nothing) so useSyncExternalStore never needlessly
// re-subscribes. Cross-tab "storage" events are native; same-tab updates arrive
// via the synthetic StorageEvent dispatched by setValue below.
function storageSubscribe(cb: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener("storage", cb);
  return () => window.removeEventListener("storage", cb);
}

/**
 * SSR-safe persisted state backed by localStorage via useSyncExternalStore.
 *
 * @param key            localStorage key.
 * @param defaultValue   value used during SSR + when no/invalid stored value.
 * @param opts.serialize T -> string for writes (default JSON.stringify).
 * @param opts.deserialize raw string -> T for reads (default JSON.parse).
 * @returns [value, setValue] — setValue supports a functional updater.
 */
export function usePersistentState<T>(
  key: string,
  defaultValue: T,
  opts?: PersistentStateOptions<T>,
): [T, (next: T | ((prev: T) => T)) => void] {
  const serialize = opts?.serialize ?? ((v: T) => JSON.stringify(v));
  const deserialize = opts?.deserialize ?? ((raw: string) => JSON.parse(raw) as T);

  // Freeze defaultValue to its first-render reference (read-only `.current`, set
  // once at init — no ref WRITE during render). getSnapshot / getServerSnapshot
  // return this on empty/blocked/corrupt storage; useSyncExternalStore bails out
  // only on Object.is, so an object default passed as a fresh literal each render
  // would otherwise return a NEW reference every getSnapshot call → infinite
  // loop. All call sites pass a per-mount constant, so freezing is
  // behavior-identical and makes object-valued defaults safe.
  const frozenDefault = useRef(defaultValue).current;

  // CRITICAL footgun: getSnapshot runs on every render and useSyncExternalStore
  // re-renders unless Object.is(prev, next). A fresh JSON.parse each call returns
  // a NEW object reference every render → infinite loop. Cache the last
  // (rawString -> parsedValue) pair and return the cached parsed value when the
  // raw string is unchanged (read-only `.current`). Primitives compare by ===
  // anyway; the cache makes object-valued keys safe too.
  const cacheRef = useRef<{ raw: string | null; value: T } | null>(null);

  // getSnapshot/getServerSnapshot/setValue close over the (possibly fresh-each-
  // render) serialize/deserialize directly. Their identity may change per render,
  // but that only makes useSyncExternalStore re-READ (cheap) — it never loops,
  // because the cache guarantees the RETURNED value is referentially stable.
  const getSnapshot = (): T => {
    let raw: string | null;
    try {
      raw = localStorage.getItem(key);
    } catch {
      // Storage blocked (Safari private mode, etc.) → treat as absent.
      return frozenDefault;
    }
    if (raw === null) return frozenDefault;

    const cached = cacheRef.current;
    if (cached && cached.raw === raw) return cached.value;

    let value: T;
    try {
      value = deserialize(raw);
    } catch {
      return frozenDefault;
    }
    cacheRef.current = { raw, value };
    return value;
  };

  // Server (and the initial hydration render) always sees the default — exactly
  // reproducing the prior "SSR starts at default" behavior → zero mismatch.
  const getServerSnapshot = (): T => frozenDefault;

  const value = useSyncExternalStore(
    storageSubscribe,
    getSnapshot,
    getServerSnapshot,
  );

  const setValue = (next: T | ((prev: T) => T)) => {
    // Resolve a functional updater against the live stored value (not a stale
    // render-time snapshot) so rapid successive updates compose correctly.
    let prev: T;
    const cached = cacheRef.current;
    try {
      const raw = localStorage.getItem(key);
      if (raw === null) prev = frozenDefault;
      else if (cached && cached.raw === raw) prev = cached.value;
      else prev = deserialize(raw);
    } catch {
      prev = frozenDefault;
    }

    const resolved =
      typeof next === "function" ? (next as (p: T) => T)(prev) : next;

    const encoded = serialize(resolved);
    try {
      localStorage.setItem(key, encoded);
      // Keep the cache hot so the synchronous re-read returns the new value with
      // a stable reference.
      cacheRef.current = { raw: encoded, value: resolved };
    } catch {
      // Storage blocked / quota — persistence silently skipped. The synthetic
      // event below still notifies same-tab subscribers; getSnapshot falls back
      // to the default on the next read (matching the prior behavior).
    }
    // Notify same-tab subscribers (the native "storage" event only fires in
    // OTHER tabs). Mirrors collapseState.writeExpanded.
    try {
      window.dispatchEvent(
        new StorageEvent("storage", {
          key,
          newValue: encoded,
          storageArea: localStorage,
        }),
      );
    } catch {
      // StorageEvent constructor unavailable — extremely rare; ignore.
    }
  };

  return [value, setValue];
}

// useIsHydrated — companion hook for the "have we mounted on the client yet?"
// flag that several Phase-A components used as `useState(false) + useEffect(()
// => setState(true))` (also a set-state-in-effect warning). useSyncExternalStore
// returns the server snapshot (false) during SSR + the first hydration render,
// then the client snapshot (true) — no effect, no mismatch. This is the
// canonical hydration-gate pattern.
function hydratedSubscribe(): () => void {
  return () => {};
}
function getHydratedClient(): boolean {
  return true;
}
function getHydratedServer(): boolean {
  return false;
}
export function useIsHydrated(): boolean {
  return useSyncExternalStore(
    hydratedSubscribe,
    getHydratedClient,
    getHydratedServer,
  );
}
