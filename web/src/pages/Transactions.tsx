import { useEffect, useRef, useState, type ReactNode } from "react";
import { G } from "../components/Glyphs";
import { TxnDrawer } from "../components/TxnDrawer";
import { useToast } from "../components/Toast";
import { ErrorState } from "../components/ui";
import type { AppCtx } from "../ctx";
import { allTxns, api, dataOf, invalidate, read } from "../lib/api";
import { categoryColor, monogramColor } from "../lib/colors";
import { addDays, dayShort, inr, monthYear, plural } from "../lib/format";
import { navigate, useLocation } from "../lib/router";
import type { Direction, ISODate, Sort, Transaction, TxnKind, TxnQuery } from "../lib/types";

const PAGE = 50;
const KINDS: [TxnKind, string][] = [
  ["spend", "Spend"],
  ["income", "Income"],
  ["investment", "Investment"],
  ["transfer", "Transfer"],
  ["refund", "Refund"],
  ["fee", "Fee"],
  ["cash", "Cash"],
];

type Preset = "month" | "last" | "year" | "fy" | "90" | "all";
function presetRange(p: Preset, app: AppCtx): { from?: ISODate; to?: ISODate } {
  const today = app.asOf;
  const cur = app.cycle(today.slice(0, 7));
  if (p === "month") return { from: cur.period.start, to: cur.period.end };
  if (p === "last") {
    const prev = app.cycle(addMonthKey(today.slice(0, 7), -1));
    return { from: prev.period.start, to: prev.period.end };
  }
  if (p === "year") return { from: `${today.slice(0, 4)}-01-01`, to: today };
  if (p === "fy") {
    const y = Number(today.slice(0, 4)) - (Number(today.slice(5, 7)) < 4 ? 1 : 0);
    return { from: `${y}-04-01`, to: today };
  }
  if (p === "90") return { from: addDays(today, -89), to: today };
  return {};
}
const addMonthKey = (m: string, n: number) => {
  const i = Number(m.slice(0, 4)) * 12 + Number(m.slice(5, 7)) - 1 + n;
  return `${Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, "0")}`;
};
const PRESETS: [Preset, string][] = [
  ["month", "This month"],
  ["last", "Last month"],
  ["year", "This year"],
  ["fy", "FY"],
  ["90", "Last 90 days"],
  ["all", "All time"],
];

/** The URL is the filter's source of truth, so Overview links and reloads land on the same view. */
function useFilter(app: AppCtx): [TxnQuery, (patch: Partial<TxnQuery> & { txn?: string | null }) => void, URLSearchParams] {
  const { params } = useLocation();
  const hasRange = params.has("from") || params.has("to") || params.get("range") === "all";
  const def = hasRange ? {} : presetRange("month", app);
  const f: TxnQuery = {
    from: params.get("from") ?? def.from,
    to: params.get("to") ?? def.to,
    q: params.get("q") ?? undefined,
    min: params.get("min") ?? undefined,
    max: params.get("max") ?? undefined,
    accounts: params.getAll("account").map(Number).filter((n) => n > 0),
    categories: params.getAll("category").filter((c) => c === "none" || /^\d+$/.test(c)),
    kind: (params.get("kind") as TxnKind | null) ?? undefined,
    direction: (params.get("direction") as Direction | null) ?? undefined,
    sort: (params.get("sort") as Sort | null) ?? "date_desc",
    paidWith: (["bank", "card"] as const).find((v) => v === params.get("paid_with")),
  };
  const set = (patch: Partial<TxnQuery> & { txn?: string | null }) => {
    const n = { ...f, ...patch };
    const p = new URLSearchParams();
    if (n.from) p.set("from", n.from);
    if (n.to) p.set("to", n.to);
    if (!n.from && !n.to) p.set("range", "all");
    if (n.q) p.set("q", n.q);
    if (n.min) p.set("min", n.min);
    if (n.max) p.set("max", n.max);
    for (const a of n.accounts ?? []) p.append("account", String(a));
    for (const c of n.categories ?? []) p.append("category", c);
    if (n.kind) p.set("kind", n.kind);
    if (n.direction) p.set("direction", n.direction);
    if (n.sort && n.sort !== "date_desc") p.set("sort", n.sort);
    if (n.paidWith) p.set("paid_with", n.paidWith);
    const txn = "txn" in patch ? patch.txn : params.get("txn");
    if (txn) p.set("txn", txn);
    navigate(`/transactions?${p}`, { replace: true });
  };
  return [f, set, params];
}

