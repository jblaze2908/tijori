import { useMemo, type ReactNode } from "react";
import { NetWorthChart, ProgressChart } from "../components/charts";
import { G } from "../components/Glyphs";
import { ErrorState, Loading } from "../components/ui";
import { MonthGate, prevCycles, txnsFor, type AppCtx, type MonthCtx } from "../ctx";
import { api, dataOf, invalidate, read } from "../lib/api";
import { categoryColor, monogramColor } from "../lib/colors";
import { compact, dayShort, daysBetween, inr, monthLong, monthShort, monthApos, plural, toPaise } from "../lib/format";
import { within } from "../lib/insights";
import { byKey, categoryOf, cumulative, largestCharge, normalByKey, normalCurve, NOTABLE, paidFrom, projection, spendOf } from "../lib/metrics";
import { Link, navigate, useLocation } from "../lib/router";
import type { Transaction } from "../lib/types";

export function Overview({ app, head }: { app: AppCtx; head: ReactNode }) {
  return (
    <>
      {head}
      <MonthGate app={app}>{(m) => <OverviewMonth app={app} m={m} />}</MonthGate>
    </>
  );
}

function OverviewMonth({ app, m }: { app: AppCtx; m: MonthCtx }) {
  const back = prevCycles(app, m, 3);
  const st = txnsFor(app, back.at(-1)!.period.start, m.period.end);
  if (st.status === "loading") return <Loading />;
  if (st.status === "error") return <ErrorState error={st.error} title="Couldn't load this month" onRetry={() => invalidate(["/api/transactions"])} />;
  return <Body m={m} back={back} txns={st.data} />;
}

const signedK = (v: number) => (v === 0 ? "±₹0" : `${v > 0 ? "+" : "−"}${compact(Math.abs(v))}`);

function Body({ m, back, txns }: { m: MonthCtx; back: MonthCtx[]; txns: Transaction[] }) {
  const days = daysBetween(m.period.start, m.period.end) + 1;
  const dayN = m.complete ? days : daysBetween(m.period.start, m.through) + 1;
  const T = useMemo(() => within(txns, m.period.start, m.through), [txns, m.key, m.through]);
  const prevPeriods = back.map((b) => b.period);
  const d = useMemo(() => {
    const curve = normalCurve(txns, prevPeriods, days);
    const big = largestCharge(T);
    return {
      spent: spendOf(T),
      actual: cumulative(T, m.period.start, dayN),
      normal: curve,
      projected: m.complete ? null : projection(txns, m.period, m.through),
      big,
      paid: paidFrom(T),
    };
    // Period fields are the real deps; the objects are rebuilt every render.
  }, [txns, T, m.key, dayN]);
  const normalMonth = d.normal?.at(-1) ?? null;
  const normalToday = d.normal ? d.normal[dayN - 1]! : null;
  const mon = monthShort(m.key);

  return (
    <>
      <NeedsYou month={m.key} />
      <section className="panel progress" aria-label="Month progress">
        <div className="stats">
          <span className="lbl">
            {monthLong(m.key)} · {m.complete ? "full month" : `day ${dayN} of ${days}`}
          </span>
          <div className="figs">
            <Fig line={<i style={{ width: 16, height: 2, borderRadius: 1, background: "var(--c1)", display: "block" }} />} k={m.complete ? "Spent" : "Spent so far"} v={inr(d.spent)} />
            {d.projected != null && <Fig line={<Dash color="#3987E5" />} k={`Projected by ${dayShort(m.period.end)}`} v={inr(d.projected)} />}
            {normalMonth != null && <Fig line={<Dash color="#8A8A8A" gap />} k="Your normal month" v={inr(normalMonth)} muted />}
            {normalToday != null && !m.complete && <Fig line={<span />} k={`Normal by day ${dayN}`} v={inr(normalToday)} muted />}
          </div>
          <span className="foot">
            Normal = average of {monthShort(back.at(-1)!.key)}–{monthShort(back[0]!.key)}
            {m.complete ? "" : ` up to day ${dayN}`}.{" "}
            {d.projected != null && "Projection = last 14 days' daily average × days left, leaving out single charges over ₹5,000."}
          </span>
        </div>
        <div className="chart2">
          <ProgressChart
            days={days}
            actual={d.actual}
            normal={d.normal}
            projected={d.projected}
            mark={d.big ? { day: daysBetween(m.period.start, d.big.date) + 1, label: `${d.big.merchant} · ${inr(d.big.amount)}` } : null}
            dayLabel={(x) => (x === 1 ? `1 ${mon}` : String(x))}
          />
        </div>
      </section>
      <div className="row2">
        <WhereItWent m={m} T={T} txns={txns} back={back} dayN={dayN} />
        <PaidFromPanel rows={d.paid} total={d.spent} />
      </div>
      <div className="row2">
        <Recent T={T} m={m} />
        <NetWorthCard />
      </div>
    </>
  );
}

