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
    // PERF-1 — refresh() now passes { limit: PAGE_SIZE } explicitly.
    expect(mockListResources).toHaveBeenCalledWith(1, { limit: 50 });
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

// PERF-1 (#1315 deferred review) — GET /api/projects/{id}/resources supports
// ?limit&offset (api/src/routers/resources.py, default 50/max 500); the panel
// now wires a real "Load more" instead of silently capping at the first page.
describe("ResourcesPanel — load more (PERF-1)", () => {
  function make50Files(): Resource[] {
    return Array.from({ length: 50 }, (_, i) =>
      fileResource({ id: i + 1, filename: `f${i}.csv`, tags: {} }),
    );
  }

  it("shows Load more after a full 50-row page and calls listResources with limit/offset", async () => {
    const page1 = make50Files();
    mockListResources.mockResolvedValueOnce(page1);
    render(<ResourcesPanel projectId={1} />);
    fireEvent.click(screen.getByRole("button", { name: /resources/i }));

    await screen.findByText("f0.csv");
    expect(mockListResources).toHaveBeenCalledWith(1, { limit: 50 });

    const btn = document.querySelector("[data-resources-load-more]");
    expect(btn).not.toBeNull();

    const page2 = [fileResource({ id: 51, filename: "f50.csv", tags: {} })];
    mockListResources.mockResolvedValueOnce(page2);
    fireEvent.click(btn!);

    await waitFor(() => {
      expect(mockListResources).toHaveBeenCalledWith(1, {
        limit: 50,
        offset: 50,
      });
    });
    expect(await screen.findByText("f50.csv")).toBeInTheDocument();
  });

  it("hides Load more once a short page (<50 rows) returns", async () => {
    const shortPage = [fileResource({ id: 1, filename: "only.csv", tags: {} })];
    mockListResources.mockResolvedValueOnce(shortPage);
    render(<ResourcesPanel projectId={1} />);
    fireEvent.click(screen.getByRole("button", { name: /resources/i }));

    await screen.findByText("only.csv");
    // A page shorter than PAGE_SIZE (1 < 50) means no more rows.
    expect(document.querySelector("[data-resources-load-more]")).toBeNull();
  });

  it("does not fetch more rows without a click (no auto-pagination)", async () => {
    mockListResources.mockResolvedValueOnce(make50Files());
    render(<ResourcesPanel projectId={1} />);
    fireEvent.click(screen.getByRole("button", { name: /resources/i }));

    await screen.findByText("f0.csv");
    expect(mockListResources).toHaveBeenCalledTimes(1);
  });
});

// TYPE-1 (#1315 deferred review) — the mime chip is suppressed when it's just
// the detected format's canonical mime restated; content_type_resolved (tags)
// is preferred over the top-level content_type column when both are present.
describe("ResourcesPanel — mime chip de-dup (TYPE-1)", () => {
  it("suppresses the mime chip when it duplicates format_detected (csv / text/csv)", async () => {
    mockListResources.mockResolvedValue([
      fileResource({
        content_type: "text/csv",
        tags: { format_detected: "csv" },
      }),
    ]);
    render(<ResourcesPanel projectId={1} />);
    fireEvent.click(screen.getByRole("button", { name: /resources/i }));
    await screen.findByText("data.csv");

    const row = document.querySelector('[data-resources-row="1"]');
    expect(row?.querySelector('[data-resources-chip="fmt"]')).not.toBeNull();
    expect(row?.querySelector('[data-resources-chip="mime"]')).toBeNull();
  });

  it("keeps the mime chip when it does NOT duplicate the format (xlsx has no canonical mime)", async () => {
    mockListResources.mockResolvedValue([
      fileResource({
        content_type:
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        tags: { format_detected: "xlsx" },
      }),
    ]);
    render(<ResourcesPanel projectId={1} />);
    fireEvent.click(screen.getByRole("button", { name: /resources/i }));
    await screen.findByText("data.csv");

    const row = document.querySelector('[data-resources-row="1"]');
    const mimeChip = row?.querySelector('[data-resources-chip="mime"]');
    expect(mimeChip).not.toBeNull();
    expect(mimeChip?.textContent).toContain("spreadsheetml");
  });

  it("prefers tags.content_type_resolved over the top-level content_type column", async () => {
    mockListResources.mockResolvedValue([
      fileResource({
        content_type: "application/octet-stream", // stale top-level value
        tags: { format_detected: "json", content_type_resolved: "application/json" },
      }),
    ]);
    render(<ResourcesPanel projectId={1} />);
    fireEvent.click(screen.getByRole("button", { name: /resources/i }));
    await screen.findByText("data.csv");

    const row = document.querySelector('[data-resources-row="1"]');
    // application/json === json's canonical mime -> suppressed (also proves
    // the resolved tag value, not the stale octet-stream, drove the check).
    expect(row?.querySelector('[data-resources-chip="mime"]')).toBeNull();
  });
});