function activePreset(f: TxnQuery, app: AppCtx): Preset | null {
  for (const [p] of PRESETS) {
    const r = presetRange(p, app);
    if (r.from === f.from && r.to === f.to) return p;
  }
  return null;
}

export function Transactions({ app }: { app: AppCtx }) {
  const [f, set, params] = useFilter(app);
  const [pages, setPages] = useState(1);
  const key = JSON.stringify(f);
  useEffect(() => setPages(1), [key]);
  const states = Array.from({ length: pages }, (_, i) => read(api.txnPage(f, i + 1, PAGE)));
  const first = states[0]!;
  const items = states.flatMap((s) => (s.status === "ready" ? s.data.items : []));
  const total = first.status === "ready" ? first.data.total : 0;
  const totals = first.status === "ready" ? first.data.totals : null;
  const openId = params.get("txn");
  const toast = useToast();
  const [exporting, setExporting] = useState(false);

  const exportCsv = async () => {
    setExporting(true);
    try {
      const rows = await allTxns(f);
      download(`tijori-${f.from ?? "all"}-${f.to ?? "time"}.csv`, toCsv(rows));
      toast(`Exported ${plural(rows.length, "transaction")}`);
    } catch {
      toast("Export failed. Try again.");
    } finally {
      setExporting(false);
    }
  };

  const ids = items.map((t) => t.id);
  const at = openId ? ids.indexOf(openId) : -1;
  return (
    <>
      <div className="ph">
        <h1>Transactions</h1>
        <div className="sp" />
        <button type="button" className="btn2" onClick={exportCsv} disabled={exporting || !total}>
          {G.download}
          {exporting ? "Exporting…" : "Export CSV"}
        </button>
      </div>
      <TimeRange f={f} set={set} app={app} />
      <Filters f={f} set={set} />
      {first.status === "error" ? (
        <ErrorState error={first.error} title="Couldn't load transactions" onRetry={() => invalidate(["/api/transactions"])} />
      ) : (
        <>
          <div className="summary" aria-live="polite">
            <div>
              <b className="lg">{first.status === "ready" ? total.toLocaleString("en-IN") : "…"}</b>
              <span className="k">transactions match</span>
            </div>
            <SumCell k="Income" t={totals?.income} sign="+" cls="good-t" />
            <SumCell
              k="Spent"
              t={totals?.spend}
              sign="−"
              sub={totals ? [totals.on_card?.count ? `${inr(totals.on_card.amount)} on cards` : null, totals.card.count ? `${inr(totals.card.amount)} card bills not itemised` : null].filter(Boolean).join(" · ") || undefined : undefined}
            />
            <SumCell k="Invested" t={totals?.invest} sign="−" />
            <SumCell k="Transfers & excluded" t={totals?.excluded} cls="muted" />
          </div>
          <Table f={f} set={set} items={items} total={total} loading={states.some((s) => s.status === "loading")} openId={openId} />
          {items.length < total && (
            <div className="tfoot" style={{ borderTop: 0, marginTop: -16 }}>
              <span className="foot">
                Showing {items.length.toLocaleString("en-IN")} of {total.toLocaleString("en-IN")}
              </span>
              <button type="button" className="btn2 sm" onClick={() => setPages((p) => p + 1)}>
                Load {Math.min(PAGE, total - items.length)} more
              </button>
            </div>
          )}
        </>
      )}
      <TxnDrawer
        id={openId}
        onClose={() => set({ txn: null })}
        prev={at > 0 ? () => set({ txn: ids[at - 1]! }) : undefined}
        next={at >= 0 && at < ids.length - 1 ? () => set({ txn: ids[at + 1]! }) : undefined}
      />
    </>
  );
}

function SumCell({ k, t, sign = "", cls = "", sub }: { k: string; t?: { amount: number; count: number }; sign?: string; cls?: string; sub?: string }) {
  return (
    <div>
      <span className="k">
        {k}
        {t ? ` · ${t.count}` : ""}
      </span>
      <b className={cls}>{t ? `${t.amount ? sign : ""}${inr(t.amount)}` : "…"}</b>
      {sub && <small className="foot">{sub}</small>}
    </div>
  );
}

