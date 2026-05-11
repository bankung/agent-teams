# Next.js — Server / Client component composition

**Scope:** when to split a feature into a Server parent + Client child, and how to wire the boundary correctly. App Router only (Pages Router is out of scope; if it returns, file a separate standard). Read-only state stays on the server; interactive surface ships to the browser.

## Rule

A Server Component **may** import and render a Client Component. A Client Component **may not** render a Server Component as a child (Next.js auto-creates the boundary at the Server→Client edge).

**Default direction:** Server parent + Client child sibling. Reach for Client only when the surface needs `useState` / `useEffect` / `useRef` / `useRouter` / event handlers / browser APIs (`window`, `document`, `localStorage`).

## Verification recipe

`grep -l '"use client"' <file>` at the top — if absent, the file IS a Server Component, **regardless of what it imports.** Importing a Client module does NOT make the parent Client.

## Canonical worked example (in-repo)

[web/components/ProjectConsentBanner.tsx](../../../web/components/ProjectConsentBanner.tsx) (Server) decides zinc / emerald branch from `project.auto_run_consent_at`. In the zinc branch it renders [web/components/ProjectConsentGrantModal.tsx](../../../web/components/ProjectConsentGrantModal.tsx) (Client) as a sibling — the modal handles `useState` + form submit + `router.refresh()`. Banner read-only state never ships to the browser; only the modal does. Kanban #407.

## Anti-pattern

Making the parent Client just to embed an interactive child:

```tsx
// ❌ Wrong — entire banner ships to the browser
"use client";
function Banner({ project }) {
  const [open, setOpen] = useState(false);
  return <div>{project.consent ? "..." : <button>Grant</button>}</div>;
}
```

```tsx
// ✅ Right — only the trigger ships
// Banner.tsx (no "use client" — Server)
function Banner({ project }) {
  return <div>{project.consent ? "..." : <GrantButton project={project} />}</div>;
}
// GrantButton.tsx ("use client" — Client; Next.js auto-boundary)
```

The browser bundle pays only for what's interactive. A 200-line Server parent that wraps a 20-line Client button should ship 20 lines, not 220.

## Server parent constraints

- No `useState` / `useEffect` / `useRef` / `useReducer` / `useMemo` / `useContext`.
- No `window` / `document` / `localStorage` / `sessionStorage` references.
- No event-handler props on JSX elements (`onClick={...}`, `onSubmit={...}`).
- May `await` async functions directly in the component body (Server Components are async by default).
- May read cookies / headers via `next/headers` (Client may not).

If a feature needs ANY of the above, it goes in a Client child — not in the parent.

## Boundary edge cases

- **Passing data across the boundary:** Server parents pass plain serializable props to Client children (strings, numbers, plain objects, arrays). NOT functions, Dates (serialize to ISO string first), Maps, Sets, class instances. If you need to pass a function, the Client child must define it locally.
- **`router.refresh()` re-runs Server Components.** After a Client mutation (`grantConsent` POST returns 200), `useRouter().refresh()` re-fetches the Server-rendered tree so the banner flips zinc → emerald. The Server parent re-runs with fresh data; the Client child stays mounted with preserved state.
- **`<Suspense>` boundaries** wrap async Server children, NOT Client children. Client children manage their own loading state via `useState` / `isLoading`.

## Out of scope

- Server Actions (`'use server'`) — separate standard when first used.
- Streaming + selective hydration tuning — defer until a perf issue surfaces.
- Pages Router (`pages/**`) — App Router is the canonical direction.
