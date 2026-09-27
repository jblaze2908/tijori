import { useState } from "react";
import { api, dataOf, invalidate, read, request, resource } from "../lib/api";
import { monogramColor } from "../lib/colors";
import { dayShort, inr, plural, toPaise } from "../lib/format";
import type { Transaction } from "../lib/types";
import { useToast } from "./Toast";

type Dir = "lent" | "borrowed";
interface Loan {
  id: number;
  direction: Dir;
  counterparty: string;
  started_on: string;
  opening_amount: string;
  note: string | null;
  status: "open" | "settled" | "written_off";
  closed_on: string | null;
  given: string;
  returned: string;
  outstanding: string;
  repayments: number;
  last_at: string | null;
  same_payee?: boolean;
}
interface LoanTxn {
  id: number;
  occurred_at: string;
  amount: string;
  direction: "debit" | "credit";
  merchant: string | null;
  account: { label: string } | null;
  balance_after?: string;
}
interface LoansData {
  items: Loan[];
  unassigned: LoanTxn[];
  totals: { owed_to_you: string; you_owe: string; open_lent: number; open_borrowed: number; repaid_fy: string; repaid_fy_count: number; fy_start: string };
}
interface Detail {
  loan: Loan;
  txns: LoanTxn[];
  suggestions: LoanTxn[];
}
interface Picker {
  loan_id: number | null;
  new_direction: Dir;
  counterparty: string | null;
  loans: Loan[];
  other_txns: LoanTxn[];
}

const loansOf = () => resource("/api/loans", (r: LoansData) => r);
const loanOf = (id: number) => resource(`/api/loans/${id}`, (r: Detail) => r);
const pickerOf = (txnId: string) => resource(`/api/loans/for-txn/${encodeURIComponent(txnId)}`, (r: Picker) => r);
// A loan change moves txns in and out of totals, the Inbox and net worth.
const AFTER = ["/api/loans", "/api/transactions", "/api/summary", "/api/inbox", "/api/networth", "/api/alerts", "/api/trends", "/api/budgets"];

async function send(path: string, body: unknown = {}, method: "POST" | "PATCH" = "POST") {
  const r = await request(path, method, body);
  invalidate(AFTER);
  return r;
}

const initial = (s: string) => (s.replace(/[^A-Za-z0-9]/g, "")[0] ?? "•").toUpperCase();
const signed = (t: LoanTxn) => `${t.direction === "credit" ? "+" : "−"}${inr(toPaise(t.amount))}`;
const TAG: Record<string, [string, string]> = { lent: ["Lent", "income"], borrowed: ["Borrowed", "mixed"], settled: ["Settled", ""], written_off: ["Written off", ""] };

/** What a txn does to a loan, from both directions. */
function moveLabel(loan: Dir, txn: "debit" | "credit"): string {
  if (loan === "lent") return txn === "debit" ? "Lent" : "Repaid";
  return txn === "credit" ? "Borrowed" : "Paid back";
}

/** Net worth's Loans section: what's owed each way, every loan, and the one selected. */
export function LoansPanel() {
  const d = dataOf(read(loansOf()));
  const [sel, setSel] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  if (!d) return null;
  const t = d.totals;
  const current = sel ?? d.items[0]?.id ?? null;
  return (
    <section className="panel loans" aria-label="Loans">
      <div className="panel-h">
        <h2>Loans</h2>
        <span className="foot">open by amount · settled below</span>
        <span className="sp" style={{ flex: 1 }} />
        <button type="button" className="btn2 sm" onClick={() => setAdding(!adding)}>
          {adding ? "Close" : "New loan"}
        </button>
      </div>
      {d.items.length > 0 && (
        <div className="lsum">
          <div>
            <span>Owed to you</span>
            <b className="mono-n good-t">{inr(toPaise(t.owed_to_you))}</b>
            <small>{plural(t.open_lent, "open loan")} · in net worth as an asset</small>
          </div>
          <div>
            <span>You owe</span>
            <b className="mono-n bad-t">{inr(toPaise(t.you_owe))}</b>
            <small>{plural(t.open_borrowed, "open loan")} · subtracted from net worth</small>
          </div>
          <div>
            <span>Repaid to you this FY</span>
            <b className="mono-n">{inr(toPaise(t.repaid_fy))}</b>
            <small>since {dayShort(t.fy_start)} · {plural(t.repaid_fy_count, "repayment")}</small>
          </div>
        </div>
      )}
      {adding && <NewLoan done={() => setAdding(false)} />}
      {d.items.length === 0 && !adding && <span className="foot">No loans. File a payment under Loans, or add one that started before your statements.</span>}
      {d.items.length > 0 && (
        <div className="lgrid">
          <div className="llist">
            {d.items.map((l) => (
              <LoanRow key={l.id} l={l} on={l.id === current} pick={() => setSel(l.id)} />
            ))}
          </div>
          {current != null && <LoanDetail key={current} id={current} />}
        </div>
      )}
      {d.unassigned.length > 0 && <span className="foot">{plural(d.unassigned.length, "payment")} filed under Loans without a loan. Open one from Transactions to pick its loan.</span>}
    </section>
  );
}

