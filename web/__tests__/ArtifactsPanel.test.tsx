// Component tests for ArtifactsPanel — Kanban #2558.
//
// Strategy: mock @/lib/api (listProjectOutputs). The panel fetches on mount
// (no lazy-expand gate, unlike ResourcesPanel), so every assertion after the
// initial render uses findBy/waitFor — a sync query on post-fetch state is a
// known async-fetch RTL race class in this codebase (#1310).
//
// Coverage (per the #2558 FE AC):
//   (a) Rows render: filename, task link ("#id title"), formatted mtime.
//   (b) A row whose task_title is null renders "#id (deleted task)" unlinked.
//   (c) Load more appends the next page and disappears once items.length
//       reaches total (items.length-vs-total heuristic, not page-length).
//   (d) Empty state ("No task outputs yet").
//   (e) Error state surfaces the extracted message.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, configure } from "@testing-library/react";
import type { ProjectOutputItem, ProjectOutputsResponse } from "@/lib/api";

configure({ asyncUtilTimeout: 5000 });

const mockListProjectOutputs = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    listProjectOutputs: (
      ...args: Parameters<typeof actual.listProjectOutputs>
    ) => mockListProjectOutputs(...args),
  };
});

// Imported AFTER the mock registers.
import { ArtifactsPanel } from "@/components/ArtifactsPanel";

function item(over: Partial<ProjectOutputItem> = {}): ProjectOutputItem {
  return {
    filename: "sample.png",
    task_id: 1305,
    task_title: "[platform] X.8 — UI: Output viewer",
    role: "dev-frontend",
    mtime: "2026-06-12T09:17:50.277015Z",
    size: 70,
    mime: "image/png",
    kind: "chart",
    download_url: "/api/tasks/1305/outputs/sample.png?download=1",
    ...over,
  };
}

function page(items: ProjectOutputItem[], total: number): ProjectOutputsResponse {
  return { items, total };
}

beforeEach(() => {
  mockListProjectOutputs.mockReset();
});

describe("ArtifactsPanel — rows", () => {
  it("renders filename, task link, and formatted mtime", async () => {
    mockListProjectOutputs.mockResolvedValue(page([item()], 1));
    render(<ArtifactsPanel projectId={1} projectName="agent-teams" />);

    expect(await screen.findByText("sample.png")).toBeInTheDocument();

    const link = screen.getByRole("link", {
      name: /#1305 \[platform\] X\.8 — UI: Output viewer/,
    });
    expect(link).toHaveAttribute("href", "/p/agent-teams?task=1305");

    // "Jun 12, 2026" from the ISO mtime (local-tz hour/minute varies by CI TZ,
    // so assert only the stable date portion).
    expect(screen.getByText(/Jun 12, 2026/)).toBeInTheDocument();

    expect(mockListProjectOutputs).toHaveBeenCalledWith(1, { limit: 50 });
  });

  it("renders a null task_title as '#id (deleted task)' unlinked", async () => {
    mockListProjectOutputs.mockResolvedValue(
      page([item({ task_id: 42, task_title: null })], 1),
    );
    render(<ArtifactsPanel projectId={1} projectName="agent-teams" />);

    expect(await screen.findByText("#42 (deleted task)")).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: /#42/ }),
    ).not.toBeInTheDocument();
  });

  it("renders a Download link resolved via the API origin", async () => {
    mockListProjectOutputs.mockResolvedValue(page([item()], 1));
    render(<ArtifactsPanel projectId={1} projectName="agent-teams" />);

    const download = await screen.findByText("Download");
    // Derive the expected origin from the SAME env var apiOrigin()/apiBaseUrl()
    // read (NEXT_PUBLIC_API_URL) rather than hardcoding a value — the test
    // environment sets this (e.g. http://localhost:8456), so a literal ""
    // prefix assumption is wrong and env-fragile. This still proves the
    // component prefixed download_url with the API origin, not a hardcoded
    // or web-relative value.
    const expectedOrigin = process.env.NEXT_PUBLIC_API_URL ?? "";
    expect(download).toHaveAttribute(
      "href",
      `${expectedOrigin}/api/tasks/1305/outputs/sample.png?download=1`,
    );
  });
});

describe("ArtifactsPanel — pagination", () => {
  it("Load more appends the next page and disappears once total is reached", async () => {
    mockListProjectOutputs.mockResolvedValueOnce(
      page([item({ task_id: 1, filename: "a.png" })], 2),
    );
    render(<ArtifactsPanel projectId={1} projectName="agent-teams" />);

    await screen.findByText("a.png");
    const loadMore = screen.getByRole("button", { name: /load more/i });
    expect(loadMore).toBeInTheDocument();

    mockListProjectOutputs.mockResolvedValueOnce(
      page([item({ task_id: 2, filename: "b.png" })], 2),
    );
    fireEvent.click(loadMore);

    expect(await screen.findByText("b.png")).toBeInTheDocument();
    // Both rows present (appended, not replaced).
    expect(screen.getByText("a.png")).toBeInTheDocument();

    expect(mockListProjectOutputs).toHaveBeenLastCalledWith(1, {
      limit: 50,
      offset: 1,
    });

    // items.length (2) === total (2) → button gone.
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: /load more/i }),
      ).not.toBeInTheDocument(),
    );
  });

  it("does not show Load more when the first page already covers total", async () => {
    mockListProjectOutputs.mockResolvedValue(page([item()], 1));
    render(<ArtifactsPanel projectId={1} projectName="agent-teams" />);

    await screen.findByText("sample.png");
    expect(
      screen.queryByRole("button", { name: /load more/i }),
    ).not.toBeInTheDocument();
  });
});

describe("ArtifactsPanel — empty + error states", () => {
  it("shows the empty state when there are no outputs", async () => {
    mockListProjectOutputs.mockResolvedValue(page([], 0));
    render(<ArtifactsPanel projectId={1} projectName="agent-teams" />);

    expect(await screen.findByText("No task outputs yet")).toBeInTheDocument();
  });

  it("shows an error message when the fetch fails", async () => {
    mockListProjectOutputs.mockRejectedValue(new Error("network down"));
    render(<ArtifactsPanel projectId={1} projectName="agent-teams" />);

    expect(await screen.findByText("network down")).toBeInTheDocument();
  });
});
