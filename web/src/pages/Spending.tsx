import { useMemo, useState } from "react";
import { Sparkline, StackedBars } from "../components/charts";
import { ErrorState, Loading } from "../components/ui";
import { prevCycles, txnsFor, type AppCtx } from "../ctx";
import { all, api, dataOf, invalidate, read } from "../lib/api";
import { BudgetsPanel } from "../components/BudgetsPanel";
import { G } from "../components/Glyphs";
import { categoryColor, slotColor } from "../lib/colors";
import { addDays, compact, dayShort, daysBetween, inr, monthShort, plural, toPaise } from "../lib/format";
import { isExpense, within } from "../lib/insights";
import { byKey, categoryOf, NOTABLE, normalByKey, paidFrom, projection, spendOf } from "../lib/metrics";
import { Link, navigate } from "../lib/router";
import type { ApiCards, ISODate, Transaction, TrendPoint } from "../lib/types";

type Preset = "month" | "last" | "year" | "fy" | "custom";
type Group = "category" | "merchant" | "account";
const PRESETS: [Preset, string][] = [
  ["month", "This month"],
  ["last", "Last month"],
  ["year", "This year"],
  ["fy", "FY"],
  ["custom", "Custom"],
];
const TOP = 7;

function range(p: Preset, app: AppCtx, custom: { from: ISODate; to: ISODate }): { from: ISODate; to: ISODate; label: string } {
  const today = app.asOf;
  const cur = app.cycle(today.slice(0, 7));
  if (p === "month") return { from: cur.period.start, to: today, label: cur.period.label };
  if (p === "last") {
    const prev = prevCycles(app, cur, 1)[0]!;
    return { from: prev.period.start, to: prev.period.end, label: prev.period.label };
  }
  if (p === "year") return { from: `${today.slice(0, 4)}-01-01`, to: today, label: `1 Jan – ${dayShort(today)}` };
  if (p === "fy") {
    const y = Number(today.slice(0, 4)) - (Number(today.slice(5, 7)) < 4 ? 1 : 0);
    return { from: `${y}-04-01`, to: today, label: `FY ${y}–${String(y + 1).slice(2)} to date` };
  }
  return { ...custom, label: `${dayShort(custom.from)} – ${dayShort(custom.to)}` };
}

export function Spending({ app }: { app: AppCtx }) {
  const [preset, setPreset] = useState<Preset>("year");
  const [group, setGroup] = useState<Group>("category");
  const [custom, setCustom] = useState({ from: addDays(app.asOf, -89), to: app.asOf });
  const r = range(preset, app, custom);
  const cur = app.cycle(app.asOf.slice(0, 7));
  const back = prevCycles(app, cur, 3);
  const loadFrom = r.from < back.at(-1)!.period.start ? r.from : back.at(-1)!.period.start;
  const txState = txnsFor(app, loadFrom, app.asOf > r.to ? app.asOf : r.to);
  const trends = read(api.trendsBy(group, "month", 12, app.asOf));
  const recurring = read(api.recurring());
  const st = all(txState, trends);
  return (
    <>
      <div className="ph">
        <h1>Spending</h1>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <div className="seg lg" role="group" aria-label="Period">
          {PRESETS.map(([p, l]) => (
            <button key={p} type="button" className={preset === p ? "on" : ""} aria-pressed={preset === p} onClick={() => setPreset(p)}>
              {l}
            </button>
          ))}
        </div>
        {preset === "custom" ? (
          <label className="datefield">
            <input type="date" aria-label="From" value={custom.from} max={custom.to} onChange={(e) => e.target.value && setCustom({ ...custom, from: e.target.value })} />
            <span className="faint">→</span>
            <input type="date" aria-label="To" value={custom.to} min={custom.from} max={app.asOf} onChange={(e) => e.target.value && setCustom({ ...custom, to: e.target.value })} />
          </label>
        ) : (
          <span className="muted mono-n" style={{ fontSize: 13 }}>
            {r.label}
          </span>
        )}
        <div style={{ flex: 1 }} />
        <span className="muted" style={{ fontSize: 13 }}>
          Group by
        </span>
        <div className="seg lg" role="group" aria-label="Group by">
          {(["category", "merchant", "account"] as const).map((g) => (
            <button key={g} type="button" className={group === g ? "on" : ""} aria-pressed={group === g} onClick={() => setGroup(g)}>
              {g[0]!.toUpperCase() + g.slice(1)}
            </button>
          ))}
        </div>
      </div>
      {st.status === "loading" && <Loading />}
      {st.status === "error" && <ErrorState error={st.error} title="Couldn't load spending" onRetry={() => invalidate(["/api/transactions", "/api/trends"])} />}
      {st.status === "ready" && (
        <Body app={app} preset={preset} r={r} group={group} txns={st.data[0]} trends={st.data[1]} recurringIds={new Set((dataOf(recurring) ?? []).map((x) => x.id.split("@")[0]!))} />
      )}
    </>
  );
}

