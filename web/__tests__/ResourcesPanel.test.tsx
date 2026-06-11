// Component tests for ResourcesPanel — Kanban #1315.
//
// Strategy: mock @/lib/api (listResources / deleteResource) + stub the heavy
// upload modal + preview drawer (tested by their own surface / out of scope
// here). Assert: (1) collapsed by default — no list fetch; (2) expanding lazily
// fetches + renders rows with tag chips; (3) empty state shows the CTA;
// (4) [+ Add] opens the modal; (5) collapse pref persists via collapseState.
//
// Determinism: async-fetch assertions use findBy*/waitFor (never sync
// querySelector on post-fetch state). asyncUtilTimeout raised for full-suite load.

import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  fireEvent,
  configure,
} from "@testing-library/react";
import type { Resource } from "@/lib/api";

configure({ asyncUtilTimeout: 5000 });

const mockListResources = vi.fn();
const mockDeleteResource = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    listResources: (...args: Parameters<typeof actual.listResources>) =>
      mockListResources(...args),
    deleteResource: (...args: Parameters<typeof actual.deleteResource>) =>
      mockDeleteResource(...args),
  };
});

// Stub the upload modal so opening it is observable without its own fetches.
vi.mock("@/components/ResourceUploadModal", () => ({
  ResourceUploadModal: ({ open }: { open: boolean }) =>
    open ? <div data-testid="upload-modal-open" /> : null,
  formatBytes: (n: number | null | undefined) => (n == null ? "—" : `${n} B`),
}));
vi.mock("@/components/ResourcePreviewDrawer", () => ({
  ResourcePreviewDrawer: () => <div data-testid="preview-drawer-open" />,
}));

// Imported AFTER mocks register.
import { ResourcesPanel } from "@/components/ResourcesPanel";

function fileResource(over: Partial<Resource> = {}): Resource {
  return {
    id: 1,
    project_id: 1,
    task_id: null,
    kind: "file",
    filename: "data.csv",
    url: null,
    content_type: "text/csv",
    size_bytes: 2048,
    label: null,
    tags: { format_detected: "csv", row_count: 42 },
    created_at: "2026-06-09T10:00:00Z",
    updated_at: "2026-06-09T10:00:00Z",
    ...over,
  };
}

beforeEach(() => {
  mockListResources.mockReset();
  mockDeleteResource.mockReset();
  localStorage.clear();
});

describe("ResourcesPanel — collapse + lazy load", () => {
  it("is collapsed by default and does NOT fetch the list", () => {
    mockListResources.mockResolvedValue([]);
    render(<ResourcesPanel projectId={1} />);
    // Toggle is present; body is not rendered while collapsed.
    expect(
      screen.getByRole("button", { name: /resources/i }),
    ).toHaveAttribute("aria-expanded", "false");
    expect(document.querySelector("[data-resources-body]")).toBeNull();
    expect(mockListResources).not.toHaveBeenCalled();
  });

  it("lazily fetches + renders rows with tag chips when expanded", async () => {
    mockListResources.mockResolvedValue([fileResource()]);
    render(<ResourcesPanel projectId={1} />);

    fireEvent.click(screen.getByRole("button", { name: /resources/i }));

    // Row renders after the fetch resolves.
    expect(await screen.findByText("data.csv")).toBeInTheDocument();
    expect(mockListResources).toHaveBeenCalledWith(1);
    // Tag chips: size + format + row_count.
    const row = document.querySelector('[data-resources-row="1"]');
    expect(row).not.toBeNull();
    expect(row?.textContent).toContain("csv");
    expect(row?.textContent).toContain("42 rows");
  });

  it("persists the expanded preference via collapseState", async () => {
    mockListResources.mockResolvedValue([]);
    render(<ResourcesPanel projectId={7} />);
    fireEvent.click(screen.getByRole("button", { name: /resources/i }));
    await waitFor(() =>
      expect(localStorage.getItem("resources-panel:7")).toBe("true"),
    );
  });
});

describe("ResourcesPanel — empty + add", () => {
  it("shows the friendly empty state with a CTA", async () => {
    mockListResources.mockResolvedValue([]);
    render(<ResourcesPanel projectId={1} />);
    fireEvent.click(screen.getByRole("button", { name: /resources/i }));

    expect(
      await screen.findByText(/no resources yet/i),
    ).toBeInTheDocument();
    expect(
      document.querySelector("[data-resources-empty-add]"),
    ).not.toBeNull();
  });

  it("opens the upload modal when [+ Add] is clicked", async () => {
    mockListResources.mockResolvedValue([fileResource()]);
    render(<ResourcesPanel projectId={1} />);
    fireEvent.click(screen.getByRole("button", { name: /resources/i }));
    await screen.findByText("data.csv");

    fireEvent.click(document.querySelector("[data-resources-add]")!);
    expect(await screen.findByTestId("upload-modal-open")).toBeInTheDocument();
  });
});
