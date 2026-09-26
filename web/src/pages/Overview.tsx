import { useMemo } from "react";
import { ChartView } from "../components/ChartView";
import { CardHead, Delta, Dot, ErrorState, InlineState, Loading, Monogram } from "../components/ui";
import { MonthGate, monthSummary, prevCycles, txnsFor, type AppCtx, type MonthCtx } from "../ctx";
import { all, api, dataOf, invalidate, read } from "../lib/api";
import { categoryColor, FLOW } from "../lib/colors";
import { addDays, compact, dayName, dayShort, daysBetween, inr, monthLong, monthShort, weekday } from "../lib/format";
import {
  committedSplit,
  deltaRows,
  leftOver,
  merchantsBefore,
  needsAttention,
  paceValue,
  periodStats,
  recent,
  summarize,
  upcoming,
  within,
  type Alert,
} from "../lib/insights";
import { Link } from "../lib/router";
import type { Budgets, MonthSummary, Recurring, Totals, Transaction } from "../lib/types";
import { paceChart } from "./pace";

const GLYPH: Record<string, string> = { duplicate: "⚠", price_increase: "↑", bounce_risk: "⏱", salary: "₹" };
const SEVERITY_GLYPH = { bad: "⚠", warn: "!", good: "✓" } as const;

export function Overview({ app }: { app: AppCtx }) {
  return <MonthGate app={app}>{(m) => <OverviewMonth app={app} m={m} />}</MonthGate>;
}

function OverviewMonth({ app, m }: { app: AppCtx; m: MonthCtx }) {
  // This cycle plus the three before it: pace compares against their average, and the unusual-amount check needs history.
  const back = prevCycles(app, m, 3);
  const txState = txnsFor(app, back.at(-1)!.period.start, m.period.end);
  const core = all<[Transaction[], MonthSummary]>(txState, monthSummary(m));
  if (core.status === "loading") return <Loading />;
  if (core.status === "error")
    return <ErrorState error={core.error} title="Couldn't load this month" onRetry={() => invalidate(["/api/summary", "/api/transactions"])} />;
  const [txns, s] = core.data;
  return <Body app={app} m={m} back={back} txns={txns} s={s} />;
}

/** The comparable window of the previous cycle: the same number of days in, or all of it once this month is over. */
function prevWindow(m: MonthCtx, prev: MonthCtx) {
  if (m.complete) return { from: prev.period.start, to: prev.period.end };
  return { from: prev.period.start, to: addDays(prev.period.start, daysBetween(m.period.start, m.through)) };
}

