// Screen maths for UI v1. Every figure is a fixed rule over transactions, and each rule is stated on screen.
import { addDays, daysBetween, minDate } from "./format";
import { isExpense, within } from "./insights";
import type { ISODate, Paise, Period, Transaction } from "./types";

/** A single charge above this is a one-off: left out of the projection's daily pace (₹5,000). */
export const ONE_OFF: Paise = 500_000;
/** The projection's pace window. */
export const PACE_DAYS = 14;
/** A category's difference from normal is coloured only past this share of normal. */
export const NOTABLE = 0.15;

export const spendOf = (txns: Transaction[]) => txns.reduce((a, t) => (isExpense(t) ? a + t.amount : a), 0);

/** Cumulative spend for each day of [start, start + days), as of the end of that day. */
export function cumulative(txns: Transaction[], start: ISODate, days: number): Paise[] {
  const daily = new Array<number>(days).fill(0);
  for (const t of txns) {
    if (!isExpense(t)) continue;
    const i = daysBetween(start, t.date);
    if (i >= 0 && i < days) daily[i]! += t.amount;
  }
  let acc = 0;
  return daily.map((v) => (acc += v));
}

/** Normal: the average of the previous cycles' cumulative curves, day by day. */
export function normalCurve(txns: Transaction[], previous: Period[], days: number): Paise[] | null {
  if (!previous.length) return null;
  const curves = previous.map((p) => {
    const len = daysBetween(p.start, p.end) + 1;
    const c = cumulative(txns, p.start, len);
    return Array.from({ length: days }, (_, i) => c[Math.min(i, len - 1)] ?? 0);
  });
  if (curves.every((c) => (c.at(-1) ?? 0) === 0)) return null;
  return Array.from({ length: days }, (_, i) => Math.round(curves.reduce((a, c) => a + c[i]!, 0) / curves.length));
}

/** Spent so far + the last 14 days' daily average (single charges over ₹5,000 left out) × days left. */
export function projection(txns: Transaction[], period: Period, through: ISODate): Paise | null {
  const left = daysBetween(through, period.end);
  const spent = spendOf(within(txns, period.start, through));
  if (left <= 0) return spent;
  const from = addDays(through, -(PACE_DAYS - 1)) < period.start ? period.start : addDays(through, -(PACE_DAYS - 1));
  const days = daysBetween(from, through) + 1;
  const pace = within(txns, from, through).reduce((a, t) => (isExpense(t) && t.amount <= ONE_OFF ? a + t.amount : a), 0) / days;
  return Math.round(spent + pace * left);
}

export function byKey(txns: Transaction[], key: (t: Transaction) => string): Map<string, { amount: Paise; count: number }> {
  const out = new Map<string, { amount: Paise; count: number }>();
  for (const t of txns) {
    if (!isExpense(t)) continue;
    const k = key(t);
    const e = out.get(k) ?? { amount: 0, count: 0 };
    e.amount += t.amount;
    e.count += 1;
    out.set(k, e);
  }
  return out;
}
export const categoryOf = (t: Transaction) => t.category ?? "Uncategorized";

export type Group = "category" | "merchant" | "account";
/** The label a transaction is grouped under on Spending: its rows, and the detail page each row opens. */
export const groupKey = (g: Group): ((t: Transaction) => string) => (g === "category" ? categoryOf : g === "merchant" ? (t) => t.merchant : (t) => t.account);

/** Per key, the average over previous cycles of spend in their first `dayN` days. */
export function normalByKey(txns: Transaction[], previous: Period[], dayN: number, key: (t: Transaction) => string): Map<string, Paise> {
  const sums = new Map<string, Paise>();
  for (const p of previous) {
    const end = minDate(addDays(p.start, dayN - 1), p.end);
    for (const [k, v] of byKey(within(txns, p.start, end), key)) sums.set(k, (sums.get(k) ?? 0) + v.amount);
  }
  const n = Math.max(1, previous.length);
  return new Map([...sums].map(([k, v]) => [k, Math.round(v / n)]));
}

export function largestCharge(txns: Transaction[]): Transaction | null {
  let best: Transaction | null = null;
  for (const t of txns) if (isExpense(t) && t.amount > ONE_OFF && (!best || t.amount > best.amount)) best = t;
  return best;
}

export interface PaidFrom {
  /** An account id, or "standin" for bill payments standing in for un-itemised card spend. */
  id: string;
  label: string;
  kind: string | null;
  amount: Paise;
  count: number;
}

export function paidFrom(txns: Transaction[]): PaidFrom[] {
  const out = new Map<string, PaidFrom>();
  for (const t of txns) {
    if (!isExpense(t)) continue;
    // A bill payment still in the `card` bucket wasn't matched to a parsed card statement: it gets its own row
    // so the bank account's direct spend stays separate from card spend it stands in for.
    const stand = t.bucket === "card";
    const id = stand ? "standin" : (t.account_id ?? "none");
    const e = out.get(id) ?? { id, label: stand ? "Card bills, not itemised" : t.account, kind: stand ? "standin" : t.account_kind, amount: 0, count: 0 };
    e.amount += t.amount;
    e.count += 1;
    out.set(id, e);
  }
  return [...out.values()].sort((a, b) => (a.id === "standin" ? 1 : b.id === "standin" ? -1 : b.amount - a.amount));
}
