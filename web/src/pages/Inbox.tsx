import { useState } from "react";
import { G } from "../components/Glyphs";
import { useToast } from "../components/Toast";
import { ErrorState, Loading } from "../components/ui";
import type { AppCtx } from "../ctx";
import { api, dataOf, fileInboxPayee, invalidate, read, undoFiling } from "../lib/api";
import { LoanPicker } from "../components/Loans";
import { monogramColor } from "../lib/colors";
import { dayShort, inr, monthShort, plural, toPaise } from "../lib/format";
import { Link } from "../lib/router";
import type { Category, InboxPayee, Scope } from "../lib/types";

const REASON: Record<string, [string, string]> = {
  person: ["Person", "person"],
  conflict: ["Mixed history", "mixed"],
  merchant_over_cap: ["Merchant QR", ""],
  new_payee: ["New payee", ""],
};
// First-time suggestions by why it's waiting; payee history always comes first.
const DEFAULTS: Record<string, string[]> = {
  "person:debit": ["Family", "Services", "Local shops"],
  "person:credit": ["Other income", "Refunds", "Family"],
  "merchant_over_cap:debit": ["Local shops", "Groceries", "Eating out"],
  "debit": ["Shopping", "Eating out", "Entertainment"],
  "credit": ["Other income", "Refunds", "Interest"],
};
interface Filed {
  id: string;
  label: string;
  txnIds: number[];
  ruleId: string | null;
}

export function Inbox({ app }: { app: AppCtx }) {
  const st = read(api.inbox());
  const cats = dataOf(read(api.categories())) ?? [];
  const me = dataOf(read(api.me()));
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [filed, setFiled] = useState<Filed[]>([]);
  const toast = useToast();
  const month = app.asOf.slice(0, 7);

  const onFiled = (p: InboxPayee, cat: Category, ruleId: string | null) => {
    setFiled((f) => [{ id: p.key, label: `${p.vpa ?? p.payee} · ${inr(p.total)} · Filed as ${cat.name}${ruleId ? " · rule created" : ""}`, txnIds: p.payments.map((x) => Number(x.id)), ruleId }, ...f].slice(0, 3));
    setSelected((s) => {
      const n = new Set(s);
      n.delete(p.key);
      return n;
    });
  };
  const undo = async (f: Filed) => {
    try {
      await undoFiling(f.txnIds, f.ruleId);
      setFiled((all) => all.filter((x) => x !== f));
      toast("Back in the Inbox");
    } catch {
      toast("Couldn't undo. Try again.");
    }
  };

  const items = st.status === "ready" ? st.data.items : [];
  const picked = items.filter((p) => selected.has(p.key));
  return (
    <>
      <div className="ph">
        <h1>Inbox</h1>
        <div className="sp" />
        <span className="muted" style={{ fontSize: 13 }}>
          {st.status === "ready" ? `${plural(st.data.total, "payee")} to categorize` : ""}
        </span>
      </div>
      <div className="ibx2">
        <div className="col">
          {filed.map((f) => (
            <div key={f.id + f.txnIds.join()} className="done-row">
              <span className="ok">{G.check}</span>
              <span className="grow">{f.label}</span>
              <button type="button" className="linkish" onClick={() => undo(f)}>
                Undo
              </button>
            </div>
          ))}
          {st.status === "loading" && <Loading />}
          {st.status === "error" && <ErrorState error={st.error} title="Couldn't load the Inbox" onRetry={() => invalidate(["/api/inbox"])} />}
          {st.status === "ready" && !items.length && (
            <section className="panel">
              <h2 style={{ margin: 0, fontSize: 16 }}>All filed</h2>
              <p className="muted" style={{ margin: 0 }}>
                New payees land here when no rule, payee memory or brand match can file them.
              </p>
            </section>
          )}
          {items.map((p) => (
            <Card
              key={p.key}
              p={p}
              cats={cats}
              cap={toPaise(me?.settings?.local_shop_cap ?? "500")}
              selected={selected.has(p.key)}
              onSelect={(on) =>
                setSelected((s) => {
                  const n = new Set(s);
                  if (on) n.add(p.key);
                  else n.delete(p.key);
                  return n;
                })
              }
              onFiled={onFiled}
            />
          ))}
          {picked.length > 0 && <Bulk picked={picked} cats={cats} onFiled={onFiled} clear={() => setSelected(new Set())} />}
        </div>
        <Stats month={month} />
      </div>
    </>
  );
}