function TimeRange({ f, set, app }: { f: TxnQuery; set: (p: Partial<TxnQuery>) => void; app: AppCtx }) {
  const cur = activePreset(f, app);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
      <div className="seg lg" role="group" aria-label="Time range">
        {PRESETS.map(([p, label]) => (
          <button key={p} type="button" className={cur === p ? "on" : ""} aria-pressed={cur === p} onClick={() => set({ ...presetRange(p, app), ...(p === "all" ? { from: undefined, to: undefined } : {}) })}>
            {label}
          </button>
        ))}
      </div>
      <label className="datefield">
        {G.calendar}
        <input type="date" aria-label="From" value={f.from ?? ""} max={f.to} onChange={(e) => set({ from: e.target.value || undefined })} />
        <span className="faint">→</span>
        <input type="date" aria-label="To" value={f.to ?? ""} min={f.from} onChange={(e) => set({ to: e.target.value || undefined })} />
      </label>
      <span className="foot rangehint">Pick any start date; leave the end empty for today</span>
    </div>
  );
}

type Open = "amount" | "direction" | "category" | "account" | "kind" | "paid" | null;

function Filters({ f, set }: { f: TxnQuery; set: (p: Partial<TxnQuery>) => void }) {
  const [open, setOpen] = useState<Open>(null);
  const [q, setQ] = useState(f.q ?? "");
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => setQ(f.q ?? ""), [f.q]);
  useEffect(() => {
    const t = setTimeout(() => (q.trim() || undefined) !== f.q && set({ q: q.trim() || undefined }), 300);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(null);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(null);
    addEventListener("mousedown", close);
    addEventListener("keydown", esc);
    return () => {
      removeEventListener("mousedown", close);
      removeEventListener("keydown", esc);
    };
  }, [open]);
  const cats = dataOf(read(api.categories())) ?? [];
  const accounts = dataOf(read(api.accounts())) ?? [];
  const amountLabel = f.min && f.max ? `₹${Number(f.min).toLocaleString("en-IN")}–${Number(f.max).toLocaleString("en-IN")}` : f.min ? `> ₹${Number(f.min).toLocaleString("en-IN")}` : f.max ? `< ₹${Number(f.max).toLocaleString("en-IN")}` : null;
  const catLabel = f.categories?.length ? (f.categories.length === 1 ? (f.categories[0] === "none" ? "Uncategorized" : (cats.find((c) => String(c.id) === f.categories![0])?.name ?? "1")) : `${f.categories.length} selected`) : null;
  const acctLabel = f.accounts?.length ? (f.accounts.length === 1 ? (accounts.find((a) => a.id === f.accounts![0])?.label ?? "1") : `${f.accounts.length} selected`) : null;
  const any = amountLabel || catLabel || acctLabel || f.kind || f.direction || f.q || f.paidWith;
  const chip = (k: Exclude<Open, null>, label: string, value: string | null, clear: () => void) => (
    <button type="button" className={`chip2${value ? " on" : ""}`} aria-expanded={open === k} onClick={() => setOpen(open === k ? null : k)}>
      <span>{label}</span>
      {value && <span className="v">{value}</span>}
      {value ? (
        <span
          role="button"
          aria-label={`Clear ${label}`}
          onClick={(e) => {
            e.stopPropagation();
            clear();
            setOpen(null);
          }}
        >
          {G.close}
        </span>
      ) : (
        G.down
      )}
    </button>
  );
  return (
    <div className="filters2" ref={box}>
      <label className="search2">
        {G.search}
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Merchant, UPI id or narration" aria-label="Search transactions" maxLength={100} />
      </label>
      <div className="chiprow">
        {chip("amount", "Amount", amountLabel, () => set({ min: undefined, max: undefined }))}
        {chip("direction", "Direction", f.direction === "debit" ? "Money out" : f.direction === "credit" ? "Money in" : null, () => set({ direction: undefined }))}
        {chip("category", "Category", catLabel, () => set({ categories: [] }))}
        {chip("paid", "Paid with", f.paidWith === "card" ? "Card" : f.paidWith === "bank" ? "Bank" : null, () => set({ paidWith: undefined }))}
        {chip("account", "Account", acctLabel, () => set({ accounts: [] }))}
        {chip("kind", "Kind", KINDS.find(([k]) => k === f.kind)?.[1] ?? null, () => set({ kind: undefined }))}
      </div>
      <div style={{ flex: 1 }} />
      {any && (
        <button type="button" className="btn2 sm" style={{ border: 0, color: "var(--t2)" }} onClick={() => set({ q: undefined, min: undefined, max: undefined, categories: [], accounts: [], kind: undefined, direction: undefined, paidWith: undefined })}>
          Clear all
        </button>
      )}
      {open === "amount" && <AmountPop f={f} set={set} close={() => setOpen(null)} />}
      {open === "direction" && (
        <Pop left={608}>
          <span className="lbl">Direction</span>
          <div className="seg full">
            {([undefined, "debit", "credit"] as const).map((d) => (
              <button key={d ?? "all"} type="button" className={f.direction === d ? "on" : ""} onClick={() => (set({ direction: d }), setOpen(null))}>
                {d === "debit" ? "Money out" : d === "credit" ? "Money in" : "All"}
              </button>
            ))}
          </div>
        </Pop>
      )}
      {open === "category" && (
        <Pop left={720}>
          <span className="lbl">Category</span>
          <div className="list">
            {[{ id: "none", name: "Uncategorized" }, ...cats.map((c) => ({ id: String(c.id), name: c.name }))].map((c) => (
              <label key={c.id}>
                <input
                  type="checkbox"
                  checked={f.categories?.includes(c.id) ?? false}
                  onChange={(e) => set({ categories: e.target.checked ? [...(f.categories ?? []), c.id] : (f.categories ?? []).filter((x) => x !== c.id) })}
                />
                <i className="sq" style={{ background: c.id === "none" ? "var(--t3)" : categoryColor(c.name) }} />
                {c.name}
              </label>
            ))}
          </div>
        </Pop>
      )}
      {open === "paid" && (
        <Pop left={830}>
          <span className="lbl">Paid with</span>
          <div className="seg full">
            {([undefined, "bank", "card"] as const).map((v) => (
              <button key={v ?? "all"} type="button" className={f.paidWith === v ? "on" : ""} onClick={() => (set({ paidWith: v }), setOpen(null))}>
                {v === "bank" ? "Bank & UPI" : v === "card" ? "Credit card" : "All"}
              </button>
            ))}
          </div>
          <span className="foot">Card purchases come from card statements. A card bill paid from the bank is a transfer once it's matched to the card.</span>
        </Pop>
      )}
      {open === "account" && (
        <Pop left={900}>
          <span className="lbl">Account</span>
          <div className="list">
            {(["bank", "card", "wallet"] as const).map((kind) => {
              const group = accounts.filter((a) => (kind === "bank" ? a.kind !== "card" && a.kind !== "wallet" : a.kind === kind));
              return group.length ? (
                <div key={kind} className="grp">
                  <span className="foot">{kind === "bank" ? "Bank accounts" : kind === "card" ? "Credit cards" : "Wallets"}</span>
                  {group.map((a) => (
                    <label key={a.id}>
                      <input type="checkbox" checked={f.accounts?.includes(a.id) ?? false} onChange={(e) => set({ accounts: e.target.checked ? [...(f.accounts ?? []), a.id] : (f.accounts ?? []).filter((x) => x !== a.id) })} />
                      {a.kind === "card" && <span className="faint">{G.card}</span>}
                      {a.label}
                    </label>
                  ))}
                </div>
              ) : null;
            })}
            {!accounts.length && <span className="foot">No accounts yet.</span>}
          </div>
        </Pop>
      )}
      {open === "kind" && (
        <Pop left={930}>
          <span className="lbl">Kind</span>
          <div className="list">
            {KINDS.map(([k, l]) => (
              <label key={k}>
                <input type="radio" name="kind" checked={f.kind === k} onChange={() => (set({ kind: k }), setOpen(null))} />
                {l}
              </label>
            ))}
          </div>
        </Pop>
      )}
    </div>
  );
}

