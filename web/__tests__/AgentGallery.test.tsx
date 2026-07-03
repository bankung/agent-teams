// Component tests for the agent gallery — Kanban #1017 AC[1][2].
//
// Strategy: AgentGallery + AgentCard are prop-driven (the page Server-Component-
// fetches the listing; the client shell filters/sorts purely client-side). So
// these tests render with a fixed mocked listing — NO async fetch, therefore no
// findBy*/waitFor needed for the render itself (deterministic synchronous DOM).
// Interaction (chip click / sort change) uses fireEvent which is synchronous and
// flushed before the following assertion.
//
// Coverage:
//   - grid renders one card per agent, with the data-* test hooks
//   - domain filter chip narrows the grid (assert card count)
//   - model filter chip narrows the grid (assert card count)
//   - has-hooks filter narrows the grid
//   - filters AND-compose
//   - sort=name / sort=domain change card order
//   - invalid agent is visibly marked (data-agent-valid=false + inline error)
//   - empty-listing → grid-empty handled by the PAGE; here we assert the
//     "no matches" state when filters exclude everything

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import type { AgentSummary, AgentValidationError, ToolChip } from "@/lib/api";

// next/link → plain <a> so the cards render without a Next.js router context
// (matches the convention in CalendarView / Board tests).
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

import { AgentGallery } from "@/components/AgentGallery";

function agent(over: Partial<AgentSummary> = {}): AgentSummary {
  return {
    name: "dev-frontend",
    description: "Frontend dev agent.",
    model: "sonnet",
    tools_summary: "All tools",
    tool_count: null,
    hook_count: 0,
    source_file: "dev-frontend.md",
    domain: "dev",
    valid: true,
    validation_errors: [],
    tool_chips: [],
    ...over,
  };
}

const ERR: AgentValidationError = {
  file: "broken.md",
  line: 3,
  field: "model",
  message: "bad model enum 'opux'",
  severity: "error",
};

// A representative spread across domains / tiers / hooks / validity.
function listing(): AgentSummary[] {
  return [
    agent({ name: "dev-backend", domain: "dev", model: "opus", hook_count: 2 }),
    agent({ name: "dev-frontend", domain: "dev", model: "sonnet", hook_count: 0 }),
    agent({ name: "novel-writer", domain: "novel", model: "opus", hook_count: 0 }),
    agent({
      name: "secretary",
      domain: "secretary",
      model: null,
      hook_count: 1,
    }),
    agent({
      name: "broken-agent",
      domain: "other",
      model: "haiku",
      hook_count: 0,
      valid: false,
      source_file: "broken.md",
      validation_errors: [ERR],
    }),
  ];
}

function cardNames(): string[] {
  return Array.from(
    document.querySelectorAll("[data-agent-card]"),
  ).map((el) => el.getAttribute("data-agent-name") ?? "");
}

function chip(kind: string, value: string): HTMLElement {
  const el = document.querySelector(
    `[data-filter-chip][data-filter-kind="${kind}"][data-filter-value="${value}"]`,
  );
  if (!el) throw new Error(`chip ${kind}=${value} not found`);
  return el as HTMLElement;
}

describe("AgentGallery — grid render", () => {
  it("renders one card per agent with the test hooks", () => {
    render(<AgentGallery agents={listing()} />);
    const cards = document.querySelectorAll("[data-agent-card]");
    expect(cards.length).toBe(5);
    // data-* hooks present on a card.
    const backend = document.querySelector('[data-agent-name="dev-backend"]')!;
    expect(backend.getAttribute("data-agent-domain")).toBe("dev");
    expect(backend.getAttribute("data-agent-valid")).toBe("true");
    // count strip reflects total.
    expect(screen.getByText("5 of 5")).toBeInTheDocument();
  });

  it("default sort is by name (ascending)", () => {
    render(<AgentGallery agents={listing()} />);
    expect(cardNames()).toEqual([
      "broken-agent",
      "dev-backend",
      "dev-frontend",
      "novel-writer",
      "secretary",
    ]);
  });
});