function suggestions(p: InboxPayee, cats: Category[]): Category[] {
  const byName = new Map(cats.map((c) => [c.name, c]));
  const names = [...p.history.map((h) => h.category), ...(DEFAULTS[`${p.reason}:${p.direction}`] ?? DEFAULTS[p.direction] ?? [])];
  const out: Category[] = [];
  for (const n of names) {
    const c = byName.get(n);
    if (c && !out.includes(c)) out.push(c);
    if (out.length === 3) break;
  }
  return out;
}

function Context({ p, cap }: { p: InboxPayee; cap: number }) {
  const bits: string[] = [];
  if (p.history.length) bits.push(`Paid ${p.history.reduce((a, h) => a + h.count, 0)} times before: ${p.history.map((h) => `${h.category} ${h.count}`).join(" · ")}`);
  else bits.push(p.direction === "credit" ? "First payment from this payee" : "First payment to this payee");
  if (p.reason === "merchant_over_cap") bits.push(`Over the ${inr(cap)} Local shops cap`);
  if (p.payments.length > 1) bits.push(`${p.payments.length} payments waiting`);
  return (
    <div className="ctx">
      {bits.map((b, i) => (
        <span key={b} className={i ? "faint" : ""}>
          {b}
        </span>
      ))}
    </div>
  );
}