const keyFn = (g: Group) => (g === "category" ? categoryOf : g === "merchant" ? (t: Transaction) => t.merchant : (t: Transaction) => t.account);
const colorFor = (g: Group, k: string, i: number) => (k === "Other" ? "var(--t3)" : g === "category" ? categoryColor(k === "Uncategorized" ? null : k) : slotColor(i + 1));

function Body({ app, preset, r, group, txns, trends, recurringIds }: { app: AppCtx; preset: Preset; r: { from: ISODate; to: ISODate; label: string }; group: Group; txns: Transaction[]; trends: TrendPoint[] | null; recurringIds: Set<string> }) {
  const cur = app.cycle(app.asOf.slice(0, 7));
  const back = prevCycles(app, cur, 3);
  const dayN = daysBetween(cur.period.start, app.asOf) + 1;
  const key = keyFn(group);
  const R = useMemo(() => within(txns, r.from, r.to), [txns, r.from, r.to]);
  const C = useMemo(() => within(txns, cur.period.start, app.asOf), [txns, cur.key, app.asOf]);
  const totals = byKey(R, key);
  const curBy = byKey(C, key);
  const normal = normalByKey(txns, back.map((b) => b.period), dayN, key);
  const spent = spendOf(R);
  const count = [...totals.values()].reduce((a, v) => a + v.count, 0);

  // Monthly bars from the server (one spend definition), newest last.
  const points = trends ?? [];
  const starts = [...new Set(points.map((p) => p.start))].sort();
  const seriesTotals = new Map<string, number>();
  for (const p of points) if (p.key) seriesTotals.set(p.key, (seriesTotals.get(p.key) ?? 0) + p.amount);
  const ranked = [...seriesTotals].sort((a, b) => b[1] - a[1]).map(([k]) => k);
  const shown = ranked.slice(0, TOP);
  const monthTotal = (s: string) => points.filter((p) => p.start === s).reduce((a, p) => a + p.amount, 0);
  const curStart = starts.at(-1);
  const projected = projection(txns, cur.period, app.asOf);
  const normalMonth = back.length ? Math.round(back.reduce((a, b) => a + monthTotal(b.period.start), 0) / back.length) : null;
  const bars = starts.map((s) => {
    const stacks = shown.map((k, i) => ({ key: k, color: colorFor(group, k, i), value: points.find((p) => p.start === s && p.key === k)?.amount ?? 0 }));
    const other = points.filter((p) => p.start === s && p.key && !shown.includes(p.key)).reduce((a, p) => a + p.amount, 0);
    if (other) stacks.push({ key: "Other", color: "var(--t3)", value: other });
    return { label: s === starts[0] || s.endsWith("-01-01") ? `${monthShort(s.slice(0, 7))} '${s.slice(2, 4)}` : monthShort(s.slice(0, 7)), sub: compact(monthTotal(s)).replace("₹", ""), stacks, projected: s === curStart ? projected : null, now: s === curStart };
  });
  const spark = (k: string) => starts.map((s) => points.find((p) => p.start === s && p.key === k)?.amount ?? 0);

  // Monthly average over the complete months inside the range.
  const fullMonths = starts.filter((s) => s >= r.from && s !== curStart);
  const monthlyAvg = fullMonths.length ? Math.round(fullMonths.reduce((a, s) => a + monthTotal(s), 0) / fullMonths.length) : null;
  const yearView = preset === "year" || preset === "fy";
  const monthsLeft = yearView ? monthsBetween(cur.key, preset === "year" ? `${app.asOf.slice(0, 4)}-12` : `${Number(app.asOf.slice(0, 4)) + (Number(app.asOf.slice(5, 7)) >= 4 ? 1 : 0)}-03`) : 0;
  const projectedTotal = yearView && projected != null && monthlyAvg != null ? spent - spendOf(C) + projected + monthlyAvg * monthsLeft : null;
  const curSpent = spendOf(C);
  const normalSpan = `${monthShort(back.at(-1)!.key)}–${monthShort(back[0]!.key)}`;
  // Per row, the same rule as the strip: full months inside the range only.
  const rowAvg = (k: string) => (fullMonths.length ? Math.round(fullMonths.reduce((a, m) => a + (points.find((p) => p.start === m && p.key === k)?.amount ?? 0), 0) / fullMonths.length) : null);
  const curNormal = [...normal.values()].reduce((a, v) => a + v, 0);

  const rows = [...totals].map(([k, v]) => ({ k, ...v })).sort((a, b) => b.amount - a.amount);
  const top = rows.slice(0, 12);
  const rest = rows.slice(12);
  // Same identity as the server's series: payee_key, else the lower-cased merchant.
  const rec = R.filter((t) => isExpense(t) && t.bucket !== "card" && recurringIds.has(t.payee_key ?? t.merchant.toLowerCase()));
  const recurringSpent = rec.reduce((a, t) => a + t.amount, 0);
  const recurringCount = rec.length;
  const ids = new Map(R.filter((t) => t.category_id != null).map((t) => [t.category ?? "", t.category_id!]));
  const acctIds = new Map(R.filter((t) => t.account_id).map((t) => [t.account, t.account_id!]));
  const href = (k: string) =>
    group === "category"
      ? `/transactions?from=${r.from}&to=${r.to}&category=${k === "Uncategorized" ? "none" : (ids.get(k) ?? "")}`
      : group === "account"
        ? `/transactions?from=${r.from}&to=${r.to}&account=${acctIds.get(k) ?? ""}`
        : `/transactions?from=${r.from}&to=${r.to}&q=${encodeURIComponent(k)}`;

  return (
    <>
      <div className="figstrip">
        <div>
          <span className="k">Spent, {r.label}</span>
          <b className="lg">{inr(spent)}</b>
          <small>{plural(count, "transaction")}</small>
        </div>
        <div>
          <span className="k">Monthly average</span>
          <b className="lg">{monthlyAvg != null ? inr(monthlyAvg) : "—"}</b>
          <small>{fullMonths.length ? `${monthShort(fullMonths[0]!.slice(0, 7))}–${monthShort(fullMonths.at(-1)!.slice(0, 7))}, full months` : "needs a full month"}</small>
        </div>
        {yearView && (
          <div>
            <span className="k">Projected {preset === "year" ? app.asOf.slice(0, 4) : "FY"} total</span>
            <b className="lg">{projectedTotal != null ? inr(projectedTotal) : "—"}</b>
            <small>so far + {monthShort(cur.key)} projection + {monthsLeft} × average</small>
          </div>
        )}
        <div>
          <span className="k">{monthShort(cur.key)} vs normal</span>
          <b className="lg" style={curNormal ? { color: curSpent > curNormal * (1 + NOTABLE) ? "var(--bad)" : curSpent < curNormal * (1 - NOTABLE) ? "var(--in)" : undefined } : undefined}>
            {curNormal ? `${curSpent >= curNormal ? "+" : "−"}${inr(Math.abs(curSpent - curNormal))}` : "—"}
          </b>
          <small>
            {inr(curSpent)} vs {inr(curNormal)} by day {dayN}
          </small>
        </div>
      </div>

      <section className="panel" aria-label="Spend by month">
        <div className="panel-h">
          <h2>Spend by month</h2>
          <span className="foot">{bars.length ? `${bars[0]!.label} – ${bars.at(-1)!.label}` : ""}</span>
          <div className="legend" style={{ marginLeft: "auto" }}>
            {normalMonth != null && <span>- - Normal {compact(normalMonth)} ({normalSpan} avg)</span>}
            <span>▭ Projected rest of {monthShort(cur.key)}</span>
          </div>
        </div>
        <div className="chart2">
          <StackedBars bars={bars} normal={normalMonth} />
        </div>
        <div className="legend">
          {[...shown, ...(ranked.length > TOP ? ["Other"] : [])].map((k, i) => (
            <span key={k}>
              <i className="sq" style={{ background: colorFor(group, k, i) }} />
              {k}
            </span>
          ))}
        </div>
      </section>

      <section className="panel" aria-label="Breakdown">
        <div className="panel-h">
          <h2>By {group}</h2>
          <span className="foot">{r.label}</span>
        </div>
        <table className="t2">
          <thead>
            <tr>
              <th>{group}</th>
              <th className="r">Amount</th>
              <th className="r">Monthly avg</th>
              <th className="r">{monthShort(cur.key)} so far</th>
              <th className="r">vs normal</th>
              <th className="r">Share</th>
              <th>12 months</th>
              <th className="r">Txns</th>
            </tr>
          </thead>
          <tbody>
            {top.map((row, i) => {
              const c = curBy.get(row.k)?.amount ?? 0;
              const n = normal.get(row.k) ?? 0;
              const diff = c - n;
              const notable = n > 0 ? Math.abs(diff) > n * NOTABLE : c > 0;
              const color = colorFor(group, row.k, shown.indexOf(row.k) >= 0 ? shown.indexOf(row.k) : i);
              return (
                <tr key={row.k} className="click" onClick={() => navigate(href(row.k))}>
                  <td>
                    <Link href={href(row.k)} className="nm">
                      <i className="sq" style={{ background: color }} />
                      {row.k}
                    </Link>
                  </td>
                  <td className="r">{inr(row.amount)}</td>
                  <td className="r muted">{rowAvg(row.k) != null ? inr(rowAvg(row.k)!) : "—"}</td>
                  <td className="r">{inr(c)}</td>
                  <td className="r" style={notable ? { color: diff > 0 ? "var(--bad)" : "var(--in)", fontWeight: 500 } : { color: "var(--t3)" }}>
                    {n > 0 ? `${diff >= 0 ? "+" : "−"}${compact(Math.abs(diff))}` : c > 0 ? "new" : "—"}
                  </td>
                  <td className="r muted">{spent ? `${((row.amount / spent) * 100).toFixed(1)}%` : "—"}</td>
                  <td>
                    <Sparkline values={spark(row.k)} color={color.startsWith("var(--c6") ? "#1fa31f" : color} lastOpen />
                  </td>
                  <td className="r muted">{row.count}</td>
                </tr>
              );
            })}
            {rest.length > 0 && (
              <tr>
                <td className="muted">+ {plural(rest.length, "more")}</td>
                <td className="r">{inr(rest.reduce((a, x) => a + x.amount, 0))}</td>
                <td colSpan={6} />
              </tr>
            )}
            <tr className="total">
              <td>Total</td>
              <td className="r">{inr(spent)}</td>
              <td className="r">{monthlyAvg != null ? inr(monthlyAvg) : "—"}</td>
              <td className="r">{inr(curSpent)}</td>
              <td className="r">{curNormal ? `${curSpent >= curNormal ? "+" : "−"}${compact(Math.abs(curSpent - curNormal))}` : "—"}</td>
              <td className="r">100%</td>
              <td />
              <td className="r">{count}</td>
            </tr>
          </tbody>
        </table>
        <span className="foot">
          Normal = {normalSpan} average up to day {dayN}; monthly avg = full months only; "vs normal" coloured when more than 15% off. Hollow dot = month still running. Card purchases count on the card; a card bill paid from the bank counts only while the statement it pays isn't parsed.
        </span>
      </section>

      <section className="panel" aria-label="Recurring vs everything else">
        <div className="panel-h">
          <h2>Recurring vs everything else</h2>
          <span className="foot">{r.label}</span>
        </div>
        {spent > 0 && (
          <>
            <div className="split">
              <div style={{ width: `${(recurringSpent / spent) * 100}%`, background: "var(--c3)" }} />
              <div style={{ width: `${(1 - recurringSpent / spent) * 100}%`, background: "var(--s4)" }} />
            </div>
            <div style={{ display: "flex", gap: 48, alignItems: "flex-end" }}>
              <div>
                <span className="legend">
                  <span>
                    <i className="sq" style={{ background: "var(--c3)" }} />
                    Recurring · {recurringIds.size} series · {plural(recurringCount, "charge")}
                  </span>
                </span>
                <div className="nwfig">
                  <b style={{ fontSize: 20, lineHeight: "28px" }}>{inr(recurringSpent)}</b>
                  <span className="faint mono-n">{((recurringSpent / spent) * 100).toFixed(1)}%</span>
                </div>
              </div>
              <div>
                <span className="legend">
                  <span>
                    <i className="sq" style={{ background: "var(--s4)" }} />
                    Everything else
                  </span>
                </span>
                <div className="nwfig">
                  <b style={{ fontSize: 20, lineHeight: "28px" }}>{inr(spent - recurringSpent)}</b>
                  <span className="faint mono-n">{(((spent - recurringSpent) / spent) * 100).toFixed(1)}%</span>
                </div>
              </div>
            </div>
          </>
        )}
        <span className="foot">
          Recurring = a series on the{" "}
          <Link href="/subscriptions" className="linkx">
            Subscriptions
          </Link>{" "}
          page: 3 or more charges to one payee on a steady cadence (2 for yearly), or one you marked. Card bills and investments are left out.
        </span>
      </section>

      <BudgetsPanel month={cur.key} />

      <Cards R={R} label={r.label} />
    </>
  );
}

