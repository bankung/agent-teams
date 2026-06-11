// safeMarkdown — Kanban #1005 AC#6 (SECURITY-CRITICAL).
//
// A small, self-contained markdown → React-element renderer. It does NOT emit
// HTML strings and NEVER uses dangerouslySetInnerHTML — every node is a real
// React element whose text content React escapes by default. This structurally
// eliminates stored-XSS (the comment `body` is attacker/agent-controllable):
// there is no HTML-string sink to inject into, and the renderer ONLY produces
// the fixed set of element types enumerated below. Raw HTML in the source
// (e.g. `<script>`, `<img onerror=…>`) is treated as LITERAL TEXT — it is shown
// verbatim, escaped, never parsed into DOM.
//
// Why a hand-rolled renderer rather than react-markdown + rehype-sanitize:
//   1. Zero new runtime dependencies → zero new transitive supply-chain surface
//      (the container does not need an npm reinstall to ship this).
//   2. No HTML pipeline at all → there is no sanitizer-bypass class of bug
//      (e.g. a mis-configured schema, a mutation-XSS gadget) to get wrong.
//      The "allowlist" here is the literal set of element types this code can
//      construct — it cannot be widened by crafted input.
//   3. URLs in links + images are validated to http(s)/mailto BEFORE rendering;
//      anything else (javascript:, data:, vbscript:, relative) is downgraded to
//      plain text so no active-content URL ever reaches an href/src attribute.
//
// Supported (deliberately minimal — progress notes, not a CMS):
//   - fenced code blocks ```lang … ```  (rendered <pre><code>, never executed)
//   - headings # … ######
//   - unordered (-, *, +) and ordered (1.) lists (flat; no nesting)
//   - blockquotes >
//   - paragraphs with inline: **bold**, *italic*, `code`,
//     [text](http-url), ![alt](http-img-url), autolinked bare http(s) URLs
//
// Everything that isn't recognized is rendered as escaped plain text.

import type { ReactNode } from "react";

// URL allowlist — only these schemes may reach an href/src. Relative URLs are
// intentionally REJECTED (downgraded to text): a comment thread has no business
// linking into the app's own routes, and rejecting them avoids open-redirect /
// protocol-relative ("//evil.com") ambiguity. mailto is allowed on links only.
const SAFE_LINK_SCHEME = /^(https?:|mailto:)/i;
const SAFE_IMG_SCHEME = /^https?:/i;

// Reject any control char (code point < 0x20 or == 0x7F) or whitespace smuggled
// into the URL (e.g. "java\tscript:", "java\nscript:", "java script:") BEFORE
// the scheme test — those are classic scheme-filter bypasses. We scan by code
// point rather than a regex with literal control bytes (avoids source-encoding
// fragility). A clean URL has none of these, so legit links/images are
// unaffected.
function hasUnsafeUrlChars(u: string): boolean {
  for (let p = 0; p < u.length; p++) {
    const code = u.charCodeAt(p);
    if (code <= 0x20 || code === 0x7f) return true; // C0 controls, space, DEL
  }
  return false;
}

// Reject URLs that contain a userinfo component (the `user:pass@` part of an
// authority). A URL like `https://trusted.com:x@evil.com/` passes a naive
// scheme check but browsers navigate to `evil.com` — classic phishing assist.
// We scan for `@` in the authority (between the scheme `//` and the first path
// `/`, `?`, or `#`). Legitimate http(s)/mailto URLs virtually never carry
// userinfo, so no false positives for real content.
function hasUserinfo(u: string): boolean {
  const schemeEnd = u.indexOf("//");
  if (schemeEnd === -1) return false;
  const authorityStart = schemeEnd + 2;
  // Find where the authority ends: first /, ?, or # after it (or string end).
  let authorityEnd = u.length;
  for (let i = authorityStart; i < u.length; i++) {
    const c = u[i];
    if (c === "/" || c === "?" || c === "#") {
      authorityEnd = i;
      break;
    }
  }
  return u.slice(authorityStart, authorityEnd).includes("@");
}

function isSafeLinkUrl(url: string): boolean {
  const u = url.trim();
  if (hasUnsafeUrlChars(u)) return false;
  if (hasUserinfo(u)) return false;
  return SAFE_LINK_SCHEME.test(u);
}

function isSafeImgUrl(url: string): boolean {
  const u = url.trim();
  if (hasUnsafeUrlChars(u)) return false;
  if (hasUserinfo(u)) return false;
  return SAFE_IMG_SCHEME.test(u);
}

// ---------------------------------------------------------------------------
// Inline parsing — operates on a single line of text and returns React nodes.
// Order matters: code spans first (they suppress other markup inside), then
// images, links, autolinks, then emphasis.
// ---------------------------------------------------------------------------

