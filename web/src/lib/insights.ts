// Derived views over backend data. Pure: same input, same output, no I/O or DOM.
// Each one says where it should move server-side (M2 = summaries/trends, M4 = alerts/recurring).
import { NW_SHEET_KEYS } from "./colors";
import { addDays, dayName, daysBetween, inr, inr2, weekday } from "./format";
import type { Bucket, ISODate, Paise, Period, Recurring, ServerAlert, Snapshot, Totals, Transaction, TrendPoint } from "./types";

// ---------- classification: the same rules as docs/api.md's summary totals ----------

const EXPENSE_BUCKETS: ReadonlySet<Bucket | null> = new Set<Bucket | null>(["everyday", "oneoff", "card"]);
export const isExpense = (t: Transaction) => t.direction === "debit" && (t.category_id == null || EXPENSE_BUCKETS.has(t.bucket));
export const isIncome = (t: Transaction) => t.direction === "credit" && t.bucket === "income";
export const isInvest = (t: Transaction) => t.direction === "debit" && t.bucket === "invest";
export const isRefund = (t: Transaction) => t.direction === "credit" && t.kind === "refund";

export const within = (txns: Transaction[], from: ISODate, to: ISODate) => txns.filter((t) => t.date >= from && t.date <= to);

// Move server-side in M2 (summary accepts from/to).
/** Totals for an arbitrary window, e.g. week-to-date or "last month up to the same day". */
export function summarize(txns: Transaction[]): Totals {
  const t = emptyTotals();
  for (const x of txns) addTo(t, x);
  return t;
}
const emptyTotals = (): Totals => ({ expense: 0, income: 0, invest: 0, card: 0, refunds: 0 });
function addTo(t: Totals, x: Transaction) {
  if (isExpense(x)) t.expense += x.amount;
  if (x.direction === "debit" && x.bucket === "card") t.card += x.amount;
  if (isIncome(x)) t.income += x.amount;
  if (isInvest(x)) t.invest += x.amount;
  if (isRefund(x)) t.refunds = (t.refunds ?? 0) + x.amount;
}

export const leftOver = (t: Totals) => Math.max(0, t.income - t.expense - t.invest);
/** Savings rate = (income − spend) ÷ income; money invested counts as saved. */
export const savingsRate = (t: Totals) => (t.income > 0 ? (t.income - t.expense) / t.income : null);

/** Relative change, or null when there's nothing to compare against. */
export const change = (cur: number, prev: number) => (prev > 0 ? (cur - prev) / prev : null);

// ---------- pace ----------

export type Point = [x: number, y: Paise];

/** Cumulative spend by day of the period: x = 1..days. */
export function cumulative(txns: Transaction[], start: ISODate, days: number): Point[] {
  const byDay = new Float64Array(days + 1);
  for (const t of txns) {
    if (!isExpense(t)) continue;
    const d = daysBetween(start, t.date) + 1;
    if (d >= 1 && d <= days) byDay[d] = (byDay[d] ?? 0) + t.amount;
  }
  const out: Point[] = [];
  let acc = 0;
  for (let d = 1; d <= days; d++) out.push([d, (acc += byDay[d] ?? 0)]);
  return out;
}

const at = (pts: Point[], d: number) => (pts[Math.min(d, pts.length) - 1] ?? [0, 0])[1];

export interface Pace {
  cur: Point[];
  last: Point[] | null;
  avg: Point[] | null;
  ideal: Point[] | null;
  /** Average full-period total of the comparison periods. */
  avgTotal: Paise | null;
  days: number;
}

// Move server-side in M2 (summary returns the daily series and compare_to).
/** This period to date vs the previous period, the trailing-3 average and an even "ideal" pace to that average. */
export function pace(txns: Transaction[], cur: Period, through: ISODate, previous: Period[]): Pace {
  const days = daysBetween(cur.start, cur.end) + 1;
  const upto = Math.max(1, Math.min(days, daysBetween(cur.start, through) + 1));
  const curve = (p: Period) => cumulative(txns, p.start, daysBetween(p.start, p.end) + 1);
  const curPts = cumulative(txns, cur.start, days).slice(0, upto);
  const prevCurves = previous.map(curve);
  const last = prevCurves[0] ?? null;
  if (!prevCurves.length) return { cur: curPts, last: null, avg: null, ideal: null, avgTotal: null, days };
  const avg: Point[] = [];
  for (let d = 1; d <= days; d++) avg.push([d, Math.round(prevCurves.reduce((s, c) => s + at(c, d), 0) / prevCurves.length)]);
  const avgTotal = Math.round(prevCurves.reduce((s, c) => s + (c.at(-1)?.[1] ?? 0), 0) / prevCurves.length);
  const ideal: Point[] = [];
  for (let d = 1; d <= days; d++) ideal.push([d, Math.round((avgTotal * d) / days)]);
  return { cur: curPts, last: last ? last.slice(0, days) : null, avg, ideal, avgTotal, days };
}
export const paceValue = at;

