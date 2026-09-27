import { useState } from "react";
import { api, dataOf, invalidate, read, request, resource } from "../lib/api";
import { categoryColor } from "../lib/colors";
import { inr, toPaise } from "../lib/format";
import { useToast } from "./Toast";

interface Line {
  category_id: number;
  category: string;
  amount: string;
  carry: string;
  limit: string;
  spent: string;
  remaining: string;
  expected_by_today: string;
  projected: string;
  state: "ok" | "ahead" | "over";
  rollover: boolean;
}
interface Data {
  month: string;
  day: number;
  days: number;
  items: Line[];
  totals: { limit: string; spent: string };
}
const budgetsOf = (month: string) => resource(`/api/budgets?month=${month}`, (r: Data) => r);

/** Monthly limits per category: spent against the limit, where straight-line pace says you'd be by today. */
export function BudgetsPanel({ month }: { month: string }) {
  const b = dataOf(read(budgetsOf(month)));
  const cats = (dataOf(read(api.categories())) ?? []).filter((c) => c.bucket === "everyday" || c.bucket === "oneoff");
  const toast = useToast();
  const [cat, setCat] = useState<number | "">("");
  const [amount, setAmount] = useState("");
  const [rollover, setRollover] = useState(false);
  const put = async (id: number, body: { amount: string | null; rollover: boolean }, done: string) => {
    try {
      await request(`/api/budgets/${id}`, "PUT", body);
      invalidate(["/api/budgets", "/api/alerts"]);
      toast(done);
    } catch {
      toast("Couldn't save the budget.");
    }
  };
  const used = new Set((b?.items ?? []).map((i) => i.category_id));
  return (
    <section className="panel" aria-label="Budgets">
      <div className="panel-h">
        <h2>Budgets</h2>
        <span className="foot">{b ? `Day ${b.day} of ${b.days} · ${inr(toPaise(b.totals.spent))} of ${inr(toPaise(b.totals.limit))}` : ""}</span>
      </div>
      <div className="budgets">
        {(b?.items ?? []).map((i) => {
          const limit = toPaise(i.limit), spent = toPaise(i.spent), expected = toPaise(i.expected_by_today);
          const pct = limit ? Math.min(100, (spent / limit) * 100) : 0;
          return (
            <div key={i.category_id} className={`brow ${i.state}`}>
              <span className="nm">
                <i className="sq" style={{ background: categoryColor(i.category) }} />
                {i.category}
              </span>
              <span className="bar">
                <i style={{ width: `${pct}%` }} />
                {limit > 0 && <b className="tick" style={{ left: `${Math.min(100, (expected / limit) * 100)}%` }} title={`Pace: ${inr(expected)} by today`} />}
              </span>
              <span className="mono-n">
                {inr(spent)} <span className="faint">of {inr(limit)}</span>
              </span>
              <span className={`mono-n st ${i.state === "over" ? "bad-t" : i.state === "ahead" ? "warn-t" : "faint"}`}>
                {i.state === "over" ? `over by ${inr(spent - limit)}` : i.state === "ahead" ? "ahead of pace" : `${inr(limit - spent)} left`}
              </span>
              <span className="faint mono-n pr">→ {inr(toPaise(i.projected))}</span>
              <button type="button" className="linkish" onClick={() => put(i.category_id, { amount: null, rollover: false }, `${i.category} budget removed`)}>
                Remove
              </button>
            </div>
          );
        })}
        {b && !b.items.length && <p className="state" style={{ padding: 0 }}>No budgets yet.</p>}
      </div>
      <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <select className="inp2" value={cat} onChange={(e) => setCat(e.target.value ? Number(e.target.value) : "")} aria-label="Category">
          <option value="">Add a budget…</option>
          {cats.filter((c) => !used.has(c.id)).map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        <input className="inp2" inputMode="decimal" placeholder="₹ a month" value={amount} style={{ width: 120 }} aria-label="Monthly limit" onChange={(e) => /^\d{0,9}(\.\d{0,2})?$/.test(e.target.value) && setAmount(e.target.value)} />
        <label className="row" style={{ gap: 6, fontSize: 13 }}>
          <input type="checkbox" checked={rollover} onChange={(e) => setRollover(e.target.checked)} />
          Carry unspent over
        </label>
        <button type="button" className="btn2 sm primary" disabled={cat === "" || !Number(amount)} onClick={() => cat !== "" && put(cat, { amount, rollover }, "Budget saved").then(() => (setCat(""), setAmount("")))}>
          Save
        </button>
      </div>
      <span className="foot">Spent uses the one spend definition. The tick is where a straight line from 0 to the limit is today; → is where this pace ends the month.</span>
    </section>
  );
}
