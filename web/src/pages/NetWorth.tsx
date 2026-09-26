import { useMemo, useState } from "react";
import { columnChart, donut, lineChart, tipRow, tipTitle, type Chart } from "../charts";
import { ChartView } from "../components/ChartView";
import { useToast } from "../components/Toast";
import { CardHead, Delta, Dot, Empty, ErrorState, InlineState, Loading } from "../components/ui";
import { api, invalidate, read, saveRemark, type ApiError, type ResourceState } from "../lib/api";
import { DIV_BAD, DIV_GOOD, NW_SHEET_KEYS, slotColor } from "../lib/colors";
import { html } from "../lib/dom";
import { compact, dayIST, dayLong, dayShort, inr, monthShortOf, monthShortYear, signed, toPaise } from "../lib/format";
import { allocation, allocationSlots, idleCash } from "../lib/insights";
import { useStore } from "../lib/useStore";
import type { Account, Health, Snapshot } from "../lib/types";

const HEALTH: Record<Health, string> = { good: "var(--in)", warn: "var(--warn)", bad: "var(--bad)" };

export function NetWorth() {
  useStore();
  const st = read(api.networth());
  const accounts = read(api.accounts());
  if (st.status === "loading") return <Loading />;
  if (st.status === "error") return <ErrorState error={st.error} title="Couldn't load net worth" onRetry={() => invalidate(["/api/networth"])} />;
  const S = st.data;
  const last = S.at(-1);
  if (!last) return <Empty title="No net-worth snapshots yet">The first snapshot appears once balances and holdings are imported.</Empty>;
  return <Body S={S} last={last} accounts={accounts} />;
}

