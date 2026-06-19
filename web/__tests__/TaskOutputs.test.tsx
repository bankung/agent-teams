// Component tests for TaskOutputs — Kanban #1305.
//
// Strategy: mock @/lib/api (getTaskOutputs + fetchTaskOutputBytes).
// All async renders use findBy*/waitFor per project determinism rules.
// asyncUtilTimeout raised for full-suite CPU load.
//
// Coverage:
//   1. Empty state — exact text "No outputs yet — task may still be running"
//   2. Listing renders one row per entry with data-output-row + data-output-kind
//   3. PNG chart row renders inline img (after blob URL created, after expand)
//   4. Markdown doc row renders formatted content (after expand)
//   5. CSV export row renders as table with row-count note (after expand)
//   6. Text row renders in scrollable pre (after expand)
//   7. Chart row click opens modal (AC[2])
//   8. Modal closes on ModalShell backdrop click / ESC
//   9. Download button present on each row (after expand)
//  10. No fetch fires on mount — only fires after expand (Fix 2 #2502)

import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  fireEvent,
  configure,
} from "@testing-library/react";
import type { TaskOutputEntry } from "@/lib/api";

configure({ asyncUtilTimeout: 5000 });

const mockGetTaskOutputs = vi.fn();
const mockFetchTaskOutputBytes = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getTaskOutputs: (...args: Parameters<typeof actual.getTaskOutputs>) =>
      mockGetTaskOutputs(...args),
    // Return type widened to unknown so test stubs (BlobLike) satisfy the mock.
    fetchTaskOutputBytes: (
      ...args: Parameters<typeof actual.fetchTaskOutputBytes>
    ) => mockFetchTaskOutputBytes(...args) as unknown as Promise<Blob>,
  };
});

// Stub URL.createObjectURL / URL.revokeObjectURL (not available in jsdom).
global.URL.createObjectURL = vi.fn(() => "blob:mock");
global.URL.revokeObjectURL = vi.fn();

// Imported AFTER mocks are registered.
import { TaskOutputs } from "@/components/TaskOutputs";

// BlobLike — minimal Blob-compatible stub for tests.
// jsdom's Blob may not expose .text() in all environments; provide it explicitly.
type BlobLike = {
  text: () => Promise<string>;
  type: string;
  size: number;
};

function textBlob(content: string, type = "text/plain"): BlobLike {
  return {
    type,
    size: content.length,
    text: () => Promise.resolve(content),
  };
}

function binaryBlob(type = "image/png"): BlobLike {
  return {
    type,
    size: 10,
    // Binary blobs: .text() is not called by the component for chart png/svg.
    // Provide it as a no-op stub so the mock is uniform.
    text: () => Promise.resolve(""),
  };
}

function entry(over: Partial<TaskOutputEntry>): TaskOutputEntry {
  return {
    filename: "sample.txt",
    mime: "text/plain",
    size: 100,
    kind: "text",
    ...over,
  };
}

// Helper: click the Show button on the first (or only) output row.
function expandFirstRow() {
  const btn = document.querySelector("[data-output-expand]") as HTMLElement;
  if (!btn) throw new Error("data-output-expand button not found");
  fireEvent.click(btn);
}

beforeEach(() => {
  mockGetTaskOutputs.mockReset();
  mockFetchTaskOutputBytes.mockReset();
  vi.mocked(URL.createObjectURL).mockReturnValue("blob:mock");
});

