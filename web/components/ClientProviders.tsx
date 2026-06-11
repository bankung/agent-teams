"use client";

// Kanban #2111 Part 1 — client-side provider tree for the root layout.
// Wraps children with WildcardSSEProvider so InboxBadge, FlagBellBadge,
// and DashboardRefresher share one EventSource for the wildcard SSE channel.

import { WildcardSSEProvider } from "@/lib/WildcardSSEContext";

export function ClientProviders({ children }: { children: React.ReactNode }) {
  return <WildcardSSEProvider>{children}</WildcardSSEProvider>;
}
