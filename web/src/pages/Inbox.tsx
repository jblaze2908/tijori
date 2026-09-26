import { useMemo, useState } from "react";
import { useToast } from "../components/Toast";
import { Empty, ErrorState, Loading, Monogram, Switch } from "../components/ui";
import { all, api, fileInboxPayee, invalidate, read, type ApiError } from "../lib/api";
import { dayName, inr, plural } from "../lib/format";
import { useStore } from "../lib/useStore";
import type { Category, Direction, Inbox as InboxData, InboxPayee, TxnKind } from "../lib/types";

/** Matches the .icard fade in styles.css; the request goes out once the card has faded. */
const FADE_MS = 280;

// Quick-pick chips: categories that make sense for money going out vs coming in.
const CHIP_KINDS: Record<Direction, ReadonlySet<TxnKind>> = {
  debit: new Set(["spend", "fee", "cash"]),
  credit: new Set(["income", "refund"]),
};

// docs/api.md review_reason values; anything newer falls back to a readable form of the code.
const REASON: Record<string, string> = { person: "Person", merchant_over_cap: "Above local-shop cap", conflict: "Conflicting history", new_payee: "New payee" };
const reasonLabel = (r: string) => REASON[r] ?? r.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

export function Inbox() {
  useStore();
  const st = all<[InboxData, Category[]]>(read(api.inbox()), read(api.categories()));
  const toast = useToast();
  const [leaving, setLeaving] = useState<ReadonlySet<string>>(new Set());
  const cats = st.status === "ready" ? st.data[1] : null;
  const chips = useMemo(
    () => ({ debit: (cats ?? []).filter((c) => CHIP_KINDS.debit.has(c.kind)), credit: (cats ?? []).filter((c) => CHIP_KINDS.credit.has(c.kind)) }),
    [cats],
  );

  if (st.status === "loading") return <Loading />;
  if (st.status === "error") return <ErrorState error={st.error} title="Couldn't load the Inbox" onRetry={() => invalidate(["/api/inbox", "/api/categories"])} />;
  const [{ items, total, fetched }] = st.data;
  const remaining = items.filter((p) => !leaving.has(p.key)).length;

  const file = (p: InboxPayee, c: Category, remember: boolean) => {
    setLeaving((s) => new Set(s).add(p.key));
    // Waiting for the fade means a fast refetch can't yank the card mid-animation.
    setTimeout(async () => {
      try {
        const r = await fileInboxPayee(p, c.id, remember ? "payee" : "this");
        toast(remember ? `${p.payee} → ${c.name}. Future payments file automatically.` : `Filed ${plural(r.filed, "payment")} from ${p.payee} as ${c.name}.`);
      } catch (e) {
        setLeaving((s) => {
          const n = new Set(s);
          n.delete(p.key);
          return n;
        });
        toast(`Couldn't file ${p.payee}. ${(e as ApiError).message}`);
      }
    }, FADE_MS);
  };

  return (
    <>
      <div className="ib-head">
        <div className="ib-n">{plural(remaining, "payee")} to file</div>
        <div className="sub">
          New people and shops. File one once and every future payment files itself.
          {total > fetched && ` Showing the newest ${fetched} of ${total}.`}
        </div>
      </div>
      {items.length ? (
        <div className="ibx">
          {items.map((p) => (
            <InboxCard key={p.key} p={p} categories={chips[p.direction]} leaving={leaving.has(p.key)} onFile={file} />
          ))}
        </div>
      ) : (
        <Empty title="Inbox zero">Everything is filed. New payees will show up here.</Empty>
      )}
    </>
  );
}

function InboxCard(props: { p: InboxPayee; categories: Category[]; leaving: boolean; onFile: (p: InboxPayee, c: Category, remember: boolean) => void }) {
  const { p, categories, leaving, onFile } = props;
  const [remember, setRemember] = useState(true);
  const before = p.history.map((h) => `${h.category} ×${h.count}`).join(", ");
  const credit = p.direction === "credit";
  return (
    <div className={`card icard${leaving ? " done" : ""}`} aria-hidden={leaving}>
      <div className="top2">
        <Monogram name={p.payee} />
        <div className="grow1">
          <div className="b wrap">{p.payee}</div>
          <div className="sub">{[p.account, p.reason && reasonLabel(p.reason)].filter(Boolean).join(" · ")}</div>
        </div>
        <div className={`num b r${credit ? " in" : ""}`}>
          {credit ? "+" : ""}
          {inr(p.total)}
          <div className="sub">{plural(p.payments.length, "payment")}</div>
        </div>
      </div>
      <div className="hist">
        {p.payments
          .slice(-3)
          .reverse()
          .map((x) => (
            <div key={x.id}>
              <span>{dayName(x.date)}</span>
              <span className="num">{inr(x.amount)}</span>
            </div>
          ))}
      </div>
      {before && <div className="sub mt-s">Filed before as {before}</div>}
      <div className="cats" role="group" aria-label={`File ${p.payee} as`}>
        {categories.map((c) => (
          <button type="button" key={c.id} className={c.name === p.suggestedCategory ? "sugg" : ""} disabled={leaving} onClick={() => onFile(p, c, remember)}>
            {c.name}
          </button>
        ))}
      </div>
      <Switch on={remember} onChange={setRemember} label={<>Remember for {p.payee}</>} />
    </div>
  );
}
