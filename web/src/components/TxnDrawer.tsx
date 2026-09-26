import { useEffect, useRef, useState } from "react";
import { api, categorize, read, SOURCE_LABEL, type ApiError } from "../lib/api";
import { dayFull, inr, inr2, plural } from "../lib/format";
import { useStore } from "../lib/useStore";
import type { Category, ClassifiedBy, Transaction, TxnStatus } from "../lib/types";
import { useToast } from "./Toast";
import { Monogram, Switch } from "./ui";

const FILED_BY: Record<ClassifiedBy, string> = {
  dictionary: "Brand dictionary",
  payee_memory: "Payee memory (your past choices)",
  rule: "Rule",
  heuristic: "Payment-type heuristic",
  user: "You",
  system: "System",
};
const STATUS: Record<TxnStatus, [string, string]> = {
  pending: ["Alert", "Confirmed when the statement arrives"],
  posted: ["Posted", "Waiting for reconciliation"],
  reconciled: ["Statement", "Reconciled against the statement"],
  flagged: ["Flagged", "Needs a look before it counts"],
};

export function TxnDrawer({ txn, categories, onClose }: { txn: Transaction | null; categories: Category[]; onClose: () => void }) {
  // Keeps the last transaction rendered while the drawer slides shut.
  const [shown, setShown] = useState(txn);
  if (txn && txn !== shown) setShown(txn);
  const open = txn != null;
  const panel = useRef<HTMLDivElement>(null);

  // Focus moves into the drawer on open and back to the row that opened it on close.
  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    panel.current?.querySelector<HTMLElement>(".x")?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    addEventListener("keydown", onKey);
    return () => {
      removeEventListener("keydown", onKey);
      opener?.focus();
    };
  }, [open, onClose]);

  return (
    <div
      ref={panel}
      className={`drawer${open ? " open" : ""}`}
      role="dialog"
      aria-modal="true"
      aria-labelledby="drawer-title"
      aria-hidden={!open}
      inert={!open}
    >
      {shown && <DrawerBody key={shown.id} t={shown} categories={categories} onClose={onClose} />}
    </div>
  );
}

function DrawerBody({ t, categories, onClose }: { t: Transaction; categories: Category[]; onClose: () => void }) {
  useStore();
  const detail = read(api.transaction(t.id));
  const toast = useToast();
  const [categoryId, setCategoryId] = useState(t.category_id != null ? String(t.category_id) : "");
  const [everyPayment, setEveryPayment] = useState(true);
  const [saving, setSaving] = useState(false);
  const credit = t.direction === "credit";
  const options = categories.length || t.category_id == null ? categories : null;
  const chosen = categories.find((c) => String(c.id) === categoryId);
  const info = detail.status === "ready" ? detail.data : null;
  const [seenTitle, seenDetail] = STATUS[t.status];

  const save = async () => {
    if (!chosen) return;
    setSaving(true);
    try {
      await categorize(t.id, chosen.id, everyPayment ? "payee" : "this");
      toast(everyPayment ? `Saved. Future payments to ${t.merchant} file as ${chosen.name}.` : "Saved.");
      onClose();
    } catch (e) {
      toast(`Couldn't save. ${(e as ApiError).message}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <button type="button" className="x" aria-label="Close" onClick={onClose}>
        ✕
      </button>
      <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 4 }}>
        <Monogram name={t.merchant} />
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 600, fontSize: 16, overflowWrap: "anywhere" }} id="drawer-title">
            {t.merchant}
          </div>
          <div className="sub">{t.account}</div>
        </div>
      </div>
      <div className={`amtbig num${credit ? " in" : ""}`}>
        {credit ? "+" : "−"}
        {inr2(t.amount)}
      </div>
      <div className="sub">{dayFull(t.date)}</div>
      <div className="kv">
        <span>Category</span>
        <span>
          {options ? (
            <select className="sel" style={{ padding: "5px 8px" }} value={categoryId} aria-label="Category" onChange={(e) => setCategoryId(e.target.value)}>
              {t.category_id == null && <option value="">Choose a category</option>}
              {options.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          ) : (
            (t.category ?? "Uncategorized")
          )}
        </span>
        <span>Filed by</span>
        <span title={t.rule_id ?? undefined}>{t.classified_by ? FILED_BY[t.classified_by] : "Not filed yet"}</span>
        <span>Kind</span>
        <span style={{ textTransform: "capitalize" }}>{t.kind}</span>
        <span>This payee</span>
        <span>
          {info?.payee ? `${plural(info.payee.count, "payment")} · ${inr(info.payee.total)} total` : detail.status === "loading" ? "…" : "—"}
        </span>
      </div>
      <Switch on={everyPayment} onChange={setEveryPayment} label={<>Apply to every payment to {t.merchant}</>} />
      <div className="seen">Seen in</div>
      <div className="tl">
        {info?.observations.length ? (
          info.observations.map((o, i) => (
            <div key={i}>
              {o.label}
              <small>{o.detail}</small>
            </div>
          ))
        ) : t.sources.length ? (
          t.sources.map((s) => (
            <div key={s}>
              {SOURCE_LABEL[s] ?? s}
              <small>{seenDetail}</small>
            </div>
          ))
        ) : (
          <div>
            {seenTitle}
            <small>{seenDetail}</small>
          </div>
        )}
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 24 }}>
        <button type="button" className="btn" disabled={!chosen || saving} onClick={save}>
          {saving ? "Saving…" : "Save"}
        </button>
        <button type="button" className="btn ghost" onClick={onClose}>
          Cancel
        </button>
      </div>
    </>
  );
}
