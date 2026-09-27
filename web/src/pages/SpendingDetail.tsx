import { useMemo } from "react";
import { LineChart, ProgressChart, type Line } from "../components/charts";
import { G } from "../components/Glyphs";
import { ErrorState, Loading } from "../components/ui";
import { prevCycles, txnsFor, type AppCtx, type MonthCtx } from "../ctx";
import { invalidate } from "../lib/api";
import { categoryColor, lineInk, OTHER, slotColor } from "../lib/colors";
import { addDays, addMonths, changeText, compact, dayShort, daysBetween, inr, monthApos, monthShort, monthYear, plural } from "../lib/format";
import { isExpense, spendAmount, within } from "../lib/insights";
import { categoryOf, cumulative, groupKey, normalByKey, normalCurve, NOTABLE, type Group } from "../lib/metrics";
import { Link, navigate, useLocation } from "../lib/router";
import type { Paise, Transaction } from "../lib/types";

const SPANS = [6, 12, 24] as const;
/** Up to five lines: past that the categorical palette stops telling them apart. */
const MAX_VS = 4;
const OPTIONS = 40;
const PARTS = 8;
const GROUP_LABEL: Record<Group, string> = { category: "Category", merchant: "Merchant", account: "Account" };

export const detailHref = (group: Group, key: string) => `/spending/${group}/${encodeURIComponent(key)}`;

let spendingUrl: string | null = null;
/** Spending records its URL on the way in, so this page's back link restores its period and grouping. */
export const rememberSpending = () => {
  spendingUrl = location.pathname + location.search;
};

export function SpendingDetail({ app, group, name }: { app: AppCtx; group: Group; name: string }) {
  const { params } = useLocation();
  const span = SPANS.find((s) => String(s) === params.get("months")) ?? 12;
  const vs = [...new Set(params.getAll("vs"))].filter((k) => k !== name).slice(0, MAX_VS);
  const set = (patch: { span?: number; vs?: string[] }) => {
    const p = new URLSearchParams();
    const s = patch.span ?? span;
    if (s !== 12) p.set("months", String(s));
    for (const k of patch.vs ?? vs) p.append("vs", k);
    const q = p.toString();
    navigate(`${detailHref(group, name)}${q ? `?${q}` : ""}`, { replace: true });
  };
  const cur = app.cycle(app.asOf.slice(0, 7));
  const first = app.months[0];
  const cycles = Array.from({ length: span }, (_, i) => addMonths(cur.key, i - span + 1))
    .filter((k) => !first || k >= first)
    .map((k) => app.cycle(k));
  const back = prevCycles(app, cur, 3);
  const from = cycles[0]!.period.start < back.at(-1)!.period.start ? cycles[0]!.period.start : back.at(-1)!.period.start;
  const st = txnsFor(app, from, app.asOf);
  const backHref = spendingUrl ?? `/spending${group === "category" ? "" : `?group=${group}`}`;
  return (
    <>
      <div className="ph">
        <Link href={backHref} className="iconbtn" aria-label="Back to Spending" title="Back to Spending">
          {G.left}
        </Link>
        <h1>{name}</h1>
        <span className="faint">{GROUP_LABEL[group]}</span>
      </div>
      {st.status === "loading" && <Loading />}
      {st.status === "error" && <ErrorState error={st.error} title="Couldn't load spending" onRetry={() => invalidate(["/api/transactions"])} />}
      {st.status === "ready" && <Body app={app} group={group} name={name} vs={vs} span={span} set={set} cur={cur} cycles={cycles} back={back} txns={st.data} />}
    </>
  );
}

interface Per {
  values: Paise[];
  counts: number[];
  total: Paise;
}

