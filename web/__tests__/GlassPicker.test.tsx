// Component tests for the glass theme axis — Kanban #2453.
//
// Covers the SECOND theme axis (glassmorphism on/off), orthogonal to light/dark:
//   - GlassProvider hydrates from localStorage and toggles `.glass` on <html>.
//   - GlassPicker reflects state via aria-pressed and persists on click.
//   - Default (no stored value) = OFF, so the flat theme is preserved.
//
// Determinism: hydration runs in a useEffect, so we assert via waitFor.

import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

import { GlassProvider } from "@/components/GlassProvider";
import { GlassPicker } from "@/components/GlassPicker";

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.classList.remove("glass");
});

function renderPicker() {
  return render(
    <GlassProvider>
      <GlassPicker />
    </GlassProvider>,
  );
}

describe("GlassPicker / GlassProvider — #2453 glass axis", () => {
  it("defaults to OFF (flat theme preserved) when nothing is stored", async () => {
    renderPicker();
    await waitFor(() => {
      expect(screen.getByLabelText("flat")).toHaveAttribute("aria-pressed", "true");
    });
    expect(screen.getByLabelText("glass")).toHaveAttribute("aria-pressed", "false");
    expect(document.documentElement.classList.contains("glass")).toBe(false);
  });

  it("hydrates to ON and adds the .glass class when localStorage glass=on", async () => {
    window.localStorage.setItem("glass", "on");
    renderPicker();
    await waitFor(() => {
      expect(document.documentElement.classList.contains("glass")).toBe(true);
    });
    expect(screen.getByLabelText("glass")).toHaveAttribute("aria-pressed", "true");
  });

  it("toggles the .glass class and persists the choice on click", async () => {
    renderPicker();
    // wait for hydration (off) before interacting
    await waitFor(() =>
      expect(screen.getByLabelText("flat")).toHaveAttribute("aria-pressed", "true"),
    );

    fireEvent.click(screen.getByLabelText("glass"));
    expect(document.documentElement.classList.contains("glass")).toBe(true);
    expect(window.localStorage.getItem("glass")).toBe("on");

    fireEvent.click(screen.getByLabelText("flat"));
    expect(document.documentElement.classList.contains("glass")).toBe(false);
    expect(window.localStorage.getItem("glass")).toBe("off");
  });

  it("exposes data-glass-selected on the group for E2E/probe hooks", async () => {
    renderPicker();
    const group = screen.getByRole("group", { name: "surface style" });
    await waitFor(() => expect(group).toHaveAttribute("data-glass-selected", "off"));
    fireEvent.click(screen.getByLabelText("glass"));
    expect(group).toHaveAttribute("data-glass-selected", "on");
  });
});
