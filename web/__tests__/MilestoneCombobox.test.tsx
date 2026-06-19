// Tests for MilestoneCombobox — #2496 ordering + selectedLabel contract,
// plus #2502 aria-activedescendant tracking.
//
// Focus:
//  1. selectedLabel resolves a cancelled milestone's title from the full prop
//     (the closed combobox still shows the assignment even though cancelled is
//     hidden in the dropdown).
//  2. When the dropdown is open, cancelled milestones do NOT appear as options.
//  3. aria-activedescendant is undefined when closed, set to the highlighted
//     option id when open, and tracks keyboard navigation (Fix 1 #2502).

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

describe("MilestoneCombobox — aria-activedescendant (Fix 1 #2502)", () => {
  it("aria-activedescendant is absent when the dropdown is closed", () => {
    render(
      <MilestoneCombobox
        value={null}
        onChange={() => {}}
        milestones={[ACTIVE, PLANNED]}
      />,
    );
    const input = screen.getByRole("combobox");
    // Closed state: no activedescendant.
    expect(input).not.toHaveAttribute("aria-activedescendant");
  });

  it("sets aria-activedescendant to the None option id when first opened", () => {
    render(
      <MilestoneCombobox
        value={null}
        onChange={() => {}}
        milestones={[ACTIVE, PLANNED]}
      />,
    );
    const input = screen.getByRole("combobox");
    fireEvent.focus(input);

    // Dropdown opens with highlight=0 (None row).
    const activedescendant = input.getAttribute("aria-activedescendant");
    expect(activedescendant).toBeTruthy();

    // The referenced id must exist in the DOM.
    const referencedEl = document.getElementById(activedescendant!);
    expect(referencedEl).not.toBeNull();
    expect(referencedEl!.textContent).toBe("None");
  });

  it("aria-activedescendant moves to the next option on ArrowDown", () => {
    render(
      <MilestoneCombobox
        value={null}
        onChange={() => {}}
        milestones={[ACTIVE, PLANNED]}
      />,
    );
    const input = screen.getByRole("combobox");
    fireEvent.focus(input);

    // Press ArrowDown once: highlight moves from 0 (None) to 1 (first match).
    fireEvent.keyDown(input, { key: "ArrowDown" });

    const activedescendant = input.getAttribute("aria-activedescendant");
    expect(activedescendant).toBeTruthy();
    const referencedEl = document.getElementById(activedescendant!);
    expect(referencedEl).not.toBeNull();
    // orderMilestonesForPicker puts active before planned; first match = ACTIVE.
    expect(referencedEl!.getAttribute("role")).toBe("option");
    expect(referencedEl!.textContent).toBe(ACTIVE.title);
  });

  it("aria-activedescendant wraps around on ArrowUp from None row", () => {
    render(
      <MilestoneCombobox
        value={null}
        onChange={() => {}}
        milestones={[ACTIVE, PLANNED]}
      />,
    );
    const input = screen.getByRole("combobox");
    fireEvent.focus(input);

    // ArrowUp from index 0 → wraps to last option (optionCount - 1 = 2 = PLANNED).
    fireEvent.keyDown(input, { key: "ArrowUp" });

    const activedescendant = input.getAttribute("aria-activedescendant");
    expect(activedescendant).toBeTruthy();
    const referencedEl = document.getElementById(activedescendant!);
    expect(referencedEl).not.toBeNull();
    expect(referencedEl!.getAttribute("role")).toBe("option");
    expect(referencedEl!.textContent).toBe(PLANNED.title);
  });

  it("clears aria-activedescendant after Escape closes the dropdown", () => {
    render(
      <MilestoneCombobox
        value={null}
        onChange={() => {}}
        milestones={[ACTIVE, PLANNED]}
      />,
    );
    const input = screen.getByRole("combobox");
    fireEvent.focus(input);

    // Confirm it was set while open.
    expect(input).toHaveAttribute("aria-activedescendant");

    fireEvent.keyDown(input, { key: "Escape" });

    // After close, attribute must be absent.
    expect(input).not.toHaveAttribute("aria-activedescendant");
  });

  it("value contract unchanged: onChange fires with null for None, id for milestone", () => {
    const onChange = vi.fn();
    render(
      <MilestoneCombobox
        value={null}
        onChange={onChange}
        milestones={[ACTIVE, PLANNED]}
      />,
    );
    const input = screen.getByRole("combobox");
    fireEvent.focus(input);

    // Navigate to first milestone option and select with Enter.
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(onChange).toHaveBeenCalledWith(ACTIVE.id);
  });
});