const Pop = ({ left, children }: { left: number; children: ReactNode }) => (
  <div className="pop" style={{ left: Math.min(left, 900) }} role="dialog">
    {children}
  </div>
);

const MONEY = /^\d{0,12}(\.\d{0,2})?$/;
function AmountPop({ f, set, close }: { f: TxnQuery; set: (p: Partial<TxnQuery>) => void; close: () => void }) {
  const [mode, setMode] = useState<"more" | "less" | "between">(f.min && f.max ? "between" : f.max ? "less" : "more");
  const [a, setA] = useState(f.min ?? f.max ?? "");
  const [b, setB] = useState(f.max ?? "");
  const valid = (v: string) => v !== "" && MONEY.test(v);
  const ok = mode === "between" ? valid(a) && valid(b) && Number(a) <= Number(b) : valid(a);
  const apply = () => {
    if (!ok) return;
    if (mode === "more") set({ min: a, max: undefined });
    else if (mode === "less") set({ min: undefined, max: a });
    else set({ min: a, max: b });
    close();
  };
  const money = (v: string, on: (v: string) => void, label: string) => (
    <label className="money">
      <span className="mono-n faint">₹</span>
      <input inputMode="decimal" aria-label={label} value={v} onChange={(e) => MONEY.test(e.target.value) && on(e.target.value)} onKeyDown={(e) => e.key === "Enter" && apply()} autoFocus={label !== "To"} />
    </label>
  );
  return (
    <div className="pop" style={{ left: 308, width: 300 }} role="dialog" aria-label="Amount filter">
      <span className="lbl">Amount</span>
      <div className="seg full">
        {(["more", "less", "between"] as const).map((m) => (
          <button key={m} type="button" className={mode === m ? "on" : ""} onClick={() => setMode(m)}>
            {m === "more" ? "More than" : m === "less" ? "Less than" : "Between"}
          </button>
        ))}
      </div>
      {money(a, setA, mode === "between" ? "From" : "Amount")}
      {mode === "between" && money(b, setB, "To")}
      <div className="quick">
        {["1000", "5000", "10000", "25000", "100000"].map((v) => (
          <button key={v} type="button" className={a === v ? "on" : ""} onClick={() => setA(v)}>
            {Number(v) >= 100000 ? `${Number(v) / 100000}L` : `${Number(v) / 1000}k`}
          </button>
        ))}
      </div>
      <div className="acts">
        <button type="button" className="btn2 sm" style={{ border: 0 }} onClick={() => (set({ min: undefined, max: undefined }), close())}>
          Clear
        </button>
        <button type="button" className="btn2 sm primary" disabled={!ok} onClick={apply}>
          Apply
        </button>
      </div>
    </div>
  );
}

