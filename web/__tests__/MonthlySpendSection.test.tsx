// Component tests for MonthlySpendSection — Kanban #2356 (AC3).
//
// Strategy: component is prop-driven (no internal fetch) — tests are fully
// synchronous + deterministic; no waitFor/findBy needed.
//
// Coverage:
//   (a) Renders one row per cycle with correct A / B / total text.
//   (b) Mode A carries the "≈" estimate marker.
//   (c) Clicking a cycle's drilldown button reveals task rows incl. "Unattributed".
//   (d) A zero / empty cycles array shows the muted no-spend line.
//   (e) Money strings (4-dp strings from API) parsed + formatted to 2-dp display.

import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MonthlySpendSection } from "@/components/MonthlySpendSection";
import type { MonthlyUsageResponse } from "@/lib/api";

// ── fixtures ──────────────────────────────────────────────────────────────────

const TWO_CYCLE_DATA: MonthlyUsageResponse = {
  months: 2,
  cycle_day: 1,
  total_cost_usd: "210.5000",
  cycles: [
    {
      cycle_start: "2026-06-01",
      cycle_end: "2026-06-30",
      mode_a_cost_usd: "195.2930",
      mode_a_input_tokens: 19956,
      mode_a_output_tokens: 136228,
      mode_b_cost_usd: "0.0000",
      mode_b_input_tokens: 0,
      mode_b_output_tokens: 0,
      total_cost_usd: "195.2930",
      tasks: [
        {
          task_id: 2355,
          task_title: "[mode-a-cost][P2] build usage endpoint",
          mode_a_cost_usd: "10.3678",
          mode_b_cost_usd: "0.0000",
          total_cost_usd: "10.3678",
        },
        {
          task_id: null,
          task_title: null,
          mode_a_cost_usd: "0.5000",
          mode_b_cost_usd: "0.0000",
          total_cost_usd: "0.5000",
        },
      ],
    },
    {
      cycle_start: "2026-05-01",
      cycle_end: "2026-05-31",
      mode_a_cost_usd: "15.2070",
      mode_a_input_tokens: 1200,
      mode_a_output_tokens: 8000,
      mode_b_cost_usd: "0.0000",
      mode_b_input_tokens: 0,
      mode_b_output_tokens: 0,
      total_cost_usd: "15.2070",
      tasks: [],
    },
  ],
};

const EMPTY_DATA: MonthlyUsageResponse = {
  months: 6,
  cycle_day: 1,
  total_cost_usd: "0.0000",
  cycles: [],
};

// ── (a) one row per cycle ─────────────────────────────────────────────────────

describe("MonthlySpendSection — cycle rows", () => {
  it("(a) renders a date range + cost values for each cycle", () => {
    render(<MonthlySpendSection data={TWO_CYCLE_DATA} />);

    // Two billing-cycle date ranges present.
    expect(screen.getByText(/Jun 1, 2026/)).toBeInTheDocument();
    expect(screen.getByText(/May 1, 2026/)).toBeInTheDocument();
  });

  it("(a) renders both cycles' total values", () => {
    render(<MonthlySpendSection data={TWO_CYCLE_DATA} />);

    // Jun cycle total $195.29; May cycle total $15.21.
    // getByText uses partial match — check for the formatted values in the DOM.
    expect(screen.getByText(/Total \$195\.29/)).toBeInTheDocument();
    expect(screen.getByText(/Total \$15\.21/)).toBeInTheDocument();
  });
});

// ── (b) Mode A estimate marker ────────────────────────────────────────────────

describe("MonthlySpendSection — estimate marker", () => {
  it("(b) Mode A rows carry the ≈ character", () => {
    render(<MonthlySpendSection data={TWO_CYCLE_DATA} />);

    // Both cycle rows show "A ≈ $…" — at least one must be present.
    const markers = screen.getAllByText(/A ≈/);
    expect(markers.length).toBeGreaterThan(0);
  });

  it("(b) Mode A span has a title attribute indicating it is an estimate", () => {
    render(<MonthlySpendSection data={TWO_CYCLE_DATA} />);

    // title includes the word "estimated"
    const modeASpan = screen.getAllByTitle(/estimated/i)[0];
    expect(modeASpan).toBeDefined();
  });
});

