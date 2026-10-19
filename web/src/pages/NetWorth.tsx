import { useRef, useState } from "react";
import { NetWorthChart } from "../components/charts";
import { LoansPanel } from "../components/Loans";
import { useToast } from "../components/Toast";
import { ErrorState, Loading } from "../components/ui";
import { CLASS_LABEL, CLASSES, kindLine } from "../lib/accounts";
import { api, dataOf, deleteValuation, invalidate, read, setNetWorthNote, setValuation } from "../lib/api";
import { accountColors, ASSET_CLASS_COLOR, OTHER } from "../lib/colors";
import { dayNum, dayShort, daysBetween, fromDayNum, inr, monthApos, toPaise } from "../lib/format";
import { Link } from "../lib/router";
import type { Account, ApiCards, AssetClass, LiveComponent, LiveNetWorth } from "../lib/types";

const classColor = (k: string) => ASSET_CLASS_COLOR[k] ?? OTHER;
type ColorOf = (c: LiveComponent) => string;
const classLabel = (k: string) => CLASS_LABEL[k as AssetClass] ?? k;
const SOURCE: Record<LiveComponent["source"] | "imported", string> = { statement: "Statement", manual: "Set by you", imported: "Imported", loans: "Loans", prices: "Daily prices", estimate: "Estimated" };
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
      {st.status === "ready" && <Unvalued nw={st.data} />}
    </>
  );
}

function EmptyNw() {
  return (
    <section className="panel">
      <h2 style={{ margin: 0, fontSize: 16 }}>No net worth yet</h2>
      <p className="muted" style={{ margin: 0 }}>
        Statements fill in bank balances and what you owe on cards; the monthly CAS fills in stocks and funds. For what no statement reaches, such as EPF, gold, property or a home loan, add the account under{" "}
        <Link href="/settings/sources">Settings → Accounts &amp; sources</Link> and set its value here.
      </p>
    </section>
  );
}

const signed = (v: number) => `${v >= 0 ? "+" : "−"}${inr(Math.abs(v))}`;