function Card({ p, cats, cap, selected, onSelect, onFiled }: { p: InboxPayee; cats: Category[]; cap: number; selected: boolean; onSelect: (on: boolean) => void; onFiled: (p: InboxPayee, c: Category, rule: string | null) => void }) {
  const [cat, setCat] = useState<Category | null>(null);
  const [scope, setScope] = useState<Scope>("payee");
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const sugg = suggestions(p, cats);
  const last = p.payments.at(-1);
  const tag = REASON[p.reason ?? "new_payee"] ?? REASON.new_payee!;
  const file = async () => {
    if (!cat) return;
    setBusy(true);
    try {
      const r = await fileInboxPayee(p, cat.id, scope);
      onFiled(p, cat, r.rule_id);
    } catch {
      toast("Couldn't file that. Try again.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <article className={`icard2${selected ? " sel" : ""}`}>
      <div className="ihead">
        <input type="checkbox" aria-label={`Select ${p.payee}`} checked={selected} onChange={(e) => onSelect(e.target.checked)} />
        <span className="mg" style={{ background: monogramColor(p.payee) }}>
          {(p.payee.replace(/[^A-Za-z0-9]/g, "")[0] ?? "•").toUpperCase()}
        </span>
        <div className="who2">
          <b>
            {p.payee}
            <span className={`kindtag ${tag[1]}`}>{tag[0]}</span>
          </b>
          <small>{p.vpa ?? (p.reason === "new_payee" ? "no UPI id" : "")}</small>
        </div>
        <span className="when">
          {last ? dayShort(last.date) : ""} · {p.account}
        </span>
        <span className={`amt${p.direction === "credit" ? " good-t" : ""}`}>
          {p.direction === "credit" ? "+" : "−"}
          {inr(p.total)}
        </span>
      </div>
      <Context p={p} cap={cap} />
      <div className="acts2">
        {sugg.map((c) => (
          <button key={c.id} type="button" className={`catchip${cat?.id === c.id ? " on" : ""}`} onClick={() => setCat(c)}>
            {c.name}
          </button>
        ))}
        <select className="catchip more" aria-label="More categories" value={cat && !sugg.includes(cat) ? cat.id : ""} onChange={(e) => setCat(cats.find((c) => c.id === Number(e.target.value)) ?? null)}>
          <option value="">More…</option>
          {cats.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        <span className="sp" />
        {cat?.name !== "Loans" && (
          <span className="seg">
            <button type="button" className={scope === "this" ? "on" : ""} onClick={() => setScope("this")}>
              Just this one
            </button>
            <button type="button" className={scope === "payee" ? "on" : ""} onClick={() => setScope("payee")}>
              Always for this payee
            </button>
          </span>
        )}
        {cat?.name !== "Loans" && (
          <button type="button" className="btn2 sm primary" disabled={!cat || busy} onClick={file}>
            {busy ? "Filing…" : "File"}
          </button>
        )}
      </div>
      {cat?.name === "Loans" && p.payments[0] && <LoanPicker txnId={p.payments[0].id} txnIds={p.payments.map((x) => Number(x.id))} onDone={() => onFiled(p, cat, null)} />}
    </article>
  );
}

function Bulk({ picked, cats, onFiled, clear }: { picked: InboxPayee[]; cats: Category[]; onFiled: (p: InboxPayee, c: Category, rule: string | null) => void; clear: () => void }) {
  const [catId, setCatId] = useState<number | "">("");
  const [scope, setScope] = useState<Scope>("this");
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const file = async () => {
    const cat = cats.find((c) => c.id === catId);
    if (!cat) return;
    setBusy(true);
    let done = 0;
    // One call per payee: /api/inbox/{payee}/file files one group at a time.
    for (const p of picked) {
      try {
        const r = await fileInboxPayee(p, cat.id, scope);
        onFiled(p, cat, r.rule_id);
        done++;
      } catch {
        /* reported below */
      }
    }
    setBusy(false);
    toast(done === picked.length ? `Filed ${plural(done, "payee")} as ${cat.name}` : `Filed ${done} of ${picked.length}; try the rest again`);
  };
  return (
    <div className="bulk" role="region" aria-label="Bulk file">
      <b>{picked.length} selected</b>
      <span className="muted">File as</span>
      <select className="inp2" value={catId} onChange={(e) => setCatId(e.target.value ? Number(e.target.value) : "")} aria-label="Category for selected">
        <option value="">Choose…</option>
        {cats.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
      </select>
      <span className="seg">
        <button type="button" className={scope === "this" ? "on" : ""} onClick={() => setScope("this")}>
          Just these
        </button>
        <button type="button" className={scope === "payee" ? "on" : ""} onClick={() => setScope("payee")}>
          Always for these payees
        </button>
      </span>
      <span className="sp" />
      <button type="button" className="btn2 sm" style={{ border: 0 }} onClick={clear}>
        Clear
      </button>
      <button type="button" className="btn2 sm primary" disabled={!catId || busy} onClick={file}>
        {busy ? "Filing…" : `File ${picked.length}`}
      </button>
    </div>
  );
}

function Stats({ month }: { month: string }) {
  const s = dataOf(read(api.inboxStats(month)));
  const rows: [string, number | undefined][] = [
    ["Your rules", s?.by.rules],
    ["Payee memory", s?.by.payee_memory],
    ["Brand dictionary", s?.by.dictionary],
    ["Salary, transfers, card bills, QR", s?.by.structural],
    ["Filed by you", s?.by.user],
  ];
  return (
    <aside className="aside panel" aria-label="Filing so far">
      <span className="lbl">{monthShort(month)} · filed so far</span>
      <div className="nwfig">
        <b>{s ? s.automatic : "…"}</b>
        <span className="muted">of {s?.total ?? "…"} automatic</span>
      </div>
      <div className="statlist">
        {rows.map(([k, v]) => (
          <div key={k} className="r">
            <span>{k}</span>
            <b>{v ?? "…"}</b>
          </div>
        ))}
        <div className="r">
          <span style={{ color: "var(--t1)" }}>Waiting for you</span>
          <b className="acc-t">{s?.by.waiting ?? "…"}</b>
        </div>
        <div className="r">
          <span>{s ? plural(s.rules, "rule") : ""}</span>
          <Link href="/settings/rules" className="linkx">
            Manage rules →
          </Link>
        </div>
      </div>
      <span className="foot">
        Filing "Always for this payee" creates a rule; two matching choices also teach payee memory.
      </span>
    </aside>
  );
}
