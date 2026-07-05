// Component tests for SessionCostPanel — Kanban #2735.
//
// Strategy: the INITIAL render is prop-driven (no internal fetch) so the
// list / expand / empty-state tests are fully synchronous + deterministic.
// The "Load more" test injects a mock `fetcher` and uses findBy/waitFor for the
// async append (async-fetch RTL races are a known flake class here — #1310).
//
// Coverage:
//   (a) Renders one row per session with short id + formatted cost + cache %.
//   (b) Expanding a row reveals the per-agent breakdown: "Lead" for a null
//       agent_name + the subagent name; both models present.
//   (c) Empty sessions array shows the muted no-session line.
//   (d) event_count is tooltipped as "ledger events … not transcript turns".
//   (e) "Load more" appends the next page (async; findBy/waitFor).

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SessionCostPanel } from "@/components/SessionCostPanel";
import type { UsageSessionsResponse } from "@/lib/api";

// ── fixtures ──────────────────────────────────────────────────────────────────

const TWO_SESSION_DATA: UsageSessionsResponse = {
  limit: 50,
  offset: 0,
  returned: 2,
  total_cost_usd: "12.5000",
  sessions: [
    {
      session_ext_id: "abcdef12-3456-7890-aaaa-bbbbbbbbbbbb",
      total_cost_usd: "10.0000",
      input_tokens: 1000,
      output_tokens: 2000,
      cache_read_input_tokens: 8000,
      cache_creation_input_tokens: 500,
      cache_hit_ratio: 0.8123,
      event_count: 42,
      first_occurred_at: "2026-06-25T10:00:00Z",
      last_occurred_at: "2026-06-25T14:03:00Z",
      agents: [
        {
          agent_name: null, // Lead / main turn
          model: "claude-opus-4-8",
          cost_usd: "7.5000",
          input_tokens: 600,
          output_tokens: 1200,
          cache_read_input_tokens: 6000,
          cache_creation_input_tokens: 300,
          event_count: 30,
        },
        {
          agent_name: "dev-sr-frontend",
          model: "claude-opus-4-8",
          cost_usd: "2.5000",
          input_tokens: 400,
          output_tokens: 800,
          cache_read_input_tokens: 2000,
          cache_creation_input_tokens: 200,
          event_count: 12,
        },
      ],
    },
    {
      session_ext_id: "99887766-5544-3322-1100-ffeeddccbbaa",
      total_cost_usd: "2.5000",
      input_tokens: 300,
      output_tokens: 600,
      cache_read_input_tokens: 1000,
      cache_creation_input_tokens: 100,
      cache_hit_ratio: 0.5,
      event_count: 5,
      first_occurred_at: "2026-06-24T09:00:00Z",
      last_occurred_at: "2026-06-24T09:30:00Z",
      agents: [
        {
          agent_name: null,
          model: "claude-sonnet-4-5",
          cost_usd: "2.5000",
          input_tokens: 300,
          output_tokens: 600,
          cache_read_input_tokens: 1000,
          cache_creation_input_tokens: 100,
          event_count: 5,
        },
      ],
    },
  ],
};

const EMPTY_DATA: UsageSessionsResponse = {
  limit: 50,
  offset: 0,
  returned: 0,
  total_cost_usd: "0.0000",
  sessions: [],
};

// A full page (returned === limit) so the "Load more" button shows.
function makeFullPage(limit: number, offset: number): UsageSessionsResponse {
  const sessions = Array.from({ length: limit }, (_, i) => ({
    session_ext_id: `page-${offset}-sess-${i}-padding-padding`,
    total_cost_usd: "1.0000",
    input_tokens: 10,
    output_tokens: 20,
    cache_read_input_tokens: 5,
    cache_creation_input_tokens: 1,
    cache_hit_ratio: 0.1,
    event_count: 1,
    first_occurred_at: "2026-06-20T00:00:00Z",
    last_occurred_at: "2026-06-20T00:00:00Z",
    agents: [],
  }));
  return { sessions, limit, offset, returned: limit, total_cost_usd: "0.0000" };
}

// ── (a) one row per session ───────────────────────────────────────────────────

describe("SessionCostPanel — session rows", () => {
  it("(a) renders a short id + cost + cache % for each session", () => {
    render(<SessionCostPanel data={TWO_SESSION_DATA} />);

    // Short ids (first 8 chars) present, full id in title.
    expect(screen.getByText("abcdef12")).toBeInTheDocument();
    expect(screen.getByText("99887766")).toBeInTheDocument();

    // Formatted costs (2dp display from 4dp strings).
    expect(screen.getByText("$10.00")).toBeInTheDocument();
    expect(screen.getByText("$2.50")).toBeInTheDocument();

    // cache_hit_ratio 0.8123 → "81.2% cache"; 0.5 → "50.0% cache".
    expect(screen.getByText(/81\.2% cache/)).toBeInTheDocument();
    expect(screen.getByText(/50\.0% cache/)).toBeInTheDocument();
  });

  it("(a) puts the full session id in a title attribute", () => {
    render(<SessionCostPanel data={TWO_SESSION_DATA} />);
    const short = screen.getByText("abcdef12");
    expect(short).toHaveAttribute(
      "title",
      "abcdef12-3456-7890-aaaa-bbbbbbbbbbbb",
    );
  });
});

