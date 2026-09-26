import { useMemo, useState, type ReactNode } from "react";
import { columnChart, divergingBars, heatmap, lineChart, sparkline, tipRow, tipTitle, type Band, type Chart } from "../charts";
import { ChartHost, ChartView } from "../components/ChartView";
import { CardHead, Delta, Dot, ErrorState, InlineState, Loading } from "../components/ui";
import { MonthGate, prevCycles, txnsFor, type AppCtx, type MonthCtx } from "../ctx";
import { api, dataOf, invalidate, read } from "../lib/api";
import { CATEGORY_SLOT, categoryColor, DIV_BAD, DIV_GOOD, FLOW, OTHER } from "../lib/colors";
import { color, html } from "../lib/dom";
import { addDays, changeText, compact, dayShort, daysBetween, inr, monthLong, signed, WEEKDAYS } from "../lib/format";
import {
  committedSplit,
  dailySpend,
  deltaRows,
  merchantsBefore,
  movers,
  periodStats,
  savingsRate,
  topN,
  trailingAverage,
  UNCATEGORIZED,
  weekdayAverages,
  withServerTotals,
  withServerTrends,
  within,
  type DeltaRow,
  type PeriodStat,
} from "../lib/insights";
import { DEFAULT_RANGE, elapsed, GRANULARITY_LABEL, lastPeriods, RANGE_OPTIONS } from "../lib/periods";
import { Link, navigate, useLocation } from "../lib/router";
import type { Granularity, Period, Transaction, TrendPoint } from "../lib/types";
import { paceChart } from "./pace";

const GRANS: Granularity[] = ["week", "month", "quarter", "fy"];
const UNIT: Record<Granularity, string> = { week: "week", month: "month", quarter: "quarter", fy: "year" };
/** Stacks show at most this many named categories; the rest fold into Other (dataviz: never a 9th hue). */
const STACK_CATEGORIES = 7;
type SortKey = "key" | "current" | "previous" | "delta" | "pct";

function controls(p: URLSearchParams): { g: Granularity; n: number } {
  const g = (GRANS as string[]).includes(p.get("g") ?? "") ? (p.get("g") as Granularity) : "month";
  const n = Number(p.get("n"));
  return { g, n: RANGE_OPTIONS[g].includes(n) ? n : DEFAULT_RANGE[g] };
}
const setControls = (g: Granularity, n: number) => navigate(`/trends?${new URLSearchParams({ g, n: String(n) })}`, { replace: true });
const activityLink = (p: Period, category?: string) => `/activity?${new URLSearchParams({ ...(category ? { category } : {}), kind: "all", from: p.start, to: p.end })}`;

export function Trends({ app }: { app: AppCtx }) {
  const { params } = useLocation();
  const { g, n } = controls(params);
  return (
    <MonthGate app={app}>
      {(m) => (
        <>
          <Controls app={app} g={g} n={n} last={lastPeriods(g, m.through, 1, app.settings.monthStartDay)[0]!} />
          <TrendsData app={app} m={m} g={g} n={n} />
        </>
      )}
    </MonthGate>
  );
}

function Controls({ app, g, n, last }: { app: AppCtx; g: Granularity; n: number; last: Period }) {
  return (
    <div className="controls" role="group" aria-label="Trend period">
      <div className="seg" role="group" aria-label="Granularity">
        {GRANS.map((x) => (
          <button type="button" key={x} className={x === g ? "on" : ""} aria-pressed={x === g} onClick={() => setControls(x, DEFAULT_RANGE[x])}>
            {GRANULARITY_LABEL[x]}
          </button>
        ))}
      </div>
      <select className="sel" aria-label="Range" value={n} onChange={(e) => setControls(g, Number(e.target.value))}>
        {RANGE_OPTIONS[g].map((v) => (
          <option key={v} value={v}>
            Last {v} {UNIT[g]}s
          </option>
        ))}
      </select>
      <span className="sub">
        Ending {last.label}
        {g === "week" ? " · weeks start Monday" : ""}
        {app.settings.monthStartDay !== 1 ? ` · months start on day ${app.settings.monthStartDay}` : ""}
      </span>
    </div>
  );
}

