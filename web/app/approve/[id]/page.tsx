// Kanban #1451 — push-click alias route. `/approve/<id>` re-exports the
// existing /tasks/<id> focus view so the BE `_fire_hitl_push` click_url
// migration (`/tasks/<id>` → `/approve/<id>`) resolves to the same React
// tree without duplicating component code. The semantic distinction is
// purely cosmetic in the URL — the same TaskFocusView handles the HITL
// resume branch internally based on `interaction_kind` + `is_pending`.
//
// Why re-export instead of a fresh page: Next.js App Router resolves a
// dynamic segment `[id]` independently per route folder; re-exporting
// `default` from the sibling route is the minimum-viable alias (zero
// duplicated fetch/wiring code). If a future need diverges the two URLs
// (different metadata, different chrome), peel this off then.

// Re-export the default export. The Next.js route-segment config (`dynamic`)
// cannot be re-exported across module boundaries in Next 16/Turbopack — it
// must be declared as a named const in each route file. Declaring it here
// directly preserves the same force-dynamic SSR behavior as the canonical
// /tasks/[id] route.
//
// Next 16 async params (#2487): TaskFocusPage now takes `params`/`searchParams`
// as `Promise<…>` (awaited inside the canonical route). This alias re-exports
// that same component verbatim, so it inherits the async-params contract — no
// per-file change needed here.
export { default } from "../../tasks/[id]/page";
export const dynamic = "force-dynamic";
