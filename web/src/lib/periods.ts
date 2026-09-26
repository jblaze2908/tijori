// Reporting periods. Weeks start Monday; months, quarters and FYs start on the member's month-start day
// (salary cycle, default 1), so a "September" with start day 25 runs 25 Sep – 24 Oct.
import { addDays, addMonths, dayShort, daysInMonth, maxDate, minDate, monthShort, monthShortYear, monthYear, weekday } from "./format";
import type { Granularity, ISODate, MonthKey, Period } from "./types";

const startOf = (m: MonthKey, startDay: number): ISODate => `${m}-${String(Math.min(startDay, daysInMonth(m))).padStart(2, "0")}`;

/** The month-cycle (labelled by its first month) that contains date d. */
export function cycleMonthOf(d: ISODate, startDay: number): MonthKey {
  const m = d.slice(0, 7);
  return d >= startOf(m, startDay) ? m : addMonths(m, -1);
}

export function monthPeriod(m: MonthKey, startDay: number): Period {
  const end = addDays(startOf(addMonths(m, 1), startDay), -1);
  const start = startOf(m, startDay);
  return {
    key: m,
    start,
    end,
    label: startDay === 1 ? monthYear(m) : `${dayShort(start)} – ${dayShort(end)}`,
    short: monthShort(m),
  };
}

/** Indian financial year of month m, as the calendar year it starts in (Apr–Mar). */
const fyStartYear = (m: MonthKey) => Number(m.slice(0, 4)) - (Number(m.slice(5, 7)) < 4 ? 1 : 0);

function build(g: Granularity, anchor: ISODate, startDay: number): Period {
  if (g === "week") {
    const start = addDays(anchor, -weekday(anchor));
    const end = addDays(start, 6);
    return { key: start, start, end, label: `${dayShort(start)} – ${dayShort(end)}`, short: dayShort(start) };
  }
  const m = cycleMonthOf(anchor, startDay);
  if (g === "month") return monthPeriod(m, startDay);
  if (g === "quarter") {
    const offset = (Number(m.slice(5, 7)) - 4 + 12) % 3;
    const first = addMonths(m, -offset);
    const last = addMonths(first, 2);
    const fy = fyStartYear(first) + 1;
    const q = Math.floor(((Number(first.slice(5, 7)) - 4 + 12) % 12) / 3) + 1;
    return {
      key: `${first}-Q`,
      start: monthPeriod(first, startDay).start,
      end: monthPeriod(last, startDay).end,
      label: `${monthShort(first)}–${monthShortYear(`${last}-01`)}`,
      short: `Q${q} FY${String(fy).slice(2)}`,
    };
  }
  const y = fyStartYear(m);
  return {
    key: `FY${y}`,
    start: monthPeriod(`${y}-04`, startDay).start,
    end: monthPeriod(`${y + 1}-03`, startDay).end,
    label: `FY ${y}–${String(y + 1).slice(2)}`,
    short: `FY${String(y + 1).slice(2)}`,
  };
}

export function previous(p: Period, g: Granularity, startDay: number): Period {
  return build(g, addDays(p.start, -1), startDay);
}

/** The n periods ending with the one that contains anchor, oldest first. */
export function lastPeriods(g: Granularity, anchor: ISODate, n: number, startDay: number): Period[] {
  const out = [build(g, anchor, startDay)];
  while (out.length < n) out.unshift(previous(out[0]!, g, startDay));
  return out;
}

/** Calendar months overlapping [from, to]: the unit transactions are fetched in. */
export function monthsCovering(from: ISODate, to: ISODate): MonthKey[] {
  const out: MonthKey[] = [];
  for (let m = from.slice(0, 7); m <= to.slice(0, 7); m = addMonths(m, 1)) out.push(m);
  return out;
}

/** The part of p that has happened by `through`: the fair window for comparing a period in progress. */
export function elapsed(p: Period, through: ISODate): Period {
  return { ...p, end: maxDate(p.start, minDate(p.end, through)) };
}

export const GRANULARITY_LABEL: Record<Granularity, string> = { week: "Week", month: "Month", quarter: "Quarter", fy: "FY" };
export const RANGE_OPTIONS: Record<Granularity, number[]> = { week: [8, 12, 26], month: [6, 12, 24], quarter: [4, 8], fy: [2, 3] };
export const DEFAULT_RANGE: Record<Granularity, number> = { week: 12, month: 12, quarter: 4, fy: 2 };