// ---------- per-period statistics ----------

export interface PeriodStat {
  period: Period;
  totals: Totals;
  byCategory: Map<string, Paise>;
  byMerchant: Map<string, Paise>;
  txns: number;
}

// Move server-side in M2 (GET /api/trends).
/** One pass over the transactions; periods may come in any order and need not touch (windows, not a calendar). */
export function periodStats(txns: Transaction[], periods: Period[]): PeriodStat[] {
  const stats: PeriodStat[] = periods.map((period) => ({
    period,
    totals: emptyTotals(),
    byCategory: new Map(),
    byMerchant: new Map(),
    txns: 0,
  }));
  const order = periods.map((_, i) => i).sort((a, b) => (periods[a]!.start < periods[b]!.start ? -1 : 1));
  const starts = order.map((i) => periods[i]!.start);
  const first = starts[0] ?? "";
  for (const t of txns) {
    if (t.date < first) continue;
    let lo = 0;
    let hi = starts.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if ((starts[mid] ?? "") <= t.date) lo = mid;
      else hi = mid - 1;
    }
    const idx = order[lo]!;
    if (t.date > periods[idx]!.end) continue;
    const s = stats[idx]!;
    s.txns++;
    addTo(s.totals, t);
    if (isExpense(t)) {
      const c = t.category ?? UNCATEGORIZED;
      s.byCategory.set(c, (s.byCategory.get(c) ?? 0) + t.amount);
      s.byMerchant.set(t.merchant, (s.byMerchant.get(t.merchant) ?? 0) + t.amount);
    }
  }
  return stats;
}
export const UNCATEGORIZED = "Uncategorized";

/** Server trend points (group_by=category) replace the client's spend figures where the periods line up. */
export function withServerTrends(stats: PeriodStat[], points: TrendPoint[] | null): PeriodStat[] {
  if (!points?.length) return stats;
  const byStart = new Map<string, TrendPoint[]>();
  for (const p of points) {
    const list = byStart.get(p.start);
    if (list) list.push(p);
    else byStart.set(p.start, [p]);
  }
  return stats.map((s) => {
    const pts = byStart.get(s.period.start);
    if (!pts) return s;
    const byCategory = new Map(pts.map((p) => [p.key ?? UNCATEGORIZED, p.amount]));
    const expense = pts.reduce((a, p) => a + p.amount, 0);
    return { ...s, byCategory, totals: { ...s.totals, expense } };
  });
}

/** Mean of the completed periods: the reference line on spend-per-period. */
export function trailingAverage(values: Paise[], lastInProgress: boolean): Paise | null {
  const done = lastInProgress ? values.slice(0, -1) : values;
  return done.length ? Math.round(done.reduce((a, b) => a + b, 0) / done.length) : null;
}

export const topN = (m: Map<string, Paise>, n: number) => [...m].filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).slice(0, n);

// ---------- comparisons ----------

export interface DeltaRow {
  key: string;
  current: Paise;
  previous: Paise;
  delta: Paise;
  pct: number | null;
  isNew: boolean;
}

// Move server-side in M2 (spend_by with compare_to).
/** Per-key current vs previous; `seenBefore` marks keys that existed before the previous period too. */
export function deltaRows(cur: Map<string, Paise>, prev: Map<string, Paise>, seenBefore?: ReadonlySet<string>): DeltaRow[] {
  const keys = new Set([...cur.keys(), ...prev.keys()]);
  return [...keys].map((key) => {
    const current = cur.get(key) ?? 0;
    const previous = prev.get(key) ?? 0;
    return { key, current, previous, delta: current - previous, pct: change(current, previous), isNew: !!seenBefore && previous === 0 && current > 0 && !seenBefore.has(key) };
  });
}

/** Largest increases and decreases, for the diverging movers chart. */
export function movers(rows: DeltaRow[], n = 5): { up: DeltaRow[]; down: DeltaRow[] } {
  const sorted = rows.filter((r) => r.delta !== 0).sort((a, b) => b.delta - a.delta);
  return { up: sorted.filter((r) => r.delta > 0).slice(0, n), down: sorted.filter((r) => r.delta < 0).slice(-n).reverse() };
}