function Table({ f, set, items, total, loading, openId }: { f: TxnQuery; set: (p: Partial<TxnQuery> & { txn?: string | null }) => void; items: Transaction[]; total: number; loading: boolean; openId: string | null }) {
  const byDate = f.sort === "date_desc" || f.sort === "date_asc" || !f.sort;
  const complete = items.length >= total;
  const groups: { key: string; rows: Transaction[] }[] = [];
  for (const t of items) {
    const k = byDate ? t.date.slice(0, 7) : "all";
    if (groups.at(-1)?.key !== k) groups.push({ key: k, rows: [] });
    groups.at(-1)!.rows.push(t);
  }
  const sortBtn = (col: "date" | "amount", label: string) => {
    const on = f.sort?.startsWith(col) || (col === "date" && !f.sort);
    const asc = f.sort === `${col}_asc`;
    return (
      <button type="button" className={on ? "on" : ""} onClick={() => set({ sort: on ? (asc ? `${col}_desc` : `${col}_asc`) : `${col}_desc` })} aria-label={`Sort by ${label}`}>
        {label}
        {on && (asc ? G.sortUp : G.sortDown)}
      </button>
    );
  };
  return (
    <section className="panel ttable" aria-label="Transactions" aria-busy={loading}>
      <div className="tcols">
        <span className="c-date">{sortBtn("date", "Date")}</span>
        <span className="c-name">Merchant / payee</span>
        <span className="c-cat">Category</span>
        <span className="c-acct">Account</span>
        <span className="c-amt" style={{ display: "flex", justifyContent: "flex-end" }}>
          {sortBtn("amount", "Amount")}
        </span>
      </div>
      {groups.map((g) => {
        // Transfers (incl. matched card bills) move money between your own accounts: left out, as in the summary.
        const inn = g.rows.reduce((a, t) => (t.direction === "credit" && t.bucket !== "excluded" ? a + t.amount : a), 0);
        const out = g.rows.reduce((a, t) => (t.direction === "debit" && t.bucket !== "excluded" ? a + t.amount : a), 0);
        return (
          <div key={g.key}>
            {byDate && (
              <div className="tgroup">
                <b>
                  {monthYear(g.key)} <span className="faint" style={{ fontWeight: 400 }}>· {g.rows.length}</span>
                </b>
                {complete && inn > 0 && <span className="mono-n good-t">+{inr(inn)}</span>}
                <span className="c-amt muted">{complete && out > 0 ? `−${inr(out)}` : ""}</span>
              </div>
            )}
            {g.rows.map((t) => (
              <Row key={t.id} t={t} sel={t.id === openId} onOpen={() => set({ txn: t.id })} />
            ))}
          </div>
        );
      })}
      {!items.length && !loading && <p className="state">No transactions match these filters.</p>}
      {loading && !items.length && <p className="state">Loading…</p>}
    </section>
  );
}

