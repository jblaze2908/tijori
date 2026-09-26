import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { TxnDrawer } from "../components/TxnDrawer";
import { CategoryPill, Empty, ErrorState, Loading, Monogram } from "../components/ui";
import { MonthGate, txnsFor, type AppCtx } from "../ctx";
import { api, dataOf, invalidate, read } from "../lib/api";
import { dayName, dayShort, inr, plural } from "../lib/format";
import { isExpense, UNCATEGORIZED } from "../lib/insights";
import { navigate, useLocation } from "../lib/router";
import type { Category, Transaction } from "../lib/types";

const KINDS = [
  ["all", "All"],
  ["spend", "Spending"],
  ["income", "Income"],
  ["investment", "Investing"],
  ["transfer", "Transfers"],
] as const;
type KindFilter = (typeof KINDS)[number][0];
const DEFAULT_KIND: KindFilter = "spend";
const ISO = /^\d{4}-\d{2}-\d{2}$/;
const NO_CATEGORIES: Category[] = [];

// ⌘K can fire before Activity mounts (navigation) or while it is already on screen.
const FOCUS_EVENT = "tj:focus-search";
let focusPending = false;
export function focusActivitySearch() {
  focusPending = true;
  dispatchEvent(new Event(FOCUS_EVENT));
}

interface Filters {
  q: string;
  account: string;
  category: string;
  kind: KindFilter;
  from: string | null;
  to: string | null;
}

function readFilters(p: URLSearchParams): Filters {
  const k = p.get("kind");
  const from = p.get("from");
  const to = p.get("to");
  const range = !!(from && to && ISO.test(from) && ISO.test(to) && from <= to);
  return {
    q: p.get("q") ?? "",
    account: p.get("account") ?? "",
    category: p.get("category") ?? "",
    kind: KINDS.some(([v]) => v === k) ? (k as KindFilter) : DEFAULT_KIND,
    from: range ? from : null,
    to: range ? to : null,
  };
}

function writeFilters(f: Filters) {
  const p = new URLSearchParams();
  if (f.q) p.set("q", f.q);
  if (f.account) p.set("account", f.account);
  if (f.category) p.set("category", f.category);
  if (f.kind !== DEFAULT_KIND) p.set("kind", f.kind);
  if (f.from && f.to) {
    p.set("from", f.from);
    p.set("to", f.to);
  }
  const s = p.toString();
  navigate(`/activity${s ? `?${s}` : ""}`, { replace: true });
}

const matchKind = (t: Transaction, k: KindFilter) => k === "all" || (k === "spend" ? isExpense(t) || t.kind === "refund" : t.kind === k);

export function Activity({ app }: { app: AppCtx }) {
  const { params } = useLocation();
  const f = readFilters(params);
  // A period from Trends or Overview (from/to) stands on its own; otherwise Activity follows the month switcher.
  if (f.from && f.to) return <ActivityRange app={app} f={f} from={f.from} to={f.to} />;
  return <MonthGate app={app}>{(m) => <ActivityRange app={app} f={f} from={m.period.start} to={m.period.end} />}</MonthGate>;
}