export function merchantsBefore(txns: Transaction[], before: ISODate): Set<string> {
  return new Set(txns.filter((t) => t.date < before && isExpense(t)).map((t) => t.merchant));
}

// ---------- calendar ----------

// Move server-side in M2 (spend_by weekday / day).
export function dailySpend(txns: Transaction[], from: ISODate, to: ISODate): Map<ISODate, Paise> {
  const m = new Map<ISODate, Paise>();
  for (const t of txns) if (t.date >= from && t.date <= to && isExpense(t)) m.set(t.date, (m.get(t.date) ?? 0) + t.amount);
  return m;
}

/** Quantile thresholds over the non-zero days, so one large purchase doesn't wash every other day out. */
export function heatThresholds(values: Paise[], bins: number): Paise[] {
  const v = values.filter((x) => x > 0).sort((a, b) => a - b);
  if (!v.length) return [];
  return Array.from({ length: bins - 1 }, (_, i) => v[Math.floor(((i + 1) * v.length) / bins)] ?? v.at(-1)!);
}
export const heatBin = (v: Paise, th: Paise[]) => (v <= 0 ? -1 : th.filter((t) => v > t).length);

/** Average spend per weekday (Mon..Sun) over the days of [from, to] that have happened. */
export function weekdayAverages(txns: Transaction[], from: ISODate, to: ISODate): { avg: Paise; total: Paise; days: number }[] {
  const out = Array.from({ length: 7 }, () => ({ avg: 0, total: 0, days: 0 }));
  for (let d = from; d <= to; d = addDays(d, 1)) out[weekday(d)]!.days++;
  for (const [d, v] of dailySpend(txns, from, to)) out[weekday(d)]!.total += v;
  for (const o of out) o.avg = o.days ? Math.round(o.total / o.days) : 0;
  return out;
}

// ---------- committed vs discretionary ----------

const COMMITTED_CATEGORIES = new Set(["Bills & subscriptions", "Insurance"]);

// Move server-side in M4 (recurring detection, PLAN §7.6).
/** Committed = recurring by nature (bills, insurance), a predicted ("expected") charge, or a known recurring payee;
 *  the rest is what you can cut. */
export function committedSplit(txns: Transaction[], recurring: Recurring[] | null): { committed: Paise; discretionary: Paise } {
  const payees = new Set((recurring ?? []).map((r) => r.merchant));
  let committed = 0;
  let discretionary = 0;
  for (const t of txns) {
    if (!isExpense(t)) continue;
    if ((t.category && COMMITTED_CATEGORIES.has(t.category)) || t.sources.includes("expected") || payees.has(t.merchant)) committed += t.amount;
    else discretionary += t.amount;
  }
  return { committed, discretionary };
}

// ---------- alerts ----------

export type Alert = ServerAlert;

// Move server-side in M4 (recurring/duplicate detection, PLAN §7.6).
/** Same card, merchant, day and amount twice: likely a double charge worth disputing. */
export function duplicateCharges(txns: Transaction[]): Alert[] {
  const seen = new Map<string, number>();
  const out: Alert[] = [];
  for (const t of txns) {
    if (t.account_kind !== "card" || t.kind !== "spend" || t.direction !== "debit") continue;
    const k = `${t.account_id}|${t.date}|${t.merchant}|${t.amount}`;
    const n = (seen.get(k) ?? 0) + 1;
    seen.set(k, n);
    if (n === 2)
      out.push({
        id: `dup:${k}`,
        kind: "duplicate",
        severity: "bad",
        title: `Possible duplicate: ${t.merchant} ${inr2(t.amount)} × 2`,
        detail: `Charged twice on ${dayName(t.date)}. Check the statement and ask for a reversal if both posted.`,
      });
  }
  return out;
}

const UNUSUAL_MIN: Paise = 5000_00;