function Body({ app, m, back, txns, s }: { app: AppCtx; m: MonthCtx; back: MonthCtx[]; txns: Transaction[]; s: MonthSummary }) {
  const prev = back[0]!;
  const partial = !m.complete;
  const vs = `${monthShort(prev.key)}${partial ? " at this point" : ""}`;
  const t = s.totals;
  const budgets = dataOf(read(api.budgets(m.key)));
  const recurringState = read(api.recurring());
  const serverAlerts = read(api.alerts(m.key));
  const server = dataOf(serverAlerts);

  // Recomputed only when the cached transactions or the month change, not on unrelated store updates.
  const d = useMemo(() => {
    const T = within(txns, m.period.start, m.through);
    const win = prevWindow(m, prev);
    const P = within(txns, win.from, win.to);
    const weekStart = addDays(m.through, -weekday(m.through));
    const [curStat] = periodStats(txns, [{ ...m.period, end: m.through }]);
    const [prevStat] = periodStats(txns, [{ ...prev.period, start: win.from, end: win.to }]);
    return {
      T,
      P,
      pc: paceChart(m, back, txns),
      wtd: summarize(within(txns, weekStart, m.through)).expense,
      lastWeek: summarize(within(txns, addDays(weekStart, -7), addDays(m.through, -7))).expense,
      merchants: deltaRows(curStat?.byMerchant ?? new Map(), prevStat?.byMerchant ?? new Map(), merchantsBefore(txns, win.from))
        .filter((r) => r.current > 0)
        .sort((a, b) => b.current - a.current)
        .slice(0, 6),
      catPrev: prevStat?.byCategory ?? new Map<string, number>(),
      recent: recent(T),
    };
    // The month context is rebuilt every render; its value fields are the real deps (back derives from them).
  }, [txns, m.key, m.through, m.period.start]);
  const alerts = useMemo(() => needsAttention(d.T, txns, server), [d.T, txns, server]);

  // Figures shown are the server's. A month in progress is compared with the same days last month (a comparison
  // view over the same transactions); a finished month with the server's previous totals.
  const prevTotals: Totals = partial ? summarize(d.P) : s.previous;
  const p = d.pc.pace;
  const cats = s.categories.filter((c) => c.amount > 0).slice(0, 8);
  const denom = Math.max(t.income, t.expense + t.invest, 1);
  const of = (v: number) => Math.round((v / Math.max(t.income, 1)) * 100);
  const lo = leftOver(t);
  const flow: [string, number][] = [
    [FLOW.spend, t.expense],
    [FLOW.invest, t.invest],
    [FLOW.saved, lo],
  ];

  return (
    <>
      <div className="grid g-hero">
        <div className="card hero">
          <div className="lab">
            {partial ? "Spent so far in" : "Spent in"} {monthLong(m.key)}
          </div>
          <div className="big">{inr(t.expense)}</div>
          <div className="pair">
            <span className="pi">
              <span className="pl">This month</span>
              <Delta cur={paceValue(p.cur, p.cur.length)} prev={p.last ? paceValue(p.last, p.cur.length) : null} vs={vs} upIsGood={false} />
            </span>
            <span className="pi">
              <span className="pl">This week {compact(d.wtd)}</span>
              <Delta cur={d.wtd} prev={d.lastWeek} vs="last week" upIsGood={false} />
            </span>
          </div>
          <span className="sub">
            {partial ? `Month to date · through ${dayShort(m.through)}` : "Full month"}
            {t.refunds ? ` · ${inr(t.refunds)} refunded, not netted` : ""}
          </span>
          <div className="kpis" aria-describedby="kpi-vs">
            <Kpi label="Income" v={t.income} prev={prevTotals.income} vs={vs} upIsGood />
            <Kpi label="Invested" v={t.invest} prev={prevTotals.invest} vs={vs} upIsGood />
            <Kpi label="Card spend" v={t.card} prev={prevTotals.card} vs={vs} upIsGood={false} />
            <Kpi label="Left over" v={lo} prev={leftOver(prevTotals)} vs={vs} upIsGood />
          </div>
          <div className="sub kpi-vs" id="kpi-vs">
            Changes vs {vs}
          </div>
          <div className="flow">
            <div className="bar">{t.income > 0 && flow.map(([c, v]) => <div key={c} style={{ width: `${(Math.max(0, v) / denom) * 100}%`, background: c }} />)}</div>
            <div className="leg">
              <span>
                <Dot color={FLOW.spend} />
                Spent {of(t.expense)}%
              </span>
              <span>
                <Dot color={FLOW.invest} />
                Invested {of(t.invest)}%
              </span>
              <span>
                <Dot color={FLOW.saved} />
                Left over {of(lo)}%
              </span>
              <span className="t3">of {compact(t.income)} income</span>
            </div>
          </div>
        </div>
        <div className="card">
          <CardHead title="Spending pace" x="cumulative, by day" />
          <ChartView id="pace" chart={d.pc.chart}>
            {d.pc.legend}
          </ChartView>
        </div>
      </div>

      <div className="grid g-3 mt-g">
        <div className="card">
          <CardHead title="Categories" x={budgets?.size ? "tick = budget" : `vs ${vs}`} />
          {cats.length ? (
            <CategoryBars cats={cats} budgets={budgets} derivedPrev={d.catPrev} useDerived={partial} vs={vs} />
          ) : (
            <InlineState>No spending recorded in {monthLong(m.key)}.</InlineState>
          )}
        </div>
        <div className="card">
          <CardHead title="Top merchants" x={`vs ${vs}`} />
          {d.merchants.length ? (
            <div className="list">
              {d.merchants.map((r) => (
                <div className="li" key={r.key}>
                  <Monogram name={r.key} />
                  <div className="mid">
                    <b>
                      {r.key}
                      {r.isNew && <span className="tag">new</span>}
                    </b>
                    <small>{d.T.find((x) => x.merchant === r.key)?.category ?? "Uncategorized"}</small>
                  </div>
                  <div className="amt num">
                    {inr(r.current)}
                    <small>
                      {!r.previous ? (
                        "first this period"
                      ) : r.delta === 0 ? (
                        "no change"
                      ) : (
                        <>
                          {r.delta > 0 ? "+" : "−"}
                          {compact(Math.abs(r.delta))} <Delta cur={r.current} prev={r.previous} vs={vs} upIsGood={false} compact />
                        </>
                      )}
                    </small>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <InlineState>No merchants yet this month.</InlineState>
          )}
        </div>
        <div className="card">
          <CardHead title="Needs attention" x={String(alerts.length)} />
          {alerts.slice(0, 4).map((a) => (
            <AlertRow key={a.id} a={a} />
          ))}
          {!alerts.length && <InlineState>Nothing unusual this month.</InlineState>}
          {serverAlerts.status === "error" && <InlineState onRetry={() => invalidate(["/api/alerts"])}>Some alerts couldn't load.</InlineState>}
        </div>
      </div>

      <div className="grid g-2 mt-g">
        <div className="card">
          <CardHead title="Coming up" x="predicted from history" />
          <CommittedStrip split={committedSplit(d.T, dataOf(recurringState))} />
          <ComingUp state={recurringState.status === "ready" ? recurringState.data : recurringState.status} asOf={app.asOf} />
        </div>
        <div className="card">
          <CardHead title="Recent" x={<Link href="/activity">See all →</Link>} />
          {d.recent.length ? (
            <div className="list">
              {d.recent.map((t) => (
                <RecentRow key={t.id} t={t} />
              ))}
            </div>
          ) : (
            <InlineState>No activity this month yet.</InlineState>
          )}
        </div>
      </div>
    </>
  );
}

const Kpi = ({ label, v, prev, vs, upIsGood }: { label: string; v: number; prev: number; vs: string; upIsGood: boolean }) => (
  <div className="kpi">
    <div className="l">{label}</div>
    <div className="v">{compact(v)}</div>
    <Delta cur={v} prev={prev} vs={vs} upIsGood={upIsGood} compact />
  </div>
);

function CategoryBars(props: { cats: MonthSummary["categories"]; budgets: Budgets | null; derivedPrev: Map<string, number>; useDerived: boolean; vs: string }) {
  const { cats, budgets, derivedPrev, useDerived, vs } = props;
  const mx = Math.max(cats[0]?.amount ?? 0, ...cats.map((c) => budgets?.get(c.category) ?? 0), 1);
  return (
    <div className="bars">
      {cats.map((c) => {
        const b = budgets?.get(c.category);
        const col = categoryColor(c.category === "Uncategorized" ? null : c.category);
        const prev = useDerived ? (derivedPrev.get(c.category) ?? 0) : c.previous;
        const q = new URLSearchParams({ category: c.category, kind: "all" });
        return (
          <Link key={c.category} href={`/activity?${q}`} className={`brow${b && c.amount > b ? " over" : ""}`}>
            <div className="n">
              <Dot color={col} />
              {c.category}
            </div>
            <div className="track">
              <div className="fill" style={{ width: `${(c.amount / mx) * 100}%`, background: col }} />
              {b ? <div className="mk" style={{ left: `${(b / mx) * 100}%` }} /> : null}
            </div>
            <div className="v num">
              {compact(c.amount)}
              <small>{b ? c.amount > b ? <span className="bad">over</span> : `of ${compact(b)}` : <Delta cur={c.amount} prev={prev} vs={vs} upIsGood={false} compact />}</small>
            </div>
          </Link>
        );
      })}
    </div>
  );
}

const AlertRow = ({ a }: { a: Alert }) => (
  <div className="alert">
    <div className={`ic ${a.severity}`} aria-hidden>
      {GLYPH[a.kind] ?? SEVERITY_GLYPH[a.severity]}
    </div>
    <div>
      <b>{a.title}</b>
      <small>{a.detail}</small>
    </div>
  </div>
);

function CommittedStrip({ split }: { split: { committed: number; discretionary: number } }) {
  const total = split.committed + split.discretionary;
  if (!total) return null;
  return (
    <div className="cd">
      <div className="bar">
        <div style={{ width: `${(split.committed / total) * 100}%`, background: FLOW.committed }} />
        <div style={{ width: `${(split.discretionary / total) * 100}%`, background: FLOW.spend }} />
      </div>
      <div className="leg">
        <span>
          <Dot color={FLOW.committed} />
          Committed {compact(split.committed)}
        </span>
        <span>
          <Dot color={FLOW.spend} />
          Discretionary {compact(split.discretionary)}
        </span>
        <span className="t3">the part you can cut</span>
      </div>
    </div>
  );
}

function ComingUp({ state, asOf }: { state: Recurring[] | null | "loading" | "error"; asOf: string }) {
  if (state === "loading") return <InlineState>Loading…</InlineState>;
  if (state === "error") return <InlineState onRetry={() => invalidate(["/api/recurring"])}>Predictions couldn't load.</InlineState>;
  if (!state) return <InlineState>Predictions appear once Tijori has seen a few months of recurring charges.</InlineState>;
  const next = upcoming(state, asOf);
  if (!next.length) return <InlineState>Nothing predicted for the coming weeks.</InlineState>;
  return (
    <div className="list">
      {next.map((r) => (
        <div className="li" key={r.id}>
          <Monogram name={r.merchant} />
          <div className="mid">
            <b>{r.merchant}</b>
            <small>
              {dayShort(r.next_due)} · {r.cadence}
            </small>
          </div>
          <div className="amt num">{inr(r.amountExpected)}</div>
        </div>
      ))}
    </div>
  );
}

function RecentRow({ t }: { t: Transaction }) {
  const inflow = t.direction === "credit";
  return (
    <div className="li">
      <Monogram name={t.merchant} />
      <div className="mid">
        <b>{t.merchant}</b>
        <small>
          {t.category ?? "Uncategorized"} · {dayName(t.date)}
        </small>
      </div>
      <div className={`amt num${inflow ? " in" : ""}`}>
        {inflow ? "+" : ""}
        {inr(t.amount)}
      </div>
    </div>
  );
}