const Fig = ({ line, k, v, muted }: { line: ReactNode; k: string; v: string; muted?: boolean }) => (
  <div className="r">
    <span className="ln">{line}</span>
    <span className="k">{k}</span>
    <b className={muted ? "muted" : ""}>{v}</b>
  </div>
);
const Dash = ({ color, gap }: { color: string; gap?: boolean }) => (
  <svg width="16" height="2" viewBox="0 0 16 2" aria-hidden>
    <path d={gap ? "M0 1h4M7 1h4M14 1h2" : "M0 1h2.5M5 1h2.5M10 1h2.5M15 1h1"} stroke={color} strokeWidth={gap ? 1.5 : 2} />
  </svg>
);

function NeedsYou({ month }: { month: string }) {
  const inbox = dataOf(read(api.inbox()));
  const alerts = dataOf(read(api.alerts(month))) ?? [];
  const waiting = inbox?.total ?? 0;
  const n = alerts.length + (waiting ? 1 : 0);
  if (!n) return null;
  return (
    <div className="needs" role="region" aria-label="Needs you">
      <span className="lbl">Needs you · {n}</span>
      <div className="items">
        {waiting > 0 && (
          <Link href="/inbox" className="pill2">
            <i />
            {plural(waiting, "new payee")} to categorize
          </Link>
        )}
        {alerts.map((a) => (
          <Link key={a.id} href={a.txn_ids?.length ? `/transactions?txn=${a.txn_ids[0]}` : "/spending"} className={`pill2 ${a.severity === "bad" ? "bad" : "warn"}`} title={a.detail}>
            <i />
            {a.title} · {a.detail}
          </Link>
        ))}
      </div>
      <Link href="/inbox" className="linkx">
        Review →
      </Link>
    </div>
  );
}

function WhereItWent({ m, T, txns, back, dayN }: { m: MonthCtx; T: Transaction[]; txns: Transaction[]; back: MonthCtx[]; dayN: number }) {
  // In the URL so Back from a drill-down keeps the toggle.
  const { path, params } = useLocation();
  const mode = params.get("by") === "merchant" ? "merchant" : "category";
  const setMode = (k: typeof mode) => {
    const p = new URLSearchParams(params);
    if (k === "merchant") p.set("by", k);
    else p.delete("by");
    const q = p.toString();
    navigate(`${path}${q ? `?${q}` : ""}`, { replace: true });
  };
  const key = mode === "category" ? categoryOf : (t: Transaction) => t.merchant;
  const cur = byKey(T, key);
  const normal = normalByKey(txns, back.map((b) => b.period), dayN, key);
  const ids = new Map(T.filter((t) => t.category_id != null).map((t) => [t.category ?? "", t.category_id!]));
  const all = [...cur].map(([k, v]) => ({ k, ...v, normal: normal.get(k) ?? 0 })).sort((a, b) => b.amount - a.amount);
  const rows = all.slice(0, 7);
  const rest = all.slice(7);
  if (rest.length) rows.push({ k: "Other", amount: rest.reduce((a, r) => a + r.amount, 0), count: rest.reduce((a, r) => a + r.count, 0), normal: rest.reduce((a, r) => a + r.normal, 0) });
  const max = Math.max(1, ...rows.map((r) => Math.max(r.amount, r.normal)));
  const range = `from=${m.period.start}&to=${m.through}`;
  return (
    <section className="panel grow" aria-label="Where it went">
      <div className="panel-h">
        <h2>Where it went</h2>
        <div className="seg" role="tablist">
          {(["category", "merchant"] as const).map((k) => (
            <button key={k} type="button" role="tab" aria-selected={mode === k} className={mode === k ? "on" : ""} onClick={() => setMode(k)}>
              {k === "category" ? "Category" : "Merchant"}
            </button>
          ))}
        </div>
        <div className="legend" style={{ marginLeft: "auto" }}>
          <span>
            <i className="bar" />
            so far
          </span>
          <span>
            <i className="tick" />
            normal by day {dayN}
          </span>
        </div>
      </div>
      {rows.length ? (
        <div className="cats2">
          {rows.map((r) => {
            const diff = r.amount - r.normal;
            const notable = r.normal > 0 ? Math.abs(diff) > r.normal * NOTABLE : r.amount > 0;
            const color = mode === "category" ? (r.k === "Other" ? "var(--t3)" : categoryColor(r.k === "Uncategorized" ? null : r.k)) : "var(--c1)";
            const href =
              r.k === "Other"
                ? `/transactions?${range}`
                : mode === "category"
                  ? `/transactions?${range}&category=${r.k === "Uncategorized" ? "none" : (ids.get(r.k) ?? "")}`
                  : `/transactions?${range}&q=${encodeURIComponent(r.k)}`;
            return (
              <Link key={r.k} href={href} className="catrow">
                <span className="n">
                  <i className="sq" style={{ background: color }} />
                  <span>{r.k}</span>
                  {r.k === "Other" ? <small>{rest.length} more</small> : r.count === 1 ? <small>1 charge</small> : null}
                </span>
                <span className="track2">
                  <span className="f" style={{ width: `${(r.amount / max) * 100}%`, background: color }} />
                  {r.normal > 0 && <span className="m" style={{ left: `${(r.normal / max) * 100}%` }} />}
                </span>
                <span className="a">{inr(r.amount)}</span>
                <span className="d" style={notable ? { color: diff > 0 ? "var(--bad)" : "var(--in)", fontWeight: 500 } : undefined}>
                  {r.normal > 0 ? signedK(diff) : "new"}
                </span>
              </Link>
            );
          })}
        </div>
      ) : (
        <p className="state">No spending recorded in {monthLong(m.key)} yet.</p>
      )}
      <div className="panel-h" style={{ borderTop: "1px solid var(--line)", paddingTop: 12 }}>
        <span className="foot">Coloured when more than 15% off normal. Card purchases count on the card; a card bill counts only while its statement is not parsed.</span>
        <Link href={`/spending`} className="x linkx">
          All spending →
        </Link>
      </div>
    </section>
  );
}

