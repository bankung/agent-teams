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

// Re-export the dynamic config + default export. The Next.js route-segment
// config is per-route-file; re-exporting `dynamic` ensures the alias has the
// same SSR behavior (force-dynamic) as the canonical /tasks/[id] route.
export { default, dynamic } from "../../tasks/[id]/page";