function LoanRow({ l, on, pick }: { l: Loan; on: boolean; pick: () => void }) {
  const given = toPaise(l.given), back = toPaise(l.returned), open = l.status === "open";
  const pct = given ? Math.min(100, (back / given) * 100) : 0;
  const tag = TAG[open ? l.direction : l.status]!;
  const sub = open
    ? [`Since ${dayShort(l.started_on)}`, l.repayments ? plural(l.repayments, "repayment") : l.direction === "lent" ? "nothing back yet" : "nothing paid back yet", l.last_at && l.repayments ? `last ${dayShort(l.last_at)}` : null]
    : [`${l.direction === "lent" ? "Lent" : "Borrowed"} ${dayShort(l.started_on)}`, l.closed_on ? `${l.status === "settled" ? "settled" : "written off"} ${dayShort(l.closed_on)}` : null];
  return (
    <button type="button" className={`lrow${on ? " on" : ""}${open ? "" : " closed"}`} onClick={pick}>
      <span className="mg" style={{ background: open ? monogramColor(l.counterparty) : "var(--s2)" }}>
        {initial(l.counterparty)}
      </span>
      <span className="lwho">
        <b>
          {l.counterparty}
          <span className={`kindtag ${tag[1]}`}>{tag[0]}</span>
        </b>
        <small>{sub.filter(Boolean).join(" · ")}</small>
      </span>
      <span className="lbar">
        <span className="track">
          <i style={{ width: `${pct}%`, background: open ? "var(--in)" : "var(--t3)" }} />
        </span>
        <small>
          {inr(back)} of {inr(given)} {l.direction === "lent" ? "back" : "paid"}
        </small>
      </span>
      <span className={`mono-n amt ${open ? (l.direction === "lent" ? "good-t" : "bad-t") : "faint"}`}>{inr(toPaise(l.outstanding))}</span>
    </button>
  );
}

