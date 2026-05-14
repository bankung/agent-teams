"use client";

// #930 — Dashboard SSE live updates; mirror of Board.tsx:118-126 with no projectId
// (wildcard subscription). Re-renders the server component on any task/project
// row change so lane counts + project list stay in sync without manual refresh.

import { useCallback } from "react";
import { useRouter } from "next/navigation";

import { useRowChangedEvents } from "@/lib/useRowChangedEvents";
import { ConnectionStateBadge } from "@/components/ConnectionStateBadge";

export function DashboardRefresher() {
  const router = useRouter();
  const onChange = useCallback(() => {
    router.refresh();
  }, [router]);
  const { connectionState, lastEventAt } = useRowChangedEvents({
    onTaskChange: onChange,
    onProjectChange: onChange,
  });
  return (
    <ConnectionStateBadge state={connectionState} lastEventAt={lastEventAt} />
  );
}
