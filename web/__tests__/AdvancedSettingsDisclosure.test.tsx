// AdvancedSettingsDisclosure — Kanban #2482.
//
// Covered:
//   (1) Default state: collapsed (aria-expanded=false, children hidden).
//   (2) Toggle expands and persists to localStorage.
//   (3) Hydrates from localStorage: when stored as expanded, starts expanded.

import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AdvancedSettingsDisclosure } from "@/components/AdvancedSettingsDisclosure";

const STORAGE_KEY = "settings.advanced.expanded";

beforeEach(() => {
  window.localStorage.clear();
});

describe("AdvancedSettingsDisclosure", () => {
  it("starts collapsed by default — children not visible, aria-expanded false", () => {
    render(
      <AdvancedSettingsDisclosure>
        <div data-testid="inner">hidden content</div>
      </AdvancedSettingsDisclosure>,
    );
    const btn = screen.getByRole("button", { name: /advanced/i });
    expect(btn).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("inner")).not.toBeInTheDocument();
  });

  it("toggle shows children and sets aria-expanded true + persists to localStorage", async () => {
    render(
      <AdvancedSettingsDisclosure>
        <div data-testid="inner">hidden content</div>
      </AdvancedSettingsDisclosure>,
    );
    const btn = screen.getByRole("button", { name: /advanced/i });
    fireEvent.click(btn);
    expect(btn).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("inner")).toBeInTheDocument();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("true");
  });

  it("hydrates as expanded when localStorage says expanded", async () => {
    window.localStorage.setItem(STORAGE_KEY, "true");
    render(
      <AdvancedSettingsDisclosure>
        <div data-testid="inner">hidden content</div>
      </AdvancedSettingsDisclosure>,
    );
    // useEffect fires after mount; wait for hydration to settle.
    await waitFor(() => {
      expect(screen.getByTestId("inner")).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /advanced/i })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });
});
