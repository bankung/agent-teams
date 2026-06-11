// Component tests for LlmSpendSection — Kanban #2135.
//
// Strategy: mock @/lib/api (getDailyUsage) and assert on the two critical data
// shapes: zero-data (totals = "0.0000") and nonzero-data (summary + per-provider
// breakdown). Error state asserts the quiet fallback text.
//
// Determinism: async-fetch assertions use findBy*/waitFor (never sync
// querySelector on post-fetch state), per the project's FE determinism rule.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, configure } from "@testing-library/react";
import type { DailyUsageResponse } from "@/lib/api";

configure({ asyncUtilTimeout: 5000 });

const mockGetDailyUsage = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getDailyUsage: (...args: Parameters<typeof actual.getDailyUsage>) =>
      mockGetDailyUsage(...args),
  };
});

// Imported AFTER mock registers.
import { LlmSpendSection } from "@/components/LlmSpendSection";

// Freeze "today" so todayProviderTotals matches the row date in fixtures.
const FIXED_TODAY = "2026-06-10";

function zeroResponse(): DailyUsageResponse {
  return {
    days: 31,
    rows: [],
    total_today_usd: "0.0000",
    total_month_usd: "0.0000",
    today: FIXED_TODAY,
  };
}

function nonzeroResponse(): DailyUsageResponse {
  return {
    days: 31,
    rows: [
      {
        date: FIXED_TODAY,
        provider: "anthropic",
        model: "claude-sonnet-4-6",
        input_tokens: 1000,
        output_tokens: 200,
        cost_usd: "0.0150",
      },
      {
        date: FIXED_TODAY,
        provider: "google",
        model: "gemini-2.5-flash-lite",
        input_tokens: 500,
        output_tokens: 50,
        cost_usd: "0.0030",
      },
    ],
    total_today_usd: "0.0180",
    total_month_usd: "1.2345",
    today: FIXED_TODAY,
  };
}

beforeEach(() => {
  mockGetDailyUsage.mockReset();
  // Freeze Date so todayProviderTotals picks the right rows.
  vi.setSystemTime(new Date(`${FIXED_TODAY}T12:00:00Z`));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("LlmSpendSection — zero data", () => {
  it("renders $0.0000 for today and this month without crashing", async () => {
    mockGetDailyUsage.mockResolvedValue(zeroResponse());
    render(<LlmSpendSection />);

    // Wait for loading to resolve.
    await waitFor(() =>
      expect(screen.queryByText(/Loading/i)).not.toBeInTheDocument(),
    );

    const section = document.querySelector("[data-llm-spend]");
    expect(section).not.toBeNull();

    // Both totals display as $0.0000.
    expect(section?.textContent).toContain("$0.0000");
    // No provider breakdown when rows is empty.
    expect(
      screen.queryByRole("list", { name: /today.*provider/i }),
    ).toBeNull();
  });
});

describe("LlmSpendSection — nonzero data", () => {
  it("renders summary line with today + month totals", async () => {
    mockGetDailyUsage.mockResolvedValue(nonzeroResponse());
    render(<LlmSpendSection />);

    // Summary line appears once data loads.
    expect(await screen.findByText(/\$0\.0180/)).toBeInTheDocument();
    expect(screen.getByText(/\$1\.2345/)).toBeInTheDocument();
  });

  it("renders per-provider breakdown for today", async () => {
    mockGetDailyUsage.mockResolvedValue(nonzeroResponse());
    render(<LlmSpendSection />);

    // Provider list renders after fetch.
    const list = await screen.findByRole("list", {
      name: /today.*spend.*provider/i,
    });
    expect(list.textContent).toContain("anthropic");
    expect(list.textContent).toContain("$0.0150");
    expect(list.textContent).toContain("google");
    expect(list.textContent).toContain("$0.0030");
  });
});

describe("LlmSpendSection — server date wins over client date", () => {
  it("uses data.today for breakdown even when client clock is on a different date", async () => {
    // Server says today is SERVER_TODAY; client clock is set to a different day.
    const SERVER_TODAY = "2026-06-09"; // one day behind client
    vi.setSystemTime(new Date(`${FIXED_TODAY}T00:30:00Z`)); // client = 2026-06-10

    const response: DailyUsageResponse = {
      days: 31,
      rows: [
        {
          date: SERVER_TODAY, // row dated server's today, NOT client's today
          provider: "anthropic",
          model: "claude-sonnet-4-6",
          input_tokens: 800,
          output_tokens: 100,
          cost_usd: "0.0099",
        },
      ],
      total_today_usd: "0.0099",
      total_month_usd: "0.0099",
      today: SERVER_TODAY, // server asserts SERVER_TODAY as "today"
    };
    mockGetDailyUsage.mockResolvedValue(response);
    render(<LlmSpendSection />);

    // Provider breakdown should appear — it would be absent if client date
    // (FIXED_TODAY = 2026-06-10) were used instead of SERVER_TODAY (2026-06-09).
    const list = await screen.findByRole("list", {
      name: /today.*spend.*provider/i,
    });
    expect(list.textContent).toContain("anthropic");
    expect(list.textContent).toContain("$0.0099");
  });
});

describe("LlmSpendSection — error state", () => {
  it("renders quiet fallback text on fetch error without crashing", async () => {
    mockGetDailyUsage.mockRejectedValue(new Error("Network error"));
    render(<LlmSpendSection />);

    expect(await screen.findByText(/spend unavailable/i)).toBeInTheDocument();
    // Section is still present in the DOM.
    expect(document.querySelector("[data-llm-spend]")).not.toBeNull();
  });
});
