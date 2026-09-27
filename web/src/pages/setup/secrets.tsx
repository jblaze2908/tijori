import { useRef, useState } from "react";
import { CopyField, Field, SecretInput, useAction } from "../../components/forms";
import { useToast } from "../../components/Toast";
import { InlineState, Loading } from "../../components/ui";
import { api, dataOf, read } from "../../lib/api";
import { dayIST, dayShort, inr, plural, toPaise } from "../../lib/format";
import { addStatementPassword, createInvite, removeStatementPassword, revokeInvite, setup, uploadStatement, type UploadResult } from "../../lib/setup";
import { useStore } from "../../lib/useStore";
import type { Account, StatementPasswordRef } from "../../lib/types";

/** Statement PDF passwords, any number per account. Write-only: the page learns which ones exist, never a value. */
export function StatementPasswords({ onContinue }: { onContinue?: () => void }) {
  useStore();
  const st = read(api.accounts());
  return (
    <div className="form">
      <p className="sub">
        Banks email statements as locked PDFs. Each password is sealed on your household's server, used only to open that bank's statements, and never
        shown again.
      </p>
      {st.status === "loading" ? (
        <Loading card={false} />
      ) : st.status === "error" ? (
        <InlineState>{st.error.message}</InlineState>
      ) : st.data?.length ? (
        <div className="list">
          {st.data.map((a) => (
            <PasswordRow key={a.id} a={a} />
          ))}
        </div>
      ) : (
        <InlineState>Accounts appear after the first statement from each bank. You can give a PDF's password when you upload it, in the next step.</InlineState>
      )}
      {onContinue && (
        <div className="row mt">
          <button type="button" className="btn" onClick={onContinue}>
            Continue
          </button>
          <span className="sub">Optional. You can add these later in Settings.</span>
        </div>
      )}
    </div>
  );
}

const passwordName = (p: StatementPasswordRef) => p.label ?? (p.slot === "main" ? "Statement password" : "Password");

/** One account's saved passwords, each removable, and a form to add one with an optional label. */
function PasswordRow({ a }: { a: Account }) {
  const toast = useToast();
  const act = useAction();
  const [adding, setAdding] = useState(false);
  const list = a.statement_passwords ?? [];
  return (
    <div className="li li-col">
      <div className="li-row">
        <b>{a.label}</b>
        <small className="faint" style={{ marginLeft: "auto" }}>
          {list.length ? plural(list.length, "password") : "No password"}
        </small>
      </div>
      {list.map((p) => (
        <div key={p.slot} className="li-row">
          <div className="mid">
            <small>
              {passwordName(p)} · saved {dayShort(p.updated_at.slice(0, 10))}
            </small>
          </div>
          <button
            type="button"
            className="linkish"
            disabled={act.busy}
            onClick={async () => {
              if ((await act.run(() => removeStatementPassword(a.id, p.slot))).ok) toast(`Removed ${passwordName(p)} for ${a.label}.`);
            }}
          >
            Remove
          </button>
        </div>
      ))}
      {adding ? (
        <AddPasswordForm a={a} onDone={() => setAdding(false)} />
      ) : (
        list.length < 10 && (
          <button type="button" className="linkish" style={{ alignSelf: "flex-start", marginLeft: 0 }} onClick={() => setAdding(true)}>
            + Add password
          </button>
        )
      )}
      {act.error && (
        <div className="result bad" role="alert">
          {act.error}
        </div>
      )}
    </div>
  );
}

function AddPasswordForm({ a, onDone }: { a: Account; onDone: () => void }) {
  const toast = useToast();
  const [value, setValue] = useState("");
  const [label, setLabel] = useState("");
  const act = useAction();
  return (
    <form
      className="li-col"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!value) return;
        const r = await act.run(() => addStatementPassword(a.id, value, label.trim() || undefined));
        setValue("");
        if (r.ok) {
          toast(`Saved a password for ${a.label}.`);
          onDone();
        }
      }}
    >
      <div className="row-form">
        <SecretInput value={value} onChange={setValue} placeholder="Password" label={`New password for ${a.label}`} />
        <input className="inp" style={{ flex: "0 1 200px" }} value={label} maxLength={40} placeholder="Label (optional)" aria-label={`Label for the new password for ${a.label}`} onChange={(e) => setLabel(e.target.value)} />
        <button type="submit" className="btn ghost" disabled={act.busy || !value}>
          Save
        </button>
        <button type="button" className="linkish" onClick={onDone}>
          Cancel
        </button>
      </div>
      {act.error && (
        <div className="result bad" role="alert">
          {act.error}
        </div>
      )}
    </form>
  );
}

