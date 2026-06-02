// Web Push service worker — Kanban #955.C (slice C) + #1349 (snooze actions)
//                         + #1769 (SW-lifecycle guard).
//
// PUSH-ONLY — this SW intentionally registers NO `fetch` handler and opens
// NO Cache Storage. It NEVER intercepts navigation or API requests. The only
// purpose is receiving Web Push messages and surfacing notifications.
// Kanban #1769: adding `install`/`activate` lifecycle handlers for immediate
// takeover (skipWaiting + clients.claim) so a new SW version takes control
// without requiring the operator to close all tabs.
//
// Three responsibilities:
//   1. `install` / `activate` lifecycle — skipWaiting + clients.claim so a
//      newly deployed SW replaces a stale one immediately (Kanban #1769).
//   2. `push` event — render the notification using the backend's D4 payload
//      shape: { title, body, url, icon? }. The payload is JSON.stringified by
//      notify_web_push.py before pywebpush encrypts + sends.
//      Kanban #1349 — every notification carries two snooze quick-action
//      buttons (Snooze 4h / Snooze tomorrow). When the operator picks one
//      the SW POSTs /api/tasks/{id}/snooze BEFORE the notification closes.
//      The notification's `data.task_id` carries the id; payloads without
//      a task_id (e.g. project-wide budget warnings) render the actions
//      but the click handler degrades to a no-op + a deep-link navigate.
//   3. `notificationclick` event — focus an existing window matching the
//      target URL if one is open, else open a new one. Backed by D4's `url`
//      field (e.g. `/p/<name>` or `/p/<name>?task=<id>`). Snooze actions
//      route through `_handleSnoozeAction` first and skip the navigate.
//
// Registered from web/components/ServiceWorkerRegister.tsx at app load.
// Scope: origin root (default for /sw.js).
//
// Notes:
//   - This file is plain JS (not TS) because Next.js serves files in public/
//     untouched; the SW runs in a worker context with its own globals
//     (self, clients, registration). TypeScript would need a separate build
//     step for /sw.js which is overkill.
//   - Defensive parsing: an empty push (`event.data === null`) is a valid
//     wake-up from some browsers. Render a generic message rather than throw.
//   - Icon files /icon-192.png + /badge-72.png are placeholders for this
//     slice (#955.C followup); notifications render fine without them.

// Kanban #1769 — immediate takeover lifecycle handlers.
// skipWaiting lets a waiting SW skip the "wait for all tabs to close" step
// and become active immediately. clients.claim() makes the newly-active SW
// take control of all open clients (tabs) without requiring a page reload from
// the SW side. The ServiceWorkerRegister.tsx `controllerchange` listener then
// triggers a single page reload on the client to adopt the new worker.
self.addEventListener("install", function (event) {
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(self.clients.claim());
});

// Kanban #1349 — extract a task id from a payload URL like
// "/tasks/123" or "/p/foo?task=123". Returns null when no id is found.
function _extractTaskIdFromUrl(url) {
  if (!url || typeof url !== "string") return null;
  // Path form: /tasks/<id>
  var pathMatch = url.match(/\/tasks\/(\d+)/);
  if (pathMatch) {
    var pn = Number(pathMatch[1]);
    if (Number.isInteger(pn) && pn > 0) return pn;
  }
  // Query form: ?task=<id>
  var queryMatch = url.match(/[?&]task=(\d+)/);
  if (queryMatch) {
    var qn = Number(queryMatch[1]);
    if (Number.isInteger(qn) && qn > 0) return qn;
  }
  return null;
}

