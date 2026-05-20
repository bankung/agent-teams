# Next.js — URL-driven UI state and query-param hygiene

**Scope:** when a route accepts a query parameter that drives client-side UX (scroll-into-view, highlight pulse, modal-open, filter chip), the Client Component owning the effect MUST strip the param after the effect settles. Otherwise F5 / back-button navigation re-fires the effect on a now-stale state.

## The pattern

```tsx
// web/components/Board.tsx (Kanban #1001 follow-up canonical example)

"use client";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

export function Board({ tasks }: Props) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const [highlightedTaskId, setHighlightedTaskId] = useState<number | null>(null);

  useEffect(() => {
    const raw = params.get("task");
    if (!raw) return;
    const taskId = Number.parseInt(raw, 10);
    if (Number.isNaN(taskId)) return;
    const target = tasks.find((t) => t.id === taskId);
    if (!target) {
      // Inline toast: "Task #N not found in this project"
      return;
    }
    setHighlightedTaskId(taskId);
    document
      .querySelector(`[data-task-card-id="${taskId}"]`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });

    // Strip the param after the effect settles so F5 doesn't re-fire.
    const cleanup = window.setTimeout(() => {
      setHighlightedTaskId(null);
      router.replace(pathname);
    }, 2200);
    return () => window.clearTimeout(cleanup);
  }, [params, pathname, router, tasks]);

  // ... render with `highlighted` prop on the matching card
}
```

## Why strip the param

- **F5 / hard refresh** — without stripping, every refresh re-fires the scroll + pulse. The operator sees the same animation play over and over.
- **Back-button navigation** — when the operator clicks back from a child page (e.g. `/p/agent-teams/settings`), they land on a URL with the param still set. The pulse re-fires on a card they may have just dismissed mentally.
- **URL sharing** — operators may share a URL like `/p/agent-teams?task=42`. After they click and the pulse fires, the URL bar still shows the param; copying it now produces a "first visit triggers the highlight" URL. Sometimes desired (sharing a deep-link), sometimes confusing (operator wonders why their URL has a query string). Strip + reset keeps the displayed URL clean.

## `router.replace` vs `router.push`

- **`router.replace`** — replaces the current history entry. Hitting back goes to the entry BEFORE the deep-linked landing, not back to the same URL with the param. **Correct for this pattern.**
- **`router.push`** — adds a new history entry. Back goes to the param'd URL → effect re-fires → loop. **Wrong for this pattern.**

## Timing

Strip the param AFTER the animation settles (not on first paint, not synchronously inside the effect):

- The animation needs the param to be present so the matching component can receive `highlighted={true}` on first render.
- Once the animation duration has elapsed (2s in the canonical example; +200ms grace for the cleanup), the param is no longer needed.
- `setTimeout(() => router.replace(pathname), 2200)` is the canonical shape.

## Anti-patterns

```tsx
// DON'T — strip synchronously inside the effect
useEffect(() => {
  const raw = params.get("task");
  if (raw) {
    // ...trigger scroll + pulse...
    router.replace(pathname);  // ← effect re-runs, raw is now null, pulse never finishes
  }
}, [params, pathname, router]);
```

```tsx
// DON'T — never strip
useEffect(() => {
  // ...scroll + pulse...
  // F5 re-fires forever
}, [params]);
```

## Cross-reference

- Canonical implementation: `web/components/Board.tsx` (Kanban #1001 follow-up, 2026-05-20).
- Pairs with: deep-link UX driven by a Server Component query-param consumer — the Server side passes the param through props, the Client side reads it via `useSearchParams` for reactive updates.
- Sibling concern: query-param-driven modal-open state — same strip-after-settle pattern; replace with `pathname` plus the remaining params after the modal opens.
