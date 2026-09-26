import { createContext, useContext, type ReactNode } from "react";
import { Empty, ErrorState, Loading } from "./components/ui";
import { all, api, invalidate, read, type ResourceState } from "./lib/api";
import { addMonths, monthEnd } from "./lib/format";
import { within } from "./lib/insights";
import { monthPeriod, monthsCovering } from "./lib/periods";
import type { ApiMonths, ISODate, MonthKey, MonthSummary, Period, Settings, Transaction } from "./lib/types";

/** The selected month as a reporting cycle (respects the month-start day). */
export interface MonthCtx {
  key: MonthKey;
  period: Period;
  through: ISODate;
  complete: boolean;
}

export interface AppCtx {
  /** null while the months list isn't ready, or when there's no data at all. */
  month: MonthCtx | null;
  monthsState: ResourceState<unknown>;
  cycle: (key: MonthKey) => MonthCtx;
  months: MonthKey[];
  settings: Settings;
  asOf: ISODate;
  pick: (m: MonthKey) => void;
}

const Ctx = createContext<AppCtx | null>(null);
export const AppProvider = ({ value, children }: { value: AppCtx; children: ReactNode }) => <Ctx.Provider value={value}>{children}</Ctx.Provider>;
export function useApp(): AppCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp outside AppProvider");
  return v;
}

/** Stand-in when GET /api/months is unavailable: the last 12 calendar months, through today (IST). */
function lastTwelveMonths(today: ISODate): ApiMonths {
  const cur = today.slice(0, 7);
  const items = Array.from({ length: 12 }, (_, i) => {
    const m = addMonths(cur, i - 11);
    return { month: m, through: m === cur ? today : monthEnd(m), complete: m !== cur };
  });
  return { as_of: today, items };
}

/** Builds the month context from /api/months and /api/settings (both read through the shared cache). */
export function buildCtx(today: ISODate, picked: MonthKey | null, pick: (m: MonthKey) => void): AppCtx {
  const settings = (() => {
    const s = read(api.settings());
    return s.status === "ready" && s.data ? s.data : { monthStartDay: 1 };
  })();
  const monthsState = read(api.months());
  const calendar = monthsState.status === "ready" ? (monthsState.data ?? lastTwelveMonths(today)) : null;
  const asOf = calendar?.as_of ?? today;
  const startDay = settings.monthStartDay;
  const meta = new Map(calendar?.items.map((m) => [m.month, m]));
  const cycle = (k: MonthKey): MonthCtx => {
    const period = monthPeriod(k, startDay);
    // The server applies month_start_day to its months list, so its through/complete win whenever it has the month.
    const mm = meta.get(k);
    if (mm) return { key: k, period, through: mm.through, complete: mm.complete };
    return { key: k, period, through: period.end < asOf ? period.end : asOf, complete: period.end < asOf };
  };
  const months = (calendar?.items ?? []).map((m) => m.month).filter((k) => monthPeriod(k, startDay).start <= asOf);
  const selected = picked && months.includes(picked) ? picked : (months.at(-1) ?? null);
  return { month: selected ? cycle(selected) : null, monthsState, cycle, months, settings, asOf, pick };
}

/** Month-dependent pages share one gate for the months list. */
export function MonthGate({ app, children }: { app: AppCtx; children: (m: MonthCtx) => ReactNode }) {
  if (app.monthsState.status === "loading") return <Loading />;
  if (app.monthsState.status === "error")
    return <ErrorState error={app.monthsState.error} title="Couldn't load your months" onRetry={() => invalidate(["/api/months", "/api/settings", "/api/me", "/api/inbox"])} />;
  if (!app.month) return <Empty title="No data yet">Months appear here once the first statement or alert is imported.</Empty>;
  return <>{children(app.month)}</>;
}

// ---------- shared data helpers ----------

const joined = new WeakMap<Transaction[], Map<string, Transaction[]>>();

/** Transactions for [from, to], assembled from cached calendar-month slices (months before the data starts are skipped). */
export function txnsFor(app: AppCtx, from: ISODate, to: ISODate): ResourceState<Transaction[]> {
  const firstMonth = app.months[0];
  const keys = monthsCovering(from, to).filter((k) => !firstMonth || k >= firstMonth);
  if (!keys.length) return { status: "ready", data: [] };
  const st = all<Transaction[][]>(...keys.map((k) => read(api.month(k))));
  if (st.status !== "ready") return st;
  // Same slices → same array, so memoised views downstream don't recompute on unrelated renders.
  const first = st.data[0]!;
  const sig = `${from}|${to}|${st.data.map((a) => a.length).join(",")}`;
  let byKey = joined.get(first);
  if (!byKey) joined.set(first, (byKey = new Map()));
  let out = byKey.get(sig);
  if (!out) byKey.set(sig, (out = within(st.data.flat(), from, to)));
  return { status: "ready", data: out };
}

export const prevCycles = (app: AppCtx, m: MonthCtx, n: number): MonthCtx[] => Array.from({ length: n }, (_, i) => app.cycle(addMonths(m.key, -(i + 1))));

/** The month's totals are always the server's: GET /api/summary applies month_start_day itself. */
export const monthSummary = (m: MonthCtx): ResourceState<MonthSummary> => read(api.summary(m.key));
