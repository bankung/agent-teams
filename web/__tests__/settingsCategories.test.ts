// Unit tests for the settings category source (Kanban #2716) — the single
// source of truth the settings page + SettingsNav share for the visible
// category list, active-section resolution, and the audit-fetch gate.
//
// Pure functions, no DOM / async — synchronous assertions. This covers the
// locked section-routing rules without rendering the async Server Component
// (which has no RSC test harness in this repo): default → Appearance, project
// sections hidden without a project, unknown section → Appearance, and the
// audit-fetch gate.

import { describe, it, expect } from "vitest";
import {
  getSettingsCategories,
  resolveSettingsSection,
  sectionNeedsAudit,
  DEFAULT_SETTINGS_SECTION,
} from "@/lib/settingsCategories";

describe("getSettingsCategories", () => {
  it("returns only global categories when no project is in scope", () => {
    const ids = getSettingsCategories(false).map((c) => c.id);
    expect(ids).toEqual([
      "appearance",
      "notifications",
      "integrations",
      "tour",
    ]);
    // No project-scoped category leaks in.
    expect(ids).not.toContain("project");
    expect(ids).not.toContain("advanced");
  });

  it("appends project-scoped categories when a project is in scope", () => {
    const ids = getSettingsCategories(true).map((c) => c.id);
    expect(ids).toEqual([
      "appearance",
      "notifications",
      "integrations",
      "tour",
      "project",
      "advanced",
    ]);
  });

  it("every project-scoped category is tagged scope=project", () => {
    for (const c of getSettingsCategories(true)) {
      if (c.id === "project" || c.id === "advanced") {
        expect(c.scope).toBe("project");
      } else {
        expect(c.scope).toBe("global");
      }
    }
  });
});

describe("resolveSettingsSection", () => {
  it("defaults to Appearance when ?section is absent", () => {
    expect(resolveSettingsSection(undefined, false)).toBe("appearance");
    expect(resolveSettingsSection(undefined, true)).toBe("appearance");
    expect(DEFAULT_SETTINGS_SECTION).toBe("appearance");
  });

  it("falls back to Appearance for an unknown section id", () => {
    expect(resolveSettingsSection("does-not-exist", true)).toBe("appearance");
    expect(resolveSettingsSection("", true)).toBe("appearance");
  });

  it("resolves a valid global section regardless of project scope", () => {
    expect(resolveSettingsSection("notifications", false)).toBe("notifications");
    expect(resolveSettingsSection("integrations", true)).toBe("integrations");
    expect(resolveSettingsSection("tour", false)).toBe("tour");
  });

  it("resolves a project-scoped section ONLY when a project is in scope", () => {
    expect(resolveSettingsSection("project", true)).toBe("project");
    expect(resolveSettingsSection("advanced", true)).toBe("advanced");
  });

  it("falls back to Appearance when a project-scoped section is requested without a project", () => {
    expect(resolveSettingsSection("project", false)).toBe("appearance");
    expect(resolveSettingsSection("advanced", false)).toBe("appearance");
  });
});

describe("sectionNeedsAudit", () => {
  it("gates the audit fetch to the Advanced section only", () => {
    expect(sectionNeedsAudit("advanced")).toBe(true);
    expect(sectionNeedsAudit("project")).toBe(false);
    expect(sectionNeedsAudit("appearance")).toBe(false);
    expect(sectionNeedsAudit("notifications")).toBe(false);
    expect(sectionNeedsAudit("integrations")).toBe(false);
    expect(sectionNeedsAudit("tour")).toBe(false);
  });
});
