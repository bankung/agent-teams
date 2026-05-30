// Web Push client helper — Kanban #955.C (slice C).
//
// Wraps the browser's Push API + the backend's /api/push/* CRUD endpoints
// (slice 955.A). Three surfaces:
//   - `isPushSupported()` — feature-detect PushManager + serviceWorker.
//   - `getCurrentSubscription()` — read the active browser-side subscription
//     (post-register), or null if none.
//   - `subscribeToPush(opts)` — register the SW, request notification
//     permission if needed, call PushManager.subscribe(), and POST the result
//     to /api/push/subscribe. Returns the persisted server row.
//   - `unsubscribeFromPush(serverId)` — DELETE the server row and unsubscribe
//     the browser-side PushSubscription. Both steps run; the server soft-
//     deletes regardless of whether the browser unsubscribe succeeds.
//
// VAPID public key source: NEXT_PUBLIC_VAPID_PUBLIC_KEY (set in
// docker-compose.yml web service env, mirroring api's VAPID_PUBLIC_KEY).
// Without it `subscribeToPush` throws `MissingVapidKeyError` and the FE
// renders a friendly "deploy not configured" message.
//
// The browser's PushSubscription.endpoint is the natural key (slice A D5);
// re-subscribing the same browser is idempotent on the server side.

import {
  push as pushApi,
  type PushSubscriptionRead,
  type PushSubscribeBody,
} from "./api";
import { extractErrorMessage } from "./errors";

export class MissingVapidKeyError extends Error {
  constructor() {
    super(
      "NEXT_PUBLIC_VAPID_PUBLIC_KEY is not configured. Web Push is disabled until ops sets the env var.",
    );
    this.name = "MissingVapidKeyError";
  }
}

export class PushNotSupportedError extends Error {
  constructor(reason: string) {
    super(`Push notifications not supported: ${reason}`);
    this.name = "PushNotSupportedError";
  }
}

export class PushPermissionDeniedError extends Error {
  constructor() {
    super(
      "Browser permission for notifications was denied. Re-enable in browser settings to subscribe.",
    );
    this.name = "PushPermissionDeniedError";
  }
}

// Feature detection. Three required APIs:
//   - serviceWorker (registration target)
//   - PushManager (subscribe / getSubscription)
//   - Notification (permission gate)
// iOS Safari 16.4+ supports all three only when running as an installed PWA
// (added to home screen); non-installed Safari returns false here and we
// surface the InstallPwaNudge.
export function isPushSupported(): boolean {
  if (typeof window === "undefined") return false;
  return (
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

// Decode URL-safe base64 (RFC 4648) into a Uint8Array. The VAPID public key
// is shipped as a base64url string; PushManager.subscribe requires it as a
// Uint8Array for applicationServerKey.
function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

// Convert ArrayBuffer to URL-safe base64 (RFC 4648 §5). The browser's
// PushSubscription.getKey('p256dh'|'auth') returns ArrayBuffer; the BE
// expects a base64url string (matches the Web Push JSON serialization).
function arrayBufferToBase64Url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  const base64 = btoa(binary);
  return base64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// Read the configured VAPID public key. Throws when unset so callers can
// surface a clear "ops not configured" message rather than a generic
// subscribe failure.
function readVapidPublicKey(): string {
  const key = process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY;
  if (!key || key.trim().length === 0) {
    throw new MissingVapidKeyError();
  }
  return key.trim();
}

// Ensure the service worker is registered. Idempotent — `register()` is a
// no-op if /sw.js is already controlling the page. Returns the active
// registration so callers can chain `.pushManager.subscribe()`.
async function ensureServiceWorker(): Promise<ServiceWorkerRegistration> {
  if (!("serviceWorker" in navigator)) {
    throw new PushNotSupportedError("Service workers unavailable");
  }
  // Some browsers (Firefox in private mode) reject register() entirely;
  // surface that as the standard PushNotSupportedError shape so the FE
  // can branch identically to the feature-detect path.
  let registration: ServiceWorkerRegistration;
  try {
    registration = await navigator.serviceWorker.register("/sw.js", {
      scope: "/",
    });
  } catch (err) {
    throw new PushNotSupportedError(
      extractErrorMessage(err, "register() rejected"),
    );
  }
  // `ready` resolves once a SW has reached `activated` state — guarantees
  // subscribe() can run without "no active worker" races on first load.
  await navigator.serviceWorker.ready;
  return registration;
}

// Request notification permission. Returns the granted permission state.
// Throws PushPermissionDeniedError on "denied" so callers can render an
// inline "blocked — re-enable in browser settings" hint rather than a
// generic failure.
async function ensurePermission(): Promise<NotificationPermission> {
  if (!("Notification" in window)) {
    throw new PushNotSupportedError("Notification API unavailable");
  }
  if (Notification.permission === "granted") return "granted";
  if (Notification.permission === "denied") {
    throw new PushPermissionDeniedError();
  }
  const result = await Notification.requestPermission();
  if (result === "denied") {
    throw new PushPermissionDeniedError();
  }
  if (result !== "granted") {
    throw new PushPermissionDeniedError();
  }
  return "granted";
}

export type SubscribeOptions = {
  // Project filter — null = subscribe across all projects (the default).
  // Slice A D3: backend stores NULL = all-projects, integer = scoped.
  projectId?: number | null;
};

// Subscribe the current browser to Web Push and persist the subscription
// server-side. Returns the persisted row (server id is the natural key for
// later PATCH / DELETE calls).
//
// Flow:
//   1. Read VAPID public key (throws MissingVapidKeyError if unset).
//   2. Register / await service worker.
//   3. Request notification permission (throws on deny).
//   4. PushManager.subscribe({ userVisibleOnly: true, applicationServerKey }).
//   5. POST /api/push/subscribe with the subscription JSON.
//
// `userVisibleOnly: true` is mandatory in Chrome / Edge — silent push is
// blocked unless the worker surfaces a notification on every push event.
export async function subscribeToPush(
  opts: SubscribeOptions = {},
): Promise<PushSubscriptionRead> {
  if (!isPushSupported()) {
    throw new PushNotSupportedError(
      "Push API or service worker unavailable in this browser",
    );
  }
  const vapidKey = readVapidPublicKey();
  const registration = await ensureServiceWorker();
  await ensurePermission();

  // PushManager.subscribe is idempotent on the browser side — re-calling
  // returns the existing subscription. We always call it so a freshly-
  // unsubscribed browser re-subscribes here.
  // PushManager.subscribe expects a BufferSource for applicationServerKey.
  // The strict DOM types in TypeScript 5.x narrow this to ArrayBuffer; we
  // hand off the underlying buffer of our Uint8Array (cast through unknown
  // so the type-check passes against both the wide ArrayBufferLike and the
  // narrow ArrayBuffer used in different lib.dom.d.ts versions).
  const keyBytes = urlBase64ToUint8Array(vapidKey);
  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: keyBytes.buffer as ArrayBuffer,
  });

  const p256dhBuf = subscription.getKey("p256dh");
  const authBuf = subscription.getKey("auth");
  if (!p256dhBuf || !authBuf) {
    throw new PushNotSupportedError(
      "Browser did not surface p256dh/auth keys",
    );
  }

  const body: PushSubscribeBody = {
    endpoint: subscription.endpoint,
    keys: {
      p256dh: arrayBufferToBase64Url(p256dhBuf),
      auth: arrayBufferToBase64Url(authBuf),
    },
    user_agent: navigator.userAgent.slice(0, 512),
  };
  if (opts.projectId != null) {
    body.project_id = opts.projectId;
  }

  return pushApi.subscribe(body);
}