function ActivityRange({ app, f, from, to }: { app: AppCtx; f: Filters; from: string; to: string }) {
  const st = txnsFor(app, from, to);
  const cats = read(api.categories());
  const accounts = dataOf(read(api.accounts()));
  // Search text is local so typing never waits on a URL round-trip; the URL mirrors it for sharing.
  const [q, setQ] = useState(f.q);
  const [open, setOpen] = useState<Transaction | null>(null);
  const search = useRef<HTMLInputElement>(null);
  const close = useCallback(() => setOpen(null), []);
  const all = st.status === "ready" ? st.data : null;

  // Follows external URL changes, e.g. the Activity nav link clearing the filters.
  useEffect(() => setQ(f.q), [f.q]);

  useEffect(() => {
    const focus = () => {
      if (!search.current) return;
      focusPending = false;
      search.current.focus();
    };
    if (focusPending) focus();
    addEventListener(FOCUS_EVENT, focus);
    return () => removeEventListener(FOCUS_EVENT, focus);
  }, []);

  const view = useMemo(() => {
    if (!all) return null;
    const needle = q.trim().toLowerCase();
    const rows = all
      .filter(
        (t) =>
          (!f.account || t.account_id === f.account) &&
          (!f.category || (t.category ?? UNCATEGORIZED) === f.category) &&
          matchKind(t, f.kind) &&
          (!needle || `${t.merchant} ${t.category ?? ""}`.toLowerCase().includes(needle)),
      )
      .reverse();
    const byDay = new Map<string, Transaction[]>();
    for (const t of rows) {
      const day = byDay.get(t.date);
      if (day) day.push(t);
      else byDay.set(t.date, [t]);
    }
    return { rows, byDay };
  }, [all, q, f.account, f.category, f.kind]);

  const allCategories = cats.status === "ready" ? cats.data : NO_CATEGORIES;
  const accountOptions = accounts
    ? accounts.map((a) => [String(a.id), a.label] as const)
    : [...new Map((all ?? []).flatMap((t) => (t.account_id ? [[t.account_id, t.account] as const] : [])))];
  const names = [...new Set(allCategories.length ? allCategories.map((c) => c.name) : (all ?? []).flatMap((t) => (t.category ? [t.category] : [])))].sort((a, b) =>
    a.localeCompare(b),
  );
  if (f.category && !names.includes(f.category)) names.unshift(f.category);
  if (!names.includes(UNCATEGORIZED)) names.push(UNCATEGORIZED);

  const sum = (ts: Transaction[], dir: Transaction["direction"]) => ts.reduce((a, t) => a + (t.direction === dir ? t.amount : 0), 0);

  return (
    <>
      <div className="filters">
        <input
          ref={search}
          className="inp"
          type="search"
          placeholder="Search merchant or category"
          aria-label="Search merchant or category"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            writeFilters({ ...f, q: e.target.value });
          }}
        />
        <select className="sel" aria-label="Account" value={f.account} onChange={(e) => writeFilters({ ...f, account: e.target.value })}>
          <option value="">All accounts</option>
          {accountOptions.map(([id, label]) => (
            <option key={id} value={id}>
              {label}
            </option>
          ))}
        </select>
        <select className="sel" aria-label="Category" value={f.category} onChange={(e) => writeFilters({ ...f, category: e.target.value })}>
          <option value="">All categories</option>
          {names.map((c) => (
            <option key={c}>{c}</option>
          ))}
        </select>
      </div>
      <div className="filters">
        <div className="chips" role="group" aria-label="Kind">
          {KINDS.map(([k, label]) => (
            <button type="button" key={k} className={`chip${f.kind === k ? " on" : ""}`} aria-pressed={f.kind === k} onClick={() => writeFilters({ ...f, kind: k })}>
              {label}
            </button>
          ))}
          {f.from && f.to && (
            <button type="button" className="chip on range" aria-label={`Clear period ${dayShort(f.from)} to ${dayShort(f.to)}`} onClick={() => writeFilters({ ...f, from: null, to: null })}>
              {dayShort(f.from)} – {dayShort(f.to)} ✕
            </button>
          )}
        </div>
        {view && (
          <div className="summ num">
            <span>{plural(view.rows.length, "transaction")}</span>
            <span>
              Out <b>{inr(sum(view.rows, "debit"))}</b>
            </span>
            {sum(view.rows, "credit") > 0 && (
              <span>
                In <b className="in">{inr(sum(view.rows, "credit"))}</b>
              </span>
            )}
          </div>
        )}
      </div>
      {st.status === "loading" ? (
        <Loading />
      ) : st.status === "error" ? (
        <ErrorState error={st.error} title="Couldn't load transactions" onRetry={() => invalidate(["/api/transactions"])} />
      ) : (
        <div className="card list-card">
          {view!.byDay.size ? (
            [...view!.byDay].map(([day, ts]) => (
              <section key={day} aria-label={dayName(day)}>
                <div className="day">
                  <span>{dayName(day)}</span>
                  <span className="num">{inr(sum(ts, "debit"))}</span>
                </div>
                {ts.map((t) => (
                  <TxnRow key={t.id} t={t} onOpen={setOpen} />
                ))}
              </section>
            ))
          ) : (
            <Empty title="No transactions" card={false}>
              {all?.length ? "Try another filter or period." : "Nothing recorded for this period yet."}
            </Empty>
          )}
        </div>
      )}
      <TxnDrawer txn={open} categories={allCategories} onClose={close} />
    </>
  );
}

function TxnRow({ t, onOpen }: { t: Transaction; onOpen: (t: Transaction) => void }) {
  const credit = t.direction === "credit";
  const note = t.sources.includes("expected") ? " · expected" : t.status === "pending" ? " · pending" : "";
  return (
    <button type="button" className="txn" onClick={() => onOpen(t)}>
      <Monogram name={t.merchant} />
      <span className="mid">
        <b>{t.merchant}</b>
        <small>
          {t.account}
          {note}
        </small>
      </span>
      <CategoryPill category={t.category} />
      <span className={`amt num${credit ? " in" : ""}`}>
        {credit ? "+" : ""}
        {inr(t.amount)}
      </span>
    </button>
  );
}