function Body({ S, last, accounts }: { S: Snapshot[]; last: Snapshot; accounts: ResourceState<Account[] | null> }) {
  const [changeOf, setChangeOf] = useState<"net" | "liquid">("net");
  const prev = S.at(-2) ?? null;
  const first = S[0]!;
  const d = useMemo(() => {
    const slots = allocationSlots(S);
    return {
      slots,
      slices: allocation(last, slots).filter((s) => s.amount !== 0),
      prevBy: new Map(prev ? allocation(prev, slots).map((s) => [s.key, s]) : []),
      nw: history(S, "Net worth", "var(--accent)", (s) => s.netWorth),
      liquid: history(S, "Liquid cash", slotColor(slots.get("cash")), (s) => s.liquid),
      allocTime: allocationOverTime(S, slots),
    };
  }, [S, last, prev]);
  const alloc = useMemo(
    () => donut({ label: `Allocation on ${dayLong(last.date)}`, slices: d.slices.map((s) => ({ key: s.key, label: s.label, value: s.amount, color: slotColor(s.slot) })), center: [compact(last.netWorth), "net worth"] }),
    [d, last],
  );
  const change = useMemo(() => changeChart(S, changeOf), [S, changeOf]);
  const share = (v: number) => (last.netWorth > 0 ? v / last.netWorth : 0);
  const idle = idleCash(last);
  const allocKeys = [...d.slots].filter(([k]) => S.some((s) => allocation(s, d.slots).some((x) => x.key === k && x.amount)));

  return (
    <>
      <div className="grid g-hero">
        <div className="card hero">
          <div className="lab">Net worth · {dayLong(last.date)}</div>
          <div className="big">{compact(last.netWorth)}</div>
          <div className="pair">
            {last.netChange != null && (
              <span className="pi">
                <span className="pl">{signed(last.netChange)} this month</span>
                {prev && <Delta cur={last.netWorth} prev={prev.netWorth} vs={monthShortOf(prev.date)} upIsGood />}
              </span>
            )}
            {S.length > 1 && (
              <span className="pi">
                <span className="pl">
                  {signed(last.netWorth - first.netWorth)} since {monthShortYear(first.date)}
                </span>
                <Delta cur={last.netWorth} prev={first.netWorth} vs={monthShortYear(first.date)} upIsGood />
              </span>
            )}
          </div>
          <div className="comp">
            {d.slices.map((s) => (
              <div key={s.key} title={s.label} style={{ width: `${Math.max(0, share(s.amount)) * 100}%`, background: slotColor(s.slot) }} />
            ))}
          </div>
          <table className="tbl">
            <thead>
              <tr>
                <th>Asset</th>
                <th className="r">Value</th>
                <th className="r">Share</th>
                <th className="r">vs last month</th>
              </tr>
            </thead>
            <tbody>
              {d.slices.flatMap((s) => {
                const before = d.prevBy.get(s.key);
                const dd = before ? s.amount - before.amount : null;
                return [
                  <tr key={s.key}>
                    <td>
                      <Dot color={slotColor(s.slot)} />
                      {s.label}
                    </td>
                    <td className="r num">{inr(s.amount)}</td>
                    <td className="r num t2">{(share(s.amount) * 100).toFixed(1)}%</td>
                    <td className={`r num ${dd ? (dd > 0 ? "in" : "bad") : "t3"}`}>{dd ? signed(dd) : "—"}</td>
                  </tr>,
                  ...(s.parts.length > 1
                    ? s.parts.map((p) => (
                        <tr className="sub-row" key={`${s.key}-${p.label}`}>
                          <td>{p.label}</td>
                          <td className="r num">{inr(p.amount)}</td>
                          <td className="r num t3">{(share(p.amount) * 100).toFixed(1)}%</td>
                          <td />
                        </tr>
                      ))
                    : []),
                ];
              })}
            </tbody>
          </table>
        </div>
        <div className="card">
          <CardHead title="Current allocation" x="cash grouped like the sheet" />
          <ChartView id="nw-alloc" chart={alloc}>
            <div className="dl">
              {d.slices.map((s) => (
                <div key={s.key}>
                  <Dot color={slotColor(s.slot)} />
                  <span className="dl-n">{s.label}</span>
                  <b className="num">{Math.round(share(s.amount) * 100)}%</b>
                  <span className="num t3">{compact(s.amount)}</span>
                </div>
              ))}
            </div>
          </ChartView>
          {idle && (
            <div className="alert mt">
              <div className="ic warn" aria-hidden>
                %
              </div>
              <div>
                <b>
                  {compact(idle.amount)} idle in {idle.label}
                </b>
                <small>Savings accounts usually pay well below an FD or a liquid fund. Compare current rates before moving it.</small>
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="grid g-2 mt-g">
        <div className="card">
          <CardHead title="Net worth over time" x="1st of each month" />
          <ChartView id="nw-line" chart={d.nw} />
        </div>
        <div className="card">
          <CardHead title="Liquid cash over time" x="savings + fixed deposits" />
          <ChartView id="nw-liquid" chart={d.liquid} />
        </div>
      </div>

      <div className="grid g-2 mt-g">
        <div className="card">
          <h3>
            Monthly change
            <span className="x">
              <span className="seg-ctl" role="group" aria-label="Change of">
                {(["net", "liquid"] as const).map((v) => (
                  <button type="button" key={v} className={`chip${changeOf === v ? " on" : ""}`} aria-pressed={changeOf === v} onClick={() => setChangeOf(v)}>
                    {v === "net" ? "Net worth" : "Liquid cash"}
                  </button>
                ))}
              </span>
            </span>
          </h3>
          <ChartView id={`nw-change-${changeOf}`} chart={change}>
            <div className="leg2">
              <span>
                <i className="sq" style={{ background: DIV_GOOD }} />
                Grew
              </span>
              <span>
                <i className="sq" style={{ background: DIV_BAD }} />
                Shrank
              </span>
              <span className="t3">hover a month for its remark and commentary</span>
            </div>
          </ChartView>
        </div>
        <div className="card">
          <CardHead title="Allocation over time" x="share of net worth" />
          <ChartView id="nw-alloc-time" chart={d.allocTime}>
            <div className="leg2 wrap">
              {allocKeys.map(([k, sl]) => (
                <span key={k}>
                  <i className="sq" style={{ background: slotColor(sl) }} />
                  {allocation(last, d.slots).find((x) => x.key === k)?.label ?? k}
                </span>
              ))}
            </div>
          </ChartView>
        </div>
      </div>

      <div className="card mt-g">
        <CardHead title="Monthly ledger" x="newest first · remarks are editable" />
        <Ledger S={S} />
      </div>

      <div className="card mt-g">
        <CardHead title="Sources" x="how each number stays current" />
        <Sources state={accounts} />
      </div>
    </>
  );
}

function history(S: Snapshot[], name: string, c: string, v: (s: Snapshot) => number): Chart {
  return lineChart({
    label: `${name} on the 1st of each month`,
    series: [{ id: name, name, color: c, area: true, points: S.map((s, i) => [i, v(s)]) }],
    height: 220,
    zero: false,
    formatX: (i) => (S[i] ? monthShortOf(S[i].date) : ""),
    tipTitle: (i) => (S[i] ? dayLong(S[i].date) : ""),
  });
}

/** Diverging columns on the blue ↔ red pair around a zero baseline; the tooltip carries the month's remark and commentary. */
function changeChart(S: Snapshot[], of: "net" | "liquid"): Chart {
  const rows = S.flatMap((s) => {
    const v = of === "net" ? s.netChange : s.liquidChange;
    return v == null ? [] : [{ s, v }];
  });
  const what = of === "net" ? "Net worth" : "Liquid cash";
  return columnChart({
    label: `Monthly change in ${what.toLowerCase()}`,
    mode: "group",
    bands: rows.map(({ s, v }) => ({ key: s.date, label: dayLong(s.date), short: monthShortOf(s.date), segments: [{ key: "chg", name: what, color: v >= 0 ? DIV_GOOD : DIV_BAD, value: v }] })),
    tip: (i) => {
      const r = rows[i]!;
      return html`${tipTitle(dayLong(r.s.date))}${tipRow(r.v >= 0 ? DIV_GOOD : DIV_BAD, `${what} change`, signed(r.v))}
        ${r.s.remark ? html`<div class="tnote">${r.s.remark}</div>` : ""}${r.s.commentary ? html`<div class="tnote t3">${r.s.commentary}</div>` : ""}`;
    },
    table: () => ({ head: ["Month", `${what} change`, "Remark", "Commentary"], rows: rows.map(({ s, v }) => [dayLong(s.date), signed(v), s.remark ?? "", s.commentary ?? ""]) }),
  });
}

function allocationOverTime(S: Snapshot[], slots: Map<string, number | undefined>): Chart {
  return columnChart({
    label: "Allocation of net worth by month, as a share of the total",
    mode: "stack",
    normalize: true,
    bands: S.map((s) => ({
      key: s.date,
      label: dayLong(s.date),
      short: monthShortOf(s.date),
      segments: allocation(s, slots).map((x) => ({ key: x.key, name: x.label, color: slotColor(x.slot), value: Math.max(0, x.amount) })),
    })),
    tip: (i, seg) => {
      const s = S[i]!;
      const sl = allocation(s, slots).filter((x) => x.amount > 0);
      const tot = sl.reduce((a, x) => a + x.amount, 0) || 1;
      return html`${tipTitle(dayLong(s.date))}${[...sl].reverse().map((x) => {
        const row = tipRow(slotColor(x.slot), x.label, `${Math.round((x.amount / tot) * 100)}%`);
        return x.key === seg ? html`<div class="hl">${row}</div>` : row;
      })}`;
    },
  });
}

/** The sheet's ledger, newest first, in the sheet's column order (docs/api.md component keys) with the API's labels. */
function Ledger({ S }: { S: Snapshot[] }) {
  const keys = [...new Set(S.flatMap((s) => s.components.map((c) => c.key)))].sort((a, b) => {
    const ia = NW_SHEET_KEYS.indexOf(a);
    const ib = NW_SHEET_KEYS.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
  });
  const labels = new Map(S.flatMap((s) => s.components.map((c) => [c.key, c.label] as const)));
  const neg = (v: number | null) => (v != null && v < 0 ? " bad" : "");
  return (
    <div className="ledger" role="region" aria-label="Monthly ledger" tabIndex={0}>
      <table className="tbl lg">
        <thead>
          <tr>
            <th className="stick">Month</th>
            {keys.map((k) => (
              <th key={k} className="r">
                {labels.get(k) ?? k}
              </th>
            ))}
            <th className="r">Net worth</th>
            <th className="r">Net change</th>
            <th className="r">Liquid cash</th>
            <th className="r">Liquid change</th>
            <th>Remarks</th>
            <th>Commentary</th>
          </tr>
        </thead>
        <tbody>
          {[...S].reverse().map((s) => {
            const by = new Map(s.components.map((c) => [c.key, c.amount]));
            return (
              <tr key={s.date}>
                <th className="stick" scope="row">
                  {monthShortYear(s.date)}
                </th>
                {keys.map((k) => (
                  <td key={k} className="r num">
                    {by.has(k) ? inr(by.get(k)!) : "—"}
                  </td>
                ))}
                <td className="r num b">{inr(s.netWorth)}</td>
                <td className={`r num${neg(s.netChange)}`}>{s.netChange != null ? signed(s.netChange) : "—"}</td>
                <td className="r num">{inr(s.liquid)}</td>
                <td className={`r num${neg(s.liquidChange)}`}>{s.liquidChange != null ? signed(s.liquidChange) : "—"}</td>
                <td>
                  <RemarkCell key={`${s.date}|${s.remark ?? ""}`} date={s.date} remark={s.remark} />
                </td>
                <td className="t2 cm">
                  {s.commentary ?? ""}
                  {s.commentary && s.commentarySource === "template" && <span className="tag tag-auto">auto</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Saves on Enter or blur, reverts on Escape; PATCH /api/networth/snapshots/{date} {remark}. */
function RemarkCell({ date, remark }: { date: string; remark: string | null }) {
  const toast = useToast();
  const [draft, setDraft] = useState(remark ?? "");
  const [saving, setSaving] = useState(false);
  const commit = async () => {
    const next = draft.trim();
    if (next === (remark ?? "").trim() || saving) return;
    setSaving(true);
    try {
      await saveRemark(date, next);
      toast("Remark saved.");
    } catch (e) {
      setDraft(remark ?? "");
      toast(`Couldn't save the remark. ${(e as ApiError).message}`);
    } finally {
      setSaving(false);
    }
  };
  return (
    <input
      className="inp inp-sm"
      value={draft}
      maxLength={2000}
      aria-label={`Remark for ${monthShortYear(date)}`}
      disabled={saving}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
        if (e.key === "Escape") {
          setDraft(remark ?? "");
          // Blur after the revert lands, so commit() sees nothing to save.
          const el = e.currentTarget;
          requestAnimationFrame(() => el.blur());
        }
      }}
    />
  );
}

/** Health from what M0 reports: a statement that balanced is good, one that didn't is a warning, none yet is bad. */
function accountHealth(a: Account): [Health, string] {
  if (!a.last_statement) return ["bad", "Awaiting a statement"];
  if (a.last_statement.reconciled) return ["good", "Reconciled"];
  return ["warn", `Off by ${inr(Math.abs(toPaise(a.last_statement.diff)))}`];
}

function Sources({ state }: { state: ResourceState<Account[] | null> }) {
  if (state.status === "loading") return <InlineState>Loading…</InlineState>;
  if (state.status === "error") return <InlineState onRetry={() => invalidate(["/api/accounts"])}>{state.error.message}</InlineState>;
  if (!state.data) return <InlineState>Source health appears here once the server reports it.</InlineState>;
  if (!state.data.length) return <InlineState>No accounts connected yet.</InlineState>;
  return (
    <table className="tbl">
      <thead>
        <tr>
          <th>Account</th>
          <th>Live feed</th>
          <th>Ground truth</th>
          <th className="r">Status</th>
        </tr>
      </thead>
      <tbody>
        {state.data.map((a) => {
          const [health, status] = accountHealth(a);
          return (
            <tr key={a.id}>
              <td>{a.label}</td>
              <td className="t2">
                {a.last_seen_at ? `Last alert ${dayShort(dayIST(a.last_seen_at))}` : "Not connected yet"}
                {a.coverage_pct != null ? ` · ${Math.round(a.coverage_pct)}% seen live` : ""}
              </td>
              <td className="t2">{a.last_statement ? `Statement ${dayShort(a.last_statement.period_start)} – ${dayShort(a.last_statement.period_end)}` : "No statement yet"}</td>
              <td className="r">
                <span className="health">
                  <i style={{ background: HEALTH[health] }} aria-hidden />
                  {status}
                </span>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