// A tiny token-by-token scanner. We avoid a single mega-regex with
// backreferences (ReDoS risk on attacker input) — each pattern is anchored at
// the current cursor and linear.
function parseInline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  let i = 0;
  let buf = "";
  let k = 0;

  const flush = () => {
    if (buf.length > 0) {
      out.push(buf);
      buf = "";
    }
  };
  const pushNode = (node: ReactNode) => {
    flush();
    out.push(node);
  };

  while (i < text.length) {
    const rest = text.slice(i);

    // Inline code `…` — highest precedence; contents are literal.
    const code = /^`([^`]+)`/.exec(rest);
    if (code) {
      pushNode(
        <code
          key={`${keyBase}-c${k++}`}
          className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-[0.85em] text-zinc-800 dark:bg-zinc-800 dark:text-zinc-200"
        >
          {code[1]}
        </code>,
      );
      i += code[0].length;
      continue;
    }

    // Image ![alt](url) — only http(s) src; otherwise fall through to text.
    const img = /^!\[([^\]]*)\]\(([^)\s]+)\)/.exec(rest);
    if (img) {
      const alt = img[1];
      const url = img[2];
      if (isSafeImgUrl(url)) {
        pushNode(
          // eslint-disable-next-line @next/next/no-img-element
          <img
            key={`${keyBase}-i${k++}`}
            src={url}
            alt={alt}
            loading="lazy"
            referrerPolicy="no-referrer"
            className="my-1 max-h-80 max-w-full rounded border border-zinc-200 dark:border-zinc-700"
          />,
        );
        i += img[0].length;
        continue;
      }
      // Unsafe image URL → render the raw markdown as literal text (no sink).
      buf += img[0];
      i += img[0].length;
      continue;
    }

    // Link [text](url) — only http(s)/mailto; otherwise literal text.
    const link = /^\[([^\]]+)\]\(([^)\s]+)\)/.exec(rest);
    if (link) {
      const label = link[1];
      const url = link[2];
      if (isSafeLinkUrl(url)) {
        pushNode(
          <a
            key={`${keyBase}-l${k++}`}
            href={url}
            target="_blank"
            rel="noopener noreferrer nofollow ugc"
            className="text-violet-700 underline decoration-violet-300 underline-offset-2 hover:text-violet-900 dark:text-violet-300 dark:hover:text-violet-100"
          >
            {label}
          </a>,
        );
        i += link[0].length;
        continue;
      }
      buf += link[0];
      i += link[0].length;
      continue;
    }

    // Bare autolink — a run of http(s):// up to whitespace or a closing bracket.
    const auto = /^(https?:\/\/[^\s<>()]+)/.exec(rest);
    if (auto && isSafeLinkUrl(auto[1])) {
      const url = auto[1];
      pushNode(
        <a
          key={`${keyBase}-a${k++}`}
          href={url}
          target="_blank"
          rel="noopener noreferrer nofollow ugc"
          className="break-all text-violet-700 underline decoration-violet-300 underline-offset-2 hover:text-violet-900 dark:text-violet-300 dark:hover:text-violet-100"
        >
          {url}
        </a>,
      );
      i += auto[0].length;
      continue;
    }

    // **bold** (no nesting of the same marker)
    const bold = /^\*\*([^*]+)\*\*/.exec(rest);
    if (bold) {
      pushNode(
        <strong key={`${keyBase}-b${k++}`} className="font-semibold">
          {bold[1]}
        </strong>,
      );
      i += bold[0].length;
      continue;
    }

    // *italic* / _italic_
    const ital = /^[*_]([^*_]+)[*_]/.exec(rest);
    if (ital) {
      pushNode(
        <em key={`${keyBase}-e${k++}`} className="italic">
          {ital[1]}
        </em>,
      );
      i += ital[0].length;
      continue;
    }

    // Plain character — accumulate. (React escapes `buf` text on render.)
    buf += text[i];
    i += 1;
  }

  flush();
  return out;
}

// ---------------------------------------------------------------------------
// Block parsing — splits the body into blocks and dispatches each to a renderer.
// ---------------------------------------------------------------------------

type Block =
  | { kind: "code"; lang: string; lines: string[] }
  | { kind: "heading"; level: number; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[] }
  | { kind: "quote"; lines: string[] }
  | { kind: "p"; lines: string[] };

function parseBlocks(src: string): Block[] {
  // Normalize newlines. Leading spaces inside code fences are preserved.
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code block ```lang
    const fence = /^```(.*)$/.exec(line);
    if (fence) {
      const lang = fence[1].trim();
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !/^```\s*$/.test(lines[i])) {
        body.push(lines[i]);
        i += 1;
      }
      i += 1; // consume closing fence (or EOF)
      blocks.push({ kind: "code", lang, lines: body });
      continue;
    }

    // Blank line → block separator
    if (/^\s*$/.test(line)) {
      i += 1;
      continue;
    }

    // Heading
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      blocks.push({ kind: "heading", level: h[1].length, text: h[2].trim() });
      i += 1;
      continue;
    }

    // Blockquote (consume consecutive > lines)
    if (/^\s*>\s?/.test(line)) {
      const qlines: string[] = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        qlines.push(lines[i].replace(/^\s*>\s?/, ""));
        i += 1;
      }
      blocks.push({ kind: "quote", lines: qlines });
      continue;
    }

    // Unordered list (consume consecutive -, *, + items)
    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*+]\s+/, ""));
        i += 1;
      }
      blocks.push({ kind: "ul", items });
      continue;
    }

    // Ordered list (consume consecutive `N.` items)
    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ""));
        i += 1;
      }
      blocks.push({ kind: "ol", items });
      continue;
    }

    // Paragraph (consume consecutive non-blank, non-special lines)
    const plines: string[] = [];
    while (
      i < lines.length &&
      !/^\s*$/.test(lines[i]) &&
      !/^```/.test(lines[i]) &&
      !/^(#{1,6})\s+/.test(lines[i]) &&
      !/^\s*>\s?/.test(lines[i]) &&
      !/^\s*[-*+]\s+/.test(lines[i]) &&
      !/^\s*\d+\.\s+/.test(lines[i])
    ) {
      plines.push(lines[i]);
      i += 1;
    }
    blocks.push({ kind: "p", lines: plines });
  }

  return blocks;
}

const HEADING_CLASS: Record<number, string> = {
  1: "text-base font-semibold",
  2: "text-sm font-semibold",
  3: "text-sm font-semibold",
  4: "text-xs font-semibold uppercase tracking-wide",
  5: "text-xs font-semibold uppercase tracking-wide",
  6: "text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400",
};

function renderBlock(block: Block, idx: number): ReactNode {
  const key = `blk-${idx}`;
  switch (block.kind) {
    case "code":
      return (
        <pre
          key={key}
          data-md-code
          data-md-lang={block.lang || undefined}
          className="my-1 max-h-72 overflow-auto rounded bg-zinc-100 px-2 py-1.5 dark:bg-zinc-900"
        >
          <code className="font-mono text-xs text-zinc-800 dark:text-zinc-200">
            {block.lines.join("\n")}
          </code>
        </pre>
      );
    case "heading": {
      const Tag = `h${Math.min(block.level + 2, 6)}` as "h3" | "h4" | "h5" | "h6";
      return (
        <Tag
          key={key}
          className={`mt-1 ${HEADING_CLASS[block.level] ?? HEADING_CLASS[3]} text-zinc-900 dark:text-zinc-100`}
        >
          {parseInline(block.text, `${key}-h`)}
        </Tag>
      );
    }
    case "ul":
      return (
        <ul key={key} className="my-1 list-disc pl-5">
          {block.items.map((it, j) => (
            <li key={j}>{parseInline(it, `${key}-li${j}`)}</li>
          ))}
        </ul>
      );
    case "ol":
      return (
        <ol key={key} className="my-1 list-decimal pl-5">
          {block.items.map((it, j) => (
            <li key={j}>{parseInline(it, `${key}-li${j}`)}</li>
          ))}
        </ol>
      );
    case "quote":
      return (
        <blockquote
          key={key}
          className="my-1 border-l-2 border-zinc-300 pl-3 text-zinc-600 dark:border-zinc-600 dark:text-zinc-400"
        >
          {block.lines.map((ln, j) => (
            <p key={j}>{parseInline(ln, `${key}-q${j}`)}</p>
          ))}
        </blockquote>
      );
    case "p":
      // Join paragraph lines with explicit <br/> nodes (soft breaks) so multi-
      // line paragraphs keep their shape without re-introducing an HTML sink.
      return (
        <p key={key} className="my-1 leading-relaxed">
          {block.lines.map((ln, j) => (
            <span key={j}>
              {parseInline(ln, `${key}-p${j}`)}
              {j < block.lines.length - 1 ? <br /> : null}
            </span>
          ))}
        </p>
      );
  }
}

// renderMarkdown — public entry. Returns a React fragment of block elements.
// SAFE: no HTML-string sink anywhere in the path; raw HTML in `src` is escaped
// literal text. Element types are limited to the fixed set above; link/img URLs
// are scheme-validated before reaching an href/src.
export function renderMarkdown(src: string): ReactNode {
  const blocks = parseBlocks(src);
  return <>{blocks.map((b, i) => renderBlock(b, i))}</>;
}
