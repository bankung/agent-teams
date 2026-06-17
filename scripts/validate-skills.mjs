// Skill frontmatter validator + catalog generator.
// Run from repo root:
//   node _scratch/skills-validator/validate-skills.mjs            (validate — exit 0 if all pass)
//   node _scratch/skills-validator/validate-skills.mjs --catalog  (print catalog to stdout)
//   node _scratch/skills-validator/validate-skills.mjs --write-catalog <path>  (write catalog file)
// Mirror of scripts/loc-report.mjs style: Node ESM, no shebang, no npm deps, hand-rolled argv.
import { readFileSync, writeFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { join, relative, dirname } from "node:path";

// Resolve the repo root by walking up from THIS file until we find .claude/skills.
// Location-independent on purpose: works the same whether this script lives in
// _scratch/skills-validator/ (draft) or its promoted home scripts/ — no path-depth
// assumption, so promotion needs no edit.
const HERE = dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Z]:)/, "$1"));
function findRoot(start) {
  let dir = start;
  while (dir !== dirname(dir)) {
    if (existsSync(join(dir, ".claude", "skills"))) return dir;
    dir = dirname(dir);
  }
  throw new Error(".claude/skills not found walking up from " + start);
}
const ROOT = findRoot(HERE);
const SKILLS_DIR = join(ROOT, ".claude", "skills");

const VALID_CATEGORIES = new Set(["kanban", "platform", "review", "secretary"]);
const SEMVER_RE = /^\d+\.\d+\.\d+$/;

// Clip a string to `max` chars on a word boundary with an ellipsis (no mid-word cuts).
function clip(s, max = 140) {
  s = s.replace(/\s+/g, " ").trim();
  if (s.length <= max) return s;
  const cut = s.slice(0, max);
  const sp = cut.lastIndexOf(" ");
  return (sp > 40 ? cut.slice(0, sp) : cut).trimEnd() + "…";
}

// ---------------------------------------------------------------------------
// shared frontmatter parser — the single source of truth used by both modes
// ---------------------------------------------------------------------------
// The frontmatter is always a `---` fenced block at the start of the file.
// Fields we care about:
//   name          scalar
//   description   >- folded multiline OR plain scalar (possibly wrapped)
//   argument-hint >- folded OR plain scalar
//   allowed-tools - item list
//   metadata:
//     version     scalar
//     category    scalar
//     tags        inline [a, b, c] array
//
// We do NOT use a YAML library — the corpus is uniform enough for hand-parsing.
// ---------------------------------------------------------------------------
function parseFrontmatter(text) {
  // Extract the --- block
  const match = text.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  if (!match) return null;
  const block = match[1];
  const lines = block.split(/\r?\n/);

  const result = {
    name: null,
    description: null,
    "argument-hint": null,
    "allowed-tools": [],   // array of strings
    metadata: {
      version: null,
      category: null,
      tags: [],            // array of strings
    },
  };

  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    // Top-level key detection (no leading spaces)
    const topKey = line.match(/^([\w-]+):\s*(.*)/);
    if (!topKey) { i++; continue; }

    const key = topKey[1];
    const rest = topKey[2].trim();

    // --- scalar fields (name, argument-hint) ---
    if (key === "name") {
      result.name = rest || null;
      i++;
      continue;
    }

    if (key === "argument-hint") {
      if (rest === ">-") {
        // collect indented continuation lines
        const parts = [];
        i++;
        while (i < lines.length && /^ /.test(lines[i])) {
          parts.push(lines[i].trim());
          i++;
        }
        result["argument-hint"] = parts.join(" ") || null;
      } else {
        result["argument-hint"] = rest || null;
        i++;
      }
      continue;
    }

    // --- description: may be >- (folded) or plain scalar (possibly long single line) ---
    if (key === "description") {
      if (rest === ">-") {
        const parts = [];
        i++;
        while (i < lines.length && /^ /.test(lines[i])) {
          parts.push(lines[i].trim());
          i++;
        }
        result.description = parts.join(" ") || null;
      } else {
        result.description = rest || null;
        i++;
      }
      continue;
    }

    // --- allowed-tools: list of "  - item" lines ---
    if (key === "allowed-tools") {
      // rest should be empty (the list follows on next lines with "  - ...")
      i++;
      while (i < lines.length && /^ {2,}/.test(lines[i])) {
        const itemMatch = lines[i].match(/^\s+-\s+(.*)/);
        if (itemMatch) result["allowed-tools"].push(itemMatch[1].trim());
        i++;
      }
      continue;
    }

    // --- metadata: block with indented version / category / tags ---
    if (key === "metadata") {
      i++;
      while (i < lines.length && /^ {2}/.test(lines[i])) {
        const sub = lines[i].match(/^ {2}([\w-]+):\s*(.*)/);
        if (sub) {
          const sk = sub[1];
          const sv = sub[2].trim();
          if (sk === "version")  result.metadata.version  = sv || null;
          if (sk === "category") result.metadata.category = sv || null;
          if (sk === "tags") {
            // always inline [a, b, c]
            const tagMatch = sv.match(/^\[(.*)\]$/);
            if (tagMatch) {
              result.metadata.tags = tagMatch[1]
                .split(",")
                .map(t => t.trim())
                .filter(Boolean);
            }
          }
        }
        i++;
      }
      continue;
    }

    i++;
  }

  return result;
}

