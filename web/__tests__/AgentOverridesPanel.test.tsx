// Component tests for AgentOverridesPanel — Kanban #1018.
//
// Strategy: mock @/lib/api (getAgents / getAgentOverrides / patchAgentOverrides).
// Assert: (1) an un-overridden roster agent merges to enabled + "Default" tier;
// (2) toggling a row's checkbox fires PATCH with only {name, enabled:false};
// (3) changing the tier select fires PATCH with only {name, model_override};
// (4) a failed PATCH reverts the optimistic toggle + surfaces a row error;
// (5) #1018 M1/N2 — a project switch (rerender with a new key, mirroring the
// key={project.id} fix on the real mount sites) surfaces the NEW project's
// fetched values, not the prior project's stale optimistic rowState.
//
// Determinism: async-fetch assertions use findBy*/waitFor (never sync
// querySelector on post-fetch state) — the FE test-determinism rule requires
// the full suite to hold across repeated runs.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, configure } from "@testing-library/react";
import type { AgentSummary, AgentOverridesResponse } from "@/lib/api";

configure({ asyncUtilTimeout: 5000 });

const mockGetAgents = vi.fn();
const mockGetAgentOverrides = vi.fn();
const mockPatchAgentOverrides = vi.fn();

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
    ...over,
  };
}

function overridesResponse(
  over: Partial<AgentOverridesResponse> = {},
): AgentOverridesResponse {
  return { agents: [], lead_overrides: {}, ...over };
}

beforeEach(() => {
  mockGetAgents.mockReset();
  mockGetAgentOverrides.mockReset();
  mockPatchAgentOverrides.mockReset();
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
