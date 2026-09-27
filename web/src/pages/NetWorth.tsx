import { useState } from "react";
import { NetWorthChart } from "../components/charts";
import { LoansPanel } from "../components/Loans";
import { useToast } from "../components/Toast";
import { ErrorState, Loading } from "../components/ui";
import { api, dataOf, invalidate, read, setComponent } from "../lib/api";
import { dayShort, daysBetween, inr, monthApos, toPaise } from "../lib/format";
import type { LiveComponent, LiveNetWorth } from "../lib/types";
import { CLASS_LABEL } from "./Overview";

const COLOR: Record<string, string> = {
  sbi: "#3987e5",
  hdfc: "#6da7ec",
  fd: "#b7d3f6",
  stocks: "#d95926",
  mf: "#c98500",
  ppf: "#199e70",
  epf: "#5fcf9f",
  gold: "#e3b04b",
  other: "#696969",
  loans_given: "#85c89a",
  loans_taken: "#e58d8d",
};
const SOURCE: Record<LiveComponent["source"], string> = { sheet: "Sheet", statement: "Statement balance", manual: "Set by you", loans: "Loans" };
const RANGES = [
  ["6m", "6M", 6],
  ["1y", "1Y", 12],
  ["all", "All", 0],
] as const;

export function NetWorth() {
  const st = read(api.liveNetWorth());
  return (
    <>
      <div className="ph">
        <h1>Net worth</h1>
      </div>
      {st.status === "loading" && <Loading />}
      {st.status === "error" && <ErrorState error={st.error} title="Couldn't load net worth" onRetry={() => invalidate(["/api/networth"])} />}
      {st.status === "ready" && (st.data.net_worth == null ? <EmptyNw /> : <Body nw={st.data} />)}
    </>
  );
}

function EmptyNw() {
  return (
    <section className="panel">
      <h2 style={{ margin: 0, fontSize: 16 }}>No net worth yet</h2>
      <p className="muted" style={{ margin: 0 }}>
        Import the net-worth sheet (Settings → Upload) or set a component's value below once one exists. Statement balances for SBI and HDFC savings fill in automatically.
      </p>
    </section>
  );
}

const signed = (v: number) => `${v >= 0 ? "+" : "−"}${inr(Math.abs(v))}`;

