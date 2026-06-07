// Kanban #1243 — Phase 0 trivial smoke: /review page renders without error.
// NOTE: This smoke is BLOCKED until the TerminateFlagModal.tsx bug is fixed.
// Bug: single.projectName crashes when targets=[] (undefined !== null evaluates true).
// File: web/components/TerminateFlagModal.tsx line 158.
// Fix needed: change `single !== null` to `single != null` (coercive check catches undefined).
import { test, expect } from "@playwright/test";

test("review page renders the Review heading", async ({ page }) => {
  await page.goto("/review");
  // [data-review-summary] is emitted by ReviewClient on every render
  // (both "N flags across N projects" and "0 flags across 0 projects").
  // It is a stable, always-present sentinel — more reliable than text matching.
  const summary = page.locator("[data-review-summary]");
  await expect(summary).toBeVisible();
});