// ---------------------------------------------------------------------------
// Collect all skill files
// ---------------------------------------------------------------------------
function collectSkills(dir = SKILLS_DIR) {
  const skills = [];
  for (const entry of readdirSync(dir)) {
    const skillPath = join(dir, entry);
    if (!statSync(skillPath).isDirectory()) continue;
    const skillFile = join(skillPath, "SKILL.md");
    let text;
    try { text = readFileSync(skillFile, "utf8"); } catch { continue; }
    const rel = relative(ROOT, skillFile).replace(/\\/g, "/");
    skills.push({ path: skillFile, rel, name: entry, text });
  }
  return skills;
}

// ---------------------------------------------------------------------------
// Validate mode
// ---------------------------------------------------------------------------
function runValidate(skills) {
  const offenders = [];
  let okCount = 0;

  for (const { rel, name, text } of skills) {
    const fm = parseFrontmatter(text);
    const fails = [];
    const warns = [];

    if (!fm) {
      fails.push("no frontmatter block found");
    } else {
      // HARD checks
      if (!fm.name) fails.push("name missing or empty");
      if (!fm.description) fails.push("description missing or empty");
      if (!fm["allowed-tools"] || fm["allowed-tools"].length === 0)
        fails.push("allowed-tools missing or empty list");
      if (!fm.metadata.version)
        fails.push("metadata.version missing");
      else if (!SEMVER_RE.test(fm.metadata.version))
        fails.push(`metadata.version "${fm.metadata.version}" not semver (x.y.z)`);
      if (!fm.metadata.category)
        fails.push("metadata.category missing");
      else if (!VALID_CATEGORIES.has(fm.metadata.category))
        fails.push(`metadata.category "${fm.metadata.category}" not in {kanban, platform, review, secretary}`);
      if (!fm.metadata.tags || fm.metadata.tags.length === 0)
        fails.push("metadata.tags missing or empty list");

      // SOFT warnings
      if (!fm["argument-hint"]) warns.push("argument-hint missing (SOFT)");
    }

    if (fails.length > 0) {
      offenders.push({ rel, fails, warns });
    } else {
      if (warns.length > 0) {
        console.log(`WARN  ${rel}`);
        for (const w of warns) console.log(`        - ${w}`);
      }
      okCount++;
    }
  }

  for (const { rel, fails, warns } of offenders) {
    console.log(`FAIL  ${rel}`);
    for (const f of fails) console.log(`        - ${f}`);
    for (const w of warns) console.log(`        - ${w}`);
  }

  console.log(`\n${okCount}/${skills.length} OK`);
  return offenders.length === 0;
}

// ---------------------------------------------------------------------------
// Catalog mode
// ---------------------------------------------------------------------------
function buildCatalog(skills) {
  // Parse all skills; sort by category then name
  const rows = [];
  for (const { rel, text } of skills) {
    const fm = parseFrontmatter(text);
    if (!fm) continue;
    const desc = (fm.description || "").replace(/\s+/g, " ").trim();
    // "When to use": prefer the explicit "Use when …" clause; else the first sentence.
    let whenToUse;
    const useWhenMatch = desc.match(/[Uu]se when (.+)/);
    if (useWhenMatch) {
      whenToUse = clip("Use when " + useWhenMatch[1]);
    } else {
      const dot = desc.indexOf(".");
      whenToUse = dot > 0 && dot < 140 ? desc.slice(0, dot + 1) : clip(desc);
    }

    rows.push({
      name: fm.name || rel,
      category: fm.metadata.category || "?",
      tags: fm.metadata.tags.join(", "),
      whenToUse,
      path: rel,
    });
  }

  rows.sort((a, b) => {
    if (a.category !== b.category) return a.category.localeCompare(b.category);
    return a.name.localeCompare(b.name);
  });

  const lines = [
    "<!-- GENERATED by scripts/validate-skills.mjs — do not hand-edit; regenerate with: node scripts/validate-skills.mjs --catalog -->",
    "",
    "# Skill Catalog",
    "",
  ];

  let lastCat = null;
  for (const row of rows) {
    if (row.category !== lastCat) {
      if (lastCat !== null) lines.push("");
      lines.push(`## ${row.category}`);
      lines.push("");
      lines.push("| Skill | Tags | When to use | Path |");
      lines.push("|---|---|---|---|");
      lastCat = row.category;
    }
    lines.push(`| \`${row.name}\` | ${row.tags} | ${row.whenToUse} | \`${row.path}\` |`);
  }

  return lines.join("\n") + "\n";
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
const args = process.argv.slice(2);
const catalogFlag  = args.includes("--catalog");
const writeIdx     = args.indexOf("--write-catalog");
const writeTarget  = writeIdx >= 0 ? args[writeIdx + 1] : null;
const skillsDirIdx = args.indexOf("--skills-dir");
const skillsDirOverride = skillsDirIdx >= 0 ? args[skillsDirIdx + 1] : null;

const skills = collectSkills(skillsDirOverride || SKILLS_DIR);

if (catalogFlag || writeTarget) {
  const catalog = buildCatalog(skills);
  if (writeTarget) {
    writeFileSync(writeTarget, catalog, "utf8");
    console.log(`Catalog written to ${writeTarget}  (${skills.length} skills)`);
  } else {
    process.stdout.write(catalog);
  }
} else {
  const ok = runValidate(skills);
  process.exit(ok ? 0 : 1);
}