function LoanDetail({ id }: { id: number }) {
  const st = read(loanOf(id));
  const cats = (dataOf(read(api.categories())) ?? []).filter((c) => c.bucket === "everyday" || c.bucket === "oneoff");
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [writeOff, setWriteOff] = useState<number | "">("");
  if (st.status !== "ready") return <div className="ldet">{st.status === "loading" ? "Loading…" : "This loan couldn't load."}</div>;
  const { loan: l, txns, suggestions } = st.data;
  const open = l.status === "open";
  const act = async (path: string, body: unknown, done: string) => {
    setBusy(true);
    try {
      await send(`/api/loans/${l.id}/${path}`, body);
      toast(done);
    } catch {
      toast("Couldn't save that. Try again.");
    } finally {
      setBusy(false);
    }
  };
  const woCat = writeOff === "" ? cats.find((c) => c.name === "Friends")?.id : writeOff;
  return (
    <div className="ldet">
      <div className="lhd">
        <span className="mg" style={{ background: monogramColor(l.counterparty), width: 36, height: 36, borderRadius: 18, fontSize: 14 }}>
          {initial(l.counterparty)}
        </span>
        <span className="lwho">
          <b>
            {l.counterparty} · {l.direction}
          </b>
          <small>{[`Started ${dayShort(l.started_on)}`, toPaise(l.opening_amount) ? `${inr(toPaise(l.opening_amount))} before your statements` : null, l.note].filter(Boolean).join(" · ")}</small>
        </span>
        <span className="big">
          <b className={`mono-n ${open ? (l.direction === "lent" ? "good-t" : "bad-t") : "faint"}`}>{inr(toPaise(l.outstanding))}</b>
          <small>{open ? (l.direction === "lent" ? "still owed to you" : "still owed by you") : l.status === "settled" ? "settled" : "written off"}</small>
        </span>
      </div>
      <div className="ltl">
        {txns.map((t) => (
          <div key={t.id} className="ltr">
            <span className="d">{dayShort(t.occurred_at)}</span>
            <span className="w">
              {moveLabel(l.direction, t.direction)}
              {t.account ? ` · ${t.account.label}` : ""}
            </span>
            <span className={`mono-n a${t.direction === "credit" ? " good-t" : ""}`}>{signed(t)}</span>
            <span className="mono-n b faint">{t.balance_after ? inr(toPaise(t.balance_after)) : ""}</span>
            <button type="button" className="x" title="Remove from this loan (back to the Inbox)" disabled={busy} onClick={() => act("txns/remove", { txn_ids: [t.id] }, "Removed from the loan")}>
              ×
            </button>
          </div>
        ))}
        {txns.length === 0 && <span className="foot">No transactions on this loan yet.</span>}
      </div>
      {suggestions.length > 0 && (
        <div className="lsug">
          <span className="w">
            <b>{plural(suggestions.length, "more payment")} from this handle</b>
            <small>{suggestions.slice(0, 3).map((t) => `${signed(t)} on ${dayShort(t.occurred_at)}`).join(" · ")}</small>
          </span>
          <button type="button" className="btn2 sm" disabled={busy} onClick={() => act("txns", { txn_ids: suggestions.map((t) => t.id) }, "Added to the loan")}>
            Add to this loan
          </button>
        </div>
      )}
      <div className="lact">
        {open ? (
          <>
            <button type="button" className="btn2 sm" disabled={busy} onClick={() => act("settle", {}, "Loan settled")}>
              Mark settled
            </button>
            <span className="sp" style={{ flex: 1 }} />
            {toPaise(l.outstanding) > 0 && (
              <>
                <select className="inp2" value={woCat ?? ""} onChange={(e) => setWriteOff(e.target.value ? Number(e.target.value) : "")} aria-label="Write off to category">
                  {cats.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
                <button type="button" className="btn2 sm bad-t" disabled={busy || woCat == null} onClick={() => act("write-off", { category_id: woCat }, "Written off")}>
                  Write off {inr(toPaise(l.outstanding))}
                </button>
              </>
            )}
          </>
        ) : (
          <button type="button" className="btn2 sm" disabled={busy} onClick={() => act("reopen", {}, "Loan reopened")}>
            Reopen
          </button>
        )}
      </div>
    </div>
  );
}

/** A loan that started before the statements on file: who, which way, how much, since when. */
function NewLoan({ done }: { done: () => void }) {
  const toast = useToast();
  const [dir, setDir] = useState<Dir>("lent");
  const [who, setWho] = useState("");
  const [amount, setAmount] = useState("");
  const [since, setSince] = useState("");
  const ok = who.trim() && /^\d{1,12}(\.\d{1,2})?$/.test(amount) && since;
  const save = async () => {
    try {
      await send("/api/loans", { direction: dir, counterparty: who.trim(), opening_amount: amount, started_on: since });
      toast("Loan added");
      done();
    } catch {
      toast("Couldn't add the loan.");
    }
  };
  return (
    <div className="lnew">
      <span className="seg">
        <button type="button" className={dir === "lent" ? "on" : ""} onClick={() => setDir("lent")}>
          I lent
        </button>
        <button type="button" className={dir === "borrowed" ? "on" : ""} onClick={() => setDir("borrowed")}>
          I borrowed
        </button>
      </span>
      <input className="inp2" placeholder="Person" value={who} maxLength={120} onChange={(e) => setWho(e.target.value)} aria-label="Person" />
      <input className="inp2 mono-n" placeholder="Amount owed" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ""))} aria-label="Amount owed" />
      <input className="inp2" type="date" value={since} onChange={(e) => setSince(e.target.value)} aria-label="Since" />
      <button type="button" className="btn2 sm primary" disabled={!ok} onClick={save}>
        Add loan
      </button>
    </div>
  );
}

/**
 * Filing a payment under Loans: pick the loan (same handle first) or start one. `txnIds` files a whole Inbox
 * group; without it, the drawer offers the handle's other unfiled payments too.
 */