function Row({ t, sel, onOpen }: { t: Transaction; sel: boolean; onOpen: () => void }) {
  const credit = t.direction === "credit";
  const muted = t.bucket === "excluded" || t.bucket === "card";
  const onCard = t.account_kind === "card";
  // A bill payment is a transfer once matched to the card (its purchases are counted there); unmatched, it stands in.
  const tag = t.split_parts ? ["Split", ""] : t.split_of ? ["Part", ""] : t.category == null ? null : t.loan_id != null || t.category === "Loans" ? ["Loan", ""] : t.bucket === "card" ? ["Card bill", "stand"] : t.bucket === "excluded" ? ["Transfer", ""] : t.bucket === "invest" ? ["Invest", "invest"] : t.bucket === "income" ? ["Income", "income"] : null;
  const catText = t.split_parts ? `Into ${t.split_parts} parts` : t.settles?.card ? `Paid ${t.settles.card}` : t.settles?.from_account ? `From ${t.settles.from_account}` : t.bucket === "card" ? "Counts as spend" : (t.category ?? "Uncategorized");
  return (
    <button type="button" className={`trow${sel ? " sel" : ""}`} onClick={onOpen}>
      <span className="c-date">{dayShort(t.date)}</span>
      <span className="c-name">
        <span className={`mg${t.category == null ? " unknown" : ""}`} style={{ background: muted ? "var(--s4)" : monogramColor(t.merchant) }}>
          {t.category == null ? "?" : (t.merchant.replace(/[^A-Za-z0-9]/g, "")[0] ?? "•").toUpperCase()}
        </span>
        <span className="nmcol">
          <span className={`nm${muted ? " muted" : ""}`}>{t.category == null && t.vpa && !t.named ? t.vpa : t.merchant}</span>
          <small className="msub">
            {catText} · {t.account}
          </small>
        </span>
        {onCard && (
          <span className="cardtag" title={t.account}>
            {G.card}Card
          </span>
        )}
        {t.notes && <span className="faint" title={t.notes} style={{ fontSize: 12 }}>· note</span>}
      </span>
      <span className="c-cat">
        {tag && <span className={tag[1] === "stand" ? "standtag" : `kindtag ${tag[1]}`}>{tag[0]}</span>}
        {!tag && <i className="sq" style={{ background: t.category ? categoryColor(t.category) : "var(--t3)" }} />}
        <span className={t.category ? (t.settles || t.bucket === "card" ? "muted" : "") : "acc-t"}>{catText}</span>
      </span>
      <span className="c-acct">
        {onCard && <span className="faint">{G.card}</span>}
        {t.account}
      </span>
      <span className={`c-amt${credit && !muted ? " good-t" : muted ? " muted" : ""}`}>
        {credit ? "+" : "−"}
        {inr(t.amount)}
      </span>
    </button>
  );
}

// ---------- CSV ----------

/** Narrations and payee names are untrusted: a leading = + - @ would run as a spreadsheet formula, so it's neutralised. */
const cell = (v: string | number | null) => {
  let s = v == null ? "" : String(v);
  if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`;
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};
function toCsv(rows: Transaction[]): string {
  const head = ["date", "merchant", "upi_id", "category", "kind", "direction", "amount", "account", "account_kind", "status", "notes", "tags", "narration"];
  const body = rows.map((t) =>
    [t.date, t.merchant, t.vpa, t.category, t.kind, t.direction, (t.amount / 100).toFixed(2), t.account, t.account_kind, t.status, t.notes, t.tags.join(" "), t.narration].map(cell).join(","),
  );
  return [head.join(","), ...body].join("\n") + "\n";
}
function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
