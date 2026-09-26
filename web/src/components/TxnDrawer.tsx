import { useEffect, useRef, useState } from "react";
import { api, categorize, dataOf, decideRecurring, patchTxn, read } from "../lib/api";
import { categoryColor, monogramColor } from "../lib/colors";
import { dayLong, dayShort, inr, plural, timeIST, toPaise } from "../lib/format";
import { navigate } from "../lib/router";
import type { Cadence, ClassifiedBy, Scope, Transaction } from "../lib/types";
import { G } from "./Glyphs";
import { useToast } from "./Toast";

const FILED_BY: Record<ClassifiedBy, string> = {
  dictionary: "Brand dictionary",
  payee_memory: "Payee memory",
  rule: "Rule",
  heuristic: "Payment-type rule",
  user: "You",
  system: "Import",
};
const KIND: Record<string, string> = { spend: "Spend", income: "Income", transfer: "Transfer", investment: "Investment", refund: "Refund", fee: "Fee", cash: "Cash" };

/** Opens for ?txn=<id>. Every field is read from the transaction and its sightings; nothing is inferred. */
export function TxnDrawer({ id, onClose, prev, next }: { id: string | null; onClose: () => void; prev?: () => void; next?: () => void }) {
  const open = id != null;
  const [shown, setShown] = useState(id);
  if (id && id !== shown) setShown(id);
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    panel.current?.querySelector<HTMLElement>("[data-close]")?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.target instanceof HTMLElement && /input|textarea|select/i.test(e.target.tagName)) return;
      if (e.key === "ArrowUp" && prev) prev();
      if (e.key === "ArrowDown" && next) next();
    };
    addEventListener("keydown", onKey);
    return () => {
      removeEventListener("keydown", onKey);
      opener?.focus();
    };
  }, [open, prev, next]);
  return (
    <>
      <div className={`scrim${open ? " open" : ""}`} onClick={onClose} aria-hidden />
      <aside ref={panel} className={`drawer2${open ? " open" : ""}`} role="dialog" aria-modal="true" aria-label="Transaction" aria-hidden={!open}>
        <div className="bar">
          <span className="lbl">Transaction</span>
          <button type="button" className="iconbtn" aria-label="Previous" disabled={!prev} onClick={prev}>
            {G.up}
          </button>
          <button type="button" className="iconbtn" aria-label="Next" disabled={!next} onClick={next}>
            {G.down}
          </button>
          <button type="button" className="iconbtn plain" aria-label="Close" data-close onClick={onClose}>
            {G.close}
          </button>
        </div>
        {shown && <Body key={shown} id={shown} />}
      </aside>
    </>
  );
}