function Body({ nw }: { nw: LiveNetWorth }) {
  const [range, setRange] = useState<(typeof RANGES)[number][0]>("1y");
  const [picked, setPicked] = useState<LiveComponent | null>(null);
  const historyRef = useRef<HTMLElement>(null);
  const pick = (c: LiveComponent) => {
    setPicked(picked?.key === c.key ? null : c);
    historyRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };
  const total = toPaise(nw.net_worth!);
  const n = RANGES.find((r) => r[0] === range)![2];
  const hist = nw.history.map((h) => ({ date: h.date, value: toPaise(h.net_worth) }));
  const shown = n ? hist.slice(-(n + 1)) : hist;
  const proj = (nw.projection?.points ?? []).map((p) => ({ date: p.date, value: toPaise(p.net_worth) }));
  const label = { month: "Since 1st", year: "Since 1 Jan", fy: "Since 1 Apr (FY)" } as const;
  const colors = accountColors(nw.components);
  const colorOf: ColorOf = (c) => colors.get(c.key) ?? classColor(c.asset_class);
  return (
    <>
      <section className="panel nwhero" style={{ flexDirection: "row", gap: 0, padding: 0 }}>
        <div style={{ flex: 1.4, padding: "20px 24px", display: "flex", flexDirection: "column", gap: 6 }}>
          <span className="lbl">Net worth · live</span>
          <div className="nwfig lg">
            <b>{inr(total)}</b>
          </div>
          <span className="foot">Newest value per account, from statements, the CAS, daily prices and values you set · as of {dayShort(nw.as_of)}</span>
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
              <span className="faint">no history then</span>
            )}
          </div>
        ))}
        <div style={{ flex: 1, padding: "24px 20px", borderLeft: "1px solid var(--line)", display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="muted" style={{ fontSize: 13 }} title="Accounts marked liquid in Settings → Accounts & sources: by default banks, wallets, cash and deposits">
            Liquid
          </span>
          <span className="mono-n" style={{ fontSize: 16, fontWeight: 500 }}>
            {nw.liquid ? inr(toPaise(nw.liquid)) : "—"}
          </span>
        </div>
        {nw.cards_owed != null && (
          <div style={{ flex: 1, padding: "24px 20px", borderLeft: "1px solid var(--line)", display: "flex", flexDirection: "column", gap: 4 }}>
            <span className="muted" style={{ fontSize: 13 }} title="Each card's statement total due, plus purchases and minus payments and refunds since. Net worth subtracts it.">
              Owed on cards
            </span>
            <span className="mono-n" style={{ fontSize: 16, fontWeight: 500 }}>
              {inr(toPaise(nw.cards_owed))}
            </span>
          </div>
        )}
      </section>

      <section className="panel" aria-label="History" ref={historyRef}>
        <div className="panel-h">
          <h2>History{picked && ` · ${picked.label}`}</h2>
          <div className="seg">
            {RANGES.map(([k, l]) => (
              <button key={k} type="button" className={range === k ? "on" : ""} onClick={() => setRange(k)}>
                {l}
              </button>
            ))}
          </div>
          <div className="legend" style={{ marginLeft: "auto" }}>
            <span>
              <i className="sq" style={{ background: picked ? colorOf(picked) : "var(--in)", height: 2, width: 14 }} />
              {picked ? picked.label : "Net worth"}
            </span>
            {!picked && proj.length > 0 && <span>··· Projected</span>}
            {picked && (
              <button type="button" className="linkish" onClick={() => setPicked(null)}>
                Back to net worth
              </button>
            )}
          </div>
        </div>
        {picked ? (
          <AccountChart c={picked} months={n} asOf={nw.as_of} color={colorOf(picked)} />
        ) : (
          <div className="chart2">
            <NetWorthChart history={shown} projection={proj} label={(d) => monthApos(d)} />
          </div>
        )}
        {!picked && nw.projection && (
          <span className="foot">
            Projection: {signed(toPaise(nw.projection.monthly_change))} a month, the average monthly change of the last {nw.projection.basis_months} months, continued.
          </span>
        )}
      </section>

      <Allocation nw={nw} total={total} picked={picked?.key ?? null} onPick={pick} colorOf={colorOf} />
      <LoansPanel />
      <MonthByMonth nw={nw} />
      <Holdings />
    </>
  );
}

