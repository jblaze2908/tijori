import { useEffect, useRef, useState } from "react";
import { G } from "../components/Glyphs";
import { TxnDrawer } from "../components/TxnDrawer";
import { ErrorState } from "../components/ui";
import type { AppCtx } from "../ctx";
import { api, dataOf, invalidate, read } from "../lib/api";
import { itemCategoryColor } from "../lib/colors";
import { dayIST, dayShort, inr, toPaise } from "../lib/format";
import { navigate, useLocation } from "../lib/router";
import type { MatchState, OrderItemHit, OrderItemPage, OrderItemQuery, OrderSource } from "../lib/types";
import { presetRange, type Preset } from "./Transactions";

const PAGE = 50;
const TOP = 9;
const PRESETS: [Preset, string][] = [
  ["month", "This month"],
  ["90", "Last 90 days"],
  ["year", "This year"],
  ["all", "All time"],
];
const SOURCES: [OrderSource, string][] = [
  ["blinkit", "Blinkit"],
  ["zomato", "Zomato"],
];
const STATE: Record<MatchState, string> = { matched: "matched", assigned: "on another account", unmatched: "unmatched", ambiguous: "ambiguous", cancelled: "cancelled" };

type Patch = Partial<OrderItemQuery> & { txn?: string | null };

/** The URL holds the filter, as on Transactions; no range means all time, the useful default for an item search. */
function useFilter(): [OrderItemQuery, (patch: Patch) => void, URLSearchParams] {
  const { params } = useLocation();
  const f: OrderItemQuery = {
    from: params.get("from") ?? undefined,
    to: params.get("to") ?? undefined,
    q: params.get("q") ?? undefined,
    source: SOURCES.find(([s]) => s === params.get("source"))?.[0],
    category: params.get("category") ?? undefined,
  };
  const set = (patch: Patch) => {
    const n = { ...f, ...patch };
    const p = new URLSearchParams();
    for (const k of ["from", "to", "q", "source", "category"] as const) if (n[k]) p.set(k, n[k]!);
    const txn = "txn" in patch ? patch.txn : params.get("txn");
    if (txn) p.set("txn", txn);
    const qs = p.toString();
    navigate(`/orders${qs ? `?${qs}` : ""}`, { replace: true });
  };
  return [f, set, params];
}

