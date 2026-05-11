# Next.js — Server Component try/catch must discriminate by typed error

**Scope:** how Server Components catch errors from `await`-ed API calls and decide between `notFound()`, re-throw to `error.tsx`, or handle inline. Bare `catch { notFound(); }` is the anti-pattern — it swallows non-404s into a misleading 404 UI.

## Rule

When a Server Component catches an error from a `fetch`-derived helper, the catch MUST discriminate by the typed error's `.status` field:

- **HTTP 404 → `notFound()`.** Renders the App Router not-found page (custom `app/not-found.tsx` if defined, else stock).
- **Every other status (500, 502, 503, network failure, 422) → `throw`.** Bubbles to `app/error.tsx` — the App Router's error boundary. This surfaces the real failure to the user as a server error, not as "wrong project name."

The `fetch` helper MUST throw a typed `HttpError` (see `typescript/typed-errors.md`) so the catch can use `instanceof` + `.status` for discrimination. A helper that throws bare `Error` with the detail string in `.message` forces the catch to parse strings, which is brittle.

## Canonical worked example (in-repo)

[web/app/p/[name]/page.tsx](../../../web/app/p/[name]/page.tsx) (refactored in Kanban #760):

```tsx
import { notFound } from "next/navigation";
import { getProjectByName, listTasks, HttpError } from "@/lib/api";

export default async function ProjectBoardPage({ params }: { params: { name: string } }) {
  let project;
  try {
    project = await getProjectByName(params.name);
  } catch (e) {
    if (e instanceof HttpError && e.status === 404) notFound();
    throw e; // bubble to app/error.tsx
  }
  const tasks = await listTasks(project.id, { limit: 500 });
  // ... unguarded — listTasks throws bubble to error.tsx too (symmetric)
}
```

Two things to lock:

1. **404 is the ONLY status that routes to `notFound()`.** Any other status — including 422 (validation drift), 500 (DB outage), connection-refused (api container down) — re-throws so `error.tsx` renders.
2. **Symmetric with unguarded `await`s.** `listTasks()` below the try/catch has no guard at all — its throws bubble directly to `error.tsx`. The discriminated 404 handler ABOVE produces the same end-state for non-404s. The asymmetric "404 vs everything else" treatment is intentional only for the path-param-driven `getProjectByName` (where 404 = "user typed wrong name"); other reads should hit error.tsx on any failure.

## Anti-pattern

```tsx
// ❌ Wrong — bare catch swallows non-404s into misleading 404 UI
let project;
try {
  project = await getProjectByName(params.name);
} catch {
  notFound();
}
```

Failure modes:
- **Backend outage looks like "wrong project name."** User sees the 404 page when the real cause is api container down. No diagnostic signal.
- **422 drift looks like 404.** Future Pydantic validator on the path-param renders 404 instead of a real validation error.
- **`error.tsx` never fires.** Custom error UI (retry button, status page link, support email) is unreachable for this route's failures.

```tsx
// ❌ Also wrong — string parsing on err.message is brittle
catch (e) {
  if (e instanceof Error && e.message.includes("not found")) notFound();
  throw e;
}
```

Brittle because `err.message` is intended for human display, not for machine discrimination. The backend can change the detail string (`"Project 'X' not found"` → `"No project named X"`) and silently break the 404 path.

## Multi-status discrimination

For routes that need different handling per status, switch on `.status`:

```tsx
catch (e) {
  if (!(e instanceof HttpError)) throw e;
  if (e.status === 404) notFound();
  if (e.status === 410) return <DeprecatedPage />; // inline render OK for terminal states
  throw e; // 5xx / network → error.tsx
}
```

Don't over-handle. Most routes only need 404 + fall-through. Inline rendering is for terminal states the user can act on (e.g., "this project was archived; restore it"), NOT for transient failures (5xx, network) which belong in error.tsx so the user sees a retry surface.

## Out of scope

- Route Handler (`route.ts`) error handling — different runtime; catch logic returns `NextResponse` not React. Separate standard.
- Client-side fetch error handling — covered in `react/deliberate-action-mutations.md` for mutation flows; read-side patterns deferred until a recurring need surfaces.
- Retry / backoff inside the catch — not a Server-Component concern; queue/retry belongs in a middleware or service-worker layer.
- Streaming error boundaries (`<Suspense>` + `error.tsx` for deeply-nested async) — App Router auto-handles; no explicit rule needed yet.

## Cross-references

- `typescript/typed-errors.md` — the `HttpError` class shape that makes this discrimination possible.
- `nextjs/notfound-dev-vs-prod.md` — the dev-mode rendering quirk for `notFound()` (markers live in `__next_f` JSON chunks, not raw text nodes).
- `react/deliberate-action-mutations.md` — Client-side error rendering for mutation flows.
