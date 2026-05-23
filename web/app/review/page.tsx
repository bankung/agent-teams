import { listAuditFlags } from "@/lib/api";
import { ReviewClient } from "@/components/ReviewClient";

// Kanban #1212 GOV4 — operator board-chairman /review surface.
//
// Server Component pattern matches /dashboard: fetch initial state at
// request time, then hand off to a Client component for selection +
// interactive resolve actions. SSR keeps first paint fast + lets the FE
// honor the cache: 'no-store' contract from lib/api.ts.

export const dynamic = "force-dynamic";

export default async function ReviewPage() {
  const flags = await listAuditFlags();
  return <ReviewClient initialFlags={flags} />;
}