// ── (b) expand → Lead vs subagent breakdown ──────────────────────────────────

describe("SessionCostPanel — agent drilldown", () => {
  it("(b) agent rows are hidden before the toggle", () => {
    render(<SessionCostPanel data={TWO_SESSION_DATA} />);
    expect(screen.queryByText("Lead")).not.toBeInTheDocument();
    expect(screen.queryByText("dev-sr-frontend")).not.toBeInTheDocument();
  });

  it("(b) expanding a row shows Lead (null agent_name) + the subagent name", () => {
    render(<SessionCostPanel data={TWO_SESSION_DATA} />);

    const expandBtn = screen.getAllByRole("button", {
      name: /expand agent breakdown/i,
    })[0];
    fireEvent.click(expandBtn);

    // null agent_name renders "Lead"; the named subagent renders its name.
    expect(screen.getByText("Lead")).toBeInTheDocument();
    expect(screen.getByText("dev-sr-frontend")).toBeInTheDocument();

    // Per-agent costs + the model both surface in the breakdown.
    expect(screen.getByText("$7.50")).toBeInTheDocument();
    expect(screen.getAllByText("claude-opus-4-8").length).toBeGreaterThan(0);
  });

  it("(b) the drilldown toggle flips aria-expanded", () => {
    render(<SessionCostPanel data={TWO_SESSION_DATA} />);
    const expandBtn = screen.getAllByRole("button", {
      name: /expand agent breakdown/i,
    })[0];
    expect(expandBtn).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(expandBtn);
    expect(expandBtn).toHaveAttribute("aria-expanded", "true");
  });
});

// ── (c) empty state ───────────────────────────────────────────────────────────

describe("SessionCostPanel — empty state", () => {
  it("(c) shows the no-session line when sessions is empty", () => {
    render(<SessionCostPanel data={EMPTY_DATA} />);
    expect(
      screen.getByText("No session cost recorded yet."),
    ).toBeInTheDocument();
  });

  it("(c) renders no session rows when empty", () => {
    render(<SessionCostPanel data={EMPTY_DATA} />);
    expect(screen.queryByText(/% cache/)).not.toBeInTheDocument();
  });
});

// ── (d) event_count labeled as ledger events (#2728) ─────────────────────────

describe("SessionCostPanel — ledger-events labeling", () => {
  it("(d) event_count is tooltipped as ledger events, not transcript turns", () => {
    render(<SessionCostPanel data={TWO_SESSION_DATA} />);
    const eventsCell = screen.getByText("42 events");
    expect(eventsCell).toHaveAttribute(
      "title",
      "42 ledger events (usage_events rows, not transcript turns)",
    );
  });
});

// ── (e) Load more (async — findBy / waitFor) ─────────────────────────────────

describe("SessionCostPanel — pagination", () => {
  it("(e) does NOT show Load more when returned < limit", () => {
    // TWO_SESSION_DATA: returned=2 < limit=50 → no more pages.
    render(<SessionCostPanel data={TWO_SESSION_DATA} />);
    expect(
      screen.queryByRole("button", { name: /load more/i }),
    ).not.toBeInTheDocument();
  });

  it("(e) appends the next page on Load more (async)", async () => {
    // Page 1 is a full page (returned === limit) → Load more is offered.
    const PAGE_LIMIT = 2;
    const page1 = makeFullPage(PAGE_LIMIT, 0);
    // Page 2: one row, returned < limit → Load more disappears after.
    const page2: UsageSessionsResponse = {
      limit: PAGE_LIMIT,
      offset: PAGE_LIMIT,
      returned: 1,
      total_cost_usd: "0.0000",
      sessions: [
        {
          session_ext_id: "nextpage-1111-2222-3333-444455556666",
          total_cost_usd: "9.0000",
          input_tokens: 1,
          output_tokens: 1,
          cache_read_input_tokens: 0,
          cache_creation_input_tokens: 0,
          cache_hit_ratio: 0,
          event_count: 1,
          first_occurred_at: "2026-06-19T00:00:00Z",
          last_occurred_at: "2026-06-19T00:00:00Z",
          agents: [],
        },
      ],
    };
    const fetcher = vi.fn().mockResolvedValue(page2);

    render(
      <SessionCostPanel data={page1} projectId={1} fetcher={fetcher} />,
    );

    const loadMore = screen.getByRole("button", { name: /load more/i });
    fireEvent.click(loadMore);

    // Async append: wait for the page-2 session id (short) to appear.
    expect(await screen.findByText("nextpage")).toBeInTheDocument();

    // Fetcher called with the next offset (offset + limit) + scoped project.
    expect(fetcher).toHaveBeenCalledWith({
      projectId: 1,
      limit: PAGE_LIMIT,
      offset: PAGE_LIMIT,
    });

    // returned(1) < limit(2) → Load more is gone after the append.
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: /load more/i }),
      ).not.toBeInTheDocument(),
    );
  });
});
