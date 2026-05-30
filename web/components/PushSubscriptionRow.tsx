"use client";

// PushSubscriptionRow — Kanban #955.C.
//
// One row in the PushNotificationsPanel list. Shows the device label
// (truncated user_agent), 4 kinds_enabled toggles, and an Unsubscribe
// button. Each toggle PATCHes /api/push/subscribe/{id} (slice 955.B) with
// the diff'd kinds_enabled blob.
//
// Mutation discipline (per context/standards/react/deliberate-action-mutations.md):
// kinds toggles are LOW-stakes (toggle archived class — reversible, not
// auditable, not legally-binding) → optimistic update is acceptable. The
// toggle flips immediately; on server error we revert + surface an inline
// error message. The Unsubscribe button is the slightly-higher-stakes
// path (it tears down the browser subscription too) → wait-for-server.

import { useState } from "react";

import { push as pushApi, type PushSubscriptionRead, type PushKindsEnabled } from "@/lib/api";
import { extractErrorMessage } from "@/lib/errors";
import { Switch } from "@/components/Switch";

// Friendly label for the 4 kinds. Order matches the locked KindsEnabled
// shape; presentation order is independent of JSON property order.
const KIND_LABELS: Array<{ key: keyof PushKindsEnabled; label: string }> = [
  { key: "hitl_needed", label: "HITL needed" },
  { key: "task_done", label: "Task done" },
  { key: "task_failed", label: "Task failed" },
  { key: "budget_warn", label: "Budget warn" },
];

// Render a shorter human label from the raw user-agent. Real-world UA
// strings are too noisy for inline display; we extract the browser + OS
// tokens that disambiguate one row from another and truncate hard.
function shortDeviceLabel(ua: string | null): string {
  if (!ua) return "Unknown device";
  // Cheap heuristics — no regex galleries. Caller can hover the row for
  // the full UA in the title attribute.
  const tokens: string[] = [];
  if (/iPhone/.test(ua)) tokens.push("iPhone");
  else if (/iPad/.test(ua)) tokens.push("iPad");
  else if (/Android/.test(ua)) tokens.push("Android");
  else if (/Macintosh/.test(ua)) tokens.push("Mac");
  else if (/Windows/.test(ua)) tokens.push("Windows");
  else if (/Linux/.test(ua)) tokens.push("Linux");

  if (/Edg\//.test(ua)) tokens.push("Edge");
  else if (/Chrome\//.test(ua)) tokens.push("Chrome");
  else if (/Firefox\//.test(ua)) tokens.push("Firefox");
  else if (/Safari\//.test(ua)) tokens.push("Safari");

  if (tokens.length === 0) return ua.slice(0, 40);
  return tokens.join(" · ");
}

type Props = {
  subscription: PushSubscriptionRead;
  // Called when this row updates the subscription server-side (kinds patch)
  // so the parent can refresh its local list. Receives the updated row.
  onUpdate: (next: PushSubscriptionRead) => void;
  // Called when this row is removed (Unsubscribe). Receives the id that
  // was unsubscribed so the parent can drop it from local state.
  onRemove: (id: number) => void;
};

export function PushSubscriptionRow({ subscription, onUpdate, onRemove }: Props) {
  const [kinds, setKinds] = useState<PushKindsEnabled>(subscription.kinds_enabled);
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function toggleKind(key: keyof PushKindsEnabled) {
    setError(null);
    const previous = kinds;
    const next: PushKindsEnabled = { ...kinds, [key]: !kinds[key] };
    // Optimistic — flip immediately for snappier feel; revert on error.
    setKinds(next);
    try {
      const updated = await pushApi.patchKinds(subscription.id, {
        kinds_enabled: next,
      });
      onUpdate(updated);
    } catch (err) {
      // Revert + surface. Slice B's PATCH endpoint may not have shipped yet
      // (404 / 405) — message reflects that case clearly.
      setKinds(previous);
      setError(extractErrorMessage(err, "Update failed"));
    }
  }

  async function unsubscribe() {
    if (removing) return;
    setRemoving(true);
    setError(null);
    try {
      // Server side first — source of truth for "stop sending". The
      // browser-side unsubscribe is best-effort (handled inside
      // unsubscribeFromPush). For other-device rows the browser side is
      // irrelevant anyway (we can't unsubscribe a remote browser from this
      // tab). pushApi.unsubscribe is sufficient here.
      await pushApi.unsubscribe(subscription.id);
      onRemove(subscription.id);
    } catch (err) {
      setError(extractErrorMessage(err, "Unsubscribe failed"));
      setRemoving(false);
    }
  }

  return (
    <li
      data-push-subscription-row
      data-subscription-id={subscription.id}
      className="flex flex-col gap-2 rounded-md border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900"
    >
      <header className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col">
          <span
            className="truncate text-sm font-medium text-zinc-900 dark:text-zinc-100"
            title={subscription.user_agent ?? undefined}
          >
            {shortDeviceLabel(subscription.user_agent)}
          </span>
          <span className="text-[11px] text-zinc-500 dark:text-zinc-400 tabular-nums">
            id={subscription.id}
            {subscription.project_id != null ? (
              <> · project={subscription.project_id}</>
            ) : (
              <> · all projects</>
            )}
          </span>
        </div>
        <button
          type="button"
          onClick={unsubscribe}
          disabled={removing}
          className="shrink-0 rounded border border-red-200 px-2 py-1 text-[11px] font-medium uppercase tracking-wide text-red-700 hover:bg-red-50 disabled:opacity-50 dark:border-red-900 dark:text-red-300 dark:hover:bg-red-950/30"
        >
          {removing ? "Removing…" : "Unsubscribe"}
        </button>
      </header>

      <div
        role="group"
        aria-label="Notification kinds for this subscription"
        className="flex flex-wrap gap-1.5"
      >
        {KIND_LABELS.map(({ key, label }) => (
          <Switch
            key={key}
            label={label}
            checked={kinds[key]}
            onClick={() => toggleKind(key)}
            colorOn="amber"
            disabled={removing}
            aria-label={`${kinds[key] ? "Disable" : "Enable"} ${label} notifications`}
          />
        ))}
      </div>

      {error ? (
        <p
          role="alert"
          className="text-[11px] text-red-700 dark:text-red-300"
        >
          {error}
        </p>
      ) : null}
    </li>
  );
}