function Body({ nw }: { nw: LiveNetWorth }) {
  const [range, setRange] = useState<(typeof RANGES)[number][0]>("1y");
  const total = toPaise(nw.net_worth!);
  const n = RANGES.find((r) => r[0] === range)![2];
  const hist = nw.history.map((h) => ({ date: h.date, value: toPaise(h.net_worth) }));
  const shown = n ? hist.slice(-(n + 1)) : hist;
  const proj = (nw.projection?.points ?? []).map((p) => ({ date: p.date, value: toPaise(p.net_worth) }));
  const label = { month: "Since 1st", year: "Since 1 Jan", fy: "Since 1 Apr (FY)" } as const;
  return (
    <>
      <section className="panel nwhero" style={{ flexDirection: "row", gap: 0, padding: 0 }}>
        <div style={{ flex: 1.4, padding: "20px 24px", display: "flex", flexDirection: "column", gap: 6 }}>
          <span className="lbl">Net worth · live</span>
          <div className="nwfig lg">
            <b>{inr(total)}</b>
          </div>
          <span className="foot">Newest known value per component · as of {dayShort(nw.as_of)}</span>
        </div>
        {nw.changes.map((c) => (
          <div key={c.period} style={{ flex: 1, padding: "24px 20px", borderLeft: "1px solid var(--line)", display: "flex", flexDirection: "column", gap: 4 }}>
            <span className="muted" style={{ fontSize: 13 }}>
              {c.period === "month" ? `Since ${dayShort(c.since)}` : label[c.period]}
            </span>
            {c.amount != null ? (
              <span className="mono-n" style={{ fontSize: 16, fontWeight: 500, color: toPaise(c.amount) >= 0 ? "var(--in)" : "var(--t2)" }}>
                {signed(toPaise(c.amount))} <small className="faint" style={{ fontSize: 12 }}>{c.pct != null ? `${c.pct > 0 ? "+" : ""}${c.pct}%` : ""}</small>
              </span>
            ) : (
              <span className="faint">no snapshot then</span>
            )}
          </div>
        ))}
        <div style={{ flex: 1, padding: "24px 20px", borderLeft: "1px solid var(--line)", display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="muted" style={{ fontSize: 13 }}>Liquid · SBI + HDFC + FD</span>
          <span className="mono-n" style={{ fontSize: 16, fontWeight: 500 }}>
            {nw.liquid ? inr(toPaise(nw.liquid)) : "—"}
          </span>
        </div>
      </section>

      <section className="panel" aria-label="History">
        <div className="panel-h">
          <h2>History</h2>
          <div className="seg">
            {RANGES.map(([k, l]) => (
              <button key={k} type="button" className={range === k ? "on" : ""} onClick={() => setRange(k)}>
                {l}
              </button>
            ))}
          </div>
          <div className="legend" style={{ marginLeft: "auto" }}>
            <span>
              <i className="sq" style={{ background: "var(--in)", height: 2, width: 14 }} />
              Net worth
            </span>
            {proj.length > 0 && <span>··· Projected</span>}
          </div>
        </div>
        <div className="chart2">
          <NetWorthChart history={shown} projection={proj} label={(d) => monthApos(d)} />
        </div>
        {nw.projection && (
          <span className="foot">
            Projection: {signed(toPaise(nw.projection.monthly_change))} a month, the average monthly change of the last {nw.projection.basis_months} months, continued.
          </span>
        )}
      </section>

      <Allocation nw={nw} total={total} />
      <LoansPanel />
      <MonthByMonth nw={nw} />
      <Holdings />
    </>
  );
}

function Allocation({ nw, total }: { nw: LiveNetWorth; total: number }) {
  const [editing, setEditing] = useState<string | null>(null);
  const classes = Object.entries(nw.by_asset_class).sort((a, b) => toPaise(b[1]) - toPaise(a[1]));
  const sinceTotal = nw.components.reduce((a, c) => a + (c.change_since ? toPaise(c.change_since) : 0), 0);
  return (
    <section className="panel" aria-label="Allocation">
      <div className="panel-h">
        <h2>Allocation</h2>
        <span className="x">Same rows as your sheet</span>
      </div>
      <div className="split" style={{ height: 10 }}>
        {nw.components.filter((c) => toPaise(c.amount) > 0).map((c) => (
          <div key={c.key} style={{ width: `${c.share_pct}%`, background: COLOR[c.key] ?? "var(--t3)", height: 10 }} title={`${c.label} ${c.share_pct}%`} />
        ))}
      </div>
      <div className="legend" style={{ color: "var(--t2)", fontSize: 13 }}>
        {classes.map(([k, v]) => (
          <span key={k}>
            {CLASS_LABEL[k] ?? k} {total ? ((toPaise(v) / total) * 100).toFixed(1) : 0}%
          </span>
        ))}
      </div>
      <table className="t2">
        <thead>
          <tr>
            <th>Component</th>
            <th className="r">Value</th>
            <th className="r">Share</th>
            <th className="r">Since 1st</th>
            <th>Source · as of</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {nw.components.map((c) => (
            <tr key={c.key}>
              <td>
                <span className="nm">
                  <i className="sq" style={{ background: COLOR[c.key] ?? "var(--t3)" }} />
                  {c.label}
                </span>
              </td>
              <td className="r">{inr(toPaise(c.amount))}</td>
              <td className="r muted">{c.share_pct.toFixed(1)}%</td>
              <td className="r" style={{ color: c.change_since && toPaise(c.change_since) > 0 ? "var(--in)" : "var(--t2)" }}>
                {c.change_since != null ? (toPaise(c.change_since) === 0 ? "₹0" : signed(toPaise(c.change_since))) : "—"}
              </td>
              <td>
                {editing === c.key ? (
                  <EditValue c={c} done={() => setEditing(null)} />
                ) : (
                  <span style={{ display: "inline-flex", gap: 8, alignItems: "center" }} className="muted">
                    {SOURCE[c.source]} · {dayShort(c.as_of)}
                    {c.stale && <span className="stale">{daysBetween(c.as_of, nw.as_of)} days old</span>}
                  </span>
                )}
              </td>
              <td className="r" style={{ fontFamily: "inherit" }}>
                {editing !== c.key && c.editable && (
                  <button type="button" className="linkish" onClick={() => setEditing(c.key)}>
                    Edit
                  </button>
                )}
              </td>
            </tr>
          ))}
          <tr className="total">
            <td>Net worth</td>
            <td className="r">{inr(total)}</td>
            <td className="r">100%</td>
            <td className="r" style={{ color: sinceTotal > 0 ? "var(--in)" : undefined }}>
              {signed(sinceTotal)}
            </td>
            <td colSpan={2} className="foot" style={{ fontWeight: 400 }}>
              Values older than 30 days are flagged. Edit sets today's value by hand.
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  );
}

const MONEY = /^\d{0,12}(\.\d{0,2})?$/;
function EditValue({ c, done }: { c: LiveComponent; done: () => void }) {
  const [v, setV] = useState(String(toPaise(c.amount) / 100));
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const save = async () => {
    if (!v || !MONEY.test(v)) return;
    setBusy(true);
    try {
      await setComponent(c.key, v);
      toast(`${c.label} set to ${inr(toPaise(v))}`);
      done();
    } catch {
      toast("Couldn't save that value.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
      <span className="mono-n faint">₹</span>
      <input className="inp2 mono-n" style={{ width: 130 }} inputMode="decimal" autoFocus value={v} onChange={(e) => MONEY.test(e.target.value) && setV(e.target.value)} onKeyDown={(e) => (e.key === "Enter" ? save() : e.key === "Escape" && done())} aria-label={`${c.label} value`} />
      <button type="button" className="btn2 sm primary" disabled={busy || !v} onClick={save}>
        Save
      </button>
      <button type="button" className="btn2 sm" style={{ border: 0 }} onClick={done}>
        Cancel
      </button>
    </span>
  );
}

function MonthByMonth({ nw }: { nw: LiveNetWorth }) {
  if (!nw.months.length) return null;
  const m = (v: string | null) => (v == null ? "—" : signed(toPaise(v)));
  return (
    <section className="panel" aria-label="Month by month">
      <div className="panel-h">
        <h2>Month by month</h2>
        <span className="x">Change = contributions + market + cash</span>
      </div>
      <table className="t2">
        <thead>
          <tr>
            <th>Period</th>
            <th className="r">Start</th>
            <th className="r">Contributions</th>
            <th className="r">Market & other</th>
            <th className="r">Cash</th>
            <th className="r">Change</th>
            <th className="r">End</th>
          </tr>
        </thead>
        <tbody>
          {nw.months.map((r) => (
            <tr key={r.start}>
              <td>
                {monthApos(r.start)} {r.live && <small>to {dayShort(r.end)}</small>}
              </td>
              <td className="r muted">{inr(toPaise(r.start_value))}</td>
              <td className="r">{m(r.contributions)}</td>
              <td className="r">{m(r.market)}</td>
              <td className="r">{m(r.cash_change)}</td>
              <td className="r" style={{ color: toPaise(r.change) >= 0 ? "var(--in)" : "var(--t2)" }}>
                {m(r.change)}
              </td>
              <td className="r">{inr(toPaise(r.end_value))}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <span className="foot">
        Contributions = SIP, PPF and other investment debits. Cash = SBI + HDFC + FD. Market & other = change − contributions − cash (includes values set by hand). This month's split waits until every cash balance is newer than the 1st.
      </span>
    </section>
  );
}

function Holdings() {
  const items = dataOf(read(api.holdings())) ?? [];
  return (
    <section className="panel" aria-label="Holdings">
      <div className="panel-h">
        <h2>Holdings</h2>
        <span className="x">Units × latest NAV / close</span>
      </div>
      {items.length ? (
        <table className="t2">
          <thead>
            <tr>
              <th>Holding</th>
              <th className="r">Units</th>
              <th className="r">Price</th>
              <th className="r">Value</th>
              <th>Units as of</th>
            </tr>
          </thead>
          <tbody>
            {items.map((h) => (
              <tr key={h.isin ?? h.name}>
                <td>
                  {h.name}
                  <br />
                  <small>{h.isin}</small>
                </td>
                <td className="r">{Number(h.units).toLocaleString("en-IN", { maximumFractionDigits: 3 })}</td>
                <td className="r">{h.price ? `₹${Number(h.price).toLocaleString("en-IN", { maximumFractionDigits: 2 })}` : "—"}</td>
                <td className="r">{h.value ? inr(toPaise(h.value)) : "—"}</td>
                <td className="muted">
                  {h.source.toUpperCase()} {dayShort(h.units_as_of)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="state" style={{ padding: 0 }}>
          No holdings yet. They come from the monthly CDSL CAS once it is in the mailbox.
        </p>
      )}
    </section>
  );
}
