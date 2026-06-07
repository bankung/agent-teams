// Kanban #1243 — Playwright E2E config for the /review flag-resolution flow.
// Targets the ALREADY-RUNNING dev server at http://localhost:5431.
// NEVER set `command` in webServer — that would trigger next build and corrupt
// the shared .next directory.
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  // Fail fast: if the first retry also fails, abort that test.
  // No global timeouts that hide flakiness — rely on Playwright auto-waiting.
  timeout: 30_000,
  expect: { timeout: 10_000 },
  // No retries by default — tests must be deterministic.
  retries: 0,
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://localhost:5431",
    // Chromium only — no cross-browser scope in this suite.
    ...devices["Desktop Chrome"],
    // Don't reuse state across tests — each test gets a clean context.
    storageState: undefined,
    // Screenshot on failure for diagnosis.
    screenshot: "only-on-failure",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  // reuseExistingServer: the dev server is already running — we just connect.
  // webServer is intentionally omitted to prevent any build/start attempts.
});
