import type { Decimal, ISODate, MonthKey, Paise } from "./types";

// ---------- money ----------

const WHOLE = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

/** Parses the API's 2-place decimal string into integer paise without going through a float. */
export function toPaise(d: Decimal | number): Paise {
  if (typeof d === "number") return Math.round(d * 100);
  const m = /^(-)?(\d+)(?:\.(\d{1,2}))?$/.exec(d.trim());
  if (!m) throw new Error("not an amount");
  const p = Number(m[2]) * 100 + Number((m[3] ?? "").padEnd(2, "0"));
  return m[1] ? -p : p;
}

export const inr = (p: Paise) => (p < 0 ? "−" : "") + "₹" + WHOLE.format(Math.round(Math.abs(p) / 100));

export function inr2(p: Paise): string {
  const a = Math.abs(p);
  return (p < 0 ? "−" : "") + "₹" + WHOLE.format(Math.floor(a / 100)) + "." + String(a % 100).padStart(2, "0");
}

/** Compact lakh/crore notation for headline figures and axis ticks. */
export function compact(p: Paise): string {
  const a = Math.abs(p) / 100;
  const s = p < 0 ? "−" : "";
  if (a >= 1e7) return `${s}₹${(a / 1e7).toFixed(2)}Cr`;
  if (a >= 1e5) return `${s}₹${(a / 1e5).toFixed(a >= 1e6 ? 1 : 2).replace(/\.0+$/, "")}L`;
  if (a >= 1e3) return `${s}₹${(a / 1e3).toFixed(a >= 1e4 ? 0 : 1).replace(/\.0$/, "")}K`;
  return `${s}₹${Math.round(a)}`;
}

export const signed = (p: Paise) => (p > 0 ? "+" : p < 0 ? "−" : "") + inr(Math.abs(p));
export const pct = (v: number) => `${Math.round(v * 100)}%`;
/** Signed change for tables and chips; past 3× a percentage stops meaning much ("+8317%"), so it becomes a multiplier. */
export function changeText(cur: number, prev: number): string {
  if (prev <= 0) return cur > 0 ? "new" : "—";
  const ratio = cur / prev;
  if (ratio >= 3) return `${ratio >= 10 ? Math.round(ratio) : ratio.toFixed(1)}×`;
  const p = Math.round((ratio - 1) * 100);
  return `${p > 0 ? "+" : p < 0 ? "−" : ""}${Math.abs(p)}%`;
}
export const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

// ---------- dates ----------
// ISO dates are calendar days, so all arithmetic runs on UTC day numbers: the browser's zone never shifts a date.

const DAY = 86_400_000;
export const dayNum = (d: ISODate) => Date.UTC(Number(d.slice(0, 4)), Number(d.slice(5, 7)) - 1, Number(d.slice(8, 10))) / DAY;
export const fromDayNum = (n: number): ISODate => new Date(n * DAY).toISOString().slice(0, 10);
export const addDays = (d: ISODate, n: number) => fromDayNum(dayNum(d) + n);
export const daysBetween = (a: ISODate, b: ISODate) => dayNum(b) - dayNum(a);
/** 0 = Monday … 6 = Sunday */
export const weekday = (d: ISODate) => (new Date(dayNum(d) * DAY).getUTCDay() + 6) % 7;
export const minDate = (a: ISODate, b: ISODate) => (a < b ? a : b);
export const maxDate = (a: ISODate, b: ISODate) => (a > b ? a : b);

export function addMonths(m: MonthKey, n: number): MonthKey {
  const i = Number(m.slice(0, 4)) * 12 + Number(m.slice(5, 7)) - 1 + n;
  return `${Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, "0")}`;
}
export const daysInMonth = (m: MonthKey) => new Date(Date.UTC(Number(m.slice(0, 4)), Number(m.slice(5, 7)), 0)).getUTCDate();
export const monthEnd = (m: MonthKey): ISODate => `${m}-${String(daysInMonth(m)).padStart(2, "0")}`;

const IST_DAY = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit" });
const IST_TIME = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
/** Today in IST, where Tijori's money moves, whatever zone the browser is in. */
export const todayIST = (): ISODate => IST_DAY.format(new Date());
/** The IST day of a server timestamp; slicing the UTC string would show the day before until 05:30 IST. */
export function dayIST(ts: string): ISODate {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts.slice(0, 10) : IST_DAY.format(d);
}
export const timeIST = (ts: string) => IST_TIME.format(new Date(ts));

// Formatters are cached: Activity formats a date per row, and toLocaleString builds a new one per call.
const fmt = (o: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat("en-IN", { timeZone: "UTC", ...o });
const F = {
  monthYear: fmt({ month: "long", year: "numeric" }),
  monthLong: fmt({ month: "long" }),
  monthShort: fmt({ month: "short" }),
  monthShortYear: fmt({ month: "short", year: "2-digit" }),
  day: fmt({ weekday: "short", day: "numeric", month: "short" }),
  dayShort: fmt({ day: "numeric", month: "short" }),
  dayFull: fmt({ weekday: "long", day: "numeric", month: "long", year: "numeric" }),
  dayLong: fmt({ day: "numeric", month: "long", year: "numeric" }),
  weekdayShort: fmt({ weekday: "short" }),
};
const utc = (d: ISODate) => new Date(dayNum(d) * DAY);

export const monthYear = (m: MonthKey) => F.monthYear.format(utc(`${m}-01`));
export const monthLong = (m: MonthKey) => F.monthLong.format(utc(`${m}-01`));
export const monthShort = (m: MonthKey) => F.monthShort.format(utc(`${m}-01`));
export const monthShortOf = (d: ISODate) => F.monthShort.format(utc(d));
export const monthShortYear = (d: ISODate) => F.monthShortYear.format(utc(d));
export const dayName = (d: ISODate) => F.day.format(utc(d));
export const dayShort = (d: ISODate) => F.dayShort.format(utc(d));
export const dayFull = (d: ISODate) => F.dayFull.format(utc(d));
export const dayLong = (d: ISODate) => F.dayLong.format(utc(d));
export const WEEKDAYS = [0, 1, 2, 3, 4, 5, 6].map((i) => F.weekdayShort.format(utc(addDays("2024-01-01", i))));

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const s = parts.length > 1 ? (parts[0]?.[0] ?? "") + (parts.at(-1)?.[0] ?? "") : name.slice(0, 2);
  return s.toUpperCase() || "·";
}