describe("TaskOutputs — empty state", () => {
  it("renders the exact empty-state message when the listing returns []", async () => {
    mockGetTaskOutputs.mockResolvedValue([]);

    render(<TaskOutputs projectId={1} taskId={1305} />);

    // AC[4] — exact text
    await screen.findByText(
      "No outputs yet — task may still be running"
    );
    // Must NOT render an error variant.
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("TaskOutputs — listing renders correct attributes", () => {
  it("renders data-output-row + data-output-kind for each entry", async () => {
    mockGetTaskOutputs.mockResolvedValue([
      entry({ filename: "sample.txt", kind: "text" }),
      entry({ filename: "sample.md", kind: "doc" }),
    ]);
    mockFetchTaskOutputBytes.mockResolvedValue(textBlob("hello"));

    render(<TaskOutputs projectId={1} taskId={1305} />);

    await waitFor(() => {
      const rows = document.querySelectorAll("[data-output-row]");
      expect(rows.length).toBe(2);
    });

    const kinds = Array.from(
      document.querySelectorAll("[data-output-kind]")
    ).map((el) => el.getAttribute("data-output-kind"));
    expect(kinds).toContain("text");
    expect(kinds).toContain("doc");
  });
});

describe("TaskOutputs — text kind", () => {
  it("renders text content in a scrollable pre after expanding", async () => {
    mockGetTaskOutputs.mockResolvedValue([
      entry({ filename: "log.txt", kind: "text", mime: "text/plain" }),
    ]);
    mockFetchTaskOutputBytes.mockResolvedValue(
      textBlob("line one\nline two", "text/plain")
    );

    render(<TaskOutputs projectId={1} taskId={1305} />);

    // Wait for the row shell to appear, then expand.
    await waitFor(() => expect(document.querySelector("[data-output-expand]")).not.toBeNull());
    expandFirstRow();

    // Content appears inside a <pre> element.
    const pre = await screen.findByText(/line one/);
    expect(pre.tagName.toLowerCase()).toBe("pre");
  });
});

describe("TaskOutputs — doc kind (markdown)", () => {
  it("renders markdown content as formatted elements (not raw text) after expanding", async () => {
    mockGetTaskOutputs.mockResolvedValue([
      entry({ filename: "report.md", kind: "doc", mime: "text/markdown" }),
    ]);
    mockFetchTaskOutputBytes.mockResolvedValue(
      textBlob("# Heading\n\nParagraph text.", "text/markdown")
    );

    render(<TaskOutputs projectId={1} taskId={1305} />);

    await waitFor(() => expect(document.querySelector("[data-output-expand]")).not.toBeNull());
    expandFirstRow();

    // react-markdown renders # into an <h1>
    const heading = await screen.findByText("Heading");
    expect(heading.tagName.toLowerCase()).toBe("h1");
    // Paragraph rendered too.
    await screen.findByText("Paragraph text.");
  });
});

describe("TaskOutputs — export kind (csv)", () => {
  const csvContent =
    "col_a,col_b,col_c\nrow1a,row1b,row1c\nrow2a,row2b,row2c\n" +
    "r3a,r3b,r3c\nr4a,r4b,r4c\nr5a,r5b,r5c\nr6a,r6b,r6c\n" +
    "r7a,r7b,r7c\nr8a,r8b,r8c\nr9a,r9b,r9c\nr10a,r10b,r10c\nr11a,r11b,r11c";

  it("renders CRLF-encoded CSV without stray \\r in cells (FE-M2)", async () => {
    const crlfCsv = "hea\rder,b\r\nval\rue,2\r\n";
    mockGetTaskOutputs.mockResolvedValue([
      entry({ filename: "crlf.csv", kind: "export", mime: "text/csv" }),
    ]);
    mockFetchTaskOutputBytes.mockResolvedValue(textBlob(crlfCsv, "text/csv"));

    render(<TaskOutputs projectId={1} taskId={1305} />);

    await waitFor(() => expect(document.querySelector("[data-output-expand]")).not.toBeNull());
    expandFirstRow();

    await screen.findByText("header");
    expect(screen.getByText("b")).toBeInTheDocument();
    expect(screen.queryByText("hea\rder")).not.toBeInTheDocument();

    await screen.findByText("value");
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.queryByText("val\rue")).not.toBeInTheDocument();
  });

  it("renders CSV as a table with first 10 rows and row-count note after expanding", async () => {
    mockGetTaskOutputs.mockResolvedValue([
      entry({ filename: "data.csv", kind: "export", mime: "text/csv" }),
    ]);
    mockFetchTaskOutputBytes.mockResolvedValue(textBlob(csvContent, "text/csv"));

    render(<TaskOutputs projectId={1} taskId={1305} />);

    await waitFor(() => expect(document.querySelector("[data-output-expand]")).not.toBeNull());
    expandFirstRow();

    await screen.findByText("col_a");
    expect(screen.getByText("col_b")).toBeInTheDocument();

    await screen.findByText("row1a");

    await screen.findByText(/showing first 10 of 11 rows/i);
  });
});

describe("TaskOutputs — chart kind (PNG)", () => {
  it("renders an img element and a Download link after expanding", async () => {
    mockGetTaskOutputs.mockResolvedValue([
      entry({ filename: "chart.png", kind: "chart", mime: "image/png" }),
    ]);
    mockFetchTaskOutputBytes.mockResolvedValue(binaryBlob("image/png"));

    render(<TaskOutputs projectId={1} taskId={1305} />);

    await waitFor(() => expect(document.querySelector("[data-output-expand]")).not.toBeNull());
    expandFirstRow();

    const img = await screen.findByRole("img");
    expect(img).toBeInTheDocument();

    const dlLink = await screen.findByRole("link", { name: /download/i });
    expect(dlLink).toBeInTheDocument();
  });

  it("opens and closes the expand modal on click (AC[2])", async () => {
    mockGetTaskOutputs.mockResolvedValue([
      entry({ filename: "chart.png", kind: "chart", mime: "image/png" }),
    ]);
    mockFetchTaskOutputBytes.mockResolvedValue(binaryBlob("image/png"));

    render(<TaskOutputs projectId={1} taskId={1305} />);

    await waitFor(() => expect(document.querySelector("[data-output-expand]")).not.toBeNull());
    expandFirstRow();

    const img = await screen.findByRole("img", { name: "chart.png" });
    fireEvent.click(img);

    await waitFor(() => {
      const dialog = document.querySelector("[role='dialog']");
      expect(dialog).not.toBeNull();
    });

    expect(document.querySelector("[role='dialog']")).not.toBeNull();

    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => {
      expect(document.querySelector("[role='dialog']")).toBeNull();
    });
  });
});

describe("TaskOutputs — download button", () => {
  it("renders a Download link (with href) for every file row after expanding", async () => {
    mockGetTaskOutputs.mockResolvedValue([
      entry({ filename: "sample.txt", kind: "text" }),
      entry({ filename: "sample.md", kind: "doc" }),
    ]);
    mockFetchTaskOutputBytes.mockResolvedValue(textBlob("content"));

    render(<TaskOutputs projectId={1} taskId={1305} />);

    // Expand all rows.
    await waitFor(() => {
      const btns = document.querySelectorAll("[data-output-expand]");
      expect(btns.length).toBe(2);
    });
    document.querySelectorAll<HTMLElement>("[data-output-expand]").forEach((btn) =>
      fireEvent.click(btn)
    );

    await waitFor(() => {
      const links = screen.getAllByRole("link", { name: /download/i });
      expect(links.length).toBe(2);
    });
  });
});

describe("TaskOutputs — data-outputs-section attribute", () => {
  it("always renders the section with data-outputs-section", async () => {
    mockGetTaskOutputs.mockResolvedValue([]);

    render(<TaskOutputs projectId={1} taskId={1305} />);

    await screen.findByText("No outputs yet — task may still be running");
    expect(document.querySelector("[data-outputs-section]")).not.toBeNull();
  });
});

describe("TaskOutputs — lazy-load: no fetch on mount (Fix 2 #2502)", () => {
  it("does not call fetchTaskOutputBytes on mount when rows are collapsed", async () => {
    mockGetTaskOutputs.mockResolvedValue([
      entry({ filename: "a.txt", kind: "text" }),
      entry({ filename: "b.txt", kind: "text" }),
      entry({ filename: "c.txt", kind: "text" }),
    ]);
    // fetchTaskOutputBytes should NOT be called yet.
    mockFetchTaskOutputBytes.mockResolvedValue(textBlob("content"));

    render(<TaskOutputs projectId={1} taskId={1305} />);

    // Wait for rows to render (listing fetch settled).
    await waitFor(() => {
      expect(document.querySelectorAll("[data-output-row]").length).toBe(3);
    });

    // No byte-fetch should have fired while all rows are collapsed.
    expect(mockFetchTaskOutputBytes).not.toHaveBeenCalled();
  });

  it("fires exactly one fetch per row only after that row is expanded", async () => {
    mockGetTaskOutputs.mockResolvedValue([
      entry({ filename: "x.txt", kind: "text" }),
      entry({ filename: "y.txt", kind: "text" }),
    ]);
    mockFetchTaskOutputBytes.mockResolvedValue(textBlob("hi"));

    render(<TaskOutputs projectId={1} taskId={1305} />);

    await waitFor(() => {
      expect(document.querySelectorAll("[data-output-expand]").length).toBe(2);
    });

    // Still zero fetches before any expand.
    expect(mockFetchTaskOutputBytes).toHaveBeenCalledTimes(0);

    // Expand only the first row.
    const [firstBtn] = document.querySelectorAll<HTMLElement>("[data-output-expand]");
    fireEvent.click(firstBtn);

    await waitFor(() => {
      expect(mockFetchTaskOutputBytes).toHaveBeenCalledTimes(1);
    });

    // Expanding the second row fires a second fetch.
    const [, secondBtn] = document.querySelectorAll<HTMLElement>("[data-output-expand]");
    fireEvent.click(secondBtn);

    await waitFor(() => {
      expect(mockFetchTaskOutputBytes).toHaveBeenCalledTimes(2);
    });
  });
});
