// Component tests for the glass theme axis — Kanban #2453 / #2475.
//
// Covers the SECOND theme axis (glassmorphism on/off), orthogonal to light/dark:
//   - GlassProvider hydrates from localStorage and toggles `.glass` on <html>.
//   - GlassPicker reflects state via aria-pressed and persists on click.
//   - #2453: Default (no stored value) = OFF (pre-#2475 baseline tests preserved
//     as historical but their assertions updated — see #2475 block below).
//   - #2475: Default (no stored value) = ON; explicit "off" still persists + wins.
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
  // #2475: default is now ON (glass is the default surface). Unset localStorage
  // resolves to "on"; explicit "off" persists and wins.
  it("defaults to ON (glass default) when nothing is stored (#2475)", async () => {
    renderPicker();
    await waitFor(() => {
      expect(screen.getByLabelText("glass")).toHaveAttribute("aria-pressed", "true");
    });
    expect(screen.getByLabelText("flat")).toHaveAttribute("aria-pressed", "false");
    expect(document.documentElement.classList.contains("glass")).toBe(true);
  });

  it("explicit stored 'off' stays flat and persists (#2475)", async () => {
    window.localStorage.setItem("glass", "off");
    renderPicker();
    await waitFor(() => {
      expect(document.documentElement.classList.contains("glass")).toBe(false);
    });
    expect(screen.getByLabelText("flat")).toHaveAttribute("aria-pressed", "true");
    expect(window.localStorage.getItem("glass")).toBe("off");
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
    // wait for hydration (on — new default) before interacting
    await waitFor(() =>
      expect(screen.getByLabelText("glass")).toHaveAttribute("aria-pressed", "true"),
    );

    fireEvent.click(screen.getByLabelText("flat"));
    expect(document.documentElement.classList.contains("glass")).toBe(false);
    expect(window.localStorage.getItem("glass")).toBe("off");

    fireEvent.click(screen.getByLabelText("glass"));
    expect(document.documentElement.classList.contains("glass")).toBe(true);
    expect(window.localStorage.getItem("glass")).toBe("on");
  });

  it("exposes data-glass-selected on the group for E2E/probe hooks", async () => {
    renderPicker();
    const group = screen.getByRole("group", { name: "surface style" });
    // default-on: initial hydrated value is "on"
    await waitFor(() => expect(group).toHaveAttribute("data-glass-selected", "on"));
    fireEvent.click(screen.getByLabelText("flat"));
    expect(group).toHaveAttribute("data-glass-selected", "off");
  });
});
