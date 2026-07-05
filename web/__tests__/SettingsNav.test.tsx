// Component tests for SettingsNav — Kanban #2716 (two-pane settings layout).
//
// SettingsNav is prop-driven (the settings Server Component resolves the active
// section + the visible category list and passes them in); it renders a list of
// <Link>s and owns NO state. So these tests render with fixed props — NO async
// fetch, therefore no findBy*/waitFor needed (deterministic synchronous DOM).
//
// Coverage:
//   - one nav item per category (data-settings-nav-item hooks)
//   - the active item carries aria-current="page" + data-active; others don't
//   - every item links to ?section=<id>
//   - ?project= is preserved on every link when projectName is supplied
//   - ?project= is absent from links when no projectName (global-only nav)

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// next/link → plain <a> so the nav renders without a Next.js router context
// (matches the convention in AgentGallery / CalendarView / Board tests).
vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: {
    href: string;
    children: React.ReactNode;
    [k: string]: unknown;
  }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import { SettingsNav } from "@/components/SettingsNav";
import {
  getSettingsCategories,
  type SettingsCategory,
} from "@/lib/settingsCategories";

const GLOBAL_CATEGORIES: SettingsCategory[] = getSettingsCategories(false);
const ALL_CATEGORIES: SettingsCategory[] = getSettingsCategories(true);

function item(id: string): HTMLAnchorElement {
  return document.querySelector(
    `[data-settings-nav-item="${id}"]`,
  ) as HTMLAnchorElement;
}

describe("SettingsNav", () => {
  it("renders one nav item per category", () => {
    render(<SettingsNav categories={ALL_CATEGORIES} active="appearance" />);
    const items = document.querySelectorAll("[data-settings-nav-item]");
    expect(items).toHaveLength(ALL_CATEGORIES.length);
    for (const c of ALL_CATEGORIES) {
      expect(item(c.id)).toBeInTheDocument();
    }
  });

  it("marks ONLY the active item with aria-current and data-active", () => {
    render(<SettingsNav categories={ALL_CATEGORIES} active="advanced" />);
    const active = item("advanced");
    expect(active).toHaveAttribute("aria-current", "page");
    expect(active).toHaveAttribute("data-active", "true");

    // Every other item is unmarked.
    for (const c of ALL_CATEGORIES) {
      if (c.id === "advanced") continue;
      expect(item(c.id)).not.toHaveAttribute("aria-current");
      expect(item(c.id)).not.toHaveAttribute("data-active");
    }
  });

  it("links every item to ?section=<id>", () => {
    render(<SettingsNav categories={ALL_CATEGORIES} active="appearance" />);
    for (const c of ALL_CATEGORIES) {
      const href = item(c.id).getAttribute("href") ?? "";
      const params = new URLSearchParams(href.split("?")[1] ?? "");
      expect(params.get("section")).toBe(c.id);
    }
  });

  it("preserves ?project= on every link when projectName is supplied", () => {
    render(
      <SettingsNav
        categories={ALL_CATEGORIES}
        active="project"
        projectName="agent-teams"
      />,
    );
    for (const c of ALL_CATEGORIES) {
      const href = item(c.id).getAttribute("href") ?? "";
      const params = new URLSearchParams(href.split("?")[1] ?? "");
      expect(params.get("project")).toBe("agent-teams");
      expect(params.get("section")).toBe(c.id);
    }
  });

  it("omits ?project= from links when no projectName (global-only nav)", () => {
    render(<SettingsNav categories={GLOBAL_CATEGORIES} active="appearance" />);
    for (const c of GLOBAL_CATEGORIES) {
      const href = item(c.id).getAttribute("href") ?? "";
      const params = new URLSearchParams(href.split("?")[1] ?? "");
      expect(params.get("project")).toBeNull();
    }
  });

  it("encodes a project name with special characters in the href", () => {
    render(
      <SettingsNav
        categories={ALL_CATEGORIES}
        active="appearance"
        projectName="my project & co"
      />,
    );
    const href = item("appearance").getAttribute("href") ?? "";
    // URLSearchParams encodes spaces as "+" and "&" as "%26" — assert the raw
    // href is encoded (no literal space / ampersand leaking into the query).
    expect(href).toContain("project=my+project+%26+co");
    const params = new URLSearchParams(href.split("?")[1] ?? "");
    expect(params.get("project")).toBe("my project & co");
  });
});
