// Unit tests for lib/calendarDates.ts — pure date math.
// All functions are side-effect-free; no mocking needed except where we want
// to pin "today" (todayKey / currentYearMonth accept an optional `now` arg).

import { describe, it, expect } from "vitest";
import {
  dateKey,
  todayKey,
  parseMonthParam,
  currentYearMonth,
  monthParamKey,
  monthLabel,
  addMonths,
  daysInMonth,
  weekdayOfFirst,
  buildMonthGrid,
  monthRangeKeys,
  normalizeDateOnly,
  epochDay,
  epochDayToKey,
  monthTickLabel,
  startOfMonthEpochDay,
  nextMonthEpochDay,
  WEEKDAY_LABELS,
} from "@/lib/calendarDates";

// ---------------------------------------------------------------------------
// dateKey
// ---------------------------------------------------------------------------
describe("dateKey", () => {
  it("formats a normal date", () => {
    expect(dateKey(2026, 5, 3)).toBe("2026-06-03"); // month0=5 → June
  });
  it("zero-pads month and day", () => {
    expect(dateKey(2026, 0, 1)).toBe("2026-01-01"); // Jan 1
  });
  it("December (month0=11)", () => {
    expect(dateKey(2025, 11, 31)).toBe("2025-12-31");
  });
});

// ---------------------------------------------------------------------------
// todayKey
// ---------------------------------------------------------------------------
describe("todayKey", () => {
  it("returns the local civil date of the supplied Date", () => {
    // Use a fixed local date: 2026-03-15
    const now = new Date(2026, 2, 15); // getFullYear=2026, getMonth=2, getDate=15
    expect(todayKey(now)).toBe("2026-03-15");
  });
  it("uses local-date methods, not UTC (e.g. UTC-8 just-past midnight)", () => {
    // Construct a Date where local=Jan-1 but UTC=Dec-31
    // We simulate this by passing in a Date object whose local getters give 2026-01-01.
    const localJan1 = new Date(2026, 0, 1);
    expect(todayKey(localJan1)).toBe("2026-01-01");
  });
});