/** Spend by account over the range (a card's purchases on the card), and each card's latest statement. */
function Cards({ R, label }: { R: Transaction[]; label: string }) {
  const cards = dataOf(read(api.cards()));
  const rows = paidFrom(R);
  const total = rows.reduce((a, r) => a + r.amount, 0);
  const max = Math.max(1, ...rows.map((r) => r.amount));
  const si = cards?.stand_in;
  return (
    <>
      {cards && cards.items.length > 0 && (
        <section className="panel flat" aria-label="Cards">
          <div className="panel-h">
            <h2>Cards</h2>
            <span className="foot">A purchase is spend on the card it was made with. A bill payment moves money between your accounts, so it is not spend.</span>
          </div>
          <div className="cardgrid">
            {cards.items.map((c) => (
              <CardPanel key={c.account.id} c={c} />
            ))}
          </div>
        </section>
      )}
      <section className="panel" aria-label="Paid from">
        <div className="panel-h">
          <h2>Paid from</h2>
          <span className="foot">{label} · spend only</span>
        </div>
        <div className="paidrows">
          {rows.map((r, i) => (
            <Link key={r.id} href={r.id === "standin" ? "/transactions?direction=debit" : `/transactions?account=${r.id}`} className="paidrow">
              <span className={`nm${r.id === "standin" ? " muted" : ""}`}>
                {r.kind === "card" && <span className="faint">{G.card}</span>}
                {r.label}
              </span>
              <span className="bar">
                <i style={{ width: `${(r.amount / max) * 100}%`, background: r.id === "standin" ? "var(--warn)" : slotColor(i) }} />
              </span>
              <span className="mono-n">{inr(r.amount)}</span>
              <span className="mono-n faint pc">{total ? `${Math.round((r.amount / total) * 100)}%` : "—"}</span>
            </Link>
          ))}
          {!rows.length && <p className="state">Nothing spent in this range.</p>}
        </div>
        <span className="foot">
          Card bills count as spend only when the statement they pay hasn't been parsed
          {si?.count && si.first && si.last ? ` (${plural(si.count, "payment")}, ${inr(toPaise(si.amount))} in all, ${dayShort(si.first)} '${si.first.slice(2, 4)} – ${dayShort(si.last)} '${si.last.slice(2, 4)})` : ""}. Bill
          payments matched to a card's "Payment received" line are transfers.
        </span>
      </section>
    </>
  );
}

