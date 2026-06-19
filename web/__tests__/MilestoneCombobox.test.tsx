// Tests for MilestoneCombobox — #2496 ordering + selectedLabel contract.
//
// Focus:
//  1. selectedLabel resolves a cancelled milestone's title from the full prop
//     (the closed combobox still shows the assignment even though cancelled is
//     hidden in the dropdown).
//  2. When the dropdown is open, cancelled milestones do NOT appear as options.

import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MilestoneCombobox } from "@/components/MilestoneCombobox";
import type { MilestoneRead } from "@/lib/api";

// Minimal MilestoneRead fixtures (only the fields the component uses).
function makeMilestone(
  overrides: Partial<MilestoneRead> & Pick<MilestoneRead, "id" | "title" | "milestone_status">,
): MilestoneRead {
  return {
    project_id: 1,
    description: null,
    start_date: null,
    target_date: null,
    sort_order: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    released_at: null,
    ...overrides,
  };
}

const CANCELLED = makeMilestone({
  id: 99,
  title: "Old Cancelled Sprint",
  milestone_status: "cancelled",
});
const ACTIVE = makeMilestone({
  id: 1,
  title: "Active Sprint",
  milestone_status: "active",
});
const PLANNED = makeMilestone({
  id: 2,
  title: "Planned Sprint",
  milestone_status: "planned",
});

const ALL_MILESTONES: MilestoneRead[] = [CANCELLED, ACTIVE, PLANNED];

describe("MilestoneCombobox — selectedLabel uses full prop (cancelled visible when assigned)", () => {
  it("closed combobox shows the cancelled milestone title when it is the assigned value", () => {
    render(
      <MilestoneCombobox
        value={CANCELLED.id}
        onChange={() => {}}
        milestones={ALL_MILESTONES}
      />,
    );
    const input = screen.getByRole("combobox");
    // The closed input must show the cancelled milestone's title.
    expect(input).toHaveValue(CANCELLED.title);
  });
});

describe("MilestoneCombobox — cancelled milestone hidden in open dropdown", () => {
  it("cancelled milestone does not appear in the option list when dropdown is open", () => {
    render(
      <MilestoneCombobox
        value={null}
        onChange={() => {}}
        milestones={ALL_MILESTONES}
      />,
    );
    // Open the dropdown.
    const input = screen.getByRole("combobox");
    fireEvent.focus(input);

    // Non-cancelled options should be present.
    expect(screen.getByText(ACTIVE.title)).toBeInTheDocument();
    expect(screen.getByText(PLANNED.title)).toBeInTheDocument();

    // Cancelled option should not be rendered in the list.
    const options = screen.getAllByRole("option");
    const optionTexts = options.map((o) => o.textContent);
    expect(optionTexts).not.toContain(CANCELLED.title);
  });
});