// Unsubscribe from Web Push. Two-step (server-then-browser) so even if the
// browser unsubscribe fails (rare — usually only on stale state), the server
// still soft-deletes the row and stops sending pushes to this endpoint.
export async function unsubscribeFromPush(serverId: number): Promise<void> {
  // Server-side soft delete first — the source of truth for "stop sending".
  await pushApi.unsubscribe(serverId);

  if (!("serviceWorker" in navigator)) return;
  try {
    const registration = await navigator.serviceWorker.getRegistration("/");
    if (!registration) return;
    const sub = await registration.pushManager.getSubscription();
    if (sub) await sub.unsubscribe();
  } catch {
    // Browser-side unsubscribe is best-effort — server-side already
    // soft-deleted, so even on failure no further pushes will be sent.
  }
}

// Read the current browser-side PushSubscription. Returns null when there
// is no active subscription (typical first-visit state, or post-unsubscribe).
export async function getCurrentSubscription(): Promise<PushSubscription | null> {
  if (!isPushSupported()) return null;
  try {
    const registration = await navigator.serviceWorker.getRegistration("/");
    if (!registration) return null;
    return await registration.pushManager.getSubscription();
  } catch {
    return null;
  }
}

// iOS detection — used by InstallPwaNudge. Two conditions both required:
//   1. User-agent matches iPhone / iPad / iPod.
//   2. Not running as an installed PWA (`navigator.standalone` is the
//      Apple-specific PWA-mode flag; truthy = installed-to-home-screen).
// `window.MSStream` exclusion guards against old IE/Edge user-agents that
// also contain "iPhone" as a substring.
export function isIosNonStandalone(): boolean {
  if (typeof window === "undefined") return false;
  const ua = window.navigator.userAgent;
  const isIos =
    /iPad|iPhone|iPod/.test(ua) &&
    !(window as unknown as { MSStream?: unknown }).MSStream;
  if (!isIos) return false;
  // navigator.standalone is Safari-only. `true` = installed PWA; absent or
  // false = running in normal Safari (push needs install to work).
  const standalone = (
    window.navigator as Navigator & { standalone?: boolean }
  ).standalone;
  return standalone !== true;
}
