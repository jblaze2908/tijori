import { G } from "../components/Glyphs";
import { useToast } from "../components/Toast";
import { api, dataOf, dismissAliasSuggestion, read, renamePayees, resetPayees, setRuleEnabled } from "../lib/api";
import { monogramColor } from "../lib/colors";
import { dayShort, inr, plural, timeIST, toPaise } from "../lib/format";
import { Link } from "../lib/router";
import { setup } from "../lib/setup";
import { AccountsSettings } from "./setup/profile";
import { FirstUpload } from "./setup/secrets";

const ago = (ts: string | null) => {
  if (!ts) return "never";
  const m = Math.round((Date.now() - Date.parse(ts)) / 60000);
  if (m < 60) return `${Math.max(1, m)} min ago`;
  if (m < 1440) return `${Math.round(m / 60)} h ago`;
  return `${dayShort(ts.slice(0, 10))} ${timeIST(ts)}`;
};

/** Settings → Accounts & sources: can the numbers be trusted? */
export function Sources() {
  const mail = dataOf(read(setup.mailSources())) ?? [];
  const accounts = dataOf(read(api.accounts())) ?? [];
  const queue = dataOf(read(api.sourcesQueue()));
  return (
    <>
      {mail.length ? (
        mail.map((m) => (
          <section key={m.id} className="panel">
            <div className="panel-h">
              <span className="mg acct" style={{ background: "var(--s2)", color: "var(--t2)", width: 36, height: 36, borderRadius: 8 }}>
                {G.mail}
              </span>
              <div style={{ display: "flex", flexDirection: "column" }}>
                <h2 style={{ display: "flex", gap: 10, alignItems: "center" }}>
                  Mail source · {m.provider === "gmail" ? "Gmail" : m.provider} over IMAP
                  <span className={`okbadge${m.status === "error" ? " err" : m.status === "untested" ? " idle" : ""}`}>
                    <i />
                    {m.status === "ok" ? "Connected" : m.status === "error" ? "Login refused" : "Not tested"}
                  </span>
                </h2>
                <span className="muted" style={{ fontSize: 13 }}>
                  {m.email}
                </span>
              </div>
              <Link href="/settings/mail" className="btn2 sm" style={{ marginLeft: "auto" }}>
                Manage
              </Link>
            </div>
            <div className="facts">
              <Fact k="Label" v={m.label} />
              <Fact k="Access" v="Read-only" />
              <Fact k="Host" v={`${m.host}:${m.port}`} />
              <Fact k="Last tested" v={ago(m.last_tested_at)} />
              <Fact k="Messages in label" v={m.last_message_count != null ? m.last_message_count.toLocaleString("en-IN") : "—"} />
              <Fact k="Last collected" v={m.last_poll_at ? `${ago(m.last_poll_at)}${m.last_poll_error ? ` · ${m.last_poll_error.replace(/_/g, " ")}` : ""}` : "not yet"} />
              <Fact k="Read so far" v={queue?.collected ? Object.values(queue.collected).reduce((a, n) => a + n, 0).toLocaleString("en-IN") : "—"} />
            </div>
            <span className="foot">
              App Password stored encrypted, never shown again. It grants full mailbox access; Tijori opens only the {m.label} label, read-only, every 2 minutes.
              {queue?.collected ? ` ${queue.collected.parsed ?? 0} read · ${queue.collected.ignored ?? 0} not transactions · ${queue.collected.needs_password ?? 0} need a password · ${(queue.collected.parser_needed ?? 0) + (queue.collected.failed ?? 0)} unread.` : ""}
            </span>
          </section>
        ))
      ) : (
        <section className="panel">
          <div className="panel-h">
            <h2>No mail source yet</h2>
            <Link href="/settings/mail" className="x linkx">
              Connect mail →
            </Link>
          </div>
        </section>
      )}

      <section className="panel" aria-label="Accounts">
        <div className="panel-h">
          <h2>Accounts</h2>
        </div>
        <table className="t2">
          <thead>
            <tr>
              <th>Account</th>
              <th>Last alert</th>
              <th>Last statement</th>
              <th className="r">Balance</th>
              <th className="r">Live</th>
              <th className="r">Password</th>
            </tr>
          </thead>
          <tbody>
            {accounts.map((a) => (
              <tr key={a.id}>
                <td>
                  <span className="nm">
                    <span className={`mg acct`} style={{ background: monogramColor(a.label) }}>
                      {a.institution.replace(/[^A-Za-z]/g, "").slice(0, 2).toUpperCase()}
                    </span>
                    <span>
                      {a.label}
                      <br />
                      <small>{a.kind[0]!.toUpperCase() + a.kind.slice(1)} · {plural(a.txn_count, "transaction")}</small>
                    </span>
                  </span>
                </td>
                <td className="muted">{a.last_seen_at ? ago(a.last_seen_at) : "—"}</td>
                <td>
                  {a.last_statement ? (
                    <>
                      {dayShort(a.last_statement.period_start)} – {dayShort(a.last_statement.period_end)}
                      <br />
                      <small style={{ color: a.last_statement.reconciled ? "var(--in)" : "var(--warn)" }}>
                        {a.last_statement.reconciled ? "✓ ₹0 diff" : `⚠ ${a.last_statement.diff ? inr(toPaise(a.last_statement.diff)) : "?"} diff`}
                      </small>
                    </>
                  ) : (
                    <span className="faint">{a.kind === "wallet" ? "No statements" : "None yet"}</span>
                  )}
                </td>
                <td className="r">{a.balance ? <>{inr(toPaise(a.balance.amount))}<br /><small>{dayShort(a.balance.as_of)}</small></> : "—"}</td>
                <td className="r muted">{a.coverage_pct != null ? `${Math.round(a.coverage_pct)}%` : "—"}</td>
                <td className="r muted" style={{ fontFamily: "inherit" }}>{a.statement_passwords?.length ? `✓ ${a.statement_passwords.length} saved` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <span className="foot">Live = share of the last statement's lines already seen in alert emails (fills in with the M1 collector). Diff = opening + credits − debits − printed closing balance.</span>
      </section>

      <div className="row2">
        <section className="panel grow" aria-label="Parser needed">
          <div className="panel-h">
            <h2>Parser needed · {queue?.unparsed.length ?? 0}</h2>
            <span className="x">Unknown formats · raw kept</span>
          </div>
          {queue?.unparsed.length ? (
            <div className="rows">
              {queue.unparsed.map((u) => (
                <div key={`${u.sender}${u.subject}${u.status}`} className="lrow static">
                  <span className="mid">
                    <b className="mono-n" style={{ fontSize: 13 }}>{u.sender === "upload" ? "Upload" : u.sender}</b>
                    <small>
                      {u.status === "needs_password" ? "Needs a statement password (Settings → Statement passwords) · " : u.status === "failed" ? "Couldn't be read · " : ""}
                      {u.subject}
                    </small>
                  </span>
                  <span className="amt" style={{ width: 80 }}>{u.count}</span>
                  <span className="ac" style={{ width: 110 }}>first {dayShort(u.first_seen.slice(0, 10))}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="state" style={{ padding: 0 }}>Every file so far was read.</p>
          )}
          <span className="foot">Stored unparsed; re-read when a parser for the format ships.</span>
        </section>
        <section className="panel side2" aria-label="Upload a statement">
          <div className="panel-h">
            <h2>Upload a statement</h2>
          </div>
          <FirstUpload />
          {queue?.uploads.slice(0, 3).map((u) => (
            <div key={u.id} className="panel-h" style={{ borderTop: "1px solid var(--line)", paddingTop: 10 }}>
              <span className="muted" style={{ fontSize: 13 }}>
                {u.filename} · {dayShort(u.received_at.slice(0, 10))}
              </span>
              <span className="x" style={{ color: u.reconciled ? "var(--in)" : "var(--t3)" }}>
                {u.status === "parsed" ? (u.reconciled ? "₹0 diff" : `${u.diff ?? "?"} diff`) : u.status.replace("_", " ")}
              </span>
            </div>
          ))}
        </section>
      </div>

      <section className="panel" aria-label="Manage accounts">
        <div className="panel-h">
          <h2>Manage accounts</h2>
          <span className="x">Accounts are also created from statements</span>
        </div>
        <AccountsSettings />
      </section>
    </>
  );
}

const Fact = ({ k, v }: { k: string; v: string }) => (
  <div>
    <span className="k">{k}</span>
    <span>{v}</span>
  </div>
);

export function Rules() {
  const rules = dataOf(read(api.rules())) ?? [];
  const toast = useToast();
  const toggle = async (id: string, on: boolean) => {
    try {
      await setRuleEnabled(id, on);
      toast(on ? "Rule on" : "Rule paused");
    } catch {
      toast("Couldn't change that rule.");
    }
  };
  return (
    <section className="panel" aria-label="Rules">
      <div className="panel-h">
        <h2>Rules · {rules.length}</h2>
        <span className="x">Made when you file "Always for this payee"</span>
      </div>
      {rules.length ? (
        <table className="t2">
          <thead>
            <tr>
              <th>Match</th>
              <th>Category</th>
              <th className="r">Filed</th>
              <th>Last match</th>
              <th>Created</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rules.map((r) => (
              <tr key={r.id} style={r.enabled ? undefined : { opacity: 0.5 }}>
                <td className="mono-n">
                  {Object.entries(r.match)
                    .map(([k, v]) => `${k}: ${v}`)
                    .join(" · ")}
                </td>
                <td>{r.category}</td>
                <td className="r">{r.hits}</td>
                <td className="muted">{r.last_hit_at ? dayShort(r.last_hit_at.slice(0, 10)) : "—"}</td>
                <td className="muted">
                  {dayShort(r.created_at.slice(0, 10))} · {r.scope === "household" ? "household" : "you"}
                </td>
                <td className="r" style={{ fontFamily: "inherit" }}>
                  {r.editable && (
                    <button type="button" className="linkish" onClick={() => toggle(r.id, !r.enabled)}>
                      {r.enabled ? "Pause" : "Turn on"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="state" style={{ padding: 0 }}>No rules yet. File a payee in the Inbox with "Always for this payee" to make one.</p>
      )}
    </section>
  );
}

const WHY: Record<string, string> = { prefix: "same start", words: "same words", spelling: "similar spelling" };

/** Your names for payees, and payees whose name matches one of them (a rule: services/aliases.likeness). */
export function PayeeNames() {
  const st = dataOf(read(api.payeeAliases()));
  const toast = useToast();
  const items = st?.items ?? [];
  const suggestions = st?.suggestions ?? [];
  const run = async (f: () => Promise<string>) => {
    try {
      toast(await f());
    } catch {
      toast("Couldn't save that. Try again.");
    }
  };
  const names = new Set(items.map((a) => a.name)).size;
  return (
    <>
      {suggestions.length > 0 && (
        <section className="panel" aria-label="Similar payees">
          <div className="panel-h">
            <h2>Similar payees · {suggestions.length}</h2>
            <span className="x">Name matches one of your payee names</span>
          </div>
          <table className="t2">
            <thead>
              <tr>
                <th>Payee</th>
                <th>Similar to</th>
                <th>Match</th>
                <th className="r">Payments</th>
                <th className="r">Total</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {suggestions.map((p) => (
                <tr key={p.payee_key}>
                  <td>
                    {p.merchant ?? p.payee_key}
                    {p.vpa && <span className="muted mono-n"> · {p.vpa}</span>}
                  </td>
                  <td>{p.suggest.name}</td>
                  <td className="muted">
                    {WHY[p.suggest.why]}
                    {p.suggest.like !== p.suggest.name ? ` · “${p.suggest.like}”` : ""}
                  </td>
                  <td className="r">{p.count}</td>
                  <td className="r">{inr(toPaise(p.total))}</td>
                  <td className="r" style={{ fontFamily: "inherit", whiteSpace: "nowrap" }}>
                    <button type="button" className="linkish" onClick={() => run(async () => (await dismissAliasSuggestion(p.payee_key, p.suggest.name), `Not ${p.suggest.name}`))}>
                      Not this
                    </button>
                    <button type="button" className="linkish" onClick={() => run(async () => { const r = await renamePayees(p.suggest.name, [p.payee_key]); return `Named ${r.name} · ${plural(r.updated, "transaction")}`; })}>
                      Name it
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      <section className="panel" aria-label="Payee names">
        <div className="panel-h">
          <h2>Payee names · {names}</h2>
          <span className="x">Payees with one name count as one merchant</span>
        </div>
        {items.length ? (
          <table className="t2">
            <thead>
              <tr>
                <th>Name</th>
                <th>Was</th>
                <th>Payee</th>
                <th className="r">Payments</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((a) => (
                <tr key={a.payee_key}>
                  <td>{a.name}</td>
                  <td className="muted">{a.original}</td>
                  <td className="mono-n muted">{a.payee_key}</td>
                  <td className="r">{a.count}</td>
                  <td className="r" style={{ fontFamily: "inherit" }}>
                    <button type="button" className="linkish" onClick={() => run(async () => `Name reset · ${plural((await resetPayees([a.payee_key])).restored, "transaction")}`)}>
                      Reset
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="state" style={{ padding: 0 }}>No payee names yet. Rename a payee from its transaction.</p>
        )}
      </section>
    </>
  );
}