function TrendsData({ app, m, g, n }: { app: AppCtx; m: MonthCtx; g: Granularity; n: number }) {
  const startDay = app.settings.monthStartDay;
  // One extra period in front, so the oldest shown period still has a "previous" to compare with.
  const periods = useMemo(() => lastPeriods(g, m.through, n + 1, startDay), [g, m.through, n, startDay]);
  const back = prevCycles(app, m, 3);
  const from = periods[0]!.start < back.at(-1)!.period.start ? periods[0]!.start : back.at(-1)!.period.start;
  const to = periods.at(-1)!.end > m.period.end ? periods.at(-1)!.end : m.period.end;
  const st = txnsFor(app, from, to);
  // GET /api/trends buckets by the same month-start day (docs/api.md), so its points line up with ours.
  const server = dataOf(read(api.trends(g, n + 1, m.through)));
  const totals = dataOf(read(api.trendTotals(g, n + 1, m.through)));
  if (st.status === "loading") return <Loading />;
  if (st.status === "error") return <ErrorState error={st.error} title="Couldn't load transactions" onRetry={() => invalidate(["/api/transactions", "/api/trends"])} />;
  return <Body app={app} g={g} m={m} periods={periods} txns={st.data} server={server} totals={totals} back={back} />;
}

function Body(props: { app: AppCtx; g: Granularity; m: MonthCtx; periods: Period[]; txns: Transaction[]; server: TrendPoint[] | null; totals: TrendPoint[] | null; back: MonthCtx[] }) {
  const { g, m, periods, txns, server, totals, back } = props;
  const [moversOf, setMoversOf] = useState<"category" | "merchant">("category");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "current", dir: -1 });
  const recurring = dataOf(read(api.recurring()));
  const unit = UNIT[g];

  const d = useMemo(() => {
    const all = withServerTotals(withServerTrends(periodStats(txns, periods), server), totals);
    const shown = all.slice(1);
    const cur = all.at(-1)!;
    const prev = all.at(-2)!;
    const inProgress = cur.period.end > m.through;
    // A period still in progress is compared with the same number of days of the previous one.
    const curWin = elapsed(cur.period, m.through);
    const prevWin = inProgress ? { ...prev.period, end: addDays(prev.period.start, daysBetween(curWin.start, curWin.end)) } : prev.period;
    const [curS, prevS] = periodStats(txns, [curWin, prevWin]);
    const rangeTotals = new Map<string, number>();
    for (const s of shown) for (const [c, v] of s.byCategory) rangeTotals.set(c, (rangeTotals.get(c) ?? 0) + v);
    // Categories that earn a colour in the stack: slotted ones with spend in range, largest first, drawn in slot order.
    const named = [...rangeTotals]
      .filter(([c, v]) => v > 0 && CATEGORY_SLOT[c])
      .sort((a, b) => b[1] - a[1])
      .slice(0, STACK_CATEGORIES)
      .map(([c]) => c)
      .sort((a, b) => (CATEGORY_SLOT[a] ?? 9) - (CATEGORY_SLOT[b] ?? 9));
    return {
      all,
      shown,
      cur,
      prev,
      inProgress,
      curWin,
      prevWin,
      curS: curS!,
      prevS: prevS!,
      rangeTotals,
      named,
      avg: trailingAverage(shown.map((s) => s.totals.expense), inProgress),
      catRows: deltaRows(curS!.byCategory, prevS!.byCategory),
      merchRows: deltaRows(curS!.byMerchant, prevS!.byMerchant, merchantsBefore(txns, prevWin.start)),
      pc: paceChart(m, back, txns),
      heat: heatmap({ label: `Daily spend in ${monthLong(m.key)}`, period: m.period, values: dailySpend(txns, m.period.start, m.period.end), through: m.through }),
      weekday: weekdayChart(txns, m),
    };
    // The month context is rebuilt every render; its value fields are the real deps (back derives from them).
  }, [txns, periods, server, totals, m.key, m.through, m.period.start]);

  // Figures shown are the server's (GET /api/trends) wherever it has the period; an in-progress period is compared with
  // the same days of the previous one, a finished one with the previous period's server figures.
  const now = d.cur.totals;
  const before = d.inProgress ? d.prevS.totals : d.prev.totals;
  const spentNow = now.expense;
  const spentPrev = before.expense;
  const cd = committedSplit(within(txns, d.curWin.start, d.curWin.end), recurring);
  const cdPrev = committedSplit(within(txns, d.prevWin.start, d.prevWin.end), recurring);
  const vs = `${d.prev.period.label}${d.inProgress ? ", same days" : ""}`;
  const rate = savingsRate(now);
  const prevRate = savingsRate(before);
  const charts = useMemo(
    () => ({ spend: spendChart(d.shown, d.all, d.named, d.avg), flows: flowsChart(d.shown), rate: rateChart(d.shown) }),
    [d],
  );
  const moversChart = useMemo(() => buildMovers(moversOf === "category" ? d.catRows : d.merchRows, d.cur.period, moversOf === "category"), [d, moversOf]);
  const multiples = useMemo(
    () =>
      [...d.rangeTotals]
        .filter(([, v]) => v > 0)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 12)
        .map(([c]) => ({
          c,
          chart: sparkline({
            label: `${c} per ${unit}`,
            points: d.shown.map((s, i) => [i, s.byCategory.get(c) ?? 0] as [number, number]),
            color: categoryColor(c === UNCATEGORIZED ? null : c),
            formatTip: (i) => d.shown[i]?.period.label ?? "",
          }),
          now: d.curS.byCategory.get(c) ?? 0,
          before: d.prevS.byCategory.get(c) ?? 0,
          mean: trailingAverage(d.shown.map((s) => s.byCategory.get(c) ?? 0), d.inProgress) ?? 0,
        })),
    [d, unit],
  );
  const [multiTable, setMultiTable] = useState(false);

  return (
    <>
      <div className="stats">
        <Stat label="Spent" value={compact(spentNow)} sub={`this ${unit}${d.inProgress ? " so far" : ""}`}>
          <Delta cur={spentNow} prev={spentPrev} vs={vs} upIsGood={false} compact />
        </Stat>
        <Stat label={`Average per ${unit}`} value={d.avg == null ? "—" : compact(d.avg)} sub="completed periods shown">
          {d.avg != null && <Delta cur={spentNow} prev={d.avg} vs="average" upIsGood={false} compact />}
        </Stat>
        <Stat label="Income" value={compact(now.income)} sub={`this ${unit}`}>
          <Delta cur={now.income} prev={before.income} vs={vs} upIsGood compact />
        </Stat>
        <Stat label="Invested" value={compact(now.invest)} sub={`this ${unit}`}>
          <Delta cur={now.invest} prev={before.invest} vs={vs} upIsGood compact />
        </Stat>
        <Stat label="Savings rate" value={rate == null ? "—" : `${Math.round(rate * 100)}%`} sub="(income − spend) ÷ income">
          {rate != null && prevRate != null && <Delta cur={rate} prev={prevRate} vs={vs} upIsGood points compact />}
        </Stat>
        <Stat label="Discretionary" value={compact(cd.discretionary)} sub={`committed ${compact(cd.committed)} · the part you can cut`}>
          <Delta cur={cd.discretionary} prev={cdPrev.discretionary} vs={vs} upIsGood={false} compact />
        </Stat>
      </div>
      <div className="sub mt-s">Changes are vs {vs}.</div>

      <div className="grid g-2 mt-g">
        <div className="card">
          <CardHead title={`Spend per ${unit}`} x={d.avg == null ? "by category" : `by category · dashed = avg ${compact(d.avg)}`} />
          <ChartView id="t-spend" chart={charts.spend}>
            <StackLegend named={d.named} shown={d.shown} />
          </ChartView>
        </div>
        <div className="card">
          <CardHead title={`Pace in ${monthLong(m.key)}`} x="cumulative, by day" />
          <ChartView id="t-pace" chart={d.pc.chart}>
            {d.pc.legend}
          </ChartView>
        </div>
      </div>

      <div className="grid g-2 mt-g">
        <div className="card">
          <CardHead title={`Income, spend and invested per ${unit}`} />
          <ChartView id="t-flows" chart={charts.flows}>
            <div className="leg2">
              <Key c={FLOW.income} name="Income" />
              <Key c={FLOW.spend} name="Spent" />
              <Key c={FLOW.invest} name="Invested" />
            </div>
          </ChartView>
        </div>
        <div className="card">
          <CardHead title="Savings rate" x="(income − spend) ÷ income" />
          <ChartView id="t-rate" chart={charts.rate} />
        </div>
      </div>

      <div className="card mt-g">
        <CardHead title="Category trends" x={`last ${d.shown.length} ${unit}s · click to open in Activity`} />
        {!multiples.length ? (
          <InlineState>No spending in this range yet.</InlineState>
        ) : multiTable ? (
          <div className="ctable">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Category</th>
                  {d.shown.map((s) => (
                    <th key={s.period.key} className="r">
                      {s.period.short}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {multiples.map(({ c }) => (
                  <tr key={c}>
                    <td>{c}</td>
                    {d.shown.map((s) => (
                      <td key={s.period.key} className="r num">
                        {inr(s.byCategory.get(c) ?? 0)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="multiples">
            {multiples.map(({ c, chart, now, before, mean }) => (
              <Link key={c} className="mult" href={activityLink(d.cur.period, c)} aria-label={`${c}: ${inr(now)} this ${unit}, open in Activity`}>
                <div className="mh">
                  <span className="mn">
                    <Dot color={categoryColor(c === UNCATEGORIZED ? null : c)} />
                    {c}
                  </span>
                  <Delta cur={now} prev={before} vs={vs} upIsGood={false} compact />
                </div>
                <div className="mv">{compact(now)}</div>
                <ChartHost chart={chart} />
                <div className="ms">
                  avg {compact(mean)} per {unit} · <Delta cur={now} prev={mean} vs={`the ${unit}ly average`} upIsGood={false} compact />
                </div>
              </Link>
            ))}
          </div>
        )}
        {multiples.length > 0 && (
          <div className="tv">
            <button type="button" className="linkish" aria-pressed={multiTable} onClick={() => setMultiTable(!multiTable)}>
              {multiTable ? "Show charts" : "Show table"}
            </button>
          </div>
        )}
      </div>

      <div className="grid g-2 mt-g">
        <div className="card">
          <h3>
            Biggest movers
            <span className="x">
              <span className="seg-ctl" role="group" aria-label="Movers by">
                {(["category", "merchant"] as const).map((v) => (
                  <button type="button" key={v} className={`chip${moversOf === v ? " on" : ""}`} aria-pressed={moversOf === v} onClick={() => setMoversOf(v)}>
                    {v === "category" ? "Categories" : "Merchants"}
                  </button>
                ))}
              </span>
            </span>
          </h3>
          <div className="sub">vs {vs}</div>
          <ChartView id={`t-movers-${moversOf}`} chart={moversChart}>
            <div className="leg2">
              <Key c={DIV_BAD} name="Spending more" />
              <Key c={DIV_GOOD} name="Spending less" />
            </div>
          </ChartView>
        </div>
        <div className="card">
          <CardHead title="Top merchants" x={`this ${unit} vs previous`} />
          <MerchantList rows={d.merchRows} vs={vs} />
        </div>
      </div>

      <div className="card mt-g">
        <CardHead title="Period comparison" x={`${d.cur.period.label} vs ${d.prev.period.label}${d.inProgress ? " (same days)" : ""}`} />
        <ComparisonTable rows={d.catRows} cur={d.cur.period} sort={sort} setSort={setSort} />
      </div>

      <div className="grid g-2 mt-g">
        <div className="card">
          <CardHead title={`Daily spend · ${monthLong(m.key)}`} x="darker = less" />
          <ChartView id="t-heat" chart={d.heat} />
        </div>
        <div className="card">
          <CardHead title={`Spend by weekday · ${monthLong(m.key)}`} x="average per day" />
          <ChartView id="t-weekday" chart={d.weekday} />
        </div>
      </div>

      <div className="card mt-g soon-card">
        <div className="ic" aria-hidden>
          ⇄
        </div>
        <div>
          <b>Money flow</b>
          <div className="sub">Income → spent, invested and saved → categories, as a Sankey. Coming soon.</div>
        </div>
      </div>
    </>
  );
}

const Stat = ({ label, value, sub, children }: { label: string; value: string; sub: string; children?: ReactNode }) => (
  <div className="stat">
    <div className="l">{label}</div>
    <div className="v">{value}</div>
    {children}
    <div className="s">{sub}</div>
  </div>
);

const Key = ({ c, name }: { c: string; name: string }) => (
  <span>
    <i className="sq" style={{ background: c }} />
    {name}
  </span>
);

function StackLegend({ named, shown }: { named: string[]; shown: PeriodStat[] }) {
  const hasOther = shown.some((s) => [...s.byCategory].some(([c, v]) => v > 0 && !named.includes(c)));
  return (
    <div className="leg2 wrap">
      {named.map((c) => (
        <Key key={c} c={categoryColor(c)} name={c} />
      ))}
      {hasOther && <Key c={OTHER} name="Other" />}
    </div>
  );
}

function spendChart(shown: PeriodStat[], all: PeriodStat[], named: string[], avg: number | null): Chart {
  const bands: Band[] = shown.map((s) => {
    const other = [...s.byCategory].filter(([c]) => !named.includes(c)).reduce((a, [, v]) => a + v, 0);
    return {
      key: s.period.key,
      label: s.period.label,
      short: s.period.short,
      segments: [...named.map((c) => ({ key: c, name: c, color: categoryColor(c), value: s.byCategory.get(c) ?? 0 })), { key: "__other", name: "Other", color: OTHER, value: other }],
    };
  });
  return columnChart({
    label: "Spend per period, stacked by category, with the average as a dashed line",
    mode: "stack",
    bands,
    ref: avg ? { value: avg, label: "" } : null,
    tip: (i, seg) => {
      const s = shown[i]!;
      const before = all[all.indexOf(s) - 1];
      const top = topN(s.byCategory, 3);
      const segRow = seg ? bands[i]!.segments.find((x) => x.key === seg) : null;
      return html`${tipTitle(s.period.label)}${tipRow(null, "Total", inr(s.totals.expense))}
        ${before ? tipRow(null, `vs ${before.period.label}`, changeText(s.totals.expense, before.totals.expense), false) : ""}
        ${segRow ? html`<div class="hl">${tipRow(color(segRow.color), segRow.name, inr(segRow.value))}</div>` : ""}
        ${top.length ? html`<div class="tnote">Top: ${top.map(([c, v]) => `${c} ${compact(v)}`).join(" · ")}</div>` : ""}
        <div class="tnote t3">Click to open in Activity</div>`;
    },
    onSelect: (i, seg) => {
      const s = shown[i];
      if (s) navigate(activityLink(s.period, seg && seg !== "__other" ? seg : undefined));
    },
    table: () => ({ head: ["Period", "Total", ...named, "Other"], rows: bands.map((b, i) => [b.label, inr(shown[i]!.totals.expense), ...b.segments.map((x) => inr(x.value))]) }),
  });
}

function flowsChart(shown: PeriodStat[]): Chart {
  return columnChart({
    label: "Income, spend and money invested per period, side by side",
    mode: "group",
    bands: shown.map((s) => ({
      key: s.period.key,
      label: s.period.label,
      short: s.period.short,
      segments: [
        { key: "income", name: "Income", color: FLOW.income, value: s.totals.income },
        { key: "spend", name: "Spent", color: FLOW.spend, value: s.totals.expense },
        { key: "invest", name: "Invested", color: FLOW.invest, value: s.totals.invest },
      ],
    })),
    tip: (i) => {
      const s = shown[i]!;
      const r = savingsRate(s.totals);
      return html`${tipTitle(s.period.label)}${tipRow(color(FLOW.income), "Income", inr(s.totals.income))}${tipRow(color(FLOW.spend), "Spent", inr(s.totals.expense))}${tipRow(color(FLOW.invest), "Invested", inr(s.totals.invest))}
        ${r != null ? html`<div class="tnote">Savings rate ${Math.round(r * 100)}%</div>` : ""}`;
    },
  });
}

function rateChart(shown: PeriodStat[]): Chart {
  // Basis points keep the chart on integers like every other series.
  const pts = shown.map((s, i) => [i, Math.round((savingsRate(s.totals) ?? 0) * 10000)] as [number, number]);
  const pctFmt = (v: number) => `${Math.round(v / 100)}%`;
  return lineChart({
    label: "Savings rate per period",
    series: [{ id: "rate", name: "Savings rate", color: FLOW.saved, points: pts, area: true }],
    height: 180,
    formatX: (i) => shown[i]?.period.short ?? "",
    tipTitle: (i) => shown[i]?.period.label ?? "",
    formatY: pctFmt,
    formatValue: pctFmt,
  });
}

function buildMovers(rows: DeltaRow[], cur: Period, clickable: boolean): Chart {
  const { up, down } = movers(rows, 5);
  const list = [...up, ...down.reverse()];
  return divergingBars({
    label: "Largest increases and decreases in spend vs the previous period",
    upIsBad: true,
    rows: list.map((r) => ({
      key: r.key,
      label: r.key,
      value: r.delta,
      valueLabel: signed(r.delta),
      tip: html`${tipTitle(r.key)}${tipRow(null, "This period", inr(r.current))}${tipRow(null, "Previous", inr(r.previous), false)}<div class="tnote">${changeText(r.current, r.previous)}</div>`,
    })),
    onSelect: clickable ? (k) => navigate(activityLink(cur, k)) : undefined,
    table: () => ({ head: ["", "This period", "Previous", "Change"], rows: list.map((r) => [r.key, inr(r.current), inr(r.previous), signed(r.delta)]) }),
  });
}

function MerchantList({ rows, vs }: { rows: DeltaRow[]; vs: string }) {
  const top = rows.filter((r) => r.current > 0).sort((a, b) => b.current - a.current).slice(0, 8);
  if (!top.length) return <InlineState>No merchant spending this period yet.</InlineState>;
  return (
    <div className="list">
      {top.map((r) => (
        <div className="li" key={r.key}>
          <div className="mid">
            <b>
              {r.key}
              {r.isNew && <span className="tag">new</span>}
            </b>
            <small>{r.previous ? `previous ${inr(r.previous)}` : "none last period"}</small>
          </div>
          <div className="amt num">
            {inr(r.current)}
            {r.previous > 0 && (
              <small>
                {r.delta >= 0 ? "+" : "−"}
                {compact(Math.abs(r.delta))} <Delta cur={r.current} prev={r.previous} vs={vs} upIsGood={false} compact />
              </small>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function ComparisonTable({ rows, cur, sort, setSort }: { rows: DeltaRow[]; cur: Period; sort: { key: SortKey; dir: 1 | -1 }; setSort: (s: { key: SortKey; dir: 1 | -1 }) => void }) {
  const data = rows.filter((r) => r.current || r.previous);
  if (!data.length) return <InlineState>Nothing to compare yet.</InlineState>;
  const val = (r: DeltaRow) => (sort.key === "key" ? r.key : sort.key === "pct" ? (r.pct ?? Infinity) : r[sort.key]);
  const sorted = [...data].sort((a, b) => {
    const x = val(a);
    const y = val(b);
    return (typeof x === "string" ? x.localeCompare(y as string) : (x as number) - (y as number)) * sort.dir;
  });
  const th = (k: SortKey, label: string, right = true) => (
    <th className={right ? "r" : undefined} aria-sort={sort.key === k ? (sort.dir === 1 ? "ascending" : "descending") : undefined}>
      <button type="button" className={sort.key === k ? "on" : undefined} onClick={() => setSort({ key: k, dir: sort.key === k ? (sort.dir === 1 ? -1 : 1) : k === "key" ? 1 : -1 })}>
        {label}
        {sort.key === k ? (sort.dir === 1 ? " ↑" : " ↓") : ""}
      </button>
    </th>
  );
  return (
    <div className="ctable-wrap">
      <table className="tbl sortable">
        <thead>
          <tr>
            {th("key", "Category", false)}
            {th("current", "Current")}
            {th("previous", "Previous")}
            {th("delta", "Δ ₹")}
            {th("pct", "Δ %")}
            <th className="r" aria-label="Direction" />
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.key}>
              <td>
                <Link href={activityLink(cur, r.key)}>
                  <Dot color={categoryColor(r.key === UNCATEGORIZED ? null : r.key)} />
                  {r.key}
                </Link>
              </td>
              <td className="r num">{inr(r.current)}</td>
              <td className="r num t2">{inr(r.previous)}</td>
              <td className={`r num${r.delta > 0 ? " up" : r.delta < 0 ? " down" : ""}`}>{signed(r.delta)}</td>
              <td className="r num">{changeText(r.current, r.previous)}</td>
              <td className="r">
                {r.delta > 0 ? (
                  <span className="up" aria-label="up">
                    ▲
                  </span>
                ) : r.delta < 0 ? (
                  <span className="down" aria-label="down">
                    ▼
                  </span>
                ) : (
                  <span className="t3">–</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function weekdayChart(txns: Transaction[], m: MonthCtx): Chart {
  const w = weekdayAverages(txns, m.period.start, m.through);
  return columnChart({
    label: `Average spend per weekday in ${monthLong(m.key)}`,
    mode: "group",
    bands: w.map((d, i) => ({ key: String(i), label: WEEKDAYS[i]!, short: WEEKDAYS[i]!, segments: [{ key: "avg", name: "Average per day", color: FLOW.spend, value: d.avg }] })),
    tip: (i) => {
      const d = w[i]!;
      return html`${tipTitle(WEEKDAYS[i]!)}${tipRow(color(FLOW.spend), "Average per day", inr(d.avg))}${tipRow(null, "Total", inr(d.total), false)}<div class="tnote t3">over ${d.days} ${WEEKDAYS[i]}s through ${dayShort(m.through)}</div>`;
    },
    table: () => ({ head: ["Weekday", "Average per day", "Total", "Days"], rows: w.map((d, i) => [WEEKDAYS[i]!, inr(d.avg), inr(d.total), String(d.days)]) }),
  });
}
