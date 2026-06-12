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
import type { AgentSummary, AgentValidationError } from "@/lib/api";

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

describe("AgentGallery — sort", () => {
  it("sort=domain orders by domain then name", () => {
    render(<AgentGallery agents={listing()} />);
    fireEvent.change(document.querySelector("[data-agent-sort]")!, {
      target: { value: "domain" },
    });
    expect(cardNames()).toEqual([
      "dev-backend", // dev
      "dev-frontend", // dev
      "novel-writer", // novel
      "broken-agent", // other
      "secretary", // secretary
    ]);
  });

  it("sort=model orders heaviest tier first, default last", () => {
    render(<AgentGallery agents={listing()} />);
    fireEvent.change(document.querySelector("[data-agent-sort]")!, {
      target: { value: "model" },
    });
    const names = cardNames();
    // opus agents first (dev-backend, novel-writer), then sonnet (dev-frontend),
    // then haiku (broken-agent), then null-model "default" (secretary) last.
    expect(names[0]).toBe("dev-backend");
    expect(names[1]).toBe("novel-writer");
    expect(names[2]).toBe("dev-frontend");
    expect(names[3]).toBe("broken-agent");
    expect(names[4]).toBe("secretary");
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