function Body(o: { app: AppCtx; group: Group; name: string; vs: string[]; span: number; set: (p: { span?: number; vs?: string[] }) => void; cur: MonthCtx; cycles: MonthCtx[]; back: MonthCtx[]; txns: Transaction[] }) {
  const { group, name, vs, cur, cycles, back, txns } = o;
  const key = groupKey(group);
  const n = cycles.length;
  const sig = `${cycles[0]!.key}|${n}|${cur.through}`;
  // One pass over the window: every key's spend per cycle, so the compare picker and the lines share it.
  const per = useMemo(() => {
    const starts = cycles.map((c) => c.period.start);
    const out = new Map<string, Per>();
    for (const t of txns) {
      if (!isExpense(t) || t.date < starts[0]! || t.date > cur.through) continue;
      let i = n - 1;
      while (i > 0 && t.date < starts[i]!) i--;
      const k = key(t);
      let e = out.get(k);
      if (!e) out.set(k, (e = { values: new Array<number>(n).fill(0), counts: new Array<number>(n).fill(0), total: 0 }));
      e.values[i]! += spendAmount(t);
      e.counts[i]! += 1;
      e.total += spendAmount(t);
    }
    return out;
  }, [txns, group, sig]);
  const mine = useMemo(() => txns.filter((t) => isExpense(t) && key(t) === name), [txns, group, name]);
  const zero = (): Per => ({ values: new Array<number>(n).fill(0), counts: new Array<number>(n).fill(0), total: 0 });
  const P = per.get(name) ?? zero();

  const keys = [name, ...vs];
  const colors = new Map<string, string>();
  for (const k of keys) {
    const c = group === "category" ? categoryColor(k === "Uncategorized" ? null : k) : OTHER;
    if (c !== OTHER && ![...colors.values()].includes(c)) colors.set(k, c);
  }
  for (let s = 1, j = 0; j < keys.length; j++) {
    if (colors.has(keys[j]!)) continue;
    while (s < 8 && [...colors.values()].includes(slotColor(s))) s++;
    colors.set(keys[j]!, slotColor(s));
  }
  const lines: Line[] = keys.map((k) => ({ key: k, color: lineInk(colors.get(k)!), values: (per.get(k) ?? zero()).values }));
  const options = [...per]
    .filter(([k]) => !keys.includes(k))
    .sort((a, b) => b[1].total - a[1].total)
    .slice(0, OPTIONS);

  const running = !cur.complete;
  const full = running ? n - 1 : n;
  const avg = full > 0 ? Math.round(P.values.slice(0, full).reduce((a, v) => a + v, 0) / full) : null;
  const lastFull = full > 0 ? full - 1 : null;
  const days = daysBetween(cur.period.start, cur.period.end) + 1;
  const dayN = daysBetween(cur.period.start, cur.through) + 1;
  const curSpent = P.values[n - 1]!;
  const normal = normalByKey(txns, back.map((b) => b.period), dayN, key).get(name) ?? 0;
  const diff = curSpent - normal;
  const tone = (d: number, base: number) => (base > 0 && Math.abs(d) > base * NOTABLE ? (d > 0 ? "var(--bad)" : "var(--in)") : undefined);
  const progress = useMemo(
    () => ({ actual: cumulative(within(mine, cur.period.start, cur.through), cur.period.start, dayN), normal: normalCurve(mine, back.map((b) => b.period), days) }),
    [mine, cur.key, cur.through],
  );

  const labels = cycles.map((c, i) => (i === 0 || c.key.endsWith("-01") ? monthApos(`${c.key}-01`) : monthShort(c.key)));
  const tips = cycles.map((c) => (o.app.settings.monthStartDay === 1 ? monthYear(c.key) : c.period.label));
  const range = { from: cycles[0]!.period.start, to: cur.through };
  const catIds = new Map(txns.filter((t) => t.category_id != null).map((t) => [t.category ?? "", t.category_id!]));
  const acctIds = new Map(txns.filter((t) => t.account_id).map((t) => [t.account, t.account_id!]));
  const catParam = (c: string) => (c === "Uncategorized" ? "none" : String(catIds.get(c) ?? ""));
  const scope = group === "category" ? `category=${catParam(name)}` : group === "account" ? `account=${acctIds.get(name) ?? ""}` : `q=${encodeURIComponent(name)}`;
  const txHref = `/transactions?from=${range.from}&to=${range.to}&${scope}`;

  // Category → which merchants; merchant or account → which categories.
  const partKey = group === "category" ? (t: Transaction) => t.merchant : categoryOf;
  const parts = useMemo(() => {
    const m = new Map<string, Paise>();
    for (const t of within(mine, range.from, range.to)) m.set(partKey(t), (m.get(partKey(t)) ?? 0) + t.amount);
    const sorted = [...m].sort((a, b) => b[1] - a[1]);
    const top = sorted.slice(0, PARTS);
    const rest = sorted.slice(PARTS).reduce((a, [, v]) => a + v, 0);
    return rest > 0 ? [...top, ["Other", rest] as [string, Paise]] : top;
  }, [mine, group, range.from, range.to]);
  const partMax = Math.max(1, ...parts.map((p) => p[1]));
  const partHref = (k: string) => (group === "category" ? `${txHref}&q=${encodeURIComponent(k)}` : `${txHref}&category=${catParam(k)}`);

  const spanLabel = `${monthShort(cycles[0]!.key)} '${cycles[0]!.key.slice(2, 4)} – ${monthShort(cur.key)} '${cur.key.slice(2, 4)}`;
  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <div className="seg lg" role="group" aria-label="Months">
          {SPANS.map((s) => (
            <button key={s} type="button" className={o.span === s ? "on" : ""} aria-pressed={o.span === s} onClick={() => o.set({ span: s })}>
              {s} months
            </button>
          ))}
        </div>
        <span className="muted mono-n" style={{ fontSize: 13 }}>
          {spanLabel}
        </span>
        <div style={{ flex: 1 }} />
        <div className="chiprow">
          {vs.map((k) => (
            <span key={k} className="chip-v">
              <i className="sq" style={{ background: colors.get(k), width: 12, height: 2 }} />
              {k}
              <button type="button" aria-label={`Stop comparing ${k}`} onClick={() => o.set({ vs: vs.filter((x) => x !== k) })}>
                ×
              </button>
            </span>
          ))}
          {vs.length < MAX_VS && options.length > 0 && (
            <select className="inp2" aria-label={`Compare with another ${group}`} value="" onChange={(e) => e.target.value && o.set({ vs: [...vs, e.target.value] })}>
              <option value="">+ Compare with…</option>
              {options.map(([k, v]) => (
                <option key={k} value={k}>
                  {k} · {compact(v.total)}
                </option>
              ))}
            </select>
          )}
        </div>
      </div>

      <div className="figstrip">
        <div>
          <span className="k">{monthShort(cur.key)} so far</span>
          <b className="lg">{inr(curSpent)}</b>
          <small>{plural(P.counts[n - 1]!, "transaction")}</small>
        </div>
        <div>
          <span className="k">vs normal by day {dayN}</span>
          <b className="lg" style={{ color: normal ? tone(diff, normal) : undefined }}>
            {normal ? `${diff >= 0 ? "+" : "−"}${inr(Math.abs(diff))}` : "—"}
          </b>
          <small>{normal ? `normal ${inr(normal)}, ${monthShort(back.at(-1)!.key)}–${monthShort(back[0]!.key)} avg` : "no spend in the 3 months before"}</small>
        </div>
        <div>
          <span className="k">{lastFull != null ? monthShort(cycles[lastFull]!.key) : "Last full month"}</span>
          <b className="lg">{lastFull != null ? inr(P.values[lastFull]!) : "—"}</b>
          <small style={lastFull != null && lastFull > 0 ? { color: tone(P.values[lastFull]! - P.values[lastFull - 1]!, P.values[lastFull - 1]!) } : undefined}>
            {lastFull != null && lastFull > 0 ? `${changeText(P.values[lastFull]!, P.values[lastFull - 1]!)} vs ${monthShort(cycles[lastFull - 1]!.key)} (${inr(P.values[lastFull - 1]!)})` : "needs two full months"}
          </small>
        </div>
        <div>
          <span className="k">Monthly average</span>
          <b className="lg">{avg != null ? inr(avg) : "—"}</b>
          <small>{full > 0 ? `${plural(full, "full month")}, ${inr(P.total)} in all` : "needs a full month"}</small>
        </div>
      </div>

      <section className="panel" aria-label="Spend by month">
        <div className="panel-h">
          <h2>Spend by month</h2>
          <span className="foot">{spanLabel}</span>
          {lines.length > 1 && (
            <div className="legend" style={{ marginLeft: "auto" }}>
              {lines.map((l) => (
                <span key={l.key}>
                  <i className="sq" style={{ background: l.color, width: 14, height: 2 }} />
                  {l.key}
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="chart2">
          <LineChart labels={labels} tips={tips} series={lines} openLast={running} label={`${name}: spend by month`} />
        </div>
        <span className="foot">Hover a month for every line's amount and its change on the month before. Hollow dot, dashed segment = {monthShort(cur.key)} so far.</span>
      </section>

      <section className="panel" aria-label="This month by day">
        <div className="panel-h">
          <h2>
            {monthShort(cur.key)} day by day
          </h2>
          <span className="foot">{name} · cumulative</span>
          <div className="legend" style={{ marginLeft: "auto" }}>
            <span>
              <i className="sq" style={{ background: "#3987e5", width: 14, height: 2 }} />
              Spent
            </span>
            {progress.normal && <span>- - Normal ({monthShort(back.at(-1)!.key)}–{monthShort(back[0]!.key)} avg)</span>}
          </div>
        </div>
        <div className="chart2">
          <ProgressChart
            days={days}
            actual={progress.actual}
            normal={progress.normal}
            projected={null}
            mark={null}
            dayLabel={(d) => (d === 1 ? dayShort(cur.period.start) : String(d))}
            tipLabel={(d) => `${dayShort(addDays(cur.period.start, d - 1))} · day ${d}`}
          />
        </div>
      </section>

      <section className="panel" aria-label="Month by month">
        <div className="panel-h">
          <h2>Month by month</h2>
          <Link href={txHref} className="linkx" style={{ marginLeft: "auto" }}>
            Transactions →
          </Link>
        </div>
        <table className="t2 months">
          <thead>
            <tr>
              <th>Month</th>
              {lines.map((l) => (
                <th key={l.key} className="r">
                  {lines.length > 1 && <i className="sq" style={{ background: l.color, width: 12, height: 2, marginRight: 6, verticalAlign: 3 }} />}
                  {lines.length > 1 ? l.key : "Amount"}
                </th>
              ))}
              {lines.length === 1 && <th className="r">vs month before</th>}
              {lines.length === 1 && <th className="r">Txns</th>}
            </tr>
          </thead>
          <tbody>
            {cycles
              .map((c, i) => ({ c, i }))
              .reverse()
              .map(({ c, i }) => (
                <tr key={c.key}>
                  <td>
                    {tips[i]}
                    {i === n - 1 && running && <small> · so far</small>}
                  </td>
                  {lines.map((l) => {
                    const v = l.values[i]!;
                    const p = i > 0 && !(running && i === n - 1) ? l.values[i - 1]! : null;
                    return (
                      <td key={l.key} className="r">
                        {inr(v)}
                        {lines.length > 1 && p != null && (
                          <small style={{ display: "block", color: tone(v - p, p) }}>{changeText(v, p)}</small>
                        )}
                      </td>
                    );
                  })}
                  {lines.length === 1 && (
                    <td className="r" style={{ color: i > 0 && !(running && i === n - 1) ? (tone(P.values[i]! - P.values[i - 1]!, P.values[i - 1]!) ?? "var(--t3)") : "var(--t3)" }}>
                      {i > 0 && !(running && i === n - 1) ? `${P.values[i]! >= P.values[i - 1]! ? "+" : "−"}${compact(Math.abs(P.values[i]! - P.values[i - 1]!))} · ${changeText(P.values[i]!, P.values[i - 1]!)}` : "—"}
                    </td>
                  )}
                  {lines.length === 1 && <td className="r muted">{P.counts[i]}</td>}
                </tr>
              ))}
            <tr className="total">
              <td>Monthly avg</td>
              {lines.map((l) => (
                <td key={l.key} className="r">
                  {full > 0 ? inr(Math.round(l.values.slice(0, full).reduce((a, v) => a + v, 0) / full)) : "—"}
                </td>
              ))}
              {lines.length === 1 && <td />}
              {lines.length === 1 && <td />}
            </tr>
          </tbody>
        </table>
        <span className="foot">Monthly avg = full months only. Changes coloured when more than 15% off the month before; the month still running is compared with normal above instead.</span>
      </section>

      <section className="panel" aria-label="Breakdown">
        <div className="panel-h">
          <h2>{group === "category" ? "Merchants" : "Categories"}</h2>
          <span className="foot">{spanLabel}</span>
        </div>
        <div className="paidrows">
          {parts.map(([k, v], i) => (
            <Link key={k} href={k === "Other" ? txHref : partHref(k)} className="paidrow">
              <span className={`nm${k === "Other" ? " muted" : ""}`}>{k}</span>
              <span className="bar">
                <i style={{ width: `${(v / partMax) * 100}%`, background: k === "Other" ? "var(--t3)" : group === "category" ? slotColor(i + 1) : categoryColor(k === "Uncategorized" ? null : k) }} />
              </span>
              <span className="mono-n">{inr(v)}</span>
              <span className="mono-n faint pc">{P.total ? `${Math.round((v / P.total) * 100)}%` : "—"}</span>
            </Link>
          ))}
          {!parts.length && <p className="state">Nothing spent here in the last {plural(n, "month")}.</p>}
        </div>
      </section>
    </>
  );
}