self.addEventListener("push", function (event) {
  var data = { title: "agent-teams", body: "Update", url: "/" };
  try {
    if (event.data) {
      data = event.data.json();
    }
  } catch (_) {
    // Malformed payload — fall through to the generic shape above rather
    // than swallowing the event silently (operator still sees a toast).
  }

  // Kanban #1349 — capture task_id from the URL (best-effort) and the
  // optional project_id from the payload itself. Both end up on
  // notification.data so notificationclick can POST /snooze without
  // re-parsing.
  var taskId =
    typeof data.task_id === "number" ? data.task_id : _extractTaskIdFromUrl(data.url);
  var projectId = typeof data.project_id === "number" ? data.project_id : null;

  var options = {
    body: data.body || "",
    icon: data.icon || "/icon-192.png",
    badge: "/badge-72.png",
    data: {
      url: data.url || "/",
      task_id: taskId,
      project_id: projectId,
    },
    // Kanban #1349 — snooze quick-actions on every notification. When the
    // payload has no task id the actions still render but the click
    // handler treats them as a no-op (with a debug log). Always-on is
    // intentional per spec: the BE payload does NOT currently carry an
    // event_kind discriminator the SW could gate on.
    actions: [
      { action: "snooze-4", title: "Snooze 4h" },
      { action: "snooze-24", title: "Snooze tomorrow" },
    ],
  };

  event.waitUntil(
    self.registration.showNotification(data.title || "agent-teams", options),
  );
});

// Kanban #1349 — POST /api/tasks/{id}/snooze with the given hour count.
// Best-effort: failures are logged but never re-thrown (the notification
// is already closing). Reads task_id + project_id from notification.data
// the push handler stashed at fire time.
function _handleSnoozeAction(event, hours) {
  var data = event.notification.data || {};
  var taskId = data.task_id;
  var projectId = data.project_id;
  if (!taskId || typeof taskId !== "number") {
    // No task id on this notification (e.g. project-wide budget warn).
    return Promise.resolve();
  }
  var headers = { "Content-Type": "application/json" };
  // The /snooze endpoint requires X-Project-Id (gates per Kanban #695).
  // When the payload doesn't carry the project id, fall back to the URL —
  // some payloads embed /p/<name>?task=<id> which we can't resolve to an
  // int from the SW; degrade gracefully (the POST will 400, the snooze
  // skips).
  if (projectId && typeof projectId === "number") {
    headers["X-Project-Id"] = String(projectId);
  }
  return fetch("/api/tasks/" + taskId + "/snooze", {
    method: "POST",
    headers: headers,
    body: JSON.stringify({ hours: hours }),
  }).catch(function (err) {
    // SW console is visible via DevTools > Application > Service Workers
    // > Console (per browser). A failed snooze is non-fatal.
    // eslint-disable-next-line no-console
    console.warn("[sw] snooze POST failed:", err);
  });
}

self.addEventListener("notificationclick", function (event) {
  // Kanban #1349 — handle snooze actions FIRST. Close the notification
  // either way (the operator already engaged); the snooze fetch resolves
  // in the background via event.waitUntil. Skip the focus/openWindow
  // navigation on snooze — the operator wanted to defer, not to re-engage.
  if (event.action === "snooze-4" || event.action === "snooze-24") {
    event.notification.close();
    var hours = event.action === "snooze-4" ? 4 : 24;
    event.waitUntil(_handleSnoozeAction(event, hours));
    return;
  }

  event.notification.close();
  var targetUrl =
    (event.notification.data && event.notification.data.url) || "/";

  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then(function (windowClients) {
        // Prefer focus + navigate of an already-open agent-teams tab over
        // opening a brand-new window (avoids tab proliferation for chatty
        // notifications). The first same-origin client wins.
        for (var i = 0; i < windowClients.length; i++) {
          var client = windowClients[i];
          if ("focus" in client) {
            if ("navigate" in client && client.url !== targetUrl) {
              return client.navigate(targetUrl).then(function (c) {
                return c && c.focus ? c.focus() : undefined;
              });
            }
            return client.focus();
          }
        }
        if (self.clients.openWindow) {
          return self.clients.openWindow(targetUrl);
        }
      }),
  );
});
