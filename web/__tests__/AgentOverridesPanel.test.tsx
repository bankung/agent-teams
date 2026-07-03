// Component tests for AgentOverridesPanel — Kanban #1018 + #1020.
//
// Strategy: mock @/lib/api (getAgents / getAgentOverrides / patchAgentOverrides
// / getAgentCostEstimate). Assert: (1) an un-overridden roster agent merges to
// enabled + "Default" tier; (2) toggling a row's checkbox fires PATCH with
// only {name, enabled:false}; (3) changing the tier select fires PATCH with
// only {name, model_override}; (4) a failed PATCH reverts the optimistic
// toggle + surfaces a row error; (5) #1018 M1/N2 — a project switch (rerender
// with a new key, mirroring the key={project.id} fix on the real mount sites)
// surfaces the NEW project's fetched values, not the prior project's stale
// optimistic rowState.
//
// #1020 additions: (6) badge renders green from a mocked estimate; (7) badge
// renders red; (8) spawn_count_last_30d=0 renders "no history" dash instead of
// a fabricated $0; (9) toggling ON a red agent opens the confirm modal and
// does NOT PATCH until Confirm; Cancel aborts (no PATCH, toggle stays off);
// (10) an estimate fetch rejection still renders the row without a badge.
//
// Determinism: async-fetch assertions use findBy*/waitFor (never sync
// querySelector on post-fetch state) — the FE test-determinism rule requires
// the full suite to hold across repeated runs.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, configure } from "@testing-library/react";
import type { AgentSummary, AgentOverridesResponse, AgentCostEstimate } from "@/lib/api";

configure({ asyncUtilTimeout: 5000 });

const mockGetAgents = vi.fn();
const mockGetAgentOverrides = vi.fn();
const mockPatchAgentOverrides = vi.fn();
const mockGetAgentCostEstimate = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getAgents: (...args: Parameters<typeof actual.getAgents>) =>
      mockGetAgents(...args),
    getAgentOverrides: (...args: Parameters<typeof actual.getAgentOverrides>) =>
      mockGetAgentOverrides(...args),
    patchAgentOverrides: (...args: Parameters<typeof actual.patchAgentOverrides>) =>
      mockPatchAgentOverrides(...args),
    getAgentCostEstimate: (...args: Parameters<typeof actual.getAgentCostEstimate>) =>
      mockGetAgentCostEstimate(...args),
  };
});

// Imported AFTER mocks register.
import { AgentOverridesPanel } from "@/components/AgentOverridesPanel";

function agent(over: Partial<AgentSummary> = {}): AgentSummary {
  return {
    name: "dev-frontend",
    description: "Frontend developer",
    model: "sonnet",
    tools_summary: "All tools",
    tool_count: null,
    hook_count: 0,
    source_file: "dev-frontend.md",
    domain: "dev",
    valid: true,
    validation_errors: [],
    tool_chips: [],
    ...over,
  };
}

function overridesResponse(
  over: Partial<AgentOverridesResponse> = {},
): AgentOverridesResponse {
  return { agents: [], lead_overrides: {}, ...over };
}

function costEstimate(over: Partial<AgentCostEstimate> = {}): AgentCostEstimate {
  return {
    avg_cost_per_spawn: 0.42,
    spawn_count_last_30d: 10,
    projected_monthly_usd: 4.2,
    vs_project_budget_pct: 8.4,
    traffic_light: "green",
    ...over,
  };
}

beforeEach(() => {
  mockGetAgents.mockReset();
  mockGetAgentOverrides.mockReset();
  mockPatchAgentOverrides.mockReset();
  // #1020 — default every test to a rejected estimate fetch (mirrors "the
  // live API may not have the endpoint yet" from the brief); tests that need
  // a real badge override this explicitly. A rejection is swallowed by the
  // panel's Promise.allSettled fan-out, so pre-#1020 tests are unaffected.
  mockGetAgentCostEstimate.mockReset();
  mockGetAgentCostEstimate.mockRejectedValue(new Error("not implemented yet"));
});