// Move server-side in M4 (anomaly detection over full history).
/** Big jumps on repeat payees only; a large one-off order at a new shop isn't news. */
export function unusualAmounts(txns: Transaction[], history: Transaction[]): Alert[] {
  const byMerchant = new Map<string, Paise[]>();
  for (const t of history) {
    if (t.kind !== "spend" || t.direction !== "debit") continue;
    const vs = byMerchant.get(t.merchant);
    if (vs) vs.push(t.amount);
    else byMerchant.set(t.merchant, [t.amount]);
  }
  const medians = new Map<string, Paise>();
  for (const [m, vs] of byMerchant) if (vs.length >= 3) medians.set(m, [...vs].sort((a, b) => a - b)[Math.floor(vs.length / 2)] ?? 0);
  const out: Alert[] = [];
  for (const t of txns) {
    const med = medians.get(t.merchant);
    if (t.kind !== "spend" || t.direction !== "debit" || t.amount <= UNUSUAL_MIN || !med || t.amount <= med * 4) continue;
    out.push({
      id: `unusual:${t.id}`,
      kind: "price_increase",
      severity: "warn",
      title: `${t.merchant} charged ${inr(t.amount)}`,
      detail: `${(t.amount / med).toFixed(1)}× its usual ${inr2(med)}. New plan, or an annual renewal?`,
    });
  }
  return out;
}

// Move server-side in M4 (alert table, PLAN §6).
export const salaryCredits = (txns: Transaction[]): Alert[] =>
  txns
    .filter((t) => t.direction === "credit" && t.category === "Salary")
    .map((t) => ({ id: `salary:${t.id}`, kind: "salary", severity: "good" as const, title: `Salary credited ${inr(t.amount)}`, detail: `${dayName(t.date)} · ${t.account}` }));

/** Prototype order: problems first, server alerts (e.g. bounce risk) next, good news last. */
export const needsAttention = (txns: Transaction[], history: Transaction[], server: ServerAlert[] | null): Alert[] => [
  ...duplicateCharges(txns),
  ...unusualAmounts(txns, history),
  ...(server ?? []),
  ...salaryCredits(txns),
];

export const recent = (txns: Transaction[], n = 7) => txns.filter((t) => t.kind !== "transfer" && t.kind !== "investment").slice(-n).reverse();

export const upcoming = <T extends { next_due: ISODate }>(items: T[], from: ISODate, n = 7): T[] =>
  items.filter((r) => r.next_due >= from).sort((a, b) => (a.next_due < b.next_due ? -1 : 1)).slice(0, n);

// ---------- net worth ----------

// Move server-side in M4 (idle-cash nudge, PLAN §8 Net worth).
/** The largest cash balance, when it's a meaningful share of net worth. */
export function idleCash(s: Snapshot, minShare = 0.1) {
  const top = s.components.filter((c) => c.assetClass === "cash").sort((a, b) => b.amount - a.amount)[0];
  return top && s.netWorth > 0 && top.amount / s.netWorth >= minShare ? top : null;
}

export interface AllocationSlice {
  key: string;
  label: string;
  amount: Paise;
  /** 1..8 palette slot, or undefined for grey ("other" and anything past eight). */
  slot: number | undefined;
  parts: { label: string; amount: Paise }[];
}

/** Group keys in stack order across all snapshots, so a slice keeps its colour from month to month:
 *  cash accounts grouped first, then the sheet's component order (docs/api.md), then anything new. */
export function allocationSlots(snapshots: Snapshot[]): Map<string, number | undefined> {
  const keys = new Set<string>();
  for (const s of snapshots) for (const c of s.components) keys.add(c.assetClass === "cash" ? "cash" : c.key);
  const order = (k: string) => (k === "cash" ? -1 : NW_SHEET_KEYS.indexOf(k) < 0 ? NW_SHEET_KEYS.length : NW_SHEET_KEYS.indexOf(k));
  const sorted = [...keys].sort((a, b) => order(a) - order(b) || a.localeCompare(b));
  const slots = new Map<string, number | undefined>();
  let n = 0;
  for (const k of sorted) slots.set(k, k === "other" || n >= 8 ? undefined : ++n);
  return slots;
}

/** The sheet's allocation view: cash accounts grouped into one slice, every other component on its own. */
export function allocation(s: Snapshot, slots: Map<string, number | undefined>): AllocationSlice[] {
  const cash = s.components.filter((c) => c.assetClass === "cash");
  const slices: AllocationSlice[] = [];
  if (cash.length)
    slices.push({
      key: "cash",
      label: cash.length > 1 ? `Cash (${cash.map((c) => c.label).join(" + ")})` : (cash[0]?.label ?? "Cash"),
      amount: cash.reduce((a, c) => a + c.amount, 0),
      slot: slots.get("cash"),
      parts: cash.map((c) => ({ label: c.label, amount: c.amount })),
    });
  for (const c of s.components) if (c.assetClass !== "cash") slices.push({ key: c.key, label: c.label, amount: c.amount, slot: slots.get(c.key), parts: [] });
  const rank = [...slots.keys()];
  return slices.sort((a, b) => rank.indexOf(a.key) - rank.indexOf(b.key));
}
