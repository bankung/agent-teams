// LOC report — counts lines per category across git-tracked files only.
// Run from repo root: node scripts/loc-report.mjs
import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join, basename, extname } from "node:path";

const ROOT = new URL("..", import.meta.url).pathname.replace(/^\/([A-Z]:)/, "$1");

const DENY_NAMES = new Set(["package-lock.json"]);
const DENY_EXT = new Set([".lock", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
  ".webp", ".woff", ".woff2", ".ttf", ".eot", ".pdf"]);

const files = execSync("git ls-files", { cwd: ROOT, encoding: "utf8" })
  .split("\n").map(f => f.trim()).filter(Boolean);

const cats = {
  migrations: { files: 0, lines: 0 },
  tests:      { files: 0, lines: 0 },
  methodology:{ files: 0, lines: 0 },
  generated:  { files: 0, lines: 0 },
  production: { files: 0, lines: 0 },
};

function classify(f) {
  if (f.startsWith("api/alembic/versions/")) return "migrations";
  const segs = f.split("/");
  const name = basename(f);
  if (f.startsWith("api/tests/") || segs.includes("tests") ||
      segs.includes("__tests__") || /^test_.+\.py$/.test(name) ||
      /_test\.py$/.test(name) || /\.(test|spec)\.(ts|tsx)$/.test(name)) return "tests";
  if (f.startsWith(".claude/") || f.startsWith("context/")) return "methodology";
  if (f.startsWith(".codex/")) return "generated";
  return "production";
}

for (const rel of files) {
  if (DENY_NAMES.has(basename(rel)) || DENY_EXT.has(extname(rel))) continue;
  let text;
  try { text = readFileSync(join(ROOT, rel), "utf8"); } catch { continue; }
  const lines = (text.match(/\n/g) || []).length + (text.length > 0 && !text.endsWith("\n") ? 1 : 0);
  const cat = classify(rel);
  cats[cat].files++;
  cats[cat].lines += lines;
}

const labels = {
  migrations: "migrations",
  tests:      "tests",
  methodology:"methodology",
  generated:  "generated (not a target)",
  production: "production",
};

console.log("\nLOC report — 2026-06-16\n");
console.log("Category                    files       lines");
console.log("------------------------------------------------");
let tf = 0, tl = 0;
for (const [k, v] of Object.entries(cats)) {
  const label = labels[k].padEnd(28);
  console.log(`${label}${String(v.files).padStart(5)}  ${String(v.lines).padStart(10)}`);
  tf += v.files; tl += v.lines;
}
console.log("------------------------------------------------");
console.log(`${"TOTAL".padEnd(28)}${String(tf).padStart(5)}  ${String(tl).padStart(10)}`);
console.log("\nNote: .codex/ is a GENERATED artifact regenerated from .claude/ — excluded from de-bloat targets.\n");
