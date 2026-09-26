import { useEffect, useState } from "react";
import { CopyField, Field, SecretInput, useAction } from "../../components/forms";
import { useToast } from "../../components/Toast";
import { InlineState, Loading } from "../../components/ui";
import { invalidate, read } from "../../lib/api";
import { dayShort, plural } from "../../lib/format";
import { clearStatementPassword, inviteMember, revokeInvite, setStatementPassword, setup, startBackfill, type PasswordSlot } from "../../lib/setup";
import { useStore } from "../../lib/useStore";

/** Step 5 / Settings: statement PDF passwords. Write-only: the page only ever learns whether one is set. */
export function StatementPasswords({ onContinue }: { onContinue?: () => void }) {
  useStore();
  const st = read(setup.passwords());
  return (
    <div className="form">
      <p className="sub">
        Banks email statements as password-protected PDFs. Each password is stored encrypted on your household's server, used only to open that bank's
        statements, and never shown again.
      </p>
      {st.status === "loading" ? (
        <Loading card={false} />
      ) : st.status === "error" ? (
        <InlineState>{st.error.message}</InlineState>
      ) : !st.data ? (
        <InlineState>Statement passwords aren't available on the server yet.</InlineState>
      ) : st.data.length ? (
        <div className="list">
          {st.data.map((slot) => (
            <PasswordRow key={slot.account_id} slot={slot} />
          ))}
        </div>
      ) : (
        <InlineState>Add your accounts first; each one gets its own statement password here.</InlineState>
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

function PasswordRow({ slot }: { slot: PasswordSlot }) {
  const toast = useToast();
  const [value, setValue] = useState("");
  const act = useAction();
  return (
    <form
      className="li li-col"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!value) return;
        const r = await act.run(() => setStatementPassword(slot.account_id, value));
        setValue("");
        if (r.ok) toast(`Saved the statement password for ${slot.account_label}.`);
      }}
    >
      <div className="li-row">
        <div className="mid">
          <b>{slot.account_label}</b>
          <small>{slot.set ? `Saved${slot.updated_at ? ` on ${dayShort(slot.updated_at.slice(0, 10))}` : ""}` : "Not set"}</small>
        </div>
        {slot.set && (
          <button type="button" className="linkish" disabled={act.busy} onClick={() => act.run(() => clearStatementPassword(slot.account_id))}>
            Remove
          </button>
        )}
      </div>
      <div className="row-form">
        <SecretInput value={value} onChange={setValue} placeholder={slot.set ? "Replace password" : "Statement password"} label={`Statement password for ${slot.account_label}`} />
        <button type="submit" className="btn ghost" disabled={act.busy || !value}>
          {slot.set ? "Replace" : "Save"}
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

/** Step 6: the first import. Polls GET /api/onboarding every 2 s while the server reports it running. */
export function Backfill({ onDone }: { onDone: () => void }) {
  useStore();
  const st = read(setup.onboarding());
  const act = useAction();
  const b = st.status === "ready" ? (st.data?.backfill ?? null) : null;
  const running = b?.status === "running";
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => invalidate(["/api/onboarding"]), 2000);
    return () => clearInterval(t);
  }, [running]);
  const pct = b?.total ? Math.min(100, Math.round((b.processed / b.total) * 100)) : null;
  return (
    <div className="form">
      <p className="sub">Tijori reads the bank emails already in your label, statements first, and files what it can. Anything it can't file lands in your Inbox.</p>
      {st.status === "ready" && !st.data ? (
        <InlineState>Importing isn't available on the server yet. You can still open your dashboard.</InlineState>
      ) : b && b.status !== "idle" ? (
        <div className="progress-box" role="status" aria-live="polite">
          <div className="pbar" aria-hidden>
            <div className={pct == null ? "indet" : undefined} style={pct == null ? undefined : { width: `${pct}%` }} />
          </div>
          <div className="sub">
            {b.status === "running" && `Reading ${b.total ? `${b.processed} of ${plural(b.total, "email")}` : `${plural(b.processed, "email")}`} · ${plural(b.txns, "transaction")} found`}
            {b.status === "done" && `Done: ${plural(b.processed, "email")} read, ${plural(b.txns, "transaction")} found.`}
            {b.status === "error" && <span className="bad">{b.message || "The import stopped. Check the mail source in Settings and try again."}</span>}
          </div>
        </div>
      ) : null}
      {act.error && (
        <div className="result bad" role="alert">
          {act.error}
        </div>
      )}
      <div className="row mt">
        {(!b || b.status === "idle" || b.status === "error") && st.status === "ready" && st.data && (
          <button type="button" className="btn" disabled={act.busy} onClick={() => act.run(startBackfill)}>
            {b?.status === "error" ? "Try again" : "Start import"}
          </button>
        )}
        <button type="button" className={b?.status === "done" || (st.status === "ready" && !st.data) ? "btn" : "btn ghost"} onClick={onDone}>
          {running ? "Continue in the background" : "Go to your dashboard"}
        </button>
      </div>
    </div>
  );
}

/** Settings → Household: members and invites. An invite link is shown once, to copy and send yourself. */
export function HouseholdPanel() {
  useStore();
  const toast = useToast();
  const st = read(setup.household());
  const [email, setEmail] = useState("");
  const [link, setLink] = useState<{ email: string; url: string; expires_at: string } | null>(null);
  const act = useAction();
  if (st.status === "loading") return <Loading card={false} />;
  if (st.status === "error") return <InlineState>{st.error.message}</InlineState>;
  if (!st.data) return <InlineState>Household management isn't available on the server yet.</InlineState>;
  const h = st.data;
  return (
    <div className="form">
      <h4 className="fh">{h.name}</h4>
      <p className="sub">Each member sees only their own money unless they choose to share. As admin you can invite people, but you can't see their data.</p>
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
      <h4 className="fh">Invite someone</h4>
      <form
        className="row-form"
        onSubmit={async (e) => {
          e.preventDefault();
          if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) return act.setError("Enter the email address they sign in to Google with.");
          const r = await act.run(() => inviteMember(email.trim()));
          if (!r.ok) return;
          setLink(r.value);
          setEmail("");
          toast("Invite created.");
        }}
      >
        <input className="inp" type="email" placeholder="their@gmail.com" aria-label="Email to invite" value={email} onChange={(e) => setEmail(e.target.value)} />
        <button type="submit" className="btn" disabled={act.busy}>
          Create invite
        </button>
      </form>
      {act.error && (
        <div className="result bad" role="alert">
          {act.error}
        </div>
      )}
      {link && (
        <Field label={`Invite link for ${link.email}`} hint={`Send it to them yourself. It works once and expires ${dayShort(link.expires_at.slice(0, 10))}.`}>
          {() => <CopyField value={link.url} label={`Invite link for ${link.email}`} />}
        </Field>
      )}
      {h.invites.length > 0 && (
        <div className="list mt">
          {h.invites.map((i) => (
            <div className="li" key={i.id}>
              <div className="mid">
                <b>{i.email}</b>
                <small>
                  <span className="cap">{i.status}</span> · sent {dayShort(i.created_at.slice(0, 10))}
                  {i.status === "pending" ? ` · expires ${dayShort(i.expires_at.slice(0, 10))}` : ""}
                </small>
              </div>
              {i.status === "pending" && (
                <button type="button" className="linkish" onClick={() => act.run(() => revokeInvite(i.id))}>
                  Revoke
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
