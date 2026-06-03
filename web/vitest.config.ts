/// <reference types="vitest" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["**/*.test.{ts,tsx}"],
    // Exclude the legacy hand-rolled test files that use the old runAll() pattern.
    // They import from lib/* and work fine, but they export runAll() instead of
    // describe/it — adding them here would cause Vitest to report 0 tests and exit 1.
    // The parseSteps / sortLaneTasks / cycleExclusion tests are covered by the new
    // Vitest counterparts in __tests__/.
    exclude: [
      "**/node_modules/**",
      "lib/parseSteps.test.ts",
      "lib/sortLaneTasks.test.ts",
      "lib/cycleExclusion.test.ts",
    ],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
  },
});
