// Component tests for the agent detail body — Kanban #1017 AC[3].
//
// AgentDetail is prop-driven (the page Server-Component-fetches the detail).
// Synchronous render — no async fetch, no findBy*/waitFor needed. next/link is
// mocked to a plain <a> so spawn deep-links render their href.
//
// Coverage:
//   - full description renders
//   - metadata badges render (tier + domain)
//   - raw frontmatter renders inside the <pre>
//   - recent spawns list renders with the board deep-link href
//     (/p/{project_name}?task={task_id})
//   - spawn rows carry data-spawn-row + data-task-id
//   - validation diagnostics render when present
//   - empty-spawns state renders when there are none

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { AgentDetail as AgentDetailType, ToolChip } from "@/lib/api";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: {
    href: string;
    children: React.ReactNode;
    [k: string]: unknown;
  }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import { AgentDetail } from "@/components/AgentDetail";

function detail(over: Partial<AgentDetailType> = {}): AgentDetailType {
  return {
    name: "dev-sr-frontend",
    description: "Senior FE agent.",
    model: "opus",
    tools_summary: "All tools",
    tool_count: null,
    hook_count: 1,
    source_file: "dev-sr-frontend.md",
    domain: "dev",
    valid: true,
    validation_errors: [],
    tool_chips: [],
    raw_frontmatter: "name: dev-sr-frontend\nmodel: opus\ndescription: |\n  Senior FE.",
    full_description:
      "You are a senior frontend developer in a Next.js + React + TypeScript stack.",
    spawns: [
      {
        task_id: 1017,
        project_id: 1,
        project_name: "agent-teams",
        model: "opus",
        at: "2026-06-12T08:00:00Z",
      },
      {
        task_id: 943,
        project_id: 1,
        project_name: "agent-teams",
        model: null,
        at: "2026-06-10T08:00:00Z",
      },
    ],
    ...over,
  };
}

describe("AgentDetail — body render", () => {
  it("renders the full description", () => {
    render(<AgentDetail agent={detail()} />);
    expect(
      screen.getByText(/senior frontend developer in a Next\.js/i),
    ).toBeInTheDocument();
  });

  it("renders the tier + domain badges", () => {
    render(<AgentDetail agent={detail()} />);
    expect(document.querySelector('[data-agent-tier="opus"]')).not.toBeNull();
    expect(
      document.querySelector('[data-agent-domain-chip="dev"]'),
    ).not.toBeNull();
  });

  it("renders the raw frontmatter inside the <pre>", () => {
    render(<AgentDetail agent={detail()} />);
    const pre = document.querySelector("[data-agent-frontmatter]");
    expect(pre).not.toBeNull();
    expect(pre?.tagName).toBe("PRE");
    expect(pre?.textContent).toContain("name: dev-sr-frontend");
    expect(pre?.textContent).toContain("model: opus");
  });
});

describe("AgentDetail — recent spawns", () => {
  it("renders one spawn row per spawn with the board deep-link", () => {
    render(<AgentDetail agent={detail()} />);
    const rows = document.querySelectorAll("[data-spawn-row]");
    expect(rows.length).toBe(2);

    const first = document.querySelector('[data-spawn-row][data-task-id="1017"]');
    expect(first).not.toBeNull();
    const link = first?.querySelector("a");
    // Deep-link shape: /p/{project_name}?task={task_id}.
    expect(link?.getAttribute("href")).toBe("/p/agent-teams?task=1017");
    expect(first?.textContent).toContain("#1017");
    expect(first?.textContent).toContain("agent-teams");
  });

  it("shows the empty-spawns state when there are none", () => {
    render(<AgentDetail agent={detail({ spawns: [] })} />);
    expect(
      document.querySelector("[data-agent-spawns-empty]"),
    ).not.toBeNull();
    expect(document.querySelectorAll("[data-spawn-row]").length).toBe(0);
  });

  it("renders '—' for a spawn row with at: null and does not crash", () => {
    const d = detail({
      spawns: [
        {
          task_id: 999,
          project_id: 1,
          project_name: "agent-teams",
          model: null,
          at: null,
        },
      ],
    });
    render(<AgentDetail agent={d} />);
    const row = document.querySelector('[data-spawn-row][data-task-id="999"]');
    expect(row).not.toBeNull();
    expect(row?.textContent).toContain("—");
  });
});

describe("AgentDetail — diagnostics", () => {
  it("renders validation diagnostics when present", () => {
    const d = detail({
      valid: false,
      validation_errors: [
        {
          file: "dev-sr-frontend.md",
          line: 4,
          field: "model",
          message: "bad model enum 'opux'",
          severity: "error",
        },
        {
          file: "dev-sr-frontend.md",
          line: 5,
          field: "email_actions",
          message: "unknown frontmatter key",
          severity: "warning",
        },
      ],
    });
    render(<AgentDetail agent={d} />);
    const section = document.querySelector("[data-agent-diagnostics]");
    expect(section).not.toBeNull();
    expect(
      document.querySelectorAll("[data-agent-diagnostic]").length,
    ).toBe(2);
    expect(
      document.querySelector('[data-agent-diagnostic][data-severity="error"]'),
    ).not.toBeNull();
    // invalid marker present on the detail article.
    expect(document.querySelector("[data-agent-invalid]")).not.toBeNull();
  });

  it("omits the diagnostics section when there are no errors", () => {
    render(<AgentDetail agent={detail()} />);
    expect(document.querySelector("[data-agent-diagnostics]")).toBeNull();
  });
});

// Kanban #1021 AC6 — tools grouped by risk class on the detail page.
function toolChip(name: string, risk_class: ToolChip["risk_class"]): ToolChip {
  return { name, risk_class };
}

describe("AgentDetail — tool scope groups (AC6)", () => {
  it("renders one group section per risk class present, in low->high order", () => {
    const chips: ToolChip[] = [
      toolChip("Bash", "shell-or-destructive"),
      toolChip("Read", "read-only"),
      toolChip("Edit", "write-edit"),
      toolChip("Grep", "read-only"),
    ];
    render(<AgentDetail agent={detail({ tool_chips: chips })} />);
    const groups = document.querySelectorAll("[data-tool-group]");
    // 3 distinct classes present: read-only, write-edit, shell-or-destructive.
    expect(groups.length).toBe(3);
    const order = Array.from(groups).map((g) =>
      g.getAttribute("data-tool-group-risk"),
    );
    expect(order).toEqual(["read-only", "write-edit", "shell-or-destructive"]);

    // read-only group contains both Read and Grep.
    const readGroup = document.querySelector(
      '[data-tool-group][data-tool-group-risk="read-only"]',
    )!;
    expect(readGroup.textContent).toContain("Read");
    expect(readGroup.textContent).toContain("Grep");
    // Each group carries a 1-line explanation.
    expect(readGroup.textContent).toContain("cannot change files");
  });

  it("omits the tool-scope section entirely when tool_chips is empty", () => {
    render(<AgentDetail agent={detail({ tool_chips: [] })} />);
    expect(document.querySelector("[data-agent-tool-groups]")).toBeNull();
  });

  it("shows the detail-page risk badge driven by the highest-risk chip", () => {
    const chips: ToolChip[] = [
      toolChip("Read", "read-only"),
      toolChip("WebFetch", "external"),
    ];
    render(<AgentDetail agent={detail({ tool_chips: chips })} />);
    const badge = document.querySelector("[data-agent-risk-badge]");
    expect(badge).not.toBeNull();
    expect(badge?.getAttribute("data-agent-risk-badge")).toBe("external");
  });
});