describe("AgentOverridesPanel", () => {
  it("merges an un-overridden roster agent to enabled + Default tier", async () => {
    mockGetAgents.mockResolvedValue([agent({ name: "dev-frontend" })]);
    mockGetAgentOverrides.mockResolvedValue(overridesResponse());

    render(<AgentOverridesPanel projectId={1} />);

    const row = await screen.findByText("dev-frontend");
    const li = row.closest("[data-agent-override-row]");
    expect(li).not.toBeNull();
    expect(li).toHaveAttribute("data-agent-override-enabled", "true");

    const checkbox = li!.querySelector(
      "[data-agent-override-toggle]",
    ) as HTMLInputElement;
    expect(checkbox.checked).toBe(true);

    const tierSelect = li!.querySelector(
      "[data-agent-override-tier]",
    ) as HTMLSelectElement;
    expect(tierSelect.value).toBe("");
    expect(tierSelect.options[tierSelect.selectedIndex].text).toBe("Default");
  });

  it("respects an existing override (disabled + haiku tier + notes)", async () => {
    mockGetAgents.mockResolvedValue([agent({ name: "dev-backend" })]);
    mockGetAgentOverrides.mockResolvedValue(
      overridesResponse({
        agents: [
          {
            name: "dev-backend",
            enabled: false,
            model_override: "haiku",
            notes: "downshifted for cost",
          },
        ],
      }),
    );

    render(<AgentOverridesPanel projectId={1} />);

    const li = (await screen.findByText("dev-backend")).closest(
      "[data-agent-override-row]",
    ) as HTMLElement;
    expect(li).toHaveAttribute("data-agent-override-enabled", "false");

    const checkbox = li.querySelector(
      "[data-agent-override-toggle]",
    ) as HTMLInputElement;
    expect(checkbox.checked).toBe(false);

    const tierSelect = li.querySelector(
      "[data-agent-override-tier]",
    ) as HTMLSelectElement;
    expect(tierSelect.value).toBe("haiku");

    const notesInput = li.querySelector(
      "[data-agent-override-notes]",
    ) as HTMLInputElement;
    expect(notesInput.value).toBe("downshifted for cost");
  });

  it("toggling the checkbox fires a partial PATCH with only {name, enabled}", async () => {
    mockGetAgents.mockResolvedValue([agent({ name: "dev-frontend" })]);
    mockGetAgentOverrides.mockResolvedValue(overridesResponse());
    mockPatchAgentOverrides.mockResolvedValue(
      overridesResponse({
        agents: [{ name: "dev-frontend", enabled: false, model_override: null, notes: null }],
      }),
    );

    render(<AgentOverridesPanel projectId={7} />);
    const li = (await screen.findByText("dev-frontend")).closest(
      "[data-agent-override-row]",
    ) as HTMLElement;
    const checkbox = li.querySelector(
      "[data-agent-override-toggle]",
    ) as HTMLInputElement;

    fireEvent.click(checkbox);

    // Optimistic flip is immediate.
    expect(li).toHaveAttribute("data-agent-override-enabled", "false");

    await waitFor(() => expect(mockPatchAgentOverrides).toHaveBeenCalledTimes(1));
    expect(mockPatchAgentOverrides).toHaveBeenCalledWith(7, [
      { name: "dev-frontend", enabled: false },
    ]);
  });

  it("changing the tier select fires a partial PATCH with only {name, model_override}", async () => {
    mockGetAgents.mockResolvedValue([agent({ name: "dev-frontend" })]);
    mockGetAgentOverrides.mockResolvedValue(overridesResponse());
    mockPatchAgentOverrides.mockResolvedValue(
      overridesResponse({
        agents: [{ name: "dev-frontend", enabled: true, model_override: "opus", notes: null }],
      }),
    );

    render(<AgentOverridesPanel projectId={7} />);
    const li = (await screen.findByText("dev-frontend")).closest(
      "[data-agent-override-row]",
    ) as HTMLElement;
    const tierSelect = li.querySelector(
      "[data-agent-override-tier]",
    ) as HTMLSelectElement;

    fireEvent.change(tierSelect, { target: { value: "opus" } });

    await waitFor(() => expect(mockPatchAgentOverrides).toHaveBeenCalledTimes(1));
    expect(mockPatchAgentOverrides).toHaveBeenCalledWith(7, [
      { name: "dev-frontend", model_override: "opus" },
    ]);
  });

  it("reverts the optimistic toggle and shows a row error on a failed PATCH", async () => {
    mockGetAgents.mockResolvedValue([agent({ name: "dev-frontend" })]);
    mockGetAgentOverrides.mockResolvedValue(overridesResponse());
    mockPatchAgentOverrides.mockRejectedValue(new Error("422 unknown agent"));

    render(<AgentOverridesPanel projectId={7} />);
    const li = (await screen.findByText("dev-frontend")).closest(
      "[data-agent-override-row]",
    ) as HTMLElement;
    const checkbox = li.querySelector(
      "[data-agent-override-toggle]",
    ) as HTMLInputElement;

    fireEvent.click(checkbox);
    expect(li).toHaveAttribute("data-agent-override-enabled", "false");

    await waitFor(() =>
      expect(li).toHaveAttribute("data-agent-override-enabled", "true"),
    );
    expect(checkbox.checked).toBe(true);
    await screen.findByText("422 unknown agent");
  });

  it("#1018 M1/N2 — a keyed project switch shows the new project's fetched values, not stale rowState", async () => {
    // Project 1: dev-frontend, enabled, no tier override.
    mockGetAgents.mockResolvedValueOnce([agent({ name: "dev-frontend" })]);
    mockGetAgentOverrides.mockResolvedValueOnce(overridesResponse());
    mockPatchAgentOverrides.mockResolvedValueOnce(
      overridesResponse({
        agents: [{ name: "dev-frontend", enabled: false, model_override: null, notes: null }],
      }),
    );

    // Mirrors the real mount sites (web/app/settings/page.tsx,
    // ProjectSettingsPanel.tsx): key={projectId} forces a full remount on
    // a project switch instead of App Router's "same position, new props"
    // reuse — the bug M1 fixed.
    const { rerender } = render(
      <AgentOverridesPanel key={1} projectId={1} />,
    );

    const li1 = (await screen.findByText("dev-frontend")).closest(
      "[data-agent-override-row]",
    ) as HTMLElement;
    expect(li1).toHaveAttribute("data-agent-override-enabled", "true");

    // Optimistically disable it on project 1 — this seeds rowState with a
    // LOCAL mutation that must NOT survive the switch below.
    const checkbox1 = li1.querySelector(
      "[data-agent-override-toggle]",
    ) as HTMLInputElement;
    fireEvent.click(checkbox1);
    expect(li1).toHaveAttribute("data-agent-override-enabled", "false");
    await waitFor(() => expect(mockPatchAgentOverrides).toHaveBeenCalledTimes(1));
    expect(mockPatchAgentOverrides).toHaveBeenCalledWith(1, [
      { name: "dev-frontend", enabled: false },
    ]);

    // Project 2: a DIFFERENT agent, enabled, with its own tier override.
    // If rowState leaked across the switch, the panel would still show
    // "dev-frontend" disabled instead of fetching + rendering project 2's
    // "dev-backend" row.
    mockGetAgents.mockResolvedValueOnce([agent({ name: "dev-backend" })]);
    mockGetAgentOverrides.mockResolvedValueOnce(
      overridesResponse({
        agents: [
          { name: "dev-backend", enabled: true, model_override: "opus", notes: null },
        ],
      }),
    );

    rerender(<AgentOverridesPanel key={2} projectId={2} />);

    // The stale project-1 row must be gone entirely (full remount, not a
    // patched-in-place update).
    expect(screen.queryByText("dev-frontend")).not.toBeInTheDocument();

    const li2 = (await screen.findByText("dev-backend")).closest(
      "[data-agent-override-row]",
    ) as HTMLElement;
    expect(li2).toHaveAttribute("data-agent-override-enabled", "true");
    const tierSelect2 = li2.querySelector(
      "[data-agent-override-tier]",
    ) as HTMLSelectElement;
    expect(tierSelect2.value).toBe("opus");

    // The fetches for project 2 used its own id — not a stale project-1 fetch.
    expect(mockGetAgentOverrides).toHaveBeenLastCalledWith(2);
    // Only the one PATCH from project 1 fired — the remount did not replay
    // or carry over any pending write.
    expect(mockPatchAgentOverrides).toHaveBeenCalledTimes(1);
  });
});