function AccountChart({ c, months, asOf, color }: { c: LiveComponent; months: number; asOf: string; color: string }) {
  const st = read(api.accountHistory(c.account_id!));
  if (st.status === "loading") return <Loading />;
  if (st.status === "error") return <ErrorState error={st.error} title={`Couldn't load ${c.label} history`} onRetry={() => invalidate([`/api/accounts/${c.account_id}/history`])} />;
  const from = months ? fromDayNum(dayNum(asOf) - Math.round(months * 30.44)) : "";
  const pts = st.data.points.filter((p) => p.date >= from).map((p) => ({ date: p.date, value: toPaise(p.value) }));
  const owed = st.data.side === "liability";
  return (
    <>
      {pts.length ? (
        <div className="chart2">
          <NetWorthChart history={pts} projection={[]} label={dayShort} name={owed ? `${c.label} · owed` : c.label} color={color} dots={pts.length <= 40} />
        </div>
      ) : (
        <p className="muted" style={{ margin: 0 }}>No values in this range.</p>
      )}
      {st.data.priced_from && <span className="foot">Valued at daily prices from {dayShort(st.data.priced_from)}; before that, at each statement.</span>}
      {owed && <span className="foot">What is owed: the statement's total due, moved by purchases, payments and refunds since.</span>}
      <Valuations accountId={c.account_id!} items={st.data.valuations} owed={owed} />
    </>
  );
}

/** The dated values behind an account: yours can be removed; a statement's stays (set your own on that day). */
function Valuations({ accountId, items, owed }: { accountId: number; items: { as_of: string; amount: string; source: string }[]; owed: boolean }) {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  if (!items.length) return null;
  return (
    <details>
      <summary className="foot" style={{ cursor: "pointer" }}>
        {items.length} dated value{items.length === 1 ? "" : "s"} behind this line
      </summary>
      <table className="t2">
        <thead>
          <tr>
            <th>As of</th>
            <th className="r">{owed ? "Owed" : "Value"}</th>
            <th>Source</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {items.map((v) => (
            <tr key={v.as_of}>
              <td>{dayShort(v.as_of)}</td>
              <td className="r">{inr(toPaise(v.amount))}</td>
              <td className="muted">{SOURCE[v.source as LiveComponent["source"]] ?? v.source}</td>
              <td className="r" style={{ fontFamily: "inherit" }}>
                {v.source !== "statement" && (
                  <button
                    type="button"
                    className="linkish"
                    disabled={busy === v.as_of}
                    onClick={async () => {
                      setBusy(v.as_of);
                      try {
                        await deleteValuation(accountId, v.as_of);
                        toast(`Removed the value on ${dayShort(v.as_of)}.`);
                      } catch {
                        toast("Couldn't remove that value.");
                      } finally {
                        setBusy(null);
                      }
                    }}
                  >
                    Remove
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

/** A card's last bill, as its row's second line: "Bill ₹12,000 · due 5 Nov · unpaid". */
function billLine(b: NonNullable<ApiCards["items"][number]["statement"]>): string {
  const state = b.state === "paid" ? "paid" : b.state === "part_paid" ? `${inr(toPaise(b.paid))} paid` : "unpaid";
  return [`Bill ${inr(toPaise(b.total))}`, b.due_date && b.state !== "paid" ? `due ${dayShort(b.due_date)}` : "", state].filter(Boolean).join(" · ");
}

function Allocation({ nw, total, picked, onPick, colorOf }: { nw: LiveNetWorth; total: number; picked: string | null; onPick: (c: LiveComponent) => void; colorOf: ColorOf }) {
  const [editing, setEditing] = useState<string | null>(null);
  const bills = new Map((dataOf(read(api.cards()))?.items ?? []).map((b) => [b.account.id, b]));
  const classes = Object.entries(nw.by_asset_class).sort((a, b) => toPaise(b[1]) - toPaise(a[1]));
  const sinceTotal = nw.components.reduce((a, c) => a + (c.change_since ? toPaise(c.change_since) : 0), 0);
  // The bar shows what you own, by asset class; what you owe is listed under it.
  const rank = (c: LiveComponent) => CLASSES.indexOf(c.asset_class as AssetClass) >>> 0;
  const owned = nw.components.filter((c) => c.side === "asset").sort((a, b) => rank(a) - rank(b) || toPaise(b.amount) - toPaise(a.amount));
  const owes = nw.components.filter((c) => c.side === "liability").sort((a, b) => rank(a) - rank(b) || toPaise(a.amount) - toPaise(b.amount));
  const assets = owned.reduce((a, c) => a + Math.max(toPaise(c.amount), 0), 0);
  const groups = [["What you own", owned], ["What you owe", owes]] as const;
  return (
    <section className="panel" aria-label="Allocation">
      <div className="panel-h">
        <h2>Allocation</h2>
        <span className="x">What you own and owe, by account</span>
        <Link href="/settings/sources" className="x linkx">
          Accounts →
        </Link>
      </div>
      <div className="split" style={{ height: 10 }}>
        {owned.filter((c) => toPaise(c.amount) > 0).map((c) => (
          <div key={c.key} style={{ width: `${assets ? (toPaise(c.amount) / assets) * 100 : 0}%`, background: colorOf(c), height: 10 }} title={`${c.label} ${assets ? ((toPaise(c.amount) / assets) * 100).toFixed(1) : 0}% of what you own`} />
        ))}
      </div>
      <div className="legend" style={{ color: "var(--t2)", fontSize: 13 }}>
        {classes.map(([k, v]) => (
          <span key={k}>
            <i className="sq" style={{ background: classColor(k) }} />
            {classLabel(k)} {toPaise(v) > 0 ? `${assets ? ((toPaise(v) / assets) * 100).toFixed(1) : 0}%` : inr(toPaise(v))}
          </span>
        ))}
      </div>
      <table className="t2">
        <thead>
          <tr>
            <th>Account</th>
            <th className="r">Value</th>
            <th className="r" title="Of what you own; for a liability, of what you owe">Share</th>
            <th className="r">Since 1st</th>
            <th>Source · as of</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {groups.map(([title, rows]) => rows.length > 0 && [
            owes.length > 0 && (
              <tr key={title} className="grp">
                <td colSpan={6} className="faint" style={{ fontSize: 12, paddingTop: 12 }}>
                  {title}
                </td>
              </tr>
            ),
            ...rows.map((c) => {
            const charted = c.account_id != null;
            const bill = c.kind === "card" && c.account_id != null ? bills.get(c.account_id)?.statement : null;
            const under = bill ? billLine(bill) : c.kind ? kindLine(c.kind, c.subtype, c.label) : null;
            return (
            <tr key={c.key} className={charted ? `click${picked === c.key ? " on" : ""}` : undefined} onClick={charted ? () => onPick(c) : undefined}>
              <td>
                {charted ? (
                  <button type="button" className="nm" aria-pressed={picked === c.key} title={`Show ${c.label} over time`} onClick={(e) => {
                    e.stopPropagation();
                    onPick(c);
                  }}>
                    <i className="sq" style={{ background: colorOf(c) }} />
                    <span style={{ whiteSpace: "nowrap" }}>
                      {c.label}
                      {under && (
                        <>
                          <br />
                          <small className="faint">{under}</small>
                        </>
                      )}
                    </span>
                  </button>
                ) : (
                  <span className="nm">
                    <i className="sq" style={{ background: colorOf(c) }} />
                    {c.label}
                  </span>
                )}
              </td>
              <td className="r">{inr(toPaise(c.amount))}</td>
              <td className="r muted">{c.share_pct.toFixed(1)}%</td>
              <td className="r" style={{ color: c.change_since && toPaise(c.change_since) > 0 ? "var(--in)" : "var(--t2)" }}>
                {c.change_since != null ? (toPaise(c.change_since) === 0 ? "₹0" : signed(toPaise(c.change_since))) : "—"}
              </td>
              <td onClick={editing === c.key ? (e) => e.stopPropagation() : undefined}>
                {editing === c.key ? (
                  <EditValue c={c} done={() => setEditing(null)} />
                ) : (
                  <span style={{ display: "inline-flex", gap: 8, alignItems: "center" }} className="muted">
                    {SOURCE[c.source]} · {dayShort(c.as_of)}
                    {c.stale && <span className="stale">{daysBetween(c.as_of, nw.as_of)} days old</span>}
                  </span>
                )}
              </td>
              <td className="r" style={{ fontFamily: "inherit" }} onClick={(e) => e.stopPropagation()}>
                {editing !== c.key && c.editable && (
                  <button type="button" className="linkish" onClick={() => setEditing(c.key)}>
                    Edit
                  </button>
                )}
              </td>
            </tr>
            );
          }),
          ])}
          <tr className="total">
            <td>Net worth</td>
            <td className="r">{inr(total)}</td>
            <td />
            <td className="r" style={{ color: sinceTotal > 0 ? "var(--in)" : undefined }}>
              {signed(sinceTotal)}
            </td>
            <td colSpan={2} className="foot" style={{ fontWeight: 400 }}>
              Values older than 30 days are flagged. Edit sets an account's value as of today; the newest value wins until a statement brings a newer one.
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  );
}

const MONEY = /^\d{0,12}(\.\d{0,2})?$/;
function EditValue({ c, done }: { c: LiveComponent; done: () => void }) {
  const [v, setV] = useState(toPaise(c.amount) ? String(Math.abs(toPaise(c.amount)) / 100) : "");
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const save = async () => {
    if (!v || !MONEY.test(v)) return;
    setBusy(true);
    try {
      await setValuation(c.account_id!, v);
      toast(`${c.label} set to ${inr(toPaise(v))}${c.side === "liability" ? " owed" : ""}`);
      done();
    } catch {
      toast("Couldn't save that value.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
      <span className="mono-n faint">{c.side === "liability" ? "Owed ₹" : "₹"}</span>
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

const RECENT_MONTHS = 6;

function MonthByMonth({ nw }: { nw: LiveNetWorth }) {
  const [all, setAll] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  if (!nw.months.length) return null;
  const m = (v: string | null) => (v == null ? "—" : signed(toPaise(v)));
  const cards = nw.months.some((r) => toPaise(r.card_change) !== 0);
  const rows = all ? nw.months : nw.months.slice(0, RECENT_MONTHS);
  const cols = cards ? 8 : 7;
  return (
    <section className="panel" aria-label="Month by month">
      <div className="panel-h">
        <h2>Month by month</h2>
        <span className="x">Change = contributions + market + cash{cards ? " + cards" : ""}</span>
      </div>
      <table className="t2">
        <thead>
          <tr>
            <th>Period</th>
            <th className="r">Start</th>
            <th className="r">Contributions</th>
            <th className="r">Market & other</th>
            <th className="r">Cash</th>
            {cards && <th className="r">Cards</th>}
            <th className="r">Change</th>
            <th className="r">End</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const month = r.end.slice(0, 7);
            return [
              <tr key={r.start}>
                <td>
                  {monthApos(r.start)} {r.live && <small>to {dayShort(r.end)}</small>}
                  {r.mover && (
                    <>
                      <br />
                      <small className="faint" title="The account whose value moved most this month">
                        {r.mover.label} {signed(toPaise(r.mover.change))}
                      </small>
                    </>
                  )}
                </td>
                <td className="r muted">{inr(toPaise(r.start_value))}</td>
                <td className="r">{m(r.contributions)}</td>
                <td className="r">{m(r.market)}</td>
                <td className="r">{m(r.cash_change)}</td>
                {cards && <td className="r">{m(r.card_change)}</td>}
                <td className="r" style={{ color: toPaise(r.change) >= 0 ? "var(--in)" : "var(--t2)" }}>
                  {m(r.change)}
                </td>
                <td className="r">{inr(toPaise(r.end_value))}</td>
              </tr>,
              <tr key={`${r.start}-note`} className="note">
                <td colSpan={cols} style={{ paddingTop: 0, borderTop: 0 }}>
                  {editing === month ? (
                    <NoteEdit month={month} note={r.note} done={() => setEditing(null)} />
                  ) : r.note ? (
                    <span className="muted" style={{ fontSize: 13, whiteSpace: "pre-wrap" }}>
                      {r.note}{" "}
                      <button type="button" className="linkish" onClick={() => setEditing(month)}>
                        Edit
                      </button>
                    </span>
                  ) : (
                    <button type="button" className="linkish faint" style={{ fontSize: 12 }} onClick={() => setEditing(month)}>
                      + Note
                    </button>
                  )}
                </td>
              </tr>,
            ];
          })}
        </tbody>
      </table>
      {nw.months.length > RECENT_MONTHS && (
        <button type="button" className="linkish" onClick={() => setAll(!all)}>
          {all ? "Recent months only" : `All ${nw.months.length} months`}
        </button>
      )}
      <span className="foot">
        Contributions = SIP, PPF and other investment debits. Cash = your liquid accounts.{cards ? " Cards = what you owe on cards, falling as you spend and rising as you pay." : ""} Market & other = the rest of the change, including values set by hand. Under each month, the account that moved most.
      </span>
    </section>
  );
}

/** Your note on a month: why it moved, what you decided. Saving it empty removes it. */
function NoteEdit({ month, note, done }: { month: string; note: string | null; done: () => void }) {
  const [v, setV] = useState(note ?? "");
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const save = async () => {
    setBusy(true);
    try {
      await setNetWorthNote(month, v.trim());
      done();
    } catch {
      toast("Couldn't save that note.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <span style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
      <textarea className="inp2" style={{ flex: 1, minHeight: 56 }} maxLength={2000} autoFocus value={v} onChange={(e) => setV(e.target.value)} onKeyDown={(e) => e.key === "Escape" && done()} aria-label={`Note on ${month}`} />
      <button type="button" className="btn2 sm primary" disabled={busy} onClick={save}>
        Save
      </button>
      <button type="button" className="btn2 sm" style={{ border: 0 }} onClick={done}>
        Cancel
      </button>
    </span>
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
              <th>Account · units as of</th>
            </tr>
          </thead>
          <tbody>
            {items.map((h) => (
              <tr key={`${h.account_id}-${h.isin ?? h.name}`}>
                <td>
                  {h.name}
                  <br />
                  <small>{h.isin}</small>
                </td>
                <td className="r">{Number(h.units).toLocaleString("en-IN", { maximumFractionDigits: 3 })}</td>
                <td className="r">{h.price ? `₹${Number(h.price).toLocaleString("en-IN", { maximumFractionDigits: 2 })}` : "—"}</td>
                <td className="r">{h.value ? inr(toPaise(h.value)) : "—"}</td>
                <td className="muted">
                  {h.account ?? h.source.toUpperCase()} · {dayShort(h.units_as_of)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="state" style={{ padding: 0 }}>
          No holdings yet. They come from the monthly CAS (CDSL) or a fund house's statement (CAMS) once it reaches the mailbox.
        </p>
      )}
    </section>
  );
}

/** Accounts you count that have no value yet (an EPF you just added, a property): set one to bring them in. */
function Unvalued({ nw }: { nw: LiveNetWorth }) {
  const accounts = dataOf(read(api.accounts())) ?? [];
  const valued = new Set(nw.components.map((c) => c.account_id));
  const missing = accounts.filter((a) => a.in_net_worth && !valued.has(a.id));
  if (!missing.length) return null;
  return (
    <section className="panel" aria-label="Accounts with no value yet">
      <div className="panel-h">
        <h2>No value yet</h2>
        <span className="x">Counted in net worth once they have one</span>
      </div>
      <table className="t2">
        <tbody>
          {missing.map((a) => (
            <UnvaluedRow key={a.id} a={a} />
          ))}
        </tbody>
      </table>
    </section>
  );
}

function UnvaluedRow({ a }: { a: Account }) {
  const [editing, setEditing] = useState(false);
  const c = { key: String(a.id), account_id: a.id, label: a.label, side: a.side, amount: "0" } as LiveComponent;
  return (
    <tr>
      <td>
        <span className="nm">
          <i className="sq" style={{ background: classColor(a.asset_class) }} />
          {a.label}
          {kindLine(a.kind, a.subtype, a.label) && <small className="faint"> · {kindLine(a.kind, a.subtype, a.label)}</small>}
        </span>
      </td>
      <td className="r" style={{ fontFamily: "inherit" }}>
        {editing ? (
          <EditValue c={c} done={() => setEditing(false)} />
        ) : (
          <button type="button" className="linkish" onClick={() => setEditing(true)}>
            Set {a.side === "liability" ? "what's owed" : "value"}
          </button>
        )}
      </td>
    </tr>
  );
}