function Body({ id }: { id: string }) {
  const st = read(api.txnDetail(id));
  const cats = read(api.categories());
  const toast = useToast();
  const [cat, setCat] = useState<number | null>(null);
  const [scope, setScope] = useState<Scope>("this");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [tagText, setTagText] = useState("");
  if (st.status === "loading") return <div className="body"><p className="state">Loading…</p></div>;
  if (st.status === "error") return <div className="body"><p className="state">This transaction couldn't load.</p></div>;
  const d = st.data;
  const t = d.txn;
  const raw = d.transaction;
  const categories = cats.status === "ready" ? cats.data : [];
  const chosen = cat ?? t.category_id;
  const changed = chosen != null && chosen !== t.category_id;
  const credit = t.direction === "credit";
  const alertSeen = d.observations.find((o) => o.source === "alert" && o.received_at);
  const steps: [string, string, boolean][] = [
    ["Pending", t.sources.includes("alert") ? "alert" : "no live alert", t.status !== "flagged"],
    ["Posted", raw.posted_at ? `${dayShort(raw.posted_at)} · value date` : "—", t.status === "posted" || t.status === "reconciled"],
    ["Reconciled", t.status === "reconciled" ? "statement balanced" : "waiting for a statement", t.status === "reconciled"],
  ];
  const save = async (categoryId: number, sc: Scope, msg: string) => {
    setBusy(true);
    try {
      const r = await categorize(id, categoryId, sc);
      toast(`${msg}${r.updated > 1 ? ` · ${plural(r.updated, "transaction")}` : ""}`);
      setCat(null);
    } catch {
      toast("Couldn't save that. Try again.");
    } finally {
      setBusy(false);
    }
  };
  const selfTransfer = categories.find((c) => c.name === "Self transfer");
  const saveNotes = async (patch: { notes?: string | null; tags?: string[] }) => {
    try {
      await patchTxn(id, patch);
    } catch {
      toast("Couldn't save the note. Try again.");
    }
  };
  return (
    <>
      <div className="body">
        <div className="hd">
          <span className="mg" style={{ background: monogramColor(t.merchant) }}>
            {(t.merchant.replace(/[^A-Za-z0-9]/g, "")[0] ?? "•").toUpperCase()}
          </span>
          <div style={{ minWidth: 0 }}>
            <h3>{t.merchant}</h3>
            {(t.counterparty || t.vpa) && <small>{[t.counterparty, t.vpa].filter(Boolean).join(" · ")}</small>}
          </div>
          <span className={`amt${credit ? " good-t" : ""}`}>
            {credit ? "+" : "−"}
            {inr(t.amount)}
          </span>
        </div>
        <div className="meta">
          {dayLong(t.date)}
          {alertSeen?.received_at ? ` · ${timeIST(alertSeen.received_at)}` : ""} · {t.account}
        </div>
        <div className="dl">
          <div className="r">
            <span>Category</span>
            <span className="v">
              <select value={chosen ?? ""} onChange={(e) => setCat(Number(e.target.value))} aria-label="Category">
                {chosen == null && <option value="">Uncategorized</option>}
                {categories.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
              {t.payee_key && (
                <span className="seg">
                  <button type="button" className={scope === "this" ? "on" : ""} onClick={() => setScope("this")}>
                    This one
                  </button>
                  <button type="button" className={scope === "payee" ? "on" : ""} onClick={() => setScope("payee")}>
                    All {t.merchant.length > 14 ? "from payee" : t.merchant}
                  </button>
                </span>
              )}
            </span>
          </div>
          <div className="r">
            <span>Kind</span>
            <span className="v">{KIND[t.kind] ?? t.kind}</span>
          </div>
          <div className="r">
            <span>Filed by</span>
            <span className="v">
              {t.classified_by ? FILED_BY[t.classified_by] : "Waiting in the Inbox"}
              {t.rule_id && <span className="mono-n faint" style={{ marginLeft: "auto", fontSize: 12 }}>{t.rule_id}</span>}
            </span>
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <span className="lbl">Status</span>
          <div className="stepper">
            {steps.map(([name, sub, done]) => (
              <div key={name} className={`s${done ? " done" : ""}`}>
                <i />
                <b>{name}</b>
                <small>{sub}</small>
              </div>
            ))}
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <span className="lbl">Seen in · {d.observations.length}</span>
          {d.observations.map((o) => (
            <div key={o.id} className="obs">
              {o.source === "alert" ? G.mail : G.doc}
              <span className="mid">
                <span>
                  {o.source === "alert" ? "Alert email" : "Statement"}
                  {o.received_at ? ` · ${dayShort(o.received_at.slice(0, 10))}` : ""}
                </span>
                <small className="mono-n">
                  {[o.parser && `${o.parser} v${o.parser_version}`, o.filename, o.balance_after && `balance after ${inr(toPaise(o.balance_after))}`].filter(Boolean).join(" · ")}
                </small>
              </span>
            </div>
          ))}
          {!d.observations.length && (
            <div className="obs expect">
              {G.doc}
              <span className="mid">
                <span>{t.sources.includes("import") ? "Sheet import" : "No sighting stored"}</span>
                <small>Appears here once its statement is uploaded.</small>
              </span>
            </div>
          )}
        </div>
        {t.settles && (
          <button type="button" className="obs linkrow" onClick={() => openTxn(String(t.settles!.txn_id))}>
            {G.card}
            <span className="mid">
              <span>{t.settles.card ? `Paid ${t.settles.card}` : `Paid from ${t.settles.from_account}`}</span>
              <small>
                Card bill, matched to the {t.settles.card ? "card's payment line" : "bank debit"} on {dayShort(t.settles.date)} · a transfer, not spend
              </small>
            </span>
            {G.right}
          </button>
        )}
        {!t.settles && t.bucket === "card" && (
          <div className="obs">
            {G.card}
            <span className="mid">
              <span>Stands in for card spend</span>
              <small>No card statement covers the bill this pays, so it counts as spend. It becomes a transfer once that statement is added.</small>
            </span>
          </div>
        )}
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <span className="lbl">Links · {d.links.length ? d.links.length : "none"}</span>
          {d.links.map((l) => (
            <button key={`${l.kind}${l.txn_id}`} type="button" className="obs linkrow" onClick={() => openTxn(String(l.txn_id))}>
              {G.link}
              <span className="mid">
                <span>{l.kind === "card_payment" ? "Card bill payment" : l.kind.replace("_", " ")}</span>
                <small>transaction {l.txn_id}</small>
              </span>
              {G.right}
            </button>
          ))}
        </div>
        {!credit && <RecurringBlock t={t} />}
        {d.payee && (
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <div className="panel-h">
              <span className="lbl">{t.merchant} history</span>
              <span className="x">
                {plural(d.payee.count, "charge")}
                {d.payee.history.length === 1 ? ` · all ${d.payee.history[0]!.category}` : ""}
              </span>
            </div>
            <div className="kvrows">
              {d.payee.recent.map((r) => (
                <div key={r.id} className="r">
                  <span style={{ flex: "none", width: 64, color: "var(--t3)" }}>{dayShort(r.occurred_at)}</span>
                  <span style={{ flex: 1 }}>{String(r.id) === id ? "This one" : (r.category ?? "Uncategorized")}</span>
                  <b>{inr(toPaise(r.amount))}</b>
                </div>
              ))}
              <div className="r">
                <span>Total</span>
                <b>{inr(toPaise(d.payee.total))}</b>
              </div>
            </div>
          </div>
        )}
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <span className="lbl">Notes & tags</span>
          <textarea
            placeholder="Add a note"
            maxLength={2000}
            value={note ?? t.notes ?? ""}
            onChange={(e) => setNote(e.target.value)}
            onBlur={() => note != null && note !== (t.notes ?? "") && saveNotes({ notes: note || null })}
          />
          <div className="tags">
            {t.tags.map((g) => (
              <span key={g}>
                {g}
                <button type="button" aria-label={`Remove ${g}`} onClick={() => saveNotes({ tags: t.tags.filter((x) => x !== g) })}>
                  ×
                </button>
              </span>
            ))}
            <input
              placeholder="+ Tag"
              value={tagText}
              maxLength={40}
              onChange={(e) => setTagText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && tagText.trim()) {
                  saveNotes({ tags: [...t.tags, tagText.trim()] });
                  setTagText("");
                }
              }}
            />
          </div>
        </div>
      </div>
      <div className="ft">
        {selfTransfer && t.category_id !== selfTransfer.id && t.bucket !== "excluded" && (
          <button type="button" className="btn2 sm" disabled={busy} onClick={() => save(selfTransfer.id, "this", "Marked as transfer")}>
            {G.transfer}
            Mark as transfer
          </button>
        )}
        <span className="sp" />
        <span className="sq" style={{ background: categoryColor(categories.find((c) => c.id === chosen)?.name ?? null), visibility: changed ? "visible" : "hidden" }} />
        <button type="button" className="btn2 sm primary" disabled={!changed || busy} onClick={() => chosen != null && save(chosen, scope, "Category saved")}>
          {busy ? "Saving…" : "Save category"}
        </button>
      </div>
    </>
  );
}

