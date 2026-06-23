"use client";

// useAsyncData — Kanban #2492 (Phase B of the #2489 set-state-in-effect
// remediation). One shared hook over the "fetch-in-effect" state machine that
// ~13 components previously open-coded as:
//
//   const [data, setData] = useState<T | null>(null);
//   const [loading, setLoading] = useState(false);
//   const [error, setError] = useState<string | null>(null);
//   useEffect(() => {
//     let cancelled = false;
//     setLoading(true); setError(null);
//     fetcher().then(d => { if (!cancelled) { setData(d); setLoading(false); } })
//              .catch(e => { if (!cancelled) { setError(...); setLoading(false); } });
//     return () => { cancelled = true; };
//   }, [deps]);
//
// WHY a fetch-in-effect at all (and why this trips the linter ON PURPOSE):
//   lib/api.ts fetchers take NO AbortSignal, and this codebase has NO Suspense /
//   React-Query data layer. "Cancellation" here is the `let cancelled = false`
//   flag that IGNORES a stale resolved response (on unmount / dep change) — it
//   does not abort the network request. So the fetch genuinely has to run inside
//   an effect, and the effect's synchronous reset (setLoading(true) +
//   setError(null)) is exactly the pattern react-hooks/set-state-in-effect warns
//   about. That warning is unavoidable AT THE DATA LAYER; the win of this hook is
//   that the warning is now CENTRALIZED to ONE audited site (the single
//   eslint-disable below) instead of being duplicated across ~13 call sites.
//
// The hook is behavior-equivalent to the prior hand-rolled pattern:
//   * loading flips true synchronously on mount + on every dep/reload change;
//   * a stale resolution (component unmounted, or deps changed before the
//     promise settled) is discarded via the cancel flag — no setState fires;
//   * errors are normalised through extractErrorMessage (the codebase standard);
//   * setData is exposed for local optimistic mutation (TaskComments prepends an
//     older page + appends a posted comment without a refetch);
//   * reload() re-runs the fetcher (the IntegrationsPanel "Retry" button).

import { useCallback, useEffect, useRef, useState } from "react";
import type { DependencyList, Dispatch, SetStateAction } from "react";

import { extractErrorMessage } from "./errors";

export type AsyncState<T> = {
  data: T | null;
  loading: boolean;
  error: string | null;
};

export type UseAsyncDataOptions<T> = {
  // Runs in the resolved `.then` (not lexically in a consumer effect) once a
  // fetch succeeds and is not stale. Use it to derive coupled state from the
  // result (e.g. hasMore = rows.length === PAGE) without a second effect.
  onSuccess?: (data: T) => void;
  // Message shown when the caught error is not an Error/HttpError.
  errorFallback?: string;
  // When true, data is cleared (→ null) the instant a (re)fetch starts, so the
  // UI shows its loading placeholder instead of stale rows. When false (default)
  // the prior data stays visible until the new data arrives (avoids a flash).
  resetDataOnReload?: boolean;
};

export type UseAsyncDataResult<T> = AsyncState<T> & {
  // For local optimistic mutation of the fetched value (append/prepend/patch a
  // list without a refetch). A functional updater sees the latest data.
  setData: Dispatch<SetStateAction<T | null>>;
  // Re-run the fetcher with the current deps (manual refresh / retry).
  reload: () => void;
};

/**
 * Shared {loading, data, error} state machine for a plain-fetch (no AbortSignal,
 * no Suspense) data source, with the stale-response cancel guard built in.
 *
 * @param fetcher  () => Promise<T>. Closed over freshly each render; NOT a
 *                 dependency — pass everything that should re-trigger a fetch via
 *                 `deps` (mirrors the original hand-rolled effects, which keyed
 *                 on primitives like [projectId, taskId], never on the closure).
 * @param deps     Re-fetch whenever any entry changes (same contract as a
 *                 useEffect dep array).
 * @param opts     onSuccess / errorFallback / resetDataOnReload (see type).
 */
export function useAsyncData<T>(
  fetcher: () => Promise<T>,
  deps: DependencyList,
  opts?: UseAsyncDataOptions<T>,
): UseAsyncDataResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadCount, setReloadCount] = useState(0);

  // The fetch effect must read the LATEST fetcher + opts without listing them as
  // dependencies (they are fresh every render; depending on them would re-fetch
  // on every parent render). Mirror them into refs that are updated in an effect
  // — NOT during render — so react-hooks/refs (a Compiler "error" rule) stays
  // satisfied. The fetch effect below runs after this one on each commit, so the
  // refs are current by the time a dep/reload change triggers a fetch.
  const fetcherRef = useRef(fetcher);
  const optsRef = useRef(opts);
  useEffect(() => {
    fetcherRef.current = fetcher;
    optsRef.current = opts;
  });

  const reload = useCallback(() => {
    setReloadCount((c) => c + 1);
  }, []);

  // Single centralized fetch-in-effect. This is the ONE place in the codebase
  // that knowingly does setState-synchronously-in-an-effect for a data fetch
  // (plain fetch + cancel-guard, no AbortSignal / Suspense available here);
  // ~13 call sites delegate to it so none of them carry their own disable.
  // Keyed on the caller's deps + the reload counter; the fetcher/opts are read
  // from refs (intentionally NOT deps — see fetcherRef above), so exhaustive-deps
  // is disabled on the dep-array line below exactly as the original effects did.
  useEffect(() => {
    let cancelled = false;
    // The synchronous loading/error reset is the crux of the set-state-in-effect
    // warning; it is correct + unavoidable for a plain fetch (see file header).
    // Disabled HERE (the rule reports at the first setState statement) and ONLY
    // here, so the ~13 migrated call sites stay warning-free.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    setError(null);
    if (optsRef.current?.resetDataOnReload) setData(null);
    fetcherRef
      .current()
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setLoading(false);
        optsRef.current?.onSuccess?.(d);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(
          extractErrorMessage(err, optsRef.current?.errorFallback ?? "Failed to load"),
        );
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, reloadCount]);

  return { data, loading, error, setData, reload };
}
