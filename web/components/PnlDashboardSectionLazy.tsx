"use client";

import nextDynamic from "next/dynamic";

// Kanban #2487 (Next 16) — `ssr: false` dynamic imports are no longer allowed in
// Server Components, so the code-split lives in this thin client wrapper. The
// FINANCE_PANELS_ENABLED gate stays in the (server) dashboard page, so this only
// renders when the flag is on → the chunk is still absent from the bundle when
// the flag is off (preserves Kanban #2111 Part 3b code-splitting).
const PnlDashboardSectionInner = nextDynamic(
  () =>
    import("@/components/PnlDashboardSection").then(
      (m) => m.PnlDashboardSection,
    ),
  { ssr: false },
);

type Props = {
  defaultCollapsed?: boolean;
  storageKey?: string;
};

export function PnlDashboardSectionLazy(props: Props) {
  return <PnlDashboardSectionInner {...props} />;
}
