// Web Push service worker — Kanban #955.C (slice C).
//
// Two responsibilities:
//   1. `push` event — render the notification using the backend's D4 payload
//      shape: { title, body, url, icon? }. The payload is JSON.stringified by
//      notify_web_push.py before pywebpush encrypts + sends.
//   2. `notificationclick` event — focus an existing window matching the
//      target URL if one is open, else open a new one. Backed by D4's `url`
//      field (e.g. `/p/<name>` or `/p/<name>?task=<id>`).
//
// Registered from web/components/ServiceWorkerRegister.tsx at app load.
// Scope: origin root (default for /sw.js).
//
// Notes:
//   - This file is plain JS (not TS) because Next.js serves files in public/
//     untouched; the SW runs in a worker context with its own globals
//     (self, clients, registration). TypeScript would need a separate build
//     step for /sw.js which is overkill for ~50 LOC.
//   - Defensive parsing: an empty push (`event.data === null`) is a valid
//     wake-up from some browsers. Render a generic message rather than throw.
//   - Icon files /icon-192.png + /badge-72.png are placeholders for this
//     slice (#955.C followup); notifications render fine without them.

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

  var options = {
    body: data.body || "",
    icon: data.icon || "/icon-192.png",
    badge: "/badge-72.png",
    data: { url: data.url || "/" },
  };

  event.waitUntil(
    self.registration.showNotification(data.title || "agent-teams", options),
  );
});

self.addEventListener("notificationclick", function (event) {
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
