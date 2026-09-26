import { useState } from "react";
import { CopyField, Field, SecretInput, useAction } from "../../components/forms";
import { useToast } from "../../components/Toast";
import { InlineState, Loading } from "../../components/ui";
import { dataOf, read } from "../../lib/api";
import { dayShort, plural } from "../../lib/format";
import { addMailSource, checkGmail, PROVIDERS, removeMailSource, rotateMailPassword, setup, testMail, type MailSource, type Provider } from "../../lib/setup";
import { useStore } from "../../lib/useStore";

const APP_PASSWORD_URL = "https://myaccount.google.com/apppasswords";

/** Step 3: an IMAP source with an app password. The password lives only in this form's state and is cleared after saving. */
export function MailConnect({ onSaved, submitLabel = "Save and continue" }: { onSaved?: (s: MailSource) => void; submitLabel?: string }) {
  const toast = useToast();
  const [provider, setProvider] = useState<Provider>("gmail");
  const [host, setHost] = useState(PROVIDERS.gmail.host);
  const [port, setPort] = useState(PROVIDERS.gmail.port);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const test = useAction();
  const save = useAction();
  const draft = () => ({ provider, host: host.trim(), port, email: email.trim(), app_password: password.replace(/\s+/g, "") });
  const ready = !!(email.trim() && password.trim() && host.trim() && port > 0);

  const pick = (p: Provider) => {
    setProvider(p);
    setHost(PROVIDERS[p].host);
    setPort(PROVIDERS[p].port);
    setResult(null);
  };

  return (
    <form
      className="form"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!ready) return;
        const r = await save.run(() => addMailSource(draft()));
        setPassword("");
        if (!r.ok) return;
        toast(`Connected ${r.value.email}.`);
        onSaved?.(r.value);
      }}
    >
      <div className="field">
        <span className="lab-t" id="prov-l">
          Mail provider
        </span>
        <div className="seg prov" role="group" aria-labelledby="prov-l">
          {(Object.keys(PROVIDERS) as Provider[]).map((p) => (
            <button type="button" key={p} className={p === provider ? "on" : ""} aria-pressed={p === provider} onClick={() => pick(p)}>
              {PROVIDERS[p].name}
            </button>
          ))}
        </div>
      </div>
      <div className="row-2">
        <Field label="IMAP server">{(id) => <input id={id} className="inp" value={host} onChange={(e) => setHost(e.target.value)} spellCheck={false} placeholder="imap.example.com" />}</Field>
        <Field label="Port">{(id) => <input id={id} className="inp inp-4" inputMode="numeric" value={port} onChange={(e) => setPort(Number(e.target.value.replace(/\D/g, "")) || 0)} />}</Field>
      </div>
      <Field label="Email address">{(id) => <input id={id} className="inp" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@gmail.com" />}</Field>
      <Field label="App password" hint="Not your normal password. It's stored encrypted on your household's server and never shown again.">
        {(id) => <SecretInput id={id} value={password} onChange={setPassword} placeholder="16 characters" />}
      </Field>
      <details className="help">
        <summary>How to create an app password</summary>
        {provider === "gmail" ? (
          <ol>
            <li>Turn on 2-Step Verification for your Google account, if it isn't on already.</li>
            <li>
              Open{" "}
              <a href={APP_PASSWORD_URL} target="_blank" rel="noopener noreferrer">
                myaccount.google.com/apppasswords
              </a>{" "}
              and sign in.
            </li>
            <li>Name it "Tijori" and choose Create.</li>
            <li>Copy the 16-character password into the field above. Spaces don't matter.</li>
          </ol>
        ) : (
          <p>Open your mail account's security settings and create an app password for IMAP. Paste it above. Some providers call it an "app-specific password".</p>
        )}
        <p className="sub">
          An app password can read all your mail, but Tijori only opens the <b>tijori</b> label, read-only. You can revoke it from your account at any time,
          and Tijori will simply stop syncing.
        </p>
      </details>
      {result && (
        <div className={`result ${result.ok ? "ok" : "bad"}`} role="status">
          {result.text}
        </div>
      )}
      {save.error && (
        <div className="result bad" role="alert">
          {save.error}
        </div>
      )}
      <div className="row mt">
        <button
          type="button"
          className="btn ghost"
          disabled={!ready || test.busy}
          onClick={async () => {
            setResult(null);
            const r = await test.run(() => testMail(draft()));
            if (!r.ok) return setResult({ ok: false, text: r.error });
            setResult(
              r.value.ok
                ? { ok: true, text: `Connected. Tijori can see ${plural(r.value.messages ?? 0, "message")} in the tijori label.` }
                : { ok: false, text: r.value.error || "The server couldn't sign in with these details." },
            );
          }}
        >
          {test.busy ? "Testing…" : "Test connection"}
        </button>
        <button type="submit" className="btn" disabled={!ready || save.busy}>
          {save.busy ? "Saving…" : submitLabel}
        </button>
      </div>
    </form>
  );
}

const STATUS_COLOR = { active: "var(--in)", paused: "var(--warn)", error: "var(--bad)" } as const;

