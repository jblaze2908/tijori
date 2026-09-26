import { useEffect, useRef, useState } from "react";
import { G } from "../components/Glyphs";
import { useToast } from "../components/Toast";
import { ErrorState, Loading } from "../components/ui";
import type { AppCtx } from "../ctx";
import { api, decideRecurring, invalidate, read } from "../lib/api";
import { monogramColor } from "../lib/colors";
import { addDays, dayShort, daysBetween, inr, monthApos, plural } from "../lib/format";
import { navigate } from "../lib/router";
import type { Cadence, Recurring, RecurringKind, RecurringList } from "../lib/types";

const GROUPS: { kind: RecurringKind | "ended"; title: string }[] = [
  { kind: "subscription", title: "Subscriptions" },
  { kind: "bill", title: "Bills & fixed" },
  { kind: "other", title: "Other recurring" },
  { kind: "invest", title: "Investments" },
  { kind: "ended", title: "Stopped or cancelled" },
];
const KIND_COLOR: Record<RecurringKind, string> = { subscription: "var(--accent)", bill: "var(--c4)", invest: "var(--c3)", other: "var(--t2)" };
const EVERY: Record<Cadence, string> = { weekly: "Week", monthly: "Month", quarterly: "Quarter", yearly: "Year" };
const HORIZON = 30;

export function Subscriptions({ app }: { app: AppCtx }) {
  const st = read(api.subscriptions());
  return (
    <>
      <div className="ph">
        <h1>Subscriptions</h1>
        <div className="sp" />
        <span className="muted" style={{ fontSize: 13 }}>
          {st.status === "ready" ? `${st.data.totals.active} active` : ""}
        </span>
      </div>
      {st.status === "loading" && <Loading />}
      {st.status === "error" && <ErrorState error={st.error} title="Couldn't load subscriptions" onRetry={() => invalidate(["/api/recurring"])} />}
      {st.status === "ready" && <Body data={st.data} today={app.asOf} />}
    </>
  );
}

function Body({ data, today }: { data: RecurringList; today: string }) {
  const [showHidden, setShowHidden] = useState(false);
  const live = data.items.filter((x) => x.active);
  const invest = live.filter((x) => x.kind === "invest");
  const spendLive = live.filter((x) => x.kind !== "invest");
  const soon = live.filter((x) => x.state === "upcoming" && daysBetween(today, x.next_due) <= HORIZON).sort((a, b) => a.next_due.localeCompare(b.next_due));
  const groups = GROUPS.map((g) => ({
    ...g,
    rows: data.items.filter((x) => (g.kind === "ended" ? !x.active : x.active && x.kind === g.kind)).sort((a, b) => b.monthly - a.monthly),
  })).filter((g) => g.rows.length);
  if (!data.items.length)
    return (
      <section className="panel">
        <h2 style={{ margin: 0, fontSize: 16 }}>Nothing recurring yet</h2>
        <p className="muted" style={{ margin: 0 }}>
          A payee shows up here after 3 charges on a steady cadence (2 for yearly). Mark any payment as recurring from its detail.
        </p>
      </section>
    );
  return (
    <>
      <div className="figstrip">
        <div>
          <span className="k">Per month</span>
          <b className="lg">{inr(data.totals.monthly)}</b>
          <small>
            {spendLive.length} active{invest.length ? " · SIPs not included" : ""}
          </small>
        </div>
        <div>
          <span className="k">Per year</span>
          <b className="lg">{inr(data.totals.yearly)}</b>
          <small>Per month × 12</small>
        </div>
        <div>
          <span className="k">Next {HORIZON} days</span>
          <b className="lg">{inr(data.totals.next30)}</b>
          <small>
            {plural(data.totals.next30Count, "charge")} · {dayShort(addDays(today, 1))} – {dayShort(addDays(today, HORIZON))}
          </small>
        </div>
        <div>
          <span className="k">SIPs per month</span>
          <b className="lg">{inr(data.totals.investMonthly)}</b>
          <small>{invest.length ? invest.map((x) => x.merchant).join(", ") : "none detected"}</small>
        </div>
      </div>

      {soon.length > 0 && <Timeline items={soon} today={today} />}

      {groups.map((g) => (
        <section key={g.kind} className="subgroup" aria-label={g.title}>
          <div className="subhead">
            <h2 className={g.kind === "ended" ? "muted" : ""}>{g.title}</h2>
            <span className="mono-n faint">
              {g.rows.length} ·{" "}
              {g.kind === "ended"
                ? `was ${inr(g.rows.reduce((a, x) => a + x.monthly, 0))} a month`
                : `${inr(g.rows.reduce((a, x) => a + x.monthly, 0))} a month${g.kind === "invest" ? " · not counted as spend" : ""}`}
            </span>
          </div>
          <div className="subtable" role="table">
            <div className="subrow head" role="row">
              <span className="c-pay">Payee</span>
              <span className="c-every">Every</span>
              <span className="c-amt r">Charge</span>
              <span className="c-mo r">Per month</span>
              <span className="c-last">Last charged</span>
              <span className="c-next">Next</span>
              <span className="c-hist">Last 12 charges</span>
              <span className="c-act" />
            </div>
            {g.rows.map((x) => (
              <Row key={x.id} x={x} today={today} />
            ))}
          </div>
        </section>
      ))}

      <div className="subfoot">
        <span className="foot">
          Detected from transactions: 3 or more charges to one payee on a steady cadence (2 for yearly), amounts steady or bill-like. Mark any payment as recurring from its detail. Card purchases show here once card
          statements are parsed.
        </span>
        {data.dismissed.length > 0 && (
          <button type="button" className="linkish" onClick={() => setShowHidden((v) => !v)}>
            {data.dismissed.length} hidden · {showHidden ? "Hide" : "Show"}
          </button>
        )}
      </div>
      {showHidden && <Hidden items={data.dismissed} />}
    </>
  );
}

