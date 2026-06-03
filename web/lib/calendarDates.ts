// calendarDates.ts — pure date-math for the Calendar (#1873) + Gantt (#1874)
// views. NO React imports.
//
// DESIGN — civil-date strings, NOT UTC instants.
//   Task.due_date / Milestone.start_date / Milestone.target_date are all plain
//   ISO calendar dates ("YYYY-MM-DD") with NO timezone. The board's "today"
//   highlight and every range comparison must therefore use the OPERATOR'S
//   LOCAL civil date — using Date.toISOString() (UTC) would highlight the wrong
//   cell near midnight for any non-UTC operator (the P&L presets use UTC because
//   they bucket timestamps; calendar dates are timezone-free and must not).
//
//   So: we treat a date as a {y, m, d} triple and a "YYYY-MM-DD" key. All math
//   stays in that civil space. The only Date use is `new Date()` to read the
//   operator's local "today" and `Date.UTC(...)` for weekday/length lookups
//   (where the UTC instant is just a calendar-arithmetic vehicle, never
//   serialized — getUTCDay/getUTCDate on a Date built from Date.UTC(y,m,d)
//   returns the civil weekday/day without any zone shift).

// ---------------------------------------------------------------------------
// Core key helpers
// ---------------------------------------------------------------------------

const pad2 = (n: number): string => String(n).padStart(2, "0");

// dateKey — civil {y,m(0-based),d} → "YYYY-MM-DD".
export function dateKey(year: number, month0: number, day: number): string {
  return `${year}-${pad2(month0 + 1)}-${pad2(day)}`;
}

// todayKey — the operator's LOCAL civil date as "YYYY-MM-DD". Uses getFullYear/
// getMonth/getDate (local), NOT getUTC* — this is the whole point (see header).
export function todayKey(now: Date = new Date()): string {
  return dateKey(now.getFullYear(), now.getMonth(), now.getDate());
}

// ---------------------------------------------------------------------------
// Month parsing / formatting
// ---------------------------------------------------------------------------

export type YearMonth = { year: number; month0: number }; // month0: 0..11

// parseMonthParam — "YYYY-MM" → {year, month0}. Returns null on any malformed
// input (caller falls back to the current month). Strict: requires exactly
// "YYYY-MM" with a 1..12 month.
export function parseMonthParam(raw: string | null | undefined): YearMonth | null {
  if (!raw) return null;
  const m = /^(\d{4})-(\d{2})$/.exec(raw);
  if (!m) return null;
  const year = Number(m[1]);
  const month1 = Number(m[2]);
  if (!Number.isInteger(year) || month1 < 1 || month1 > 12) return null;
  return { year, month0: month1 - 1 };
}

// currentYearMonth — operator-local current {year, month0}.
export function currentYearMonth(now: Date = new Date()): YearMonth {
  return { year: now.getFullYear(), month0: now.getMonth() };
}

// monthParamKey — {year, month0} → "YYYY-MM" (URL param form).
export function monthParamKey(ym: YearMonth): string {
  return `${ym.year}-${pad2(ym.month0 + 1)}`;
}

const MONTH_NAMES = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
] as const;

// monthLabel — {year, month0} → "June 2026".
export function monthLabel(ym: YearMonth): string {
  return `${MONTH_NAMES[ym.month0]} ${ym.year}`;
}

// addMonths — shift a {year, month0} by ±n months (handles year rollover).
export function addMonths(ym: YearMonth, delta: number): YearMonth {
  const total = ym.year * 12 + ym.month0 + delta;
  return { year: Math.floor(total / 12), month0: ((total % 12) + 12) % 12 };
}

// ---------------------------------------------------------------------------
// Calendar grid (#1873)
// ---------------------------------------------------------------------------

// daysInMonth — civil day count for {year, month0}. Date.UTC vehicle: day 0 of
// the NEXT month = last day of THIS month; getUTCDate reads it back zone-free.
export function daysInMonth(ym: YearMonth): number {
  return new Date(Date.UTC(ym.year, ym.month0 + 1, 0)).getUTCDate();
}

// weekdayOfFirst — 0(Sun)..6(Sat) for the 1st of {year, month0}. getUTCDay on a
// Date built from Date.UTC(y,m,1) gives the civil weekday with no zone shift.
export function weekdayOfFirst(ym: YearMonth): number {
  return new Date(Date.UTC(ym.year, ym.month0, 1)).getUTCDay();
}

// CalendarCell — one day cell in the month grid. `inMonth=false` are the
// leading/trailing pad days that fill the first/last week rows.
export type CalendarCell = {
  key: string; // "YYYY-MM-DD"
  day: number; // civil day-of-month (1..31)
  inMonth: boolean; // false for prev/next-month spillover pad days
};