// ── (c) drilldown reveals task rows incl. Unattributed ───────────────────────

describe("MonthlySpendSection — drilldown", () => {
  it("(c) task rows are hidden before toggle", () => {
    render(<MonthlySpendSection data={TWO_CYCLE_DATA} />);

    // Task title must not be visible before the toggle is clicked.
    expect(
      screen.queryByText("[mode-a-cost][P2] build usage endpoint")
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Unattributed")).not.toBeInTheDocument();
  });

  it("(c) clicking the Tasks button reveals task rows", () => {
    render(<MonthlySpendSection data={TWO_CYCLE_DATA} />);

    // The drilldown button's accessible name comes from its explicit aria-label
    // ("Expand task breakdown for Jun 1, 2026 – Jun 30, 2026").
    const tasksBtn = screen.getAllByRole("button", { name: /expand task breakdown/i })[0];
    fireEvent.click(tasksBtn);

    // Named task and "Unattributed" bucket must now be visible.
    expect(
      screen.getByText("[mode-a-cost][P2] build usage endpoint")
    ).toBeInTheDocument();
    expect(screen.getByText("Unattributed")).toBeInTheDocument();
  });

  it("(c) drilldown button is aria-expanded=true after click", () => {
    render(<MonthlySpendSection data={TWO_CYCLE_DATA} />);

    const tasksBtn = screen.getAllByRole("button", { name: /expand task breakdown/i })[0];
    expect(tasksBtn).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(tasksBtn);
    expect(tasksBtn).toHaveAttribute("aria-expanded", "true");
  });

  it("(c) task_id=null renders 'Unattributed' label", () => {
    render(<MonthlySpendSection data={TWO_CYCLE_DATA} />);

    const tasksBtn = screen.getAllByRole("button", { name: /expand task breakdown/i })[0];
    fireEvent.click(tasksBtn);

    expect(screen.getByText("Unattributed")).toBeInTheDocument();
  });
});

// ── (d) empty / zero window ───────────────────────────────────────────────────

describe("MonthlySpendSection — empty state", () => {
  it("(d) shows the no-spend line when cycles array is empty", () => {
    render(<MonthlySpendSection data={EMPTY_DATA} />);

    expect(screen.getByText("No spend recorded yet.")).toBeInTheDocument();
  });

  it("(d) does not render any cycle rows when cycles is empty", () => {
    render(<MonthlySpendSection data={EMPTY_DATA} />);

    // No date ranges in DOM.
    expect(screen.queryByText(/Jun 1/)).not.toBeInTheDocument();
  });
});

// ── (e) money string parsing + 2-dp display ───────────────────────────────────

describe("MonthlySpendSection — money formatting", () => {
  it("(e) 4-dp string '195.2930' displays as '$195.29'", () => {
    render(<MonthlySpendSection data={TWO_CYCLE_DATA} />);

    // The Jun cycle Mode A value: "195.2930" → "$195.29"
    expect(screen.getByText(/A ≈ \$195\.29/)).toBeInTheDocument();
  });

  it("(e) 4-dp string '0.5000' displays as '$0.50' in a task row", () => {
    render(<MonthlySpendSection data={TWO_CYCLE_DATA} />);

    // Open drilldown to see task rows.
    const tasksBtn = screen.getAllByRole("button", { name: /expand task breakdown/i })[0];
    fireEvent.click(tasksBtn);

    // Unattributed task total: "0.5000" → "$0.50"
    // Multiple $0.00 rows exist (mode B); use getAllByText + check at least one $0.50.
    const cells = screen.getAllByText("$0.50");
    expect(cells.length).toBeGreaterThan(0);
  });

  it("(e) total_cost_usd '210.5000' is not displayed as raw string", () => {
    render(<MonthlySpendSection data={TWO_CYCLE_DATA} />);

    // The raw 4-dp string must not appear literally — it should be reformatted.
    expect(screen.queryByText("210.5000")).not.toBeInTheDocument();
  });
});