/** Re-opens the drawer on another transaction, keeping the page's filters. */
function openTxn(txnId: string) {
  const p = new URLSearchParams(location.search);
  p.set("txn", txnId);
  navigate(`/transactions?${p}`, { replace: location.pathname === "/transactions" });
}

const CADENCE: [Cadence, string][] = [
  ["monthly", "Monthly"],
  ["yearly", "Yearly"],
  ["quarterly", "Quarterly"],
  ["weekly", "Weekly"],
];

/** The payee's series, or a way to mark it recurring by hand (detection needs 3 charges; 2 for yearly). */
function RecurringBlock({ t }: { t: Transaction }) {
  const list = dataOf(read(api.subscriptions()));
  const toast = useToast();
  const [cadence, setCadence] = useState<Cadence>("monthly");
  const [busy, setBusy] = useState(false);
  const key = t.payee_key ?? t.merchant.toLowerCase();
  if (!list || t.bucket === "excluded" || t.bucket === "card") return null;
  const x = list.items.find((r) => r.id === key);
  const hidden = list.dismissed.some((r) => r.id === key);
  const mark = async () => {
    setBusy(true);
    try {
      await decideRecurring(key, { decision: "confirmed", cadence });
      toast(`${t.merchant} marked recurring`);
    } catch {
      toast("Couldn't save that. Try again.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <span className="lbl">Recurring</span>
      {x ? (
        <button type="button" className="obs linkrow" onClick={() => navigate("/subscriptions")}>
          {G.subscriptions}
          <span className="mid">
            <span>
              {CADENCE.find(([c]) => c === x.cadence)?.[1]} · {inr(x.amountExpected)}
              {x.active ? ` · next ${dayShort(x.next_due)}` : x.state === "ended" ? " · cancelled" : " · stopped"}
            </span>
            <small>
              {plural(x.count, "charge")} since {dayShort(x.first_at)}
              {x.manual ? " · added by you" : x.confirmed ? " · confirmed" : " · detected"}
            </small>
          </span>
          {G.right}
        </button>
      ) : (
        <div className="markrec">
          <span className="muted" style={{ fontSize: 13 }}>
            {hidden ? "You marked this payee as not recurring." : "Not detected as recurring."}
          </span>
          <select className="inp2" value={cadence} onChange={(e) => setCadence(e.target.value as Cadence)} aria-label="Cadence">
            {CADENCE.map(([c, l]) => (
              <option key={c} value={c}>
                {l}
              </option>
            ))}
          </select>
          <button type="button" className="btn2 sm" disabled={busy} onClick={mark}>
            {busy ? "Saving…" : "Mark as recurring"}
          </button>
        </div>
      )}
    </div>
  );
}