const KIND_GROUP: Record<string, string> = { card: "Cards", bank: "Bank", wallet: "Wallet", standin: "Card bills" };
const GROUP_COLOR: Record<string, string> = { Cards: "var(--t1)", Bank: "var(--t2)", Wallet: "var(--t3)", "Card bills": "var(--warn)", Other: "var(--s4)" };

function PaidFromPanel({ rows, total }: { rows: ReturnType<typeof paidFrom>; total: number }) {
  const groups = new Map<string, number>();
  for (const r of rows) groups.set(KIND_GROUP[r.kind ?? ""] ?? "Other", (groups.get(KIND_GROUP[r.kind ?? ""] ?? "Other") ?? 0) + r.amount);
  const order = ["Cards", "Bank", "Wallet", "Card bills", "Other"].filter((g) => groups.get(g));
  const cardBills = rows.find((r) => r.id === "standin")?.amount ?? 0;
  const billCat = (dataOf(read(api.categories())) ?? []).find((c) => c.name === "Card bill payment")?.id;
  return (
    <section className="panel side2" aria-label="Paid from">
      <div className="panel-h">
        <h2>Paid from</h2>
        <span className="x">{inr(total)} this month</span>
      </div>
      {total > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div className="split">
            {order.map((g) => (
              <div key={g} style={{ width: `${((groups.get(g) ?? 0) / total) * 100}%`, background: GROUP_COLOR[g] }} />
            ))}
          </div>
          <div className="legend" style={{ color: "var(--t2)", fontSize: 13 }}>
            {order.map((g) => (
              <span key={g}>
                {g} {Math.round(((groups.get(g) ?? 0) / total) * 100)}%
              </span>
            ))}
          </div>
        </div>
      )}
      <div className="rows">
        {rows.map((r) => (
          <Link key={r.id} href={r.id === "standin" ? `/transactions?direction=debit${billCat ? `&category=${billCat}` : ""}` : `/transactions?account=${r.id}`} className="lrow">
            <span className="mg acct" style={{ background: r.id === "standin" ? "var(--warn-bg)" : monogramColor(r.label), color: r.id === "standin" ? "var(--warn)" : undefined }}>
              {r.id === "standin" ? G.card : r.label.replace(/[^A-Za-z]/g, "").slice(0, 2).toUpperCase()}
            </span>
            <span className="mid">
              <b className={r.id === "standin" ? "muted" : ""}>{r.label}</b>
              <small>
                {r.id === "standin" ? "Paid from the bank; no card statement for them yet" : (KIND_GROUP[r.kind ?? ""] ?? "Other")} · {plural(r.count, "payment")}
              </small>
            </span>
            <span className="amt">{inr(r.amount)}</span>
          </Link>
        ))}
        {!rows.length && <p className="state">Nothing spent yet this month.</p>}
      </div>
      {cardBills > 0 && (
        <span className="foot">A card bill counts as spend only until the statement it pays is parsed; then the card's purchases count instead.</span>
      )}
    </section>
  );
}

