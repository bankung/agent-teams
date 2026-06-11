// Component tests for TaskToolCalls — Kanban #2320 (lead activity events).
//
// Strategy: mock @/lib/api (getTaskToolCalls), render the component, assert:
//   1. Mixed rows (engine + lead) → header says "Activity (N)"
//   2. Lead row shows kind badge + summary + lead chip
//   3. Engine-only rows → header stays "Tool calls (N)", no lead chip
//   4. Null-safety for now-nullable engine fields (tier/duration_ms/permission_decision/input_json)
//
// Determinism: all assertions use findBy*/waitFor (async-fetch RTL races).
// asyncUtilTimeout raised for full-suite CPU load.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, configure } from "@testing-library/react";
import type { ToolCallRead } from "@/lib/api";

configure({ asyncUtilTimeout: 5000 });

const mockGetTaskToolCalls = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getTaskToolCalls: (...args: Parameters<typeof actual.getTaskToolCalls>) =>
      mockGetTaskToolCalls(...args),
  };
});

// Imported AFTER the mock is registered.
import { TaskToolCalls } from "@/components/TaskToolCalls";

function engineRow(over: Partial<ToolCallRead> = {}): ToolCallRead {
  return {
    id: 1,
    task_id: 2320,
    invoked_at: "2026-06-11T10:00:00Z",
    tool_name: "Read",
    source: "engine",
    kind: null,
    summary: null,
    tier: "read",
    input_json: { file_path: "/tmp/foo.txt" },
    success: true,
    error_code: null,
    error_msg: null,
    output_summary: "line 1\nline 2",
    duration_ms: 42,
    permission_decision: "auto_allow",
    ...over,
  };
}

function leadRow(over: Partial<ToolCallRead> = {}): ToolCallRead {
  return {
    id: 2,
    task_id: 2320,
    invoked_at: "2026-06-11T10:01:00Z",
    tool_name: "lead_event",
    source: "lead",
    kind: "spawn",
    summary: "Spawned dev-frontend for UI slice",
    tier: null,
    input_json: null,
    success: true,
    error_code: null,
    error_msg: null,
    output_summary: null,
    duration_ms: null,
    permission_decision: null,
    ...over,
  };
}

beforeEach(() => {
  mockGetTaskToolCalls.mockReset();
});

describe("TaskToolCalls — mixed rows (engine + lead)", () => {
  it('header says "Activity (N)" when ≥1 lead event is present', async () => {
    mockGetTaskToolCalls.mockResolvedValue([
      engineRow({ id: 1 }),
      leadRow({ id: 2 }),
    ]);

    render(<TaskToolCalls projectId={1} taskId={2320} />);

    // Wait for the component to finish loading and show the header.
    const heading = await screen.findByText(/activity \(2\)/i);
    expect(heading).toBeInTheDocument();
  });

  it("lead row shows kind badge, summary text, and lead chip", async () => {
    mockGetTaskToolCalls.mockResolvedValue([
      leadRow({
        id: 10,
        kind: "spawn",
        summary: "Spawned dev-frontend for UI slice",
      }),
    ]);

    render(<TaskToolCalls projectId={1} taskId={2320} />);

    // Wait for the header to appear (probe complete), then expand the panel.
    const toggle = await waitFor(() => {
      const t = document.querySelector("[data-tool-calls-toggle]");
      expect(t).not.toBeNull();
      return t as HTMLElement;
    });
    toggle.click();

    // Wait for the lead row summary to appear in the expanded panel.
    await screen.findByText("Spawned dev-frontend for UI slice");

    // Kind badge rendered.
    const kindBadge = document.querySelector("[data-lead-kind-badge]");
    expect(kindBadge).not.toBeNull();
    expect(kindBadge?.textContent?.toLowerCase()).toBe("spawn");

    // Summary rendered.
    const summary = document.querySelector("[data-lead-summary]");
    expect(summary?.textContent).toBe("Spawned dev-frontend for UI slice");

    // "lead" source chip rendered.
    const sourceChip = document.querySelector("[data-lead-source-chip]");
    expect(sourceChip).not.toBeNull();
    expect(sourceChip?.textContent?.toLowerCase()).toBe("lead");
  });
});

describe("TaskToolCalls — engine-only rows", () => {
  it('header stays "Tool calls (N)" when no lead rows present', async () => {
    mockGetTaskToolCalls.mockResolvedValue([
      engineRow({ id: 1 }),
      engineRow({ id: 2, tool_name: "Edit" }),
    ]);

    render(<TaskToolCalls projectId={1} taskId={2320} />);

    const heading = await screen.findByText(/tool calls \(2\)/i);
    expect(heading).toBeInTheDocument();
  });

  it("engine rows do not render a lead chip", async () => {
    mockGetTaskToolCalls.mockResolvedValue([engineRow({ id: 1 })]);

    render(<TaskToolCalls projectId={1} taskId={2320} />);

    await screen.findByText(/tool calls/i);

    // No lead chip should be present for engine-only rows.
    expect(document.querySelector("[data-lead-source-chip]")).toBeNull();
  });
});

describe("TaskToolCalls — null-safety for nullable engine fields", () => {
  it("renders without crash when tier/duration_ms/permission_decision/input_json are null", async () => {
    mockGetTaskToolCalls.mockResolvedValue([
      engineRow({
        id: 99,
        tier: null,
        duration_ms: null,
        permission_decision: null,
        input_json: null,
      }),
    ]);

    render(<TaskToolCalls projectId={1} taskId={2320} />);

    // Component renders a row without errors.
    await waitFor(() => {
      const count = document.querySelector("[data-tool-calls-count]");
      expect(count?.getAttribute("data-tool-calls-count")).toBe("1");
    });

    // No tier chip when tier is null.
    expect(document.querySelector("[data-tool-call-tier-chip]")).toBeNull();
  });

  it("formatDuration shows — when duration_ms is null", async () => {
    mockGetTaskToolCalls.mockResolvedValue([
      engineRow({ id: 100, duration_ms: null, tool_name: "NullDurationTool" }),
    ]);

    render(<TaskToolCalls projectId={1} taskId={2320} />);

    // Wait for probe to complete (header appears), then expand.
    const toggle = await waitFor(() => {
      const t = document.querySelector("[data-tool-calls-toggle]");
      expect(t).not.toBeNull();
      return t as HTMLElement;
    });
    toggle.click();

    // "—" appears in the duration slot once the row is visible.
    await screen.findByText("—");
  });
});

describe("TaskToolCalls — hidden-when-empty", () => {
  it("renders null when the endpoint returns an empty array", async () => {
    mockGetTaskToolCalls.mockResolvedValue([]);

    render(<TaskToolCalls projectId={1} taskId={2320} />);

    // Wait for the probe to settle.
    await waitFor(() => {
      expect(document.querySelector("[data-tool-calls]")).toBeNull();
    });
  });
});