describe("AgentGallery — filters", () => {
  it("domain chip narrows the grid to that domain", () => {
    render(<AgentGallery agents={listing()} />);
    fireEvent.click(chip("domain", "dev"));
    const names = cardNames();
    expect(names).toEqual(["dev-backend", "dev-frontend"]);
    expect(screen.getByText("2 of 5")).toBeInTheDocument();
  });

  it("model chip narrows the grid to that tier", () => {
    render(<AgentGallery agents={listing()} />);
    fireEvent.click(chip("model", "opus"));
    expect(cardNames().sort()).toEqual(["dev-backend", "novel-writer"]);
  });

  it("'default' model chip narrows to null-model agents", () => {
    render(<AgentGallery agents={listing()} />);
    fireEvent.click(chip("model", "default"));
    expect(cardNames()).toEqual(["secretary"]);
  });

  it("has-hooks chip narrows to agents with >=1 hook", () => {
    render(<AgentGallery agents={listing()} />);
    fireEvent.click(chip("has-hooks", "true"));
    expect(cardNames().sort()).toEqual(["dev-backend", "secretary"]);
  });

  it("filters AND-compose (domain=dev AND has-hooks)", () => {
    render(<AgentGallery agents={listing()} />);
    fireEvent.click(chip("domain", "dev"));
    fireEvent.click(chip("has-hooks", "true"));
    // dev-backend has 2 hooks; dev-frontend has 0 → only backend.
    expect(cardNames()).toEqual(["dev-backend"]);
  });

  it("toggling a chip off restores the full grid", () => {
    render(<AgentGallery agents={listing()} />);
    const devChip = chip("domain", "dev");
    fireEvent.click(devChip);
    expect(cardNames().length).toBe(2);
    fireEvent.click(devChip);
    expect(cardNames().length).toBe(5);
  });

  it("chip counts update against other active filters", () => {
    render(<AgentGallery agents={listing()} />);
    // Activate domain=dev; the has-hooks chip count should now reflect dev-only.
    fireEvent.click(chip("domain", "dev"));
    const hooksChip = chip("has-hooks", "true");
    // Within dev (backend=2 hooks, frontend=0) exactly 1 has hooks.
    expect(within(hooksChip).getByText("1")).toBeInTheDocument();
  });

  it("shows the no-match state when filters exclude everything", () => {
    render(<AgentGallery agents={listing()} />);
    // novel domain has only opus agents; combine novel + haiku → empty.
    fireEvent.click(chip("domain", "novel"));
    fireEvent.click(chip("model", "haiku"));
    expect(
      document.querySelector("[data-agent-grid-empty]"),
    ).not.toBeNull();
    expect(document.querySelectorAll("[data-agent-card]").length).toBe(0);
  });
});

describe("AgentGallery — invalid marking", () => {
  it("marks invalid agents and surfaces the first error inline", () => {
    render(<AgentGallery agents={listing()} />);
    const broken = document.querySelector(
      '[data-agent-name="broken-agent"]',
    )!;
    expect(broken.getAttribute("data-agent-valid")).toBe("false");
    expect(broken.querySelector("[data-agent-invalid]")).not.toBeNull();
    // First error text surfaced inline (file:line — message).
    expect(broken.textContent).toContain("broken.md:3");
    expect(broken.textContent).toContain("bad model enum 'opux'");
  });
});

// Kanban #1021 — tool-scope risk chips on the gallery card.
function toolChip(name: string, risk_class: ToolChip["risk_class"]): ToolChip {
  return { name, risk_class };
}