export function LoanPicker({ txnId, txnIds, onDone }: { txnId: string; txnIds?: number[]; onDone: () => void }) {
  const st = read(pickerOf(txnId));
  const toast = useToast();
  const [choice, setChoice] = useState<number | "new" | null>(null);
  const [name, setName] = useState<string | null>(null);
  const [withOthers, setWithOthers] = useState(true);
  const [busy, setBusy] = useState(false);
  if (st.status !== "ready") return <div className="lpick">{st.status === "loading" ? "Loading loans…" : "Loans couldn't load."}</div>;
  const p = st.data;
  const txnDir = p.new_direction === "lent" ? "debit" : "credit";
  const picked = choice ?? p.loan_id ?? p.loans.find((l) => l.same_payee)?.id ?? "new";
  const who = name ?? p.counterparty ?? "";
  const others = txnIds ? [] : p.other_txns;
  const ids = txnIds ?? [Number(txnId), ...(withOthers ? others.map((t) => t.id) : [])];
  const save = async () => {
    setBusy(true);
    try {
      if (picked === "new") await send("/api/loans", { counterparty: who.trim(), txn_ids: ids });
      else await send(`/api/loans/${picked}/txns`, { txn_ids: ids });
      toast(`Filed under Loans${ids.length > 1 ? ` · ${plural(ids.length, "transaction")}` : ""}`);
      onDone();
    } catch {
      toast("Couldn't file that. Try again.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="lpick">
      <span className="lbl">Which loan?</span>
      {p.loans.map((l) => (
        <label key={l.id} className={`lopt${picked === l.id ? " on" : ""}`}>
          <input type="radio" name={`loan-${txnId}`} checked={picked === l.id} onChange={() => setChoice(l.id)} />
          <span className="w">
            <b>
              {moveLabel(l.direction, txnDir)} · {l.counterparty}
            </b>
            <small>
              {l.direction} · {inr(toPaise(l.outstanding))} open{l.same_payee ? " · same UPI handle" : ""}
            </small>
          </span>
        </label>
      ))}
      <label className={`lopt new${picked === "new" ? " on" : ""}`}>
        <input type="radio" name={`loan-${txnId}`} checked={picked === "new"} onChange={() => setChoice("new")} />
        <span className="w">
          <b>New loan · {p.new_direction === "lent" ? "lent to" : "borrowed from"}</b>
          {picked === "new" && <input className="inp2" value={who} maxLength={120} onChange={(e) => setName(e.target.value)} aria-label="Person" placeholder="Person" />}
        </span>
      </label>
      <div className="lfoot">
        {others.length > 0 ? (
          <label className="chk">
            <input type="checkbox" checked={withOthers} onChange={(e) => setWithOthers(e.target.checked)} />
            Also add {plural(others.length, "other payment")} from this handle
          </label>
        ) : (
          <span />
        )}
        <button type="button" className="btn2 sm primary" disabled={busy || (picked === "new" && !who.trim())} onClick={save}>
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}

/** The drawer's line for a txn already on a loan. */
export function LoanLine({ txnId, loanId }: { txnId: string; loanId: number }) {
  const d = dataOf(read(loansOf()));
  const toast = useToast();
  const l = d?.items.find((x) => x.id === loanId);
  const remove = async () => {
    try {
      await send(`/api/loans/${loanId}/txns/remove`, { txn_ids: [Number(txnId)] });
      toast("Removed from the loan; it's back in the Inbox");
    } catch {
      toast("Couldn't remove it.");
    }
  };
  return (
    <span className="v">
      {l ? `${l.counterparty} · ${l.direction} · ${inr(toPaise(l.outstanding))} open` : "On a loan"}
      <button type="button" className="linkish" onClick={remove}>
        Remove
      </button>
    </span>
  );
}

/** Spending's lines outside the total: loan money in the range, by which way it moved. */
export function LoanLines({ txns }: { txns: Transaction[] }) {
  const d = dataOf(read(loansOf()));
  if (!d) return null;
  const byId = new Map(d.items.map((l) => [l.id, l]));
  const lines = new Map<string, { amount: number; people: Set<string>; n: number }>();
  for (const t of txns) {
    const l = t.loan_id != null ? byId.get(t.loan_id) : undefined;
    if (!l) continue;
    const k = `${moveLabel(l.direction, t.direction)}`;
    const x = lines.get(k) ?? { amount: 0, people: new Set<string>(), n: 0 };
    x.amount += t.amount;
    x.n += 1;
    x.people.add(l.counterparty);
    lines.set(k, x);
  }
  if (!lines.size) return null;
  const order = ["Lent", "Repaid", "Borrowed", "Paid back"];
  const LABEL: Record<string, string> = { Lent: "Loans · lent", Repaid: "Loans · repaid to you", Borrowed: "Loans · borrowed", "Paid back": "Loans · you paid back" };
  return (
    <section className="panel lnis" aria-label="Loans, not in spend">
      <span className="lbl">Not in spend</span>
      {order
        .filter((k) => lines.has(k))
        .map((k) => {
          const x = lines.get(k)!;
          const inflow = k === "Repaid" || k === "Borrowed";
          return (
            <div key={k} className="lnr">
              <span className="n">{LABEL[k]}</span>
              <span className="p faint">
                {plural(x.n, "payment")} · {[...x.people].slice(0, 3).join(", ")}
              </span>
              <span className={`mono-n a${k === "Repaid" ? " good-t" : ""}`}>
                {inflow ? "+" : ""}
                {inr(x.amount)}
              </span>
            </div>
          );
        })}
    </section>
  );
}
