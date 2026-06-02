// sw-contract.test.mjs — Kanban #1769 (AC[2]).
//
// Standalone Node assertion that verifies sw.js contains NO `fetch` event
// listener. Run with: node web/scripts/sw-contract.test.mjs
//
// This script is intentionally framework-free (no vitest/jest — see
// web/package.json, which carries no test runner). Exit 0 = assertion passed;
// exit 1 = violation detected (sw.js gained a fetch handler unexpectedly).
//
// CI integration: add `node web/scripts/sw-contract.test.mjs` as a step in
// any pipeline that lints/builds the web layer. The script reads the source
// file directly so it catches violations before the file is served.

import { readFileSync } from "fs";
import { fileURLToPath } from "url";
import { join, dirname } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const swPath = join(__dirname, "..", "public", "sw.js");

let source;
try {
  source = readFileSync(swPath, "utf8");
} catch (err) {
  console.error(`[sw-contract] ERROR: could not read ${swPath}`);
  console.error(err.message);
  process.exit(1);
}

// Patterns that would indicate a fetch interception handler. We check both
// the addEventListener form and the shorthand `onfetch =` assignment form.
const fetchHandlerPatterns = [
  /addEventListener\s*\(\s*["']fetch["']/,
  /self\.onfetch\s*=/,
];

let violated = false;
for (const pattern of fetchHandlerPatterns) {
  if (pattern.test(source)) {
    console.error(
      `[sw-contract] FAIL: sw.js contains a fetch event handler matching /${pattern.source}/.`,
    );
    console.error(
      "[sw-contract] This SW is push-only and must NOT intercept fetch/navigation.",
    );
    violated = true;
  }
}

if (violated) {
  process.exit(1);
}

console.log("[sw-contract] PASS: sw.js contains no fetch event listener. Push-only contract holds.");
process.exit(0);