function CardPanel({ c }: { c: ApiCards["items"][number] }) {
  const s = c.statement;
  const total = s ? toPaise(s.total) : 0;
  const state = !s ? ["No statement yet", "none"] : s.state === "paid" ? ["Paid", "paid"] : s.due_date ? [`Due ${dayShort(s.due_date)}`, "due"] : ["Not paid yet", "due"];
  return (
    <div className="cardp">
      <div className="hd">
        <span className="chipcard" />
        <div style={{ minWidth: 0 }}>
          <b>{c.account.label}</b>
          <small>{s ? `Statement ${dayShort(s.period_start)} – ${dayShort(s.period_end)}` : "Add a card statement to itemise its purchases"}</small>
        </div>
        <span className={`state ${state[1]}`}>{state[0]}</span>
      </div>
      <div className="facts3">
        <div>
          <span className="k">{s ? `Statement ${dayShort(s.period_end)}` : "Statement"}</span>
          <b>{s ? inr(total) : "—"}</b>
          <small>{s ? plural(s.purchases, "purchase") : ""}</small>
        </div>
        <div>
          <span className="k">{s?.paid_at ? `Paid ${dayShort(s.paid_at)}` : s?.due_date ? `Due ${dayShort(s.due_date)}` : "Paid"}</span>
          <b>{s ? inr(s.paid_at ? toPaise(s.paid) : total) : "—"}</b>
          <small>{s?.paid_from ? `From ${s.paid_from}` : s ? "No payment seen yet" : ""}</small>
        </div>
        <div>
          <span className="k">This cycle so far</span>
          <b>{inr(toPaise(c.cycle.amount))}</b>
          <small>
            {dayShort(c.cycle.since)}
            {c.cycle.seen_through ? ` – ${dayShort(c.cycle.seen_through)}` : ""} · {plural(c.cycle.count, "purchase")}
          </small>
        </div>
      </div>
    </div>
  );
}

function monthsBetween(a: string, b: string): number {
  return Number(b.slice(0, 4)) * 12 + Number(b.slice(5, 7)) - (Number(a.slice(0, 4)) * 12 + Number(a.slice(5, 7)));
}