/** Upload one statement (POST /api/uploads). The optional PDF password is used once and cleared from the form. */
export function FirstUpload({ onDone }: { onDone?: () => void }) {
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [password, setPassword] = useState("");
  const [result, setResult] = useState<UploadResult | null>(null);
  const act = useAction();
  return (
    <form
      className="form"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!file) return;
        const r = await act.run(() => uploadStatement(file, password));
        setPassword("");
        if (!r.ok) return;
        setResult(r.value);
        setFile(null);
        if (fileRef.current) fileRef.current.value = "";
        toast(r.value.duplicate ? "That statement was already uploaded." : "Statement read.");
      }}
    >
      <p className="sub">
        Upload one recent bank statement to check everything lines up. It's read on your server: every line is matched, checked against the closing
        balance, and filed. Anything Tijori can't file goes to your Inbox.
      </p>
      <Field label="Statement (PDF, or its text export)">
        {(id) => <input id={id} ref={fileRef} className="inp" type="file" accept="application/pdf,.pdf,text/plain,.txt" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />}
      </Field>
      <Field label="PDF password (if it's locked)" hint="Used once to open this file and not kept. Saved statement passwords are tried automatically.">
        {(id) => <SecretInput id={id} value={password} onChange={setPassword} placeholder="Optional" />}
      </Field>
      {result && <UploadSummary r={result} />}
      {act.error && (
        <div className="result bad" role="alert">
          {act.error}
        </div>
      )}
      <div className="row mt">
        <button type="submit" className="btn" disabled={!file || act.busy}>
          {act.busy ? "Reading…" : "Upload and read"}
        </button>
        {onDone && (
          <button type="button" className={result ? "btn" : "btn ghost"} onClick={onDone}>
            {result ? "Go to your dashboard" : "Skip and finish"}
          </button>
        )}
      </div>
    </form>
  );
}

function UploadSummary({ r }: { r: UploadResult }) {
  if (r.duplicate) return <div className="result ok">That exact file was uploaded before, so nothing changed.</div>;
  const rec = r.reconciliation;
  return (
    <div className={`result ${rec?.ok ? "ok" : "bad"}`} role="status">
      <b>{r.account?.label ?? r.institution ?? "Statement"}</b>
      {r.period_start && r.period_end ? ` · ${dayShort(r.period_start)} – ${dayShort(r.period_end)}` : ""}
      <br />
      {rec?.ok
        ? "Balances check out: opening + credits − debits = closing, with ₹0 difference."
        : `The balances don't reconcile${rec?.closing_diff ? ` (off by ${inr(Math.abs(toPaise(rec.closing_diff)))})` : ""}. The lines are kept; check the statement.`}
      {r.txns && (
        <>
          <br />
          {plural(r.txns.created, "new transaction")}, {r.txns.matched_existing} already known · {r.txns.filed} filed, {r.txns.inbox} in your Inbox
        </>
      )}
    </div>
  );
}

/** Settings → Household: members, pending invites, and (for the admin) single-use invite links shown once. */
export function HouseholdPanel() {
  useStore();
  const toast = useToast();
  const me = dataOf(read(api.me()));
  const hs = read(setup.household());
  const [email, setEmail] = useState("");
  const [link, setLink] = useState<{ email: string; url: string; expires_at: string } | null>(null);
  const act = useAction();
  if (!me || hs.status === "loading") return <Loading card={false} />;
  const h = hs.status === "ready" ? hs.data : null;
  const admin = me.role === "admin";
  return (
    <div className="form">
      <h4 className="fh">{h?.name ?? me.household?.name ?? "Your household"}</h4>
      <p className="sub">
        Each member sees only their own money unless they choose to share. {admin ? "As admin you can invite people, but you can't see their data." : ""}
      </p>
      {h ? (
        <table className="tbl">
          <thead>
            <tr>
              <th>Member</th>
              <th>Email</th>
              <th className="r">Role</th>
            </tr>
          </thead>
          <tbody>
            {h.members.map((m) => (
              <tr key={m.id}>
                <td>{m.name}</td>
                <td className="t2">{m.email}</td>
                <td className="r cap">{m.role}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <InlineState>{hs.status === "error" ? hs.error.message : "The member list isn't available on the server yet."}</InlineState>
      )}
      {admin ? (
        <>
          <h4 className="fh">Invite someone</h4>
          <form
            className="row-form"
            onSubmit={async (e) => {
              e.preventDefault();
              if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) return act.setError("Enter the Google email address they'll sign in with.");
              const r = await act.run(() => createInvite(email.trim()));
              if (!r.ok) return;
              setLink(r.value);
              setEmail("");
              toast("Invite created.");
            }}
          >
            <input className="inp" type="email" placeholder="their@gmail.com" aria-label="Email to invite" value={email} onChange={(e) => setEmail(e.target.value)} />
            <button type="submit" className="btn" disabled={act.busy}>
              Create invite link
            </button>
          </form>
          {act.error && (
            <div className="result bad" role="alert">
              {act.error}
            </div>
          )}
          {link && (
            <Field label={`Invite link for ${link.email}`} hint={`Send it to them yourself. It's shown only now, works once, and expires ${dayShort(dayIST(link.expires_at))}. They must sign in with exactly this email.`}>
              {() => <CopyField value={link.url} label={`Invite link for ${link.email}`} />}
            </Field>
          )}
          {h && h.invites.length > 0 && (
            <div className="list mt">
              {h.invites.map((i) => (
                <div className="li" key={i.id}>
                  <div className="mid">
                    <b>{i.email}</b>
                    <small>
                      <span className="cap">{i.status}</span> · sent {dayShort(dayIST(i.created_at))}
                      {i.status === "pending" ? ` · expires ${dayShort(dayIST(i.expires_at))}` : ""}
                    </small>
                  </div>
                  {i.status === "pending" && (
                    <button
                      type="button"
                      className="linkish"
                      disabled={act.busy}
                      onClick={async () => {
                        if (!(await act.run(() => revokeInvite(i.id))).ok) return;
                        // The link shown once belongs to the newest invite for that email; it's dead now.
                        if (link?.email === i.email) setLink(null);
                        toast(`Invite for ${i.email} revoked.`);
                      }}
                    >
                      Revoke
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </>
      ) : (
        <InlineState>Only the household admin can invite people.</InlineState>
      )}
    </div>
  );
}