// buildMonthGrid — Sunday-started weeks × 7 columns covering {year, month0},
// padded with the trailing days of the previous month and leading days of the
// next month so every row is full. Returns 5 OR 6 rows depending on how the
// month falls (handles both per the AC).
export function buildMonthGrid(ym: YearMonth): CalendarCell[][] {
  const firstWeekday = weekdayOfFirst(ym); // 0..6
  const totalDays = daysInMonth(ym);

  const prev = addMonths(ym, -1);
  const next = addMonths(ym, 1);
  const prevDays = daysInMonth(prev);

  const cells: CalendarCell[] = [];

  // Leading pad — trailing days of the previous month.
  for (let i = 0; i < firstWeekday; i++) {
    const day = prevDays - firstWeekday + 1 + i;
    cells.push({ key: dateKey(prev.year, prev.month0, day), day, inMonth: false });
  }
  // The month itself.
  for (let day = 1; day <= totalDays; day++) {
    cells.push({ key: dateKey(ym.year, ym.month0, day), day, inMonth: true });
  }
  // Trailing pad — leading days of the next month to complete the last row.
  let nextDay = 1;
  while (cells.length % 7 !== 0) {
    cells.push({
      key: dateKey(next.year, next.month0, nextDay),
      day: nextDay,
      inMonth: false,
    });
    nextDay++;
  }

  // Chunk into weeks of 7.
  const rows: CalendarCell[][] = [];
  for (let i = 0; i < cells.length; i += 7) {
    rows.push(cells.slice(i, i + 7));
  }
  return rows;
}

// monthRangeKeys — first + last civil day of {year, month0} as "YYYY-MM-DD".
// Used to drive listTasks({ due_from, due_to }) for the visible month.
export function monthRangeKeys(ym: YearMonth): { from: string; to: string } {
  return {
    from: dateKey(ym.year, ym.month0, 1),
    to: dateKey(ym.year, ym.month0, daysInMonth(ym)),
  };
}

// normalizeDateOnly — defensive truncation of any "YYYY-MM-DD..." value down to
// its first 10 chars. The contract is date-only, but if a timestamp ever leaks
// in (e.g. target_date serialized with a time), this keeps grid placement keyed
// on the date part. Returns null for empty/short input.
export function normalizeDateOnly(value: string | null | undefined): string | null {
  if (!value || value.length < 10) return null;
  return value.slice(0, 10);
}

export const WEEKDAY_LABELS = [
  "Sun",
  "Mon",
  "Tue",
  "Wed",
  "Thu",
  "Fri",
  "Sat",
] as const;

// ---------------------------------------------------------------------------
// Gantt time axis (#1874)
// ---------------------------------------------------------------------------
//
// The Gantt converts civil "YYYY-MM-DD" dates to an integer day-index for bar
// positioning. We use the UTC-epoch-day of each date as a PURE arithmetic
// vehicle: both the axis-min and every bar endpoint go through the same
// Date.UTC(y,m,d) basis, so the difference is an exact, timezone-free day count
// (the absolute epoch value is never serialized or shown — only differences).

const MS_PER_DAY = 86_400_000;
const MONTH_ABBR = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

// epochDay — civil "YYYY-MM-DD" → integer day index (days since 1970-01-01,
// UTC basis). Returns null for malformed input. Day differences between two
// epochDay values are exact civil-day counts regardless of operator timezone.
export function epochDay(key: string | null | undefined): number | null {
  const k = normalizeDateOnly(key);
  if (!k) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(k);
  if (!m) return null;
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const d = Number(m[3]);
  if (mo < 1 || mo > 12 || d < 1 || d > 31) return null;
  return Math.floor(Date.UTC(y, mo - 1, d) / MS_PER_DAY);
}

// epochDayToKey — inverse of epochDay: integer day index → "YYYY-MM-DD".
export function epochDayToKey(day: number): string {
  const dt = new Date(day * MS_PER_DAY);
  return dateKey(dt.getUTCFullYear(), dt.getUTCMonth(), dt.getUTCDate());
}

// monthTickLabel — civil "YYYY-MM-DD" → "Jan 2026" for axis tick labels.
export function monthTickLabel(key: string): string {
  const dt = new Date((epochDay(key) ?? 0) * MS_PER_DAY);
  return `${MONTH_ABBR[dt.getUTCMonth()]} ${dt.getUTCFullYear()}`;
}

// firstOfMonthOnOrAfter — first day-index of the calendar month containing
// `day`, snapped to the 1st. Used to seed month tick generation on the axis.
export function startOfMonthEpochDay(day: number): number {
  const dt = new Date(day * MS_PER_DAY);
  return Math.floor(Date.UTC(dt.getUTCFullYear(), dt.getUTCMonth(), 1) / MS_PER_DAY);
}

// nextMonthEpochDay — first day-index of the month AFTER the one containing
// `day` (axis tick stepping).
export function nextMonthEpochDay(day: number): number {
  const dt = new Date(day * MS_PER_DAY);
  return Math.floor(
    Date.UTC(dt.getUTCFullYear(), dt.getUTCMonth() + 1, 1) / MS_PER_DAY,
  );
}
