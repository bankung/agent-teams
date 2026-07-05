"use client";

// Kanban #2111 Part 1 — client-side provider tree for the root layout.
// Wraps children with WildcardSSEProvider so InboxBadge, FlagBellBadge,
// and DashboardRefresher share one EventSource for the wildcard SSE channel.

import { WildcardSSEProvider } from "@/lib/WildcardSSEContext";
import { AgentsChangedBanner } from "@/components/AgentsChangedBanner";

export function ClientProviders({ children }: { children: React.ReactNode }) {
  return (
    <WildcardSSEProvider>
      {/* #1019 — app-wide, INSIDE the provider so it can subscribe to the
          shared wildcard SSE connection via useWildcardRowChanged. */}
      <AgentsChangedBanner />
      {children}
    </WildcardSSEProvider>
  );
}
