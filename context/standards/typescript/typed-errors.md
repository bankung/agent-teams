# TypeScript — typed error classes for HTTP / domain failures

**Scope:** when a `fetch` helper, RPC client, or service-layer call needs to communicate failure to its callers, throw a typed error class — not bare `Error` with the detail string in `.message`. Discrimination happens at the **throw layer**, not the catch layer.

## Rule

A `fetch` / RPC helper that wraps a transport (HTTP, gRPC, WebSocket) MUST throw a typed error class with:

1. **`extends Error`** — so legacy `err instanceof Error ? err.message : "..."` catches keep working without refactor.
2. **`readonly status: number`** — the HTTP status code (or domain-specific status enum for non-HTTP transports).
3. **`readonly detail: unknown`** — the response body parsed as-is. Type `unknown` because the shape varies (string for locked source-text, array for Pydantic 422, object for custom error shapes). Callers narrow with `typeof` / `Array.isArray` / shape checks.
4. **`super(message)` in the constructor** with a human-readable message — for UI display in catch sites that don't discriminate (e.g., generic toast). Callers that need machine discrimination use `.status` instead.

Optional fields when justified:
- `readonly request?: { method, url }` — only if the catch needs to know which call failed; usually the catch site already knows.
- `readonly headers?: Headers` — only if a specific header (e.g., `Retry-After`) drives behavior.

## Canonical worked example (in-repo)

[web/lib/api.ts](../../../web/lib/api.ts) (added in Kanban #760):

```ts
export class HttpError extends Error {
  readonly status: number;
  readonly detail: unknown;
  constructor(status: number, detail: unknown, message: string) {
    super(message);
    this.name = "HttpError";
    this.status = status;
    this.detail = detail;
  }
}
```

Throw site (inside `jsonFetch`):

```ts
if (!response.ok) {
  const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
  const message = formatDetail(body.detail) ?? `${response.status} ${response.statusText}`;
  throw new HttpError(response.status, body.detail, message);
}
```

Caller — Server Component discrimination by status (see `nextjs/typed-error-catch.md`):

```ts
catch (e) {
  if (e instanceof HttpError && e.status === 404) notFound();
  throw e;
}
```

Caller — Client Component with no discrimination, relies on `.message` for UI display:

```ts
catch (err: unknown) {
  setError(err instanceof Error ? err.message : "Grant failed");
}
```

Both work from the same throw because `HttpError extends Error`. Legacy `.message` semantics preserved.

## Anti-patterns

### 1. Throwing bare `Error` with the detail in `.message`

```ts
// ❌ Wrong — forces callers to parse strings
if (!response.ok) {
  throw new Error(await extractDetail(response));
}
```

Failure modes:
- **Catch sites parse `err.message`** to figure out the status. Brittle: detail strings change; status info is lost.
- **No way to discriminate 404 vs 500 vs 422.** All errors look the same.
- **`error.tsx` boundary fires for everything OR nothing** — depending on how the caller guesses.

### 2. Throwing a plain object literal

```ts
// ❌ Wrong — fails `instanceof Error` checks
throw { status: response.status, detail: body.detail };
```

Failure modes:
- **`err instanceof Error` returns false** — every existing catch that depends on the Error prototype breaks.
- **No stack trace** — debugging non-trivial.
- **TypeScript `unknown` propagates everywhere** — every catch needs a type guard.

### 3. Mutable fields

```ts
// ❌ Wrong — status / detail should not change after construction
export class HttpError extends Error {
  status: number;  // missing readonly
  detail: unknown; // missing readonly
  // ...
}
```

Failure modes:
- **A defensive catch could mutate the error**, breaking telemetry / re-throws.
- **TypeScript can't narrow `err.status` constants** — if `.status` is mutable, the narrowing `if (e.status === 404)` doesn't preserve the type through subsequent calls.

`readonly` is free safety.

## Multi-error-class extension

When a domain has more than one error category (HTTP transport, validation, business-rule), extend the base:

```ts
export class HttpError extends Error { /* ... */ }
export class ValidationError extends HttpError {
  constructor(public readonly fieldErrors: Record<string, string[]>, message: string) {
    super(422, { errors: fieldErrors }, message);
    this.name = "ValidationError";
  }
}
```

Callers discriminate by class hierarchy: `e instanceof ValidationError` (specific) → `e instanceof HttpError` (general) → `e instanceof Error` (universal).

Don't introduce subclasses preemptively — only when at least one catch site needs the specialization.

## Server / Client boundary gotcha

In Next.js App Router, modules can be bundled separately for the Server runtime and the Client bundle. If `HttpError` is imported in both a Server Component and a Client Component, **the bundler may emit two copies of the class** — `instanceof HttpError` in the Client won't match instances thrown server-side (during SSR), because the two classes have different identities.

Workarounds:

1. **Confine `instanceof HttpError` to the runtime where the throw happens** (server-side throws caught in Server Components, client-side throws caught in Client Components). This is what `web/lib/api.ts` does today — Server-Component catch in `page.tsx` is in the same Node SSR process as the `jsonFetch` throw.
2. **Duck-typing as a fallback**: `e && typeof e === "object" && "status" in e && typeof e.status === "number"`. Lossy on type narrowing but bundler-safe.
3. **Hoist the class into a `"use server"`-only or `"use client"`-only module** to force a single bundle target. Over-engineering for most apps.

When in doubt, prefer option 1 (confine instanceof to one runtime) — the cross-boundary case is rare and the duck-typing fallback is one line.

## Out of scope

- Non-HTTP transport errors (WebSocket close codes, gRPC status enums) — same pattern; adapt `.status` field to the transport's status type.
- Retry / circuit-breaker logic — belongs in a service layer above the helper, not in the error class.
- Telemetry hooks (`Sentry.captureException`) — orthogonal; the typed error doesn't change instrumentation.
- Localized error messages — typed error carries the raw `.message`; localization happens at the UI render layer.

## Cross-references

- `nextjs/typed-error-catch.md` — Server-Component catch pattern that consumes `HttpError.status`.
- `react/deliberate-action-mutations.md` — Client-side error rendering for mutation flows; uses `.message` directly.
