// ResourceUploadModal — Kanban #1315 deferred-review NIT-2 + NIT-4.
//
// Strategy: render the modal open, mock @/lib/api (create* never actually
// called by these two assertions), and check the DOM directly for the two
// markup-only fixes:
//   NIT-2: both tabpanels stay mounted (not conditionally unmounted); the
//          inactive one carries `hidden`, and switching tabs toggles which
//          one has it — never removing either from the DOM.
//   NIT-4: the file <input> carries a non-empty `accept` hint.
//
// Determinism (#1310): ModalShell portals to document.body (mirrors
// AgentFormModal.test.tsx / ModalShell.test.tsx); tab-switch uses userEvent
// (real click + focus semantics) though a plain click suffices here since
// the assertion is DOM-state, not focus-state.

import { describe, it, expect, vi } from "vitest";
import { render, fireEvent, configure } from "@testing-library/react";

configure({ asyncUtilTimeout: 5000 });

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    createResourceFile: vi.fn(),
    createResourceLink: vi.fn(),
  };
});

import { ResourceUploadModal } from "@/components/ResourceUploadModal";

describe("ResourceUploadModal — tabpanel DOM persistence (NIT-2)", () => {
  it("keeps both tabpanels mounted and toggles `hidden` on switch (file active by default)", () => {
    render(
      <ResourceUploadModal
        projectId={1}
        open={true}
        onClose={() => {}}
        onCreated={() => {}}
      />,
    );

    const filePanel = document.querySelector("[data-resource-file-panel]");
    const linkPanel = document.querySelector("[data-resource-link-panel]");
    expect(filePanel).not.toBeNull();
    expect(linkPanel).not.toBeNull();

    // File tab is active by default.
    expect(filePanel).not.toHaveAttribute("hidden");
    expect(linkPanel).toHaveAttribute("hidden");

    // Switch to the link tab — BOTH panels stay in the DOM; `hidden` flips.
    fireEvent.click(document.querySelector('[data-resource-tab="link"]')!);

    expect(document.querySelector("[data-resource-file-panel]")).not.toBeNull();
    expect(document.querySelector("[data-resource-link-panel]")).not.toBeNull();
    expect(document.querySelector("[data-resource-file-panel]")).toHaveAttribute(
      "hidden",
    );
    expect(
      document.querySelector("[data-resource-link-panel]"),
    ).not.toHaveAttribute("hidden");
  });
});

describe("ResourceUploadModal — file input accept hint (NIT-4)", () => {
  it("sets a non-empty accept attribute covering the parser-supported formats", () => {
    render(
      <ResourceUploadModal
        projectId={1}
        open={true}
        onClose={() => {}}
        onCreated={() => {}}
      />,
    );
    const input = document.querySelector("[data-resource-file-input]");
    const accept = input?.getAttribute("accept") ?? "";
    expect(accept.length).toBeGreaterThan(0);
    // Extensions the BE's detect_format() actively parses/tags (csv/tsv/json
    // fully parsed; xlsx/pdf detected+tagged) — resource_verify.py.
    expect(accept).toContain(".csv");
    expect(accept).toContain(".json");
  });
});
