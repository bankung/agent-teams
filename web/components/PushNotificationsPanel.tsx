"use client";

// PushNotificationsPanel — Kanban #955.C orchestrator.
//
// Surfaces (in vertical order):
//   1. InstallPwaNudge (iOS-non-standalone only — top of panel)
//   2. Master "Enable push notifications" toggle (D7 — explicit opt-in)
//   3. List of active PushSubscriptionRow rows
//
// State ownership:
//   - The list of server-side subscriptions is fetched on mount + after any
//     mutation (subscribe / unsubscribe / kinds-patch on a row). Local list
//     state is the source of truth between fetches; mutations update it
//     surgically rather than triggering a full refetch (less flicker).
//   - Master toggle reflects "is this browser currently subscribed?" — the
//     browser-side PushSubscription, not the server list. The server list
//     can have rows from OTHER browsers / devices.
//
// AC mapping:
//   - AC3 (notification click → deep-link) is service-worker-side (sw.js).
//   - AC6 (iOS install nudge) → InstallPwaNudge child.
//   - D7 (explicit "Enable push" toggle, no auto-prompt) → master toggle.

import { useCallback, useEffect, useState } from "react";

import { push as pushApi, type PushSubscriptionRead } from "@/lib/api";
import { extractErrorMessage } from "@/lib/errors";
import {
  getCurrentSubscription,
  isPushSupported,
  MissingVapidKeyError,
  PushNotSupportedError,
  PushPermissionDeniedError,
  subscribeToPush,
  unsubscribeFromPush,
} from "@/lib/push";
import { InstallPwaNudge } from "@/components/InstallPwaNudge";
import { PushSubscriptionRow } from "@/components/PushSubscriptionRow";
import { Switch } from "@/components/Switch";

// Status string for the master row — drives the rendered hint.
type MasterStatus =
  | "loading"
  | "unsupported"
  | "not_subscribed"
  | "subscribing"
  | "subscribed"
  | "permission_denied"
  | "missing_vapid_key"
  | "error";

