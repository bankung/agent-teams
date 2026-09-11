// Kanban #2826 / #2827 — lib/constants.ts mirrors api/src/constants.py.
//
// #2827(a): ProjectTeam was missing NETOPS ("netops"), though the API has
// carried it since #1620. Locks that the FE enum stays in sync.
//
// #2826: TaskRole used to mirror ONLY the 6 dev-range codes; ROLE_OPTIONS
// (the New Task modal's role catalog) was dev-only as a result, and
// NewTaskModal could never offer a non-dev-team project its own roles. Locks
// the full code mirror + the roleOptionsForTeam() per-team derivation.
//
// #2871: mobile team registered (ProjectTeam.MOBILE + codes 61-62, RANGE_MAX
// 60→70). Only the two frontend roles are mobile-owned — backend / devops /
// test / review are borrowed from the dev range and keep their dev codes.

import { describe, it, expect } from "vitest";
import {
  ProjectTeam,
  ROLE_OPTIONS,
  TaskRole,
  roleOptionsForTeam,
} from "@/lib/constants";

describe("ProjectTeam (#2827a)", () => {
  it("includes NETOPS = 'netops'", () => {
    expect(ProjectTeam.NETOPS).toBe("netops");
    expect(Object.values(ProjectTeam)).toContain("netops");
  });

  // #2871 — same drift class as #2827(a): the API registered the team, the FE
  // enum has to follow or the project's team never renders in the picker.
  it("includes MOBILE = 'mobile'", () => {
    expect(ProjectTeam.MOBILE).toBe("mobile");
    expect(Object.values(ProjectTeam)).toContain("mobile");
  });
});

describe("TaskRole full catalog mirror (#2826)", () => {
  it("mirrors all 30 named codes from api/src/constants.py across 7 team ranges", () => {
    expect(Object.keys(TaskRole)).toHaveLength(30);
    // Spot-check one named code per range (full set covered indirectly via
    // ROLE_OPTIONS length + the per-team narrowing tests below).
    expect(TaskRole.FRONTEND).toBe(1); // dev
    expect(TaskRole.NOVEL_WRITER).toBe(11); // novel
    expect(TaskRole.SEO_STRATEGIST).toBe(21); // seo
    expect(TaskRole.SEM_CAMPAIGN_LEAD).toBe(31); // sem
    expect(TaskRole.BI_ANALYST).toBe(41); // data-analytics
    expect(TaskRole.CONTENT_WRITER).toBe(51); // social
    expect(TaskRole.MOBILE_FRONTEND).toBe(61); // mobile
  });

  it("ROLE_OPTIONS carries the unassigned sentinel plus all 30 named roles", () => {
    // 1 sentinel ("" unassigned) + 30 named codes.
    expect(ROLE_OPTIONS).toHaveLength(31);
    expect(ROLE_OPTIONS[0]).toEqual({ value: "", label: "— unassigned —" });
    for (const label of ROLE_OPTIONS.slice(1)) {
      expect(typeof label.value).toBe("number");
    }
  });
});

describe("roleOptionsForTeam (#2826 — per-team role dropdown scoping)", () => {
  it("dev team narrows to the 6 dev-range codes (+ unassigned)", () => {
    const opts = roleOptionsForTeam(ProjectTeam.DEV);
    expect(opts.map((o) => o.value)).toEqual([
      "",
      TaskRole.FRONTEND,
      TaskRole.BACKEND,
      TaskRole.DEVOPS,
      TaskRole.QA,
      TaskRole.REVIEWER,
      TaskRole.SECURITY_REVIEWER,
    ]);
  });

  it("seo team narrows to the 4 seo-range codes (+ unassigned) — was dev-only before #2826", () => {
    const opts = roleOptionsForTeam(ProjectTeam.SEO);
    expect(opts.map((o) => o.value)).toEqual([
      "",
      TaskRole.SEO_STRATEGIST,
      TaskRole.TECHNICAL_SEO_SPECIALIST,
      TaskRole.CONTENT_SEO_OPTIMIZER,
      TaskRole.SEO_REPORTING_ANALYST,
    ]);
    // The dev-only bug this replaces: a seo project's dropdown must NOT be
    // limited to FRONTEND/BACKEND/etc.
    expect(opts.map((o) => o.value)).not.toContain(TaskRole.FRONTEND);
  });

  it("social team narrows to the 7 social-range codes (+ unassigned)", () => {
    const opts = roleOptionsForTeam(ProjectTeam.SOCIAL);
    expect(opts.map((o) => o.value)).toEqual([
      "",
      TaskRole.CONTENT_WRITER,
      TaskRole.CONTENT_HOOK_DOCTOR,
      TaskRole.CONTENT_EDITOR,
      TaskRole.CONTENT_VERACITY_CHECKER,
      TaskRole.THAI_PROOFREADER,
      TaskRole.SOCIAL_BI_ANALYST,
      TaskRole.SOCIAL_GENERAL_RESEARCHER,
    ]);
  });

  it("mobile team narrows to the 2 mobile-range codes (+ unassigned) — #2871", () => {
    const opts = roleOptionsForTeam(ProjectTeam.MOBILE);
    expect(opts.map((o) => o.value)).toEqual([
      "",
      TaskRole.MOBILE_FRONTEND,
      TaskRole.MOBILE_SR_FRONTEND,
    ]);
    // The borrowed roles (dev-backend/devops/tester/reviewer) live in the dev
    // range by design, so they are NOT offered by the mobile dropdown even
    // though TEAM_ROSTERS[mobile] contains them.
    expect(opts.map((o) => o.value)).not.toContain(TaskRole.BACKEND);
  });

  it("unranged teams (general/content/netops) and unknown/undefined fall back to the full catalog", () => {
    expect(roleOptionsForTeam(ProjectTeam.GENERAL)).toEqual(ROLE_OPTIONS);
    expect(roleOptionsForTeam(ProjectTeam.CONTENT)).toEqual(ROLE_OPTIONS);
    expect(roleOptionsForTeam(ProjectTeam.NETOPS)).toEqual(ROLE_OPTIONS);
    expect(roleOptionsForTeam(undefined)).toEqual(ROLE_OPTIONS);
    expect(roleOptionsForTeam(null)).toEqual(ROLE_OPTIONS);
    expect(roleOptionsForTeam("not-a-real-team")).toEqual(ROLE_OPTIONS);
  });
});