describe("AgentOverridesPanel — #1020 cost-preview badge + budget-impact confirm", () => {
  it("(a) renders the badge from a mocked estimate — green case", async () => {
    mockGetAgents.mockResolvedValue([agent({ name: "dev-frontend" })]);
    mockGetAgentOverrides.mockResolvedValue(overridesResponse());
    mockGetAgentCostEstimate.mockResolvedValue(
      costEstimate({ traffic_light: "green", projected_monthly_usd: 4.2 }),
    );

    render(<AgentOverridesPanel projectId={1} />);
    const li = (await screen.findByText("dev-frontend")).closest(
      "[data-agent-override-row]",
    ) as HTMLElement;

    const badge = await waitFor(() => {
      const el = li.querySelector("[data-agent-cost-badge]");
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(badge).toHaveAttribute("data-agent-cost-badge", "green");
    expect(badge).toHaveTextContent("$4.20/mo");
    expect(mockGetAgentCostEstimate).toHaveBeenCalledWith("dev-frontend", 1);
  });

  it("(a) renders the badge from a mocked estimate — red case", async () => {
    mockGetAgents.mockResolvedValue([agent({ name: "dev-frontend" })]);
    mockGetAgentOverrides.mockResolvedValue(overridesResponse());
    mockGetAgentCostEstimate.mockResolvedValue(
      costEstimate({ traffic_light: "red", projected_monthly_usd: 55.0 }),
    );

    render(<AgentOverridesPanel projectId={1} />);
    const li = (await screen.findByText("dev-frontend")).closest(
      "[data-agent-override-row]",
    ) as HTMLElement;

    const badge = await waitFor(() => {
      const el = li.querySelector("[data-agent-cost-badge]");
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(badge).toHaveAttribute("data-agent-cost-badge", "red");
    expect(badge).toHaveTextContent("$55.00/mo");
  });

  it("(b) renders a neutral dash, not a fabricated $0, when there's no spawn history (count=0)", async () => {
    mockGetAgents.mockResolvedValue([agent({ name: "dev-frontend" })]);
    mockGetAgentOverrides.mockResolvedValue(overridesResponse());
    mockGetAgentCostEstimate.mockResolvedValue(
      costEstimate({
        traffic_light: "green",
        spawn_count_last_30d: 0,
        avg_cost_per_spawn: null,
        projected_monthly_usd: null,
      }),
    );

    render(<AgentOverridesPanel projectId={1} />);
    const li = (await screen.findByText("dev-frontend")).closest(
      "[data-agent-override-row]",
    ) as HTMLElement;

    const badge = await waitFor(() => {
      const el = li.querySelector("[data-agent-cost-badge]");
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(badge).toHaveTextContent("no history");
    expect(badge).not.toHaveTextContent("$0");
    // count=0 tooltip does NOT mention "no cost data" — that phrasing is
    // reserved for the count>0-but-null-projected case below.
    expect(badge.getAttribute("title")).toBe("avg n/a/spawn · 0 spawns last 30d");
    expect(badge.getAttribute("title")).not.toContain("no cost data");
  });

  it("(b) distinguishes count>0-with-no-cost-data from true no-history — same dash, different tooltip", async () => {
    mockGetAgents.mockResolvedValue([agent({ name: "dev-frontend" })]);
    mockGetAgentOverrides.mockResolvedValue(overridesResponse());
    mockGetAgentCostEstimate.mockResolvedValue(
      costEstimate({
        traffic_light: "green",
        spawn_count_last_30d: 5,
        avg_cost_per_spawn: null,
        projected_monthly_usd: null,
      }),
    );

    render(<AgentOverridesPanel projectId={1} />);
    const li = (await screen.findByText("dev-frontend")).closest(
      "[data-agent-override-row]",
    ) as HTMLElement;

    const badge = await waitFor(() => {
      const el = li.querySelector("[data-agent-cost-badge]");
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    // Same visible dash text as the count=0 case...
    expect(badge).toHaveTextContent("no history");
    expect(badge).not.toHaveTextContent("$0");
    // ...but the tooltip distinguishes "spawns exist, no cost data" from
    // "genuinely no history".
    expect(badge.getAttribute("title")).toBe("5 spawns last 30d, no cost data");
  });

  it("(c) toggling ON a red agent opens the confirm modal; Confirm applies the PATCH", async () => {
    mockGetAgents.mockResolvedValue([
      agent({ name: "dev-frontend" }),
    ]);
    mockGetAgentOverrides.mockResolvedValue(
      overridesResponse({
        agents: [{ name: "dev-frontend", enabled: false, model_override: null, notes: null }],
      }),
    );
    mockGetAgentCostEstimate.mockResolvedValue(
      costEstimate({ traffic_light: "red", projected_monthly_usd: 55.0, vs_project_budget_pct: 110 }),
    );
    mockPatchAgentOverrides.mockResolvedValue(
      overridesResponse({
        agents: [{ name: "dev-frontend", enabled: true, model_override: null, notes: null }],
      }),
    );

    render(<AgentOverridesPanel projectId={1} />);
    const li = (await screen.findByText("dev-frontend")).closest(
      "[data-agent-override-row]",
    ) as HTMLElement;
    // Wait for the estimate to land so the intercept has a traffic_light to
    // read — otherwise the click could race the fetch.
    await waitFor(() =>
      expect(li.querySelector("[data-agent-cost-badge]")).not.toBeNull(),
    );

    const checkbox = li.querySelector(
      "[data-agent-override-toggle]",
    ) as HTMLInputElement;
    fireEvent.click(checkbox);

    // Intercepted: no optimistic flip, no PATCH yet, modal is open.
    expect(checkbox.checked).toBe(false);
    expect(mockPatchAgentOverrides).not.toHaveBeenCalled();
    await screen.findByRole("dialog");

    fireEvent.click(screen.getByText("Enable anyway"));

    await waitFor(() => expect(mockPatchAgentOverrides).toHaveBeenCalledTimes(1));
    expect(mockPatchAgentOverrides).toHaveBeenCalledWith(1, [
      { name: "dev-frontend", enabled: true },
    ]);
    await waitFor(() =>
      expect(li).toHaveAttribute("data-agent-override-enabled", "true"),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("(c) toggling ON a red agent then Cancel aborts — no PATCH, toggle stays off", async () => {
    mockGetAgents.mockResolvedValue([agent({ name: "dev-frontend" })]);
    mockGetAgentOverrides.mockResolvedValue(
      overridesResponse({
        agents: [{ name: "dev-frontend", enabled: false, model_override: null, notes: null }],
      }),
    );
    mockGetAgentCostEstimate.mockResolvedValue(
      costEstimate({ traffic_light: "red", projected_monthly_usd: 55.0 }),
    );

    render(<AgentOverridesPanel projectId={1} />);
    const li = (await screen.findByText("dev-frontend")).closest(
      "[data-agent-override-row]",
    ) as HTMLElement;
    await waitFor(() =>
      expect(li.querySelector("[data-agent-cost-badge]")).not.toBeNull(),
    );

    const checkbox = li.querySelector(
      "[data-agent-override-toggle]",
    ) as HTMLInputElement;
    fireEvent.click(checkbox);
    await screen.findByRole("dialog");

    fireEvent.click(screen.getByText("Cancel"));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(checkbox.checked).toBe(false);
    expect(li).toHaveAttribute("data-agent-override-enabled", "false");
    expect(mockPatchAgentOverrides).not.toHaveBeenCalled();
  });

  it("(c) toggling OFF a red agent is unchanged — no intercept, PATCHes immediately", async () => {
    mockGetAgents.mockResolvedValue([agent({ name: "dev-frontend" })]);
    mockGetAgentOverrides.mockResolvedValue(overridesResponse()); // enabled=true (default)
    mockGetAgentCostEstimate.mockResolvedValue(
      costEstimate({ traffic_light: "red", projected_monthly_usd: 55.0 }),
    );
    mockPatchAgentOverrides.mockResolvedValue(
      overridesResponse({
        agents: [{ name: "dev-frontend", enabled: false, model_override: null, notes: null }],
      }),
    );

    render(<AgentOverridesPanel projectId={1} />);
    const li = (await screen.findByText("dev-frontend")).closest(
      "[data-agent-override-row]",
    ) as HTMLElement;
    await waitFor(() =>
      expect(li.querySelector("[data-agent-cost-badge]")).not.toBeNull(),
    );

    const checkbox = li.querySelector(
      "[data-agent-override-toggle]",
    ) as HTMLInputElement;
    fireEvent.click(checkbox);

    // No modal — the toggle-OFF path is untouched by #1020.
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(li).toHaveAttribute("data-agent-override-enabled", "false");
    await waitFor(() => expect(mockPatchAgentOverrides).toHaveBeenCalledTimes(1));
    expect(mockPatchAgentOverrides).toHaveBeenCalledWith(1, [
      { name: "dev-frontend", enabled: false },
    ]);
  });

  it("(d) an estimate fetch failure still renders the row, without a badge", async () => {
    mockGetAgents.mockResolvedValue([agent({ name: "dev-frontend" })]);
    mockGetAgentOverrides.mockResolvedValue(overridesResponse());
    mockGetAgentCostEstimate.mockRejectedValue(new Error("500 internal error"));

    render(<AgentOverridesPanel projectId={1} />);
    const li = (await screen.findByText("dev-frontend")).closest(
      "[data-agent-override-row]",
    ) as HTMLElement;

    // Row itself renders fully (name, toggle, tier select) despite the
    // failed estimate fetch.
    expect(
      li.querySelector("[data-agent-override-toggle]"),
    ).not.toBeNull();
    expect(li.querySelector("[data-agent-override-tier]")).not.toBeNull();

    // Give the rejected Promise.allSettled fan-out a tick to settle, then
    // assert no badge ever appears — not a transient loading state.
    await waitFor(() => expect(mockGetAgentCostEstimate).toHaveBeenCalled());
    expect(li.querySelector("[data-agent-cost-badge]")).toBeNull();
  });
});