export function PushNotificationsPanel() {
  const [status, setStatus] = useState<MasterStatus>("loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [subscriptions, setSubscriptions] = useState<PushSubscriptionRead[]>([]);
  const [browserEndpoint, setBrowserEndpoint] = useState<string | null>(null);

  // Refresh the server-side subscription list. Errors surface to the panel
  // status; an empty list with status='subscribed' is a valid first-paint
  // race (POST happened, GET hasn't propagated) but is rare in practice.
  const refreshList = useCallback(async () => {
    try {
      const rows = await pushApi.list();
      setSubscriptions(rows);
    } catch (err) {
      // Don't change the master status on a list-fetch failure — the user's
      // own subscribe / unsubscribe still works; only the device list shows
      // stale. Surface as a non-blocking inline message.
      setErrorMessage(
        err instanceof Error
          ? `Could not load subscription list: ${err.message}`
          : "Could not load subscription list",
      );
    }
  }, []);

  // First-mount bootstrap: feature-detect, read current browser-side
  // subscription, and fetch the server list. Sets status to one of:
  //   - 'unsupported' → no Push API on this browser
  //   - 'subscribed' → browser PushSubscription exists (likely matches a
  //     server row, but we don't gate on that match)
  //   - 'not_subscribed' → otherwise
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!isPushSupported()) {
        if (!cancelled) setStatus("unsupported");
        return;
      }
      const currentBrowserSub = await getCurrentSubscription();
      if (!cancelled) {
        setBrowserEndpoint(currentBrowserSub?.endpoint ?? null);
        setStatus(currentBrowserSub ? "subscribed" : "not_subscribed");
      }
      await refreshList();
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshList]);

  // Master toggle handler. Two branches:
  //   - currently subscribed → unsubscribe (server + browser).
  //   - currently not subscribed → subscribeToPush (registers SW, requests
  //     permission, POSTs to /api/push/subscribe).
  // Treated as a deliberate-action mutation: button disabled while in-flight,
  // server confirmation precedes visual flip.
  async function onToggleMaster() {
    if (status === "subscribing") return;

    if (status === "subscribed") {
      // Find the server row that matches this browser's endpoint. If we
      // can't find it (server row deleted out-of-band), still tear down
      // the browser-side subscription to leave a clean state.
      setStatus("subscribing");
      setErrorMessage(null);
      try {
        if (browserEndpoint) {
          const matching = subscriptions.find(
            (s) => s.endpoint === browserEndpoint,
          );
          if (matching) {
            await unsubscribeFromPush(matching.id);
            setSubscriptions((prev) =>
              prev.filter((s) => s.id !== matching.id),
            );
          } else {
            // No matching server row; just unsubscribe the browser side.
            const reg = await navigator.serviceWorker.getRegistration("/");
            const sub = await reg?.pushManager.getSubscription();
            if (sub) await sub.unsubscribe();
          }
        }
        setBrowserEndpoint(null);
        setStatus("not_subscribed");
      } catch (err) {
        setStatus("error");
        setErrorMessage(
          extractErrorMessage(err, "Unsubscribe failed"),
        );
      }
      return;
    }

    // Subscribe path.
    setStatus("subscribing");
    setErrorMessage(null);
    try {
      const persisted = await subscribeToPush({});
      // Insert / replace in local list — endpoint dedup matches slice A's
      // server-side ON CONFLICT DO UPDATE behavior.
      setSubscriptions((prev) => {
        const without = prev.filter((s) => s.endpoint !== persisted.endpoint);
        return [...without, persisted];
      });
      setBrowserEndpoint(persisted.endpoint);
      setStatus("subscribed");
    } catch (err) {
      if (err instanceof MissingVapidKeyError) {
        setStatus("missing_vapid_key");
        setErrorMessage(err.message);
      } else if (err instanceof PushPermissionDeniedError) {
        setStatus("permission_denied");
        setErrorMessage(err.message);
      } else if (err instanceof PushNotSupportedError) {
        setStatus("unsupported");
        setErrorMessage(err.message);
      } else {
        setStatus("error");
        setErrorMessage(
          extractErrorMessage(err, "Subscribe failed"),
        );
      }
    }
  }

  // Row callbacks — propagate surgical updates into the panel-level list.
  const handleRowUpdate = useCallback((next: PushSubscriptionRead) => {
    setSubscriptions((prev) =>
      prev.map((s) => (s.id === next.id ? next : s)),
    );
  }, []);
  const handleRowRemove = useCallback(
    (id: number) => {
      setSubscriptions((prev) => prev.filter((s) => s.id !== id));
      // If the operator unsubscribed THIS browser via a row (rather than the
      // master toggle), also reset the master state. Cheap to compare here.
      const removed = subscriptions.find((s) => s.id === id);
      if (removed && removed.endpoint === browserEndpoint) {
        setBrowserEndpoint(null);
        setStatus("not_subscribed");
      }
    },
    [subscriptions, browserEndpoint],
  );

  const masterChecked =
    status === "subscribed" || status === "subscribing";
  const masterLabel =
    status === "subscribing"
      ? "Working…"
      : status === "subscribed"
        ? "Push enabled"
        : "Enable push notifications";

  // Disable master toggle when the env / browser will prevent it from
  // working. The hint section below explains WHY in plain language.
  const masterDisabled =
    status === "subscribing" ||
    status === "unsupported" ||
    status === "missing_vapid_key" ||
    status === "permission_denied" ||
    status === "loading";

  return (
    <section
      data-push-notifications-panel
      aria-labelledby="push-panel-heading"
      className="flex flex-col gap-4"
    >
      <header className="flex flex-col gap-1">
        <h2
          id="push-panel-heading"
          className="text-base font-semibold text-zinc-900 dark:text-zinc-100"
        >
          Push notifications
        </h2>
        <p className="text-[12px] text-zinc-500 dark:text-zinc-400 leading-5">
          Receive HITL / done / failed / budget alerts in this browser via
          Web Push. Notifications fire even when the tab is closed (as long
          as the browser is running).
        </p>
      </header>

      <InstallPwaNudge />

      <div className="flex flex-col gap-2 rounded-md border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900">
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
            This browser
          </span>
          <Switch
            label={masterLabel}
            checked={masterChecked}
            onClick={onToggleMaster}
            colorOn="amber"
            disabled={masterDisabled}
            aria-label={
              masterChecked
                ? "Disable push notifications on this browser"
                : "Enable push notifications on this browser"
            }
          />
        </div>

        {status === "unsupported" ? (
          <p className="text-[12px] text-zinc-500 dark:text-zinc-400 leading-5">
            This browser does not support Web Push. On iOS Safari you must
            install agent-teams to the home screen first (see the hint
            above).
          </p>
        ) : null}
        {status === "missing_vapid_key" ? (
          <p className="text-[12px] text-amber-700 dark:text-amber-300 leading-5">
            Deploy is missing NEXT_PUBLIC_VAPID_PUBLIC_KEY. Ops must
            generate a VAPID keypair (api/scripts/generate_vapid_keys.py)
            and add both halves to the deployment env before this works.
          </p>
        ) : null}
        {status === "permission_denied" ? (
          <p className="text-[12px] text-red-700 dark:text-red-300 leading-5">
            Notifications were blocked in browser settings. Re-enable them
            via the site-settings menu (lock icon in the address bar) and
            try again.
          </p>
        ) : null}
        {status === "error" && errorMessage ? (
          <p
            role="alert"
            className="text-[12px] text-red-700 dark:text-red-300 leading-5"
          >
            {errorMessage}
          </p>
        ) : null}
      </div>

      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
          Subscribed devices
          <span className="ml-1.5 text-[11px] font-normal text-zinc-500 dark:text-zinc-400 tabular-nums">
            ({subscriptions.length})
          </span>
        </h3>
        {subscriptions.length === 0 ? (
          <p className="text-[12px] text-zinc-500 dark:text-zinc-400 leading-5">
            No subscribed devices yet. Toggle the switch above to register
            this browser.
          </p>
        ) : (
          <ul className="flex flex-col gap-2 list-none p-0">
            {subscriptions.map((sub) => (
              <PushSubscriptionRow
                key={sub.id}
                subscription={sub}
                onUpdate={handleRowUpdate}
                onRemove={handleRowRemove}
              />
            ))}
          </ul>
        )}
      </div>

    </section>
  );
}