describe("AgentGallery — tool-scope risk chips (AC3/AC4/AC5)", () => {
  it("renders chips in frontmatter order, caps at 4, and shows a +N more overflow", () => {
    const chips: ToolChip[] = [
      toolChip("Read", "read-only"),
      toolChip("Grep", "read-only"),
      toolChip("Glob", "read-only"),
      toolChip("Edit", "write-edit"),
      toolChip("Write", "write-edit"),
      toolChip("Bash", "shell-or-destructive"),
    ];
    render(
      <AgentGallery agents={[agent({ name: "dev-frontend", tool_chips: chips })]} />,
    );
    const row = document.querySelector('[data-agent-name="dev-frontend"] [data-agent-tool-chips]')!;
    const shownChips = Array.from(row.querySelectorAll("[data-tool-chip]")).map(
      (el) => el.textContent,
    );
    // Capped at 4 (first 4 in frontmatter order), overflow = remaining 2.
    expect(shownChips).toEqual(["Read", "Grep", "Glob", "Edit"]);
    const overflow = row.querySelector("[data-tool-chip-overflow]");
    expect(overflow).not.toBeNull();
    expect(overflow?.textContent).toBe("+2 more");
    // Overflow title lists the remaining tool names.
    expect(overflow?.getAttribute("title")).toBe("Write, Bash");
  });

  it("does not show a +N more affordance when chips fit within the cap", () => {
    const chips: ToolChip[] = [
      toolChip("Read", "read-only"),
      toolChip("Grep", "read-only"),
    ];
    render(
      <AgentGallery agents={[agent({ name: "dev-frontend", tool_chips: chips })]} />,
    );
    const row = document.querySelector('[data-agent-name="dev-frontend"] [data-agent-tool-chips]')!;
    expect(row.querySelectorAll("[data-tool-chip]").length).toBe(2);
    expect(row.querySelector("[data-tool-chip-overflow]")).toBeNull();
  });

  it("card risk badge reflects the HIGHEST-risk chip in a mixed set", () => {
    const chips: ToolChip[] = [
      toolChip("Read", "read-only"),
      toolChip("WebFetch", "external"),
      toolChip("Edit", "write-edit"),
    ];
    render(
      <AgentGallery agents={[agent({ name: "dev-frontend", tool_chips: chips })]} />,
    );
    const card = document.querySelector('[data-agent-name="dev-frontend"]')!;
    const badge = card.querySelector("[data-agent-risk-badge]");
    expect(badge).not.toBeNull();
    // write-edit outranks read-only and external in the mixed set.
    expect(badge?.getAttribute("data-agent-risk-badge")).toBe("write-edit");
  });

  it("card risk badge is shell-or-destructive for an All-tools agent", () => {
    const chips: ToolChip[] = [toolChip("All tools", "shell-or-destructive")];
    render(
      <AgentGallery agents={[agent({ name: "dev-frontend", tool_chips: chips })]} />,
    );
    const card = document.querySelector('[data-agent-name="dev-frontend"]')!;
    const badge = card.querySelector("[data-agent-risk-badge]");
    expect(badge).not.toBeNull();
    expect(badge?.getAttribute("data-agent-risk-badge")).toBe(
      "shell-or-destructive",
    );
  });

  it("omits the risk badge and chip row when tool_chips is empty", () => {
    render(
      <AgentGallery agents={[agent({ name: "dev-frontend", tool_chips: [] })]} />,
    );
    const card = document.querySelector('[data-agent-name="dev-frontend"]')!;
    expect(card.querySelector("[data-agent-risk-badge]")).toBeNull();
    expect(card.querySelector("[data-agent-tool-chips]")).toBeNull();
  });

  it("each chip carries a title tooltip with the tool name and risk explanation", () => {
    const chips: ToolChip[] = [toolChip("Bash", "shell-or-destructive")];
    render(
      <AgentGallery agents={[agent({ name: "dev-frontend", tool_chips: chips })]} />,
    );
    const chipEl = document.querySelector(
      '[data-agent-name="dev-frontend"] [data-tool-chip]',
    )!;
    const title = chipEl.getAttribute("title") ?? "";
    expect(title).toContain("Bash");
    expect(title).toContain("runs an arbitrary shell command");
    expect(title).toContain("Shell or destructive");
  });

  it("falls back to a generic tooltip line for an unknown tool name", () => {
    const chips: ToolChip[] = [toolChip("SomeFutureMcpTool", "external")];
    render(
      <AgentGallery agents={[agent({ name: "dev-frontend", tool_chips: chips })]} />,
    );
    const chipEl = document.querySelector(
      '[data-agent-name="dev-frontend"] [data-tool-chip]',
    )!;
    const title = chipEl.getAttribute("title") ?? "";
    expect(title).toContain("SomeFutureMcpTool");
    expect(title).toContain("a tool granted to this agent");
  });
});