// ---------------------------------------------------------------------------
// parseMonthParam
// ---------------------------------------------------------------------------
describe("parseMonthParam", () => {
  it("parses a valid YYYY-MM string", () => {
    expect(parseMonthParam("2026-06")).toEqual({ year: 2026, month0: 5 });
  });
  it("January (month 01)", () => {
    expect(parseMonthParam("2026-01")).toEqual({ year: 2026, month0: 0 });
  });
  it("December (month 12)", () => {
    expect(parseMonthParam("2026-12")).toEqual({ year: 2026, month0: 11 });
  });
  it("returns null for null input", () => {
    expect(parseMonthParam(null)).toBeNull();
  });
  it("returns null for undefined", () => {
    expect(parseMonthParam(undefined)).toBeNull();
  });
  it("returns null for empty string", () => {
    expect(parseMonthParam("")).toBeNull();
  });
  it("returns null for month 00 (out of range)", () => {
    expect(parseMonthParam("2026-00")).toBeNull();
  });
  it("returns null for month 13 (out of range)", () => {
    expect(parseMonthParam("2026-13")).toBeNull();
  });
  it("returns null for a date string YYYY-MM-DD (too long)", () => {
    expect(parseMonthParam("2026-06-01")).toBeNull();
  });
  it("returns null for malformed string", () => {
    expect(parseMonthParam("June 2026")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// currentYearMonth
// ---------------------------------------------------------------------------
describe("currentYearMonth", () => {
  it("extracts local year + month0 from the supplied date", () => {
    const d = new Date(2026, 5, 3); // June 2026
    expect(currentYearMonth(d)).toEqual({ year: 2026, month0: 5 });
  });
});

// ---------------------------------------------------------------------------
// monthParamKey
// ---------------------------------------------------------------------------
describe("monthParamKey", () => {
  it("converts YearMonth back to YYYY-MM", () => {
    expect(monthParamKey({ year: 2026, month0: 5 })).toBe("2026-06");
  });
  it("January is zero-padded", () => {
    expect(monthParamKey({ year: 2025, month0: 0 })).toBe("2025-01");
  });
  it("round-trips with parseMonthParam", () => {
    const ym = { year: 2027, month0: 11 };
    expect(parseMonthParam(monthParamKey(ym))).toEqual(ym);
  });
});

// ---------------------------------------------------------------------------
// monthLabel
// ---------------------------------------------------------------------------
describe("monthLabel", () => {
  it("formats June 2026", () => {
    expect(monthLabel({ year: 2026, month0: 5 })).toBe("June 2026");
  });
  it("formats January 2025", () => {
    expect(monthLabel({ year: 2025, month0: 0 })).toBe("January 2025");
  });
  it("formats December 2024", () => {
    expect(monthLabel({ year: 2024, month0: 11 })).toBe("December 2024");
  });
});

// ---------------------------------------------------------------------------
// addMonths
// ---------------------------------------------------------------------------
describe("addMonths", () => {
  it("adds 1 month without year rollover", () => {
    expect(addMonths({ year: 2026, month0: 4 }, 1)).toEqual({ year: 2026, month0: 5 });
  });
  it("adds 1 month with year rollover (December → January)", () => {
    expect(addMonths({ year: 2026, month0: 11 }, 1)).toEqual({ year: 2027, month0: 0 });
  });
  it("subtracts 1 month without year rollunder", () => {
    expect(addMonths({ year: 2026, month0: 5 }, -1)).toEqual({ year: 2026, month0: 4 });
  });
  it("subtracts 1 month with year rollunder (January → December)", () => {
    expect(addMonths({ year: 2026, month0: 0 }, -1)).toEqual({ year: 2025, month0: 11 });
  });
  it("adds 12 months = same month next year", () => {
    expect(addMonths({ year: 2026, month0: 5 }, 12)).toEqual({ year: 2027, month0: 5 });
  });
  it("delta=0 returns the same month", () => {
    expect(addMonths({ year: 2026, month0: 3 }, 0)).toEqual({ year: 2026, month0: 3 });
  });
  it("large positive delta (e.g. +25 months)", () => {
    // 2026-01 + 25 = 2028-02
    expect(addMonths({ year: 2026, month0: 0 }, 25)).toEqual({ year: 2028, month0: 1 });
  });
});

// ---------------------------------------------------------------------------
// daysInMonth
// ---------------------------------------------------------------------------
describe("daysInMonth", () => {
  it("January has 31 days", () => {
    expect(daysInMonth({ year: 2026, month0: 0 })).toBe(31);
  });
  it("April has 30 days", () => {
    expect(daysInMonth({ year: 2026, month0: 3 })).toBe(30);
  });
  it("February 2026 (non-leap) has 28 days", () => {
    expect(daysInMonth({ year: 2026, month0: 1 })).toBe(28);
  });
  it("February 2024 (leap year) has 29 days", () => {
    expect(daysInMonth({ year: 2024, month0: 1 })).toBe(29);
  });
  it("February 2000 (divisible by 400 — leap) has 29 days", () => {
    expect(daysInMonth({ year: 2000, month0: 1 })).toBe(29);
  });
  it("February 1900 (divisible by 100 but not 400 — NOT leap) has 28 days", () => {
    expect(daysInMonth({ year: 1900, month0: 1 })).toBe(28);
  });
  it("December has 31 days", () => {
    expect(daysInMonth({ year: 2026, month0: 11 })).toBe(31);
  });
});

// ---------------------------------------------------------------------------
// weekdayOfFirst
// ---------------------------------------------------------------------------
describe("weekdayOfFirst", () => {
  it("2026-06-01 is a Monday (weekday 1)", () => {
    expect(weekdayOfFirst({ year: 2026, month0: 5 })).toBe(1);
  });
  it("2026-01-01 is a Thursday (weekday 4)", () => {
    expect(weekdayOfFirst({ year: 2026, month0: 0 })).toBe(4);
  });
  it("2024-01-01 is a Monday (weekday 1)", () => {
    expect(weekdayOfFirst({ year: 2024, month0: 0 })).toBe(1);
  });
  it("returns value in 0..6 range (Sunday..Saturday)", () => {
    for (let m = 0; m < 12; m++) {
      const wd = weekdayOfFirst({ year: 2026, month0: m });
      expect(wd).toBeGreaterThanOrEqual(0);
      expect(wd).toBeLessThanOrEqual(6);
    }
  });
});

// ---------------------------------------------------------------------------
// buildMonthGrid
// ---------------------------------------------------------------------------
describe("buildMonthGrid", () => {
  it("every row has exactly 7 cells", () => {
    const grid = buildMonthGrid({ year: 2026, month0: 5 });
    for (const row of grid) {
      expect(row).toHaveLength(7);
    }
  });

  it("returns 5 rows for June 2026 (Mon 1st — fits in 5 rows)", () => {
    // June 2026: first=Mon, 30 days → 1+5 leading pads, 30 days, 4 trailing = 35 cells = 5 rows
    const grid = buildMonthGrid({ year: 2026, month0: 5 });
    expect(grid).toHaveLength(5);
  });

  it("returns 6 rows for a month that needs them (Jan 2023 — Sun 1st, 31 days)", () => {
    // Jan 2023: first=Sun(0), 31 days → 0 leading, 31 days, 3 trailing = 34... no, 31 = 4 weeks + 3 → 35 cells = 5 rows
    // Actually try October 2021: first=Fri(5), 31 days → 5+31+1=37 → 42 cells = 6 rows
    const grid = buildMonthGrid({ year: 2021, month0: 9 }); // Oct 2021
    expect(grid).toHaveLength(6);
  });

  it("in-month cells have inMonth=true", () => {
    const grid = buildMonthGrid({ year: 2026, month0: 5 });
    const inMonth = grid.flat().filter((c) => c.inMonth);
    expect(inMonth).toHaveLength(30); // June has 30 days
  });

  it("first in-month cell is day 1", () => {
    const grid = buildMonthGrid({ year: 2026, month0: 5 });
    const first = grid.flat().find((c) => c.inMonth);
    expect(first?.day).toBe(1);
    expect(first?.key).toBe("2026-06-01");
  });

  it("last in-month cell is the last day of the month", () => {
    const grid = buildMonthGrid({ year: 2026, month0: 5 });
    const flat = grid.flat();
    const last = [...flat].reverse().find((c) => c.inMonth);
    expect(last?.day).toBe(30);
    expect(last?.key).toBe("2026-06-30");
  });

  it("leading pad cells are from the previous month", () => {
    // June 2026 starts Monday (weekday 1) → 1 leading Sunday pad from May 2026
    const grid = buildMonthGrid({ year: 2026, month0: 5 });
    const firstRow = grid[0];
    const pads = firstRow.filter((c) => !c.inMonth);
    expect(pads).toHaveLength(1); // 1 Sunday pad
    expect(pads[0].key).toBe("2026-05-31"); // May 31
  });

  it("all cell keys match YYYY-MM-DD format", () => {
    const grid = buildMonthGrid({ year: 2026, month0: 5 });
    for (const cell of grid.flat()) {
      expect(cell.key).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    }
  });

  it("total cells count is divisible by 7", () => {
    // Check several months
    for (const ym of [
      { year: 2026, month0: 0 },
      { year: 2026, month0: 1 },
      { year: 2024, month0: 1 }, // leap Feb
      { year: 2025, month0: 11 },
    ]) {
      const total = buildMonthGrid(ym).flat().length;
      expect(total % 7).toBe(0);
    }
  });

  it("February 2024 (leap, 29 days) grid has exactly 29 in-month cells", () => {
    const grid = buildMonthGrid({ year: 2024, month0: 1 });
    expect(grid.flat().filter((c) => c.inMonth)).toHaveLength(29);
  });
});

// ---------------------------------------------------------------------------
// monthRangeKeys
// ---------------------------------------------------------------------------
describe("monthRangeKeys", () => {
  it("June 2026 range", () => {
    expect(monthRangeKeys({ year: 2026, month0: 5 })).toEqual({
      from: "2026-06-01",
      to: "2026-06-30",
    });
  });
  it("January range", () => {
    expect(monthRangeKeys({ year: 2026, month0: 0 })).toEqual({
      from: "2026-01-01",
      to: "2026-01-31",
    });
  });
  it("February 2024 (leap) range ends on 29th", () => {
    expect(monthRangeKeys({ year: 2024, month0: 1 })).toEqual({
      from: "2024-02-01",
      to: "2024-02-29",
    });
  });
});

// ---------------------------------------------------------------------------
// normalizeDateOnly
// ---------------------------------------------------------------------------
describe("normalizeDateOnly", () => {
  it("returns the first 10 chars of a date-only string", () => {
    expect(normalizeDateOnly("2026-06-03")).toBe("2026-06-03");
  });
  it("truncates a datetime string to date-only", () => {
    expect(normalizeDateOnly("2026-06-03T14:30:00Z")).toBe("2026-06-03");
  });
  it("returns null for null", () => {
    expect(normalizeDateOnly(null)).toBeNull();
  });
  it("returns null for undefined", () => {
    expect(normalizeDateOnly(undefined)).toBeNull();
  });
  it("returns null for empty string", () => {
    expect(normalizeDateOnly("")).toBeNull();
  });
  it("returns null for strings shorter than 10 chars", () => {
    expect(normalizeDateOnly("2026-06")).toBeNull();
    expect(normalizeDateOnly("2026-06-0")).toBeNull();
  });
  it("returns exactly 10 chars for 10-char string", () => {
    const v = normalizeDateOnly("2026-06-03");
    expect(v).toBe("2026-06-03");
    expect(v?.length).toBe(10);
  });
});

// ---------------------------------------------------------------------------
// epochDay
// ---------------------------------------------------------------------------
describe("epochDay", () => {
  it("1970-01-01 is epoch day 0", () => {
    expect(epochDay("1970-01-01")).toBe(0);
  });
  it("1970-01-02 is epoch day 1", () => {
    expect(epochDay("1970-01-02")).toBe(1);
  });
  it("epoch day difference between two dates is exact civil-day count", () => {
    const a = epochDay("2026-01-01")!;
    const b = epochDay("2026-01-31")!;
    expect(b - a).toBe(30);
  });
  it("returns null for null input", () => {
    expect(epochDay(null)).toBeNull();
  });
  it("returns null for undefined", () => {
    expect(epochDay(undefined)).toBeNull();
  });
  it("returns null for empty string", () => {
    expect(epochDay("")).toBeNull();
  });
  it("returns null for malformed string", () => {
    expect(epochDay("not-a-date")).toBeNull();
  });
  it("returns null for month 00 (invalid)", () => {
    expect(epochDay("2026-00-01")).toBeNull();
  });
  it("returns null for month 13 (invalid)", () => {
    expect(epochDay("2026-13-01")).toBeNull();
  });
  it("truncates datetime strings via normalizeDateOnly", () => {
    // 2026-06-03T12:00:00 should resolve to 2026-06-03's epoch day
    const withTime = epochDay("2026-06-03T12:00:00");
    const dateOnly = epochDay("2026-06-03");
    expect(withTime).toBe(dateOnly);
  });
  it("produces consistent results across all months in a year", () => {
    let prev = epochDay("2026-01-01")!;
    for (let m = 2; m <= 12; m++) {
      const key = `2026-${String(m).padStart(2, "0")}-01`;
      const curr = epochDay(key)!;
      expect(curr).toBeGreaterThan(prev);
      prev = curr;
    }
  });
});

// ---------------------------------------------------------------------------
// epochDayToKey
// ---------------------------------------------------------------------------
describe("epochDayToKey", () => {
  it("epoch day 0 → 1970-01-01", () => {
    expect(epochDayToKey(0)).toBe("1970-01-01");
  });
  it("round-trips with epochDay", () => {
    const key = "2026-06-03";
    expect(epochDayToKey(epochDay(key)!)).toBe(key);
  });
  it("round-trips for Dec 31 2025", () => {
    const key = "2025-12-31";
    expect(epochDayToKey(epochDay(key)!)).toBe(key);
  });
});

// ---------------------------------------------------------------------------
// monthTickLabel
// ---------------------------------------------------------------------------
describe("monthTickLabel", () => {
  it("returns abbreviated month + full year", () => {
    expect(monthTickLabel("2026-06-01")).toBe("Jun 2026");
  });
  it("January 2025", () => {
    expect(monthTickLabel("2025-01-15")).toBe("Jan 2025");
  });
  it("December 2024", () => {
    expect(monthTickLabel("2024-12-01")).toBe("Dec 2024");
  });
});

// ---------------------------------------------------------------------------
// startOfMonthEpochDay
// ---------------------------------------------------------------------------
describe("startOfMonthEpochDay", () => {
  it("returns the epoch day of the 1st of the same month", () => {
    const mid = epochDay("2026-06-15")!;
    const first = startOfMonthEpochDay(mid);
    expect(epochDayToKey(first)).toBe("2026-06-01");
  });
  it("is idempotent when called on the 1st itself", () => {
    const d = epochDay("2026-06-01")!;
    expect(startOfMonthEpochDay(d)).toBe(d);
  });
});

// ---------------------------------------------------------------------------
// nextMonthEpochDay
// ---------------------------------------------------------------------------
describe("nextMonthEpochDay", () => {
  it("returns the epoch day of the 1st of the NEXT month", () => {
    const mid = epochDay("2026-06-15")!;
    const next = nextMonthEpochDay(mid);
    expect(epochDayToKey(next)).toBe("2026-07-01");
  });
  it("handles December → January year rollover", () => {
    const mid = epochDay("2026-12-15")!;
    const next = nextMonthEpochDay(mid);
    expect(epochDayToKey(next)).toBe("2027-01-01");
  });
});

// ---------------------------------------------------------------------------
// WEEKDAY_LABELS
// ---------------------------------------------------------------------------
describe("WEEKDAY_LABELS", () => {
  it("has exactly 7 entries", () => {
    expect(WEEKDAY_LABELS).toHaveLength(7);
  });
  it("starts with Sun and ends with Sat", () => {
    expect(WEEKDAY_LABELS[0]).toBe("Sun");
    expect(WEEKDAY_LABELS[6]).toBe("Sat");
  });
});