function Recent({ T, m }: { T: Transaction[]; m: MonthCtx }) {
  const rows = [...T].sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : Number(b.id) - Number(a.id))).slice(0, 7);
  return (
    <section className="panel grow" aria-label="Recent transactions">
      <div className="panel-h">
        <h2>Recent transactions</h2>
        <Link href={`/transactions?from=${m.period.start}&to=${m.period.end}`} className="x linkx">
          All transactions →
        </Link>
      </div>
      <div className="rows">
        {rows.map((t) => (
          <TxnLine key={t.id} t={t} today={m.through} />
        ))}
        {!rows.length && <p className="state">No activity this month yet.</p>}
      </div>
    </section>
  );
}

export function TxnLine({ t, today }: { t: Transaction; today: string }) {
  const credit = t.direction === "credit";
  const unknown = t.category == null;
  return (
    <Link href={`/transactions?txn=${t.id}`} className="lrow">
      <span className="dt">{t.date === today ? "Today" : dayShort(t.date)}</span>
      <span className={`mg${unknown ? " unknown" : ""}`} style={{ background: monogramColor(t.merchant) }}>
        {unknown ? "?" : (t.merchant.replace(/[^A-Za-z0-9]/g, "")[0] ?? "•").toUpperCase()}
      </span>
      <span className="mid">
        <b className={t.vpa && unknown && !t.named ? "mono-n" : ""} style={t.vpa && unknown && !t.named ? { fontSize: 13 } : undefined}>
          {unknown && t.vpa && !t.named ? t.vpa : t.merchant}
        </b>
        <small>
          {unknown ? (
            <>
              <span className="acc-t">Uncategorized</span>
              <span className="faint">· {t.review_reason === "person" ? "person" : "new payee"}</span>
            </>
          ) : (
            <>
              <i className="sq" style={{ width: 6, height: 6, background: categoryColor(t.category) }} />
              {t.category}
              {t.kind === "refund" && " · refund"}
            </>
          )}
        </small>
      </span>
      <span className="ac">{t.account}</span>
      <span className={`amt${credit ? " good-t" : ""}`}>
        {credit ? "+" : "−"}
        {inr(t.amount)}
      </span>
    </Link>
  );
}

function NetWorthCard() {
  const st = read(api.liveNetWorth());
  const nw = dataOf(st);
  const body = (() => {
    if (st.status === "loading") return <p className="state">Loading…</p>;
    if (st.status === "error") return <p className="state">Net worth couldn't load.</p>;
    if (!nw || nw.net_worth == null) return <p className="state">Import the net-worth sheet or set a value to start.</p>;
    const month = nw.changes.find((c) => c.period === "month");
    const history = nw.history.slice(-12).map((h) => ({ date: h.date, value: toPaise(h.net_worth) }));
    const proj = (nw.projection?.points ?? []).map((p) => ({ date: p.date, value: toPaise(p.net_worth) }));
    const classes = Object.entries(nw.by_asset_class).sort((a, b) => toPaise(b[1]) - toPaise(a[1]));
    return (
      <>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <div className="nwfig">
            <b>{compact(toPaise(nw.net_worth))}</b>
            {month?.amount != null && (
              <span className={`mono-n ${toPaise(month.amount) >= 0 ? "good-t" : "muted"}`} style={{ fontWeight: 500 }}>
                {signedK(toPaise(month.amount))} since {dayShort(month.since)}
              </span>
            )}
          </div>
          <span className="foot">Newest known value per component · as of {dayShort(nw.as_of)}</span>
        </div>
        {history.length > 1 && (
          <div className="chart2">
            <NetWorthChart history={history} projection={proj} label={(x) => monthApos(x)} height={170} width={400} />
          </div>
        )}
        <div className="kvrows">
          {classes.map(([k, v]) => (
            <div key={k} className="r">
              <span>{CLASS_LABEL[k] ?? k}</span>
              <b>{compact(toPaise(v))}</b>
            </div>
          ))}
        </div>
        {nw.projection && (
          <span className="foot">
            Projection: {signedK(toPaise(nw.projection.monthly_change))} a month, the average monthly change of the last {nw.projection.basis_months} months.
          </span>
        )}
      </>
    );
  })();
  return (
    <section className="panel side2" aria-label="Net worth">
      <div className="panel-h">
        <h2>Net worth</h2>
        <Link href="/networth" className="x linkx">
          Details →
        </Link>
      </div>
      {body}
    </section>
  );
}

export const CLASS_LABEL: Record<string, string> = {
  cash: "Savings accounts",
  deposits: "Fixed deposits",
  equity: "Mutual funds + stocks",
  retirement: "EPF + PPF",
  gold: "Gold",
  other: "Other",
  loans: "Loans",
};

