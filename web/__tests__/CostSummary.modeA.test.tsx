// Component tests for the re-pointed CostSummary "Mode A" card — Kanban #2735.
//
// The Mode A card now sums entry.actual_interactive_cost (the real usage_events
// hook-capture ledger), NOT entry.estimated_cost (the heuristic roll-up). These
// tests pin: (1) the new value renders, (2) the new label renders, (3) the
// estimated value does NOT leak into the Mode A card.
//
// CostSummary defaults to defaultCollapsed=false → always-expanded (no toggle
// chrome), so the card is visible synchronously; no waitFor/findBy needed.

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { CostSummary } from "@/components/CostSummary";
import type { ProjectStatsEntry } from "@/lib/api";

// Entry carries DISTINCT actual_interactive_cost vs estimated_cost values so a
// regression that reads the wrong field is caught by the "$777" assertion.
function makeEntry(): ProjectStatsEntry {
  return {
    id: 1,
    name: "test-project",
    team: "dev",
    run_mode_breakdown: {} as Record<string, number>,
    counts: { "1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "6": 0 },
    last_activity_at: null,
    cost_usage: {
      total_cost_usd: "5.0000",
      total_input_tokens: 100,
      total_output_tokens: 200,
      total_context_chars: 0,
      budget_warning_count: 0,
      session_run_count: 3,
    },
    // The OLD source — must NOT appear in the Mode A card anymore.
    estimated_cost: {
      total_cost_usd: "999.0000",
      total_input_tokens: 11111,
      total_output_tokens: 22222,
    },
    // The NEW source — the Mode A card must show this.
    actual_interactive_cost: {
      total_cost_usd: "777.0000",
      total_input_tokens: 33333,
      total_output_tokens: 44444,
    },
  } as unknown as ProjectStatsEntry;
}

describe("CostSummary — Mode A re-pointed to actual_interactive_cost (#2735)", () => {
  it("shows the new 'Mode A · Actual (interactive)' label", () => {
    render(<CostSummary stats={[makeEntry()]} />);
    expect(
      screen.getByText("Mode A · Actual (interactive)"),
    ).toBeInTheDocument();
    // The old label must be gone.
    expect(screen.queryByText("Mode A · Estimated")).not.toBeInTheDocument();
  });

  it("sums actual_interactive_cost (shows $777.00), not estimated_cost", () => {
    render(<CostSummary stats={[makeEntry()]} />);
    expect(screen.getByText("$777.00")).toBeInTheDocument();
    // The estimated value must NOT leak into the Mode A card.
    expect(screen.queryByText("$999.00")).not.toBeInTheDocument();
  });

  it("renders the interactive token counts (33,333 in / 44,444 out)", () => {
    render(<CostSummary stats={[makeEntry()]} />);
    expect(
      screen.getByText(/33,333 in \/ 44,444 out tokens/),
    ).toBeInTheDocument();
  });

  it("tooltips the value as real interactive cost from the usage_events ledger", () => {
    render(<CostSummary stats={[makeEntry()]} />);
    const value = screen.getByText("$777.00");
    expect(value).toHaveAttribute(
      "title",
      expect.stringContaining("usage_events ledger"),
    );
  });

  it("sums actual_interactive_cost across multiple projects (portfolio view)", () => {
    const a = makeEntry();
    const b = makeEntry();
    b.id = 2;
    // 777 + 777 = 1554 → "$1,554.00".
    render(<CostSummary stats={[a, b]} />);
    expect(screen.getByText("$1,554.00")).toBeInTheDocument();
  });
});
