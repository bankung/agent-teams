# Next.js — handling empty-body responses (204 No Content)

**Scope:** when a `fetch` call returns 204 (or any other empty-body 2xx), the response body cannot be parsed as JSON. Wrappers that auto-`.json()` on every response throw `SyntaxError: Unexpected end of JSON input` even though the HTTP call succeeded.

## The pattern

For endpoints that return 204 (typically DELETE, or PATCH with no body), call `fetch` directly + inspect `response.ok` / `response.status` BEFORE any `.json()`:

```typescript
// web/lib/api.ts (Kanban #955.C canonical example)

export const push = {
  async unsubscribe(id: number): Promise<void> {
    // DELETE returns 204 — bypass the JSON-parsing fetch wrapper.
    const response = await fetch(`${API_BASE}/api/push/subscribe/${id}`, {
      method: "DELETE",
    });
    if (!response.ok) {
      throw new Error(`DELETE /api/push/subscribe/${id} → ${response.status}`);
    }
    // No body to parse; success is signaled by the status alone.
  },
};
```

Compare with the JSON-auto-parsing helper used elsewhere in `web/lib/api.ts`:

```typescript
async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init);
  if (!r.ok) throw new Error(...);
  return r.json() as Promise<T>;   // ← THROWS on 204 (empty body)
}
```

## Why this matters

- **204 is a contract.** DELETE handlers that return 204 are signaling "the action completed; there's nothing to return." A JSON wrapper that tries to `.json()` an empty body throws `SyntaxError` AFTER the network call succeeded — the caller incorrectly thinks the action failed.
- **Status alone is the success signal.** No body to inspect; `response.ok` + status code carry the entire contract.
- **Same trap for `Content-Length: 0` 200 responses** — any time the server intentionally returns no body, the JSON wrapper trips.

## Detection / mitigation options

1. **Per-call bypass** (canonical for one-off DELETE helpers — `push.unsubscribe` does this).
2. **Wrapper variant that tolerates empty body** — e.g. `jsonFetchOrVoid<T>(url, init): Promise<T | void>` that checks `response.status === 204 || response.headers.get('content-length') === '0'` and returns `undefined` instead of attempting `.json()`. Useful when many endpoints in a section return 204.
3. **Server-side: return 200 with `{}` instead of 204.** Tempting but compromises REST hygiene; prefer client-side handling.

## Cross-reference

- Canonical implementation: `web/lib/api.ts:push.unsubscribe` (Kanban #955.C, 2026-05-20).
- Sibling concern: `Response.text()` on an HTML error page when the server returns a non-2xx — also throws if the wrapper assumes JSON. Same fix: inspect `response.ok` first.