function Timeline({ items, today }: { items: Recurring[]; today: string }) {
  const x = (d: string) => `${(daysBetween(today, d) / HORIZON) * 100}%`;
  return (
    <section className="panel flat" aria-label={`Next ${HORIZON} days`}>
      <div className="panel-h">
        <h2>Next {HORIZON} days</h2>
        <span className="foot">Due dates from each series' last charge</span>
        <div className="legend" style={{ marginLeft: "auto" }}>
          {(["subscription", "bill", "invest", "other"] as RecurringKind[]).map((k) => (
            <span key={k}>
              <i className="dot" style={{ background: KIND_COLOR[k] }} />
              {k === "invest" ? "SIP" : k[0]!.toUpperCase() + k.slice(1)}
            </span>
          ))}
        </div>
      </div>
      <div className="tline">
        <div className="axis" />
        <span className="tl-today">Today</span>
        <span className="tl-end">{dayShort(addDays(today, HORIZON))}</span>
        {items.map((it, i) => (
          <div key={it.id} className={`tl-m ${i % 2 === 0 ? "tl-up" : "tl-down"}${daysBetween(today, it.next_due) > HORIZON * 0.8 ? " right" : ""}`} style={{ left: x(it.next_due) }}>
            <i className="dot" style={{ background: KIND_COLOR[it.kind] }} />
            <span className="stem" />
            <span className="tl-lbl">
              <small className="mono-n">{dayShort(it.next_due)}</small>
              <b>{it.merchant}</b>
              <small className="mono-n">
                {it.variable ? "~" : ""}
                {inr(it.amountExpected)}
              </small>
            </span>
          </div>
        ))}
      </div>
      <ul className="tl-list">
        {items.map((it) => (
          <li key={it.id}>
            <i className="dot" style={{ background: KIND_COLOR[it.kind] }} />
            <span className="mono-n faint">{dayShort(it.next_due)}</span>
            <span className="grow">{it.merchant}</span>
            <span className="mono-n">{inr(it.amountExpected)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function nextLine(x: Recurring, today: string): { text: string; tone: "warn" | "faint" | "" } | null {
  if (x.state === "ended") return { text: "Cancelled by you", tone: "faint" };
  if (x.state === "stopped") return { text: `No charge since · expected ${dayShort(x.next_due)}`, tone: "faint" };
  if (x.state === "late") return { text: `Not seen · statements to ${dayShort(x.seen_through)}`, tone: "warn" };
  if (x.state === "pending") return { text: `Due · statements to ${dayShort(x.seen_through)}`, tone: "faint" };
  if (x.change && daysBetween(x.change.at, today) <= 120) {
    const up = x.change.to > x.change.from;
    return { text: `${up ? "Up" : "Down"} ${inr(Math.abs(x.change.to - x.change.from))} · ${inr(x.change.from)} → ${inr(x.change.to)} on ${dayShort(x.change.at)}`, tone: up ? "warn" : "" };
  }
  if (x.variable) return { text: `Varies ${inr(x.amountMin)}–${inr(x.amountMax)} · median of last 3`, tone: "faint" };
  if (x.cadence === "yearly" || x.cadence === "quarterly") return { text: `In ${plural(daysBetween(today, x.next_due), "day")}`, tone: "faint" };
  return null;
}

function Row({ x, today }: { x: Recurring; today: string }) {
  const [open, setOpen] = useState(false);
  const ended = !x.active;
  const line = nextLine(x, today);
  const since = x.first_at === x.last_at ? dayShort(x.first_at) : `since ${monthApos(x.first_at)}`;
  return (
    <div className={`subrow${ended ? " ended" : ""}`} role="row">
      <span className="c-pay">
        <span className="mg sq" style={{ background: ended ? "var(--s2)" : monogramColor(x.merchant), color: ended ? "var(--t2)" : undefined }}>
          {(x.merchant.replace(/[^A-Za-z0-9]/g, "")[0] ?? "•").toUpperCase()}
        </span>
        <span className="who2">
          <b>
            {x.merchant}
            {x.manual && <span className="kindtag">Added by you</span>}
          </b>
          <small>
            {x.account ?? "Unknown account"} · {ended ? `${monthApos(x.first_at)} – ${monthApos(x.last_at)}` : since}
          </small>
        </span>
      </span>
      <span className="c-every muted">{EVERY[x.cadence]}</span>
      <span className="c-amt r mono-n strong">
        {x.variable ? "~" : ""}
        {inr(x.amountExpected)}
      </span>
      <span className="c-mo r mono-n muted">{ended ? "—" : inr(x.monthly)}</span>
      <span className="c-last mono-n muted">{x.last_at.slice(0, 4) !== today.slice(0, 4) ? `${dayShort(x.last_at)} ${x.last_at.slice(0, 4)}` : dayShort(x.last_at)}</span>
      <span className="c-next">
        {!ended && <span className="mono-n">{dayShort(x.next_due)}</span>}
        {line && <small className={line.tone === "warn" ? "warn-t" : line.tone === "faint" ? "faint" : ""}>{line.text}</small>}
      </span>
      <span className="c-hist">{!ended && <History x={x} />}</span>
      <span className="c-act">
        <button type="button" className="kebab" aria-label={`Actions for ${x.merchant}`} aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden>
            <circle cx="3.5" cy="8" r="1.25" fill="currentColor" />
            <circle cx="8" cy="8" r="1.25" fill="currentColor" />
            <circle cx="12.5" cy="8" r="1.25" fill="currentColor" />
          </svg>
        </button>
        {open && <Menu x={x} close={() => setOpen(false)} />}
      </span>
    </div>
  );
}

/** One bar per charge (up to 12), height by amount; an empty slot marks a charge that hasn't shown up. */
function History({ x }: { x: Recurring }) {
  const max = Math.max(1, ...x.charges.map((c) => c.amount));
  const late = x.state === "late";
  const bars = x.charges.slice(late ? -11 : -12);
  const pad = 12 - bars.length - (late ? 1 : 0);
  const current = x.change ? x.change.to : null;
  return (
    <span className="hist" aria-label={plural(x.charges.length, "charge")}>
      {Array.from({ length: pad }, (_, i) => (
        <i key={`p${i}`} className="none" />
      ))}
      {bars.map((c) => (
        <i key={c.txn_id} title={`${dayShort(c.date)} · ${inr(c.amount)}`} style={{ height: `${Math.max(3, (c.amount / max) * 24)}px`, background: KIND_COLOR[x.kind], opacity: current != null && c.amount !== current ? 0.45 : 0.8 }} />
      ))}
      {late && <i className="miss" title="Expected, not seen" />}
    </span>
  );
}

function Menu({ x, close }: { x: Recurring; close: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const toast = useToast();
  useEffect(() => {
    const off = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && close();
    const esc = (e: KeyboardEvent) => e.key === "Escape" && close();
    // Next tick: the click that opened the menu must not close it.
    const t = setTimeout(() => addEventListener("click", off));
    addEventListener("keydown", esc);
    return () => {
      clearTimeout(t);
      removeEventListener("click", off);
      removeEventListener("keydown", esc);
    };
  }, [close]);
  const act = async (body: Parameters<typeof decideRecurring>[1], done: string) => {
    close();
    try {
      await decideRecurring(x.id, { cadence: x.cadence, ...body });
      toast(done);
    } catch {
      toast("Couldn't save that. Try again.");
    }
  };
  return (
    <div ref={ref} className="menu2" role="menu">
      <button type="button" role="menuitem" onClick={() => act({ decision: x.confirmed ? "auto" : "confirmed" }, x.confirmed ? "Back to detection" : `${x.merchant} confirmed`)}>
        <span className="ic">{x.confirmed ? G.check : null}</span>
        {x.confirmed ? "Confirmed recurring" : "Confirm recurring"}
      </button>
      <button type="button" role="menuitem" onClick={() => act({ decision: "confirmed", ended: x.state !== "ended" }, x.state === "ended" ? "Marked active" : "Marked cancelled")}>
        <span className="ic" />
        {x.state === "ended" ? "Mark active again" : "Mark cancelled"}
      </button>
      <button type="button" role="menuitem" onClick={() => act({ decision: "dismissed" }, `${x.merchant} hidden`)}>
        <span className="ic" />
        Not recurring — hide
      </button>
      <div className="sep" />
      <span className="lbl2">Group</span>
      <div className="chips">
        {(["subscription", "bill", "other"] as RecurringKind[]).map((k) => (
          <button key={k} type="button" className={`catchip sm${x.kind === k ? " on" : ""}`} onClick={() => act({ decision: "confirmed", kind: k }, `Moved to ${k === "bill" ? "Bills & fixed" : k === "other" ? "Other" : "Subscriptions"}`)}>
            {k === "subscription" ? "Subscription" : k === "bill" ? "Bill" : "Other"}
          </button>
        ))}
      </div>
      <div className="sep" />
      <button type="button" role="menuitem" onClick={() => navigate(`/transactions?q=${encodeURIComponent(x.merchant)}&from=${x.first_at}`)}>
        <span className="ic" />
        See {plural(x.count, "transaction")}
      </button>
    </div>
  );
}

function Hidden({ items }: { items: { id: string; merchant: string }[] }) {
  const toast = useToast();
  const restore = async (h: { id: string; merchant: string }) => {
    try {
      await decideRecurring(h.id, { decision: "auto" });
      toast(`${h.merchant} restored`);
    } catch {
      toast("Couldn't restore. Try again.");
    }
  };
  return (
    <section className="panel" aria-label="Hidden">
      <div className="panel-h">
        <h2>Hidden</h2>
        <span className="foot">Payees you marked as not recurring</span>
      </div>
      {items.map((h) => (
        <div key={h.id} className="lrow2">
          <span className="grow">{h.merchant}</span>
          <button type="button" className="btn2 sm" onClick={() => restore(h)}>
            Restore
          </button>
        </div>
      ))}
    </section>
  );
}
