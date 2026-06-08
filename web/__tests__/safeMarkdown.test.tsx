// Security + rendering tests for lib/safeMarkdown — Kanban #1005 AC#6.
//
// The load-bearing assertions are the XSS ones: a comment body is attacker/
// agent-controllable, so we prove that raw HTML (<script>, <img onerror=…>,
// javascript: URLs, etc.) is NEVER turned into live DOM — it is escaped literal
// text. Element-type allowlisting is verified by inspecting the rendered tree.

import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { renderMarkdown } from "@/lib/safeMarkdown";

describe("safeMarkdown — XSS hardening", () => {
  it("renders a <script> tag in the body as literal text, not a real element", () => {
    const { container } = render(
      <div>{renderMarkdown('hello <script>alert("xss")</script> world')}</div>,
    );
    // No real <script> element was created.
    expect(container.querySelector("script")).toBeNull();
    // The literal angle-bracket text survives (escaped) in the output.
    expect(container.textContent).toContain("<script>");
    expect(container.textContent).toContain("alert(");
  });

  it("does not create an <img> with an onerror handler from raw HTML", () => {
    const { container } = render(
      <div>{renderMarkdown('<img src=x onerror="alert(1)">')}</div>,
    );
    // Raw HTML <img> is NOT parsed — shown as text.
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("<img");
    expect(container.textContent).toContain("onerror");
  });

  it("downgrades a javascript: link to plain text (no href sink)", () => {
    const { container } = render(
      <div>{renderMarkdown("[click me](javascript:alert(1))")}</div>,
    );
    // No anchor with a javascript: href.
    const anchors = Array.from(container.querySelectorAll("a"));
    expect(anchors.some((a) => /javascript:/i.test(a.getAttribute("href") ?? ""))).toBe(
      false,
    );
    // The raw markdown is preserved as literal text instead.
    expect(container.textContent).toContain("javascript:");
  });

  it("downgrades a data: image URL to plain text (no src sink)", () => {
    const { container } = render(
      <div>{renderMarkdown("![x](data:image/svg+xml,<svg onload=alert(1)>)")}</div>,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("data:image");
  });

  it("rejects a whitespace-obfuscated scheme bypass (java\\tscript:)", () => {
    const { container } = render(
      <div>{renderMarkdown("[x](java\tscript:alert(1))")}</div>,
    );
    const anchors = Array.from(container.querySelectorAll("a"));
    expect(anchors.length).toBe(0);
  });

  // GAP-1: vbscript: scheme must not reach an href attribute.
  it("GAP-1: downgrades vbscript: link to plain text (no href sink)", () => {
    const { container } = render(
      <div>{renderMarkdown("[x](vbscript:msgbox(1))")}</div>,
    );
    const anchors = Array.from(container.querySelectorAll("a"));
    expect(anchors.some((a) => /vbscript:/i.test(a.getAttribute("href") ?? ""))).toBe(false);
    // Raw markdown text is visible (not silently swallowed).
    expect(container.textContent).toContain("vbscript:");
  });

  // GAP-2: protocol-relative URLs (//evil.com) must not become live hrefs.
  it("GAP-2: downgrades protocol-relative URL to plain text (not a live link)", () => {
    const { container } = render(
      <div>{renderMarkdown("[x](//evil.com)")}</div>,
    );
    const anchors = Array.from(container.querySelectorAll("a"));
    // Either no anchor at all, or no anchor whose href is protocol-relative.
    expect(anchors.some((a) => /^\/\//.test(a.getAttribute("href") ?? ""))).toBe(false);
  });

  // GAP-4: raw <svg onload=…> in body must be escaped text, never a real element.
  it("GAP-4: raw <svg onload=alert(1)> is escaped literal text, not a DOM element", () => {
    const { container } = render(
      <div>{renderMarkdown("<svg onload=alert(1)>")}</div>,
    );
    expect(container.querySelector("svg")).toBeNull();
    expect(container.textContent).toContain("<svg");
    expect(container.textContent).toContain("onload");
  });

  // GAP-5: autolink <javascript:alert(1)> must not produce a javascript: href.
  it("GAP-5: autolink <javascript:alert(1)> produces no live javascript: href", () => {
    const { container } = render(
      <div>{renderMarkdown("<javascript:alert(1)>")}</div>,
    );
    const anchors = Array.from(container.querySelectorAll("a"));
    expect(anchors.some((a) => /javascript:/i.test(a.getAttribute("href") ?? ""))).toBe(false);
  });

  // GAP-6: body_markdown=false path — raw <script> must be escaped plain text.
  // TaskComments renders non-markdown bodies as <p className="whitespace-pre-wrap">{body}</p>.
  // We replicate that exact path here.
  it("GAP-6: non-markdown body with <script>alert(1)</script> renders as literal text, no script element", () => {
    const malicious = "<script>alert(1)</script>";
    const { container } = render(
      <div>
        <p className="whitespace-pre-wrap break-words">{malicious}</p>
      </div>,
    );
    // React JSX text content is always escaped — no real script element.
    expect(container.querySelector("script")).toBeNull();
    // The text is present verbatim (escaped by React, visible as characters).
    expect(container.textContent).toBe(malicious);
  });

  // NIT (SEC-NIT): userinfo component in authority (https://user@evil.com) must not produce a live link.
  it("NIT: userinfo URL https://trusted.com:p@evil.com/ is not a live link (phishing assist guard)", () => {
    const { container } = render(
      <div>{renderMarkdown("[x](https://trusted.com:p@evil.com/)")}</div>,
    );
    const anchors = Array.from(container.querySelectorAll("a"));
    // Must not produce ANY anchor pointing to the userinfo URL.
    expect(anchors.some((a) => /@/.test(a.getAttribute("href") ?? ""))).toBe(false);
    // The raw text remains visible (not silently dropped).
    expect(container.textContent).toContain("evil.com");
  });
});

describe("safeMarkdown — safe rendering", () => {
  it("renders an http link as a real anchor with safe rel/target", () => {
    const { container } = render(
      <div>{renderMarkdown("see [docs](https://example.com/x)")}</div>,
    );
    const a = container.querySelector("a");
    expect(a).not.toBeNull();
    expect(a?.getAttribute("href")).toBe("https://example.com/x");
    expect(a?.getAttribute("target")).toBe("_blank");
    expect(a?.getAttribute("rel")).toContain("noopener");
    expect(a?.textContent).toBe("docs");
  });

  it("renders an https image with the given src + alt", () => {
    const { container } = render(
      <div>{renderMarkdown("![a cat](https://example.com/cat.png)")}</div>,
    );
    const img = container.querySelector("img");
    expect(img).not.toBeNull();
    expect(img?.getAttribute("src")).toBe("https://example.com/cat.png");
    expect(img?.getAttribute("alt")).toBe("a cat");
  });

  it("renders a fenced code block as <pre><code> with the raw content escaped", () => {
    const { container } = render(
      <div>{renderMarkdown("```js\nconst x = `<b>` + 1;\n```")}</div>,
    );
    const pre = container.querySelector("pre[data-md-code]");
    expect(pre).not.toBeNull();
    expect(pre?.querySelector("code")).not.toBeNull();
    // The <b> inside the fence is literal text, not a bold element.
    expect(container.querySelector("pre b")).toBeNull();
    expect(pre?.textContent).toContain("<b>");
  });

  it("renders bold + inline code in a paragraph", () => {
    const { container } = render(
      <div>{renderMarkdown("a **bold** word and `code`")}</div>,
    );
    expect(container.querySelector("strong")?.textContent).toBe("bold");
    expect(container.querySelector("code")?.textContent).toBe("code");
  });

  it("renders unordered and ordered lists", () => {
    const { container: ul } = render(
      <div>{renderMarkdown("- one\n- two")}</div>,
    );
    expect(ul.querySelectorAll("ul li").length).toBe(2);

    const { container: ol } = render(
      <div>{renderMarkdown("1. first\n2. second")}</div>,
    );
    expect(ol.querySelectorAll("ol li").length).toBe(2);
  });

  it("renders a heading at a downshifted level (no h1/h2 injection)", () => {
    const { container } = render(<div>{renderMarkdown("# Title")}</div>);
    // Level 1 markdown maps to h3 (we never emit h1/h2 inside the drawer).
    expect(container.querySelector("h3")?.textContent).toBe("Title");
    expect(container.querySelector("h1")).toBeNull();
  });
});
