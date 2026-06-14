// Component tests for TaskOutputs — Kanban #1305.
//
// Strategy: mock @/lib/api (getTaskOutputs + fetchTaskOutputBytes).
// All async renders use findBy*/waitFor per project determinism rules.
// asyncUtilTimeout raised for full-suite CPU load.
//
// Coverage:
//   1. Empty state — exact text "No outputs yet — task may still be running"
//   2. Listing renders one row per entry with data-output-row + data-output-kind
//   3. PNG chart row renders inline img (after blob URL created)
//   4. Markdown doc row renders formatted content
//   5. CSV export row renders as table with row-count note
//   6. Text row renders in scrollable pre
//   7. Chart row click opens modal (AC[2])
//   8. Modal closes on ModalShell backdrop click / ESC
//   9. Download button present on each row

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
  it("renders text content in a scrollable pre", async () => {
    mockGetTaskOutputs.mockResolvedValue([
      entry({ filename: "log.txt", kind: "text", mime: "text/plain" }),
    ]);
    mockFetchTaskOutputBytes.mockResolvedValue(
      textBlob("line one\nline two", "text/plain")
    );

    render(<TaskOutputs projectId={1} taskId={1305} />);

    // Content appears inside a <pre> element.
    const pre = await screen.findByText(/line one/);
    expect(pre.tagName.toLowerCase()).toBe("pre");
  });
});

describe("TaskOutputs — doc kind (markdown)", () => {
  it("renders markdown content as formatted elements (not raw text)", async () => {
    mockGetTaskOutputs.mockResolvedValue([
      entry({ filename: "report.md", kind: "doc", mime: "text/markdown" }),
    ]);
    mockFetchTaskOutputBytes.mockResolvedValue(
      textBlob("# Heading\n\nParagraph text.", "text/markdown")
    );

    render(<TaskOutputs projectId={1} taskId={1305} />);

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
    // Windows CRLF CSV — each line ends with \r\n.
    // "\r" is embedded MID-TOKEN in the header ("hea\rder") and mid-value
    // ("val\rue") so that a cell .trim() backstop cannot mask the failure —
    // only explicit \r stripping in the parser produces the expected text.
    const crlfCsv = "hea\rder,b\r\nval\rue,2\r\n";
    mockGetTaskOutputs.mockResolvedValue([
      entry({ filename: "crlf.csv", kind: "export", mime: "text/csv" }),
    ]);
    mockFetchTaskOutputBytes.mockResolvedValue(textBlob(crlfCsv, "text/csv"));

    render(<TaskOutputs projectId={1} taskId={1305} />);

    // Header cells must be "header" and "b" — stray \r must be stripped.
    await screen.findByText("header");
    expect(screen.getByText("b")).toBeInTheDocument();
    // Raw "hea\rder" must NOT appear as a cell text node.
    expect(screen.queryByText("hea\rder")).not.toBeInTheDocument();

    // Data cells must be "value" and "2" — stray \r must be stripped.
    await screen.findByText("value");
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.queryByText("val\rue")).not.toBeInTheDocument();
  });

  it("renders CSV as a table with first 10 rows and row-count note", async () => {
    mockGetTaskOutputs.mockResolvedValue([
      entry({ filename: "data.csv", kind: "export", mime: "text/csv" }),
    ]);
    mockFetchTaskOutputBytes.mockResolvedValue(textBlob(csvContent, "text/csv"));

    render(<TaskOutputs projectId={1} taskId={1305} />);

    // Header cells
    await screen.findByText("col_a");
    expect(screen.getByText("col_b")).toBeInTheDocument();

    // First data row visible.
    await screen.findByText("row1a");

    // Row-count note: 11 data rows total, showing first 10.
    await screen.findByText(/showing first 10 of 11 rows/i);
  });
});

describe("TaskOutputs — chart kind (PNG)", () => {
  it("renders an img element and a Download link", async () => {
    mockGetTaskOutputs.mockResolvedValue([
      entry({ filename: "chart.png", kind: "chart", mime: "image/png" }),
    ]);
    mockFetchTaskOutputBytes.mockResolvedValue(binaryBlob("image/png"));

    render(<TaskOutputs projectId={1} taskId={1305} />);

    // img element rendered with the blob URL.
    const img = await screen.findByRole("img");
    expect(img).toBeInTheDocument();

    // Download link present.
    const dlLink = await screen.findByRole("link", { name: /download/i });
    expect(dlLink).toBeInTheDocument();
  });

  it("opens and closes the expand modal on click (AC[2])", async () => {
    mockGetTaskOutputs.mockResolvedValue([
      entry({ filename: "chart.png", kind: "chart", mime: "image/png" }),
    ]);
    mockFetchTaskOutputBytes.mockResolvedValue(binaryBlob("image/png"));

    render(<TaskOutputs projectId={1} taskId={1305} />);

    // Wait for img to appear, then click to open modal.
    const img = await screen.findByRole("img", { name: "chart.png" });
    fireEvent.click(img);

    // ModalShell renders a second img inside the dialog.
    await waitFor(() => {
      const dialog = document.querySelector("[role='dialog']");
      expect(dialog).not.toBeNull();
    });

    // Guard: modal must be open before we fire ESC (prevents vacuous pass).
    expect(document.querySelector("[role='dialog']")).not.toBeNull();

    // Close via ESC key.
    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => {
      expect(document.querySelector("[role='dialog']")).toBeNull();
    });
  });
});

describe("TaskOutputs — download button", () => {
  it("renders a Download link (with href) for every file row", async () => {
    mockGetTaskOutputs.mockResolvedValue([
      entry({ filename: "sample.txt", kind: "text" }),
      entry({ filename: "sample.md", kind: "doc" }),
    ]);
    mockFetchTaskOutputBytes.mockResolvedValue(textBlob("content"));

    render(<TaskOutputs projectId={1} taskId={1305} />);

    await waitFor(() => {
      // Each row should eventually get a download link once the blob URL is set.
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
