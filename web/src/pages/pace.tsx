import type { ReactNode } from "react";
import { lineChart, type Chart, type LineSeries } from "../charts";
import type { MonthCtx } from "../ctx";
import { html } from "../lib/dom";
import { compact, monthLong, monthShort } from "../lib/format";
import { pace, paceValue, type Pace } from "../lib/insights";
import type { Transaction } from "../lib/types";

/** Month-to-date spend against last month, the trailing-3 average and an even pace to that average. */
export function paceChart(m: MonthCtx, back: MonthCtx[], txns: Transaction[]): { chart: Chart; legend: ReactNode; pace: Pace } {
  const p = pace(txns, m.period, m.through, back.map((b) => b.period));
  const lastName = back[0] ? monthShort(back[0].key) : "Last month";
  const series: LineSeries[] = [{ id: "cur", name: monthShort(m.key), color: "var(--c1)", points: p.cur, area: true }];
  if (p.last) series.push({ id: "last", name: lastName, color: "var(--t3)", points: p.last, dash: "4 4" });
  if (p.avg) series.push({ id: "avg", name: "3-month average", color: "var(--t2)", points: p.avg, dash: "1 4" });
  if (p.ideal) series.push({ id: "ideal", name: "Even pace", color: "var(--accent)", points: p.ideal, width: 1 });
  const chart = lineChart({
    label: `Cumulative spending by day in ${monthLong(m.key)} against ${lastName}, the 3-month average and an even pace`,
    series,
    formatX: String,
    tipTitle: (d) => `Day ${d}`,
    tipExtra: (d) => {
      if (!p.avg || d > p.cur.length) return null;
      const diff = paceValue(p.cur, d) - paceValue(p.avg, d);
      return html`<div class="tnote ${diff > 0 ? "bad" : "good"}">${compact(Math.abs(diff))} ${diff > 0 ? "ahead of" : "behind"} average pace</div>`;
    },
  });
  const key = (c: string, name: string, cls = "") => (
    <span key={name}>
      <i className={cls} style={{ background: c }} />
      {name}
    </span>
  );
  const legend = (
    <div className="leg2">
      {key("var(--c1)", monthShort(m.key))}
      {p.last && key("var(--t3)", lastName, "dash")}
      {p.avg && key("var(--t2)", "3-mo avg", "dots")}
      {p.ideal && key("var(--accent)", "Even pace", "thin")}
    </div>
  );
  return { chart, legend, pace: p };
}
