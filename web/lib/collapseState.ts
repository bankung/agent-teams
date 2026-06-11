// collapseState.ts — shared localStorage helpers for collapsible panels.
// Extracted from CostSummary, PnlDashboardSection, AuditorActivityPanel,
// CrossProjectActiveTasksList (Kanban #2111 Part 4). Behavior is identical
// to the 4 prior inline copies — same logic, same localStorage contract.

export function readExpanded(key: string, defaultCollapsed: boolean): boolean {
  // expanded = !defaultCollapsed when no stored pref exists.
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return !defaultCollapsed;
    return JSON.parse(raw) !== false;
  } catch {
    return !defaultCollapsed;
  }
}

export function writeExpanded(key: string, next: boolean): void {
  try {
    localStorage.setItem(key, JSON.stringify(next));
    window.dispatchEvent(
      new StorageEvent("storage", {
        key,
        newValue: JSON.stringify(next),
        storageArea: localStorage,
      }),
    );
  } catch {
    // localStorage blocked — silently ignore.
  }
}