export function Orders({ app }: { app: AppCtx }) {
  const [f, set, params] = useFilter();
  const [pages, setPages] = useState(1);
  const key = JSON.stringify(f);
  useEffect(() => setPages(1), [key]);
  const states = Array.from({ length: pages }, (_, i) => read(api.orderItems(f, i + 1, PAGE)));
  const first = states[0]!;
  // With a category picked, its own totals hold one row; the card keeps the whole list from the unpicked query.
  const all = read(api.orderItems({ ...f, category: undefined }, 1, PAGE));
  const items = states.flatMap((s) => (s.status === "ready" ? s.data.items : []));
  const page = dataOf(first);
  const total = page?.total ?? 0;
  const openId = params.get("txn");
  const ids = [...new Set(items.flatMap((i) => (i.txn_id != null ? [String(i.txn_id)] : [])))];
  const at = openId ? ids.indexOf(openId) : -1;
  const cur = PRESETS.find(([p]) => {
    const r = presetRange(p, app);
    return r.from === f.from && r.to === f.to;
  })?.[0];
  return (
    <>
      <div className="ph">
        <h1>Orders</h1>
      </div>
      <div className="filters2">
        <div className="seg lg" role="group" aria-label="Time range">
          {PRESETS.map(([p, label]) => (
            <button key={p} type="button" className={cur === p ? "on" : ""} aria-pressed={cur === p} onClick={() => set({ from: undefined, to: undefined, ...presetRange(p, app) })}>
              {label}
            </button>
          ))}
        </div>
        <Filters f={f} set={set} cats={dataOf(all)?.totals.by_category ?? []} />
      </div>
      {first.status === "error" ? (
        <ErrorState error={first.error} title="Couldn't load orders" onRetry={() => invalidate(["/api/order-items"])} />
      ) : (
        <>
          <div className="summary" aria-live="polite">
            <div>
              <b className="lg">{page ? total.toLocaleString("en-IN") : "…"}</b>
              <span className="k">items match</span>
            </div>
            <div>
              <span className="k">Spent</span>
              <b>{page ? inr(toPaise(page.totals.spent)) : "…"}</b>
            </div>
            <div>
              <span className="k">Orders</span>
              <b>{page ? page.totals.orders.toLocaleString("en-IN") : "…"}</b>
            </div>
            <div>
              <span className="k">Priced</span>
              <b>{page ? `${page.totals.priced} of ${total}` : "…"}</b>
              {page && page.totals.priced < total && <small className="foot">Zomato counted by its bill</small>}
            </div>
          </div>
          <div className="ordgrid">
            <ByCategory page={dataOf(all)} picked={f.category} pick={(c) => set({ category: c })} />
            <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
              <Table items={items} loading={states.some((s) => s.status === "loading")} openId={openId} open={(id) => set({ txn: id })} />
              {items.length < total && (
                <div className="tfoot" style={{ borderTop: 0 }}>
                  <span className="foot">
                    Showing {items.length.toLocaleString("en-IN")} of {total.toLocaleString("en-IN")}
                  </span>
                  <button type="button" className="btn2 sm" onClick={() => setPages((p) => p + 1)}>
                    Load {Math.min(PAGE, total - items.length)} more
                  </button>
                </div>
              )}
            </div>
          </div>
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

const catName = (c: string | null | undefined) => (c == null || c === "none" ? "Uncategorised" : c);

function Filters({ f, set, cats }: { f: OrderItemQuery; set: (p: Patch) => void; cats: OrderItemPage["totals"]["by_category"] }) {
  const [open, setOpen] = useState<"source" | "category" | null>(null);
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
  const chip = (k: "source" | "category", label: string, value: string | null, clear: () => void) => (
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
    <div className="filters2" ref={box} style={{ flex: 1 }}>
      <label className="search2">
        {G.search}
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Item, restaurant, category or address" aria-label="Search order items" maxLength={100} />
      </label>
      <div className="chiprow">
        {chip("source", "Source", SOURCES.find(([s]) => s === f.source)?.[1] ?? null, () => set({ source: undefined }))}
        {chip("category", "Category", f.category ? catName(f.category) : null, () => set({ category: undefined }))}
      </div>
      {open === "source" && (
        <div className="pop" style={{ left: 308 }} role="dialog" aria-label="Source">
          <span className="lbl">Source</span>
          <div className="seg full">
            {([undefined, ...SOURCES.map(([s]) => s)] as const).map((s) => (
              <button key={s ?? "all"} type="button" className={f.source === s ? "on" : ""} onClick={() => (set({ source: s }), setOpen(null))}>
                {SOURCES.find(([x]) => x === s)?.[1] ?? "All"}
              </button>
            ))}
          </div>
        </div>
      )}
      {open === "category" && (
        <div className="pop" style={{ left: 400 }} role="dialog" aria-label="Category">
          <span className="lbl">Category</span>
          <div className="list">
            {cats.map((c) => {
              const v = c.category ?? "none";
              return (
                <label key={v}>
                  <input type="radio" name="ocat" checked={f.category?.toLowerCase() === v.toLowerCase()} onChange={() => (set({ category: v }), setOpen(null))} />
                  <i className="dot" style={{ background: itemCategoryColor(c.category) }} />
                  {catName(c.category)}
                </label>
              );
            })}
            {!cats.length && <span className="foot">No items in this range.</span>}
          </div>
        </div>
      )}
    </div>
  );
}

function ByCategory({ page, picked, pick }: { page: OrderItemPage | null; picked: string | undefined; pick: (c: string | undefined) => void }) {
  const [more, setMore] = useState(false);
  const rows = page?.totals.by_category ?? [];
  const max = Math.max(1, ...rows.map((r) => toPaise(r.spent)));
  const shown = more ? rows : rows.slice(0, TOP);
  return (
    <section className="panel ocats" aria-label="By category">
      <span className="lbl">By category</span>
      {!page && <p className="state">Loading…</p>}
      {page && !rows.length && <p className="state">No items in this range.</p>}
      {shown.map((r) => {
        const v = r.category ?? "none";
        const on = picked?.toLowerCase() === v.toLowerCase();
        const color = itemCategoryColor(r.category);
        return (
          <button key={v} type="button" className={`ocat${on ? " on" : ""}`} aria-pressed={on} onClick={() => pick(on ? undefined : v)}>
            <span className="t">
              <i className="dot" style={{ background: color }} />
              <span className="nm">{catName(r.category)}</span>
              <span className="mono-n">{inr(toPaise(r.spent))}</span>
            </span>
            <span className="bar">
              <span style={{ width: `${(toPaise(r.spent) / max) * 100}%`, background: color }} />
            </span>
          </button>
        );
      })}
      {rows.length > TOP && (
        <button type="button" className="linkish" style={{ margin: 0, alignSelf: "flex-start" }} onClick={() => setMore((v) => !v)}>
          {more ? "Show fewer" : `+ ${rows.length - TOP} more`}
        </button>
      )}
    </section>
  );
}

function Table({ items, loading, openId, open }: { items: OrderItemHit[]; loading: boolean; openId: string | null; open: (id: string) => void }) {
  return (
    <section className="panel ttable otable" aria-label="Order items" aria-busy={loading}>
      <div className="tcols">
        <span className="c-date">Date</span>
        <span className="c-name">Item</span>
        <span className="c-cat">Category</span>
        <span className="c-ord">Order</span>
        <span className="c-amt">Price</span>
      </div>
      {items.map((i, k) => {
        const id = i.txn_id != null ? String(i.txn_id) : null;
        const each = i.qty > 1 && i.line_price != null && i.note !== "unavailable" ? `${inr(Math.round(toPaise(i.line_price) / i.qty))} each` : null;
        const sub = [[i.unit, i.qty > 1 ? `× ${i.qty}` : null].filter(Boolean).join(" "), each, i.note].filter(Boolean).join(" · ");
        const body = (
          <>
            <span className="c-date">{dayShort(dayIST(i.placed_at))}</span>
            <span className="c-name">
              <span className="nmcol">
                <span>{i.name}</span>
                {sub && <small className="faint">{sub}</small>}
              </span>
            </span>
            <span className="c-cat">
              <i className="dot" style={{ background: itemCategoryColor(i.category) }} />
              <span>{catName(i.category)}</span>
            </span>
            <span className="c-ord nmcol">
              <span>{i.source === "zomato" ? (i.store ?? "Zomato") : "Blinkit"}</span>
              <small className="faint">{STATE[i.match_state] ?? i.match_state}</small>
            </span>
            <span className="c-amt">{i.line_price != null && i.note !== "unavailable" ? inr(toPaise(i.line_price)) : <span className="faint">—</span>}</span>
          </>
        );
        return id ? (
          <button key={`${i.order_no}-${k}`} type="button" className={`trow${id === openId ? " sel" : ""}`} onClick={() => open(id)}>
            {body}
          </button>
        ) : (
          <div key={`${i.order_no}-${k}`} className="trow static" title="No transaction paid for this order yet">
            {body}
          </div>
        );
      })}
      {!items.length && !loading && <p className="state">No order items match these filters.</p>}
      {loading && !items.length && <p className="state">Loading…</p>}
    </section>
  );
}