/** Settings → Mail sources: status, last sync, rotate the app password, remove. */
export function MailSources() {
  useStore();
  const toast = useToast();
  const st = read(setup.mailSources());
  const [adding, setAdding] = useState(false);
  const [rotating, setRotating] = useState<number | null>(null);
  const [confirm, setConfirm] = useState<number | null>(null);
  const [secret, setSecret] = useState("");
  const act = useAction();
  if (st.status === "loading") return <Loading card={false} />;
  if (st.status === "error") return <InlineState>{st.error.message}</InlineState>;
  if (!st.data) return <InlineState>Mail sources aren't available on the server yet.</InlineState>;
  return (
    <div className="form">
      {st.data.length ? (
        <div className="list">
          {st.data.map((s) => (
            <div className="li li-col" key={s.id}>
              <div className="li-row">
                <div className="mid">
                  <b>{s.email}</b>
                  <small>
                    {PROVIDERS[s.provider]?.name ?? s.provider} · {s.host}:{s.port} · label <b className="mono-t">{s.label}</b>
                  </small>
                </div>
                <span className="health">
                  <i style={{ background: STATUS_COLOR[s.status] }} aria-hidden />
                  <span className="cap">{s.status}</span>
                </span>
              </div>
              <div className="sub">
                {s.last_sync_at ? `Last sync ${dayShort(s.last_sync_at.slice(0, 10))} ${s.last_sync_at.slice(11, 16)}` : "Not synced yet"} · {plural(s.messages_seen, "message")} read
                {s.last_error && <span className="bad"> · {s.last_error}</span>}
              </div>
              {rotating === s.id ? (
                <form
                  className="row-form"
                  onSubmit={async (e) => {
                    e.preventDefault();
                    if (!secret.trim()) return;
                    const r = await act.run(() => rotateMailPassword(s.id, secret.replace(/\s+/g, "")));
                    setSecret("");
                    if (!r.ok) return;
                    setRotating(null);
                    toast("App password replaced.");
                  }}
                >
                  <SecretInput value={secret} onChange={setSecret} placeholder="New app password" label="New app password" />
                  <button type="submit" className="btn" disabled={act.busy || !secret.trim()}>
                    Replace
                  </button>
                  <button type="button" className="btn ghost" onClick={() => (setRotating(null), setSecret(""))}>
                    Cancel
                  </button>
                </form>
              ) : confirm === s.id ? (
                <div className="row">
                  <span className="sub">Stop syncing {s.email} and delete its stored password?</span>
                  <button type="button" className="btn ghost danger" onClick={() => act.run(() => removeMailSource(s.id)).then((r) => r.ok && (setConfirm(null), toast("Mail source removed.")))}>
                    Remove
                  </button>
                  <button type="button" className="btn ghost" onClick={() => setConfirm(null)}>
                    Keep
                  </button>
                </div>
              ) : (
                <div className="row">
                  <button type="button" className="linkish nm" onClick={() => (setRotating(s.id), setConfirm(null))}>
                    Rotate password
                  </button>
                  <button type="button" className="linkish" onClick={() => (setConfirm(s.id), setRotating(null))}>
                    Remove
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <InlineState>No mail connected yet.</InlineState>
      )}
      {act.error && (
        <div className="result bad" role="alert">
          {act.error}
        </div>
      )}
      {adding ? (
        <div className="card-in">
          <MailConnect submitLabel="Save" onSaved={() => setAdding(false)} />
        </div>
      ) : (
        <div className="row mt">
          <button type="button" className="btn ghost" onClick={() => setAdding(true)}>
            Add a mail source
          </button>
        </div>
      )}
    </div>
  );
}

/** Step 4: the Gmail filter and label, so the app password only ever sees bank mail. */
export function GmailSetup({ onContinue }: { onContinue?: () => void }) {
  useStore();
  const st = read(setup.onboarding());
  const sources = dataOf(read(setup.mailSources())) ?? [];
  const act = useAction();
  const o = st.status === "ready" ? st.data : null;
  const label = o?.label || "tijori";
  const gmail = !sources.length || sources.some((s) => s.provider === "gmail");
  const check = o?.gmail_check;
  return (
    <div className="form">
      {gmail ? (
        <ol className="steps-list">
          <li>
            In Gmail, create a label named <b className="mono-t">{label}</b>.
          </li>
          <li>
            Open <b>Settings → Filters and blocked addresses → Create a new filter</b>. Paste this into <b>Has the words</b>:
            {o?.gmail_filter ? <CopyField value={o.gmail_filter} label="Gmail filter query" /> : <InlineState>The filter query appears here once the server provides it.</InlineState>}
          </li>
          <li>
            Choose <b>Create filter</b>, tick <b>Apply the label: {label}</b> and <b>Also apply filter to matching conversations</b>.
          </li>
          <li>
            In <b>Settings → Labels</b>, untick <b>Show in IMAP</b> for every label except <b>{label}</b>. That limits what the app password can see to bank
            mail.
          </li>
        </ol>
      ) : (
        <ol className="steps-list">
          <li>
            Create a folder named <b className="mono-t">{label}</b> in your mailbox.
          </li>
          <li>Add a rule that moves emails from your banks and card issuers into it.</li>
          {o?.gmail_filter && (
            <li>
              These are the senders and subjects Tijori reads:
              <CopyField value={o.gmail_filter} label="Senders and subjects" />
            </li>
          )}
        </ol>
      )}
      {check && (
        <div className={`result ${check.label_found ? "ok" : "bad"}`} role="status">
          {check.label_found
            ? `Found the ${label} label with ${plural(check.messages, "message")}.`
            : `The ${label} label isn't visible over IMAP yet. Check the label name and its "Show in IMAP" setting.`}
        </div>
      )}
      {act.error && (
        <div className="result bad" role="alert">
          {act.error}
        </div>
      )}
      <div className="row mt">
        <button type="button" className="btn ghost" disabled={act.busy} onClick={() => act.run(checkGmail)}>
          {act.busy ? "Checking…" : check ? "Check again" : "Check"}
        </button>
        {onContinue && (
          <button type="button" className="btn" onClick={onContinue}>
            Continue
          </button>
        )}
      </div>
    </div>
  );
}
