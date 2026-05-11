# Next.js — `notFound()` and `redirect()` dev-vs-prod wire behavior

**Scope:** how Tier-1 web smoke probes must assert against `notFound()` / `redirect()` routes. `next dev` and `next build && next start` emit **different wire responses** for both calls — asserting against wire status codes in dev produces paper-PASS / paper-FAIL outcomes.

## The footgun

| Call | `next dev` (development) | `next build && next start` (production) |
|---|---|---|
| `notFound()` from a Server Component try/catch | HTTP **200** + rendered not-found page body (`>This page could not be found<`) | HTTP **404** wire-level |
| `redirect("/x")` from a Server Component | HTTP **200** + meta-refresh sentinel: `<meta http-equiv="refresh" content="1;url=/x"/>` + `NEXT_REDIRECT;...;307` template hint | HTTP **307** wire-level + `Location: /x` header |

Root cause: `next dev` performs in-process navigation through the React Server Components shell; production compile emits real wire-level responses.

## Rule

**Tier-1 web smoke runs against `next dev`.** Smoke matrices on `notFound()` / `redirect()` routes MUST assert against rendered markers, not wire status codes:

- `notFound()`: grep response body for `>This page could not be found<` (Next.js stock not-found marker) OR a custom marker if `app/not-found.tsx` exists. Combine with grep counts for board markers (`data-board="dnd"` = 0, `data-task-id=` = 0) to prove the page did NOT fall through to a real route.
- `redirect("/x")`: grep response body for `url=/x` (meta-refresh content attribute) AND `NEXT_REDIRECT;...;<status>;` template hint. Lock the target byte-exact.

For **wire-level** 404 / 307 verification, run a production build:

```bash
docker compose exec -T web sh -c "cd /app && npx next build && PORT=3001 npx next start &"
# … probe localhost:3001 with curl -w "%{http_code}" …
```

Production-wire verification is Tier-2 release-wrap territory, not per-task Tier-1. Don't run it on every commit.

## Causal probe pair

`notFound()` rendered-marker assertion in dev mode could vacuously pass if `next dev` is totally broken (every URL returns the not-found page). Pair it with a POSITIVE probe on the **same web server** that asserts a known-good URL renders the real board (e.g., `data-task-id=` >= 50 on `/p/agent-teams`). Both probes must run against the same `localhost:<port>` mount; binding is then causal — only the unknown URL hits not-found.

Same pattern for `redirect()`: pair the meta-refresh-marker assertion with a positive fetch on the redirect target URL.

## Canonical worked example (in-repo)

[web/app/p/[name]/page.tsx](../../../web/app/p/[name]/page.tsx) wraps `getProjectByName(params.name)` in `try / catch { notFound() }`. The dev-tester Tier-1 probe for Kanban #407 asserted `>This page could not be found<` × 2 + `data-task-id=` × 0 on `/p/_nonexistent-407-test`, paired with `data-task-id=` × 56 + `data-board="dnd"` × 1 on `/p/agent-teams` (same server). Causal binding proved.

[web/app/page.tsx](../../../web/app/page.tsx) uses `redirect(\`/p/\${name}\`)`. Same Tier-1 probe asserted `NEXT_REDIRECT;...;/p/agent-teams;307` template hint × 2 + `url=/p/agent-teams` × 1 in the dev-mode body. Wire-307 verification deferred to a prod-build smoke (Tier-2).

## Out of scope

- `notFound()` / `redirect()` from Route Handlers (`route.ts`) — different runtime (no React render); wire behavior matches prod in dev too. Test directly with status codes.
- Middleware redirects (`middleware.ts`) — emit wire-307/308 in both dev and prod (no React shell). Use status assertions.
- Client-side `router.push` — no wire response; assert post-navigation DOM state.
