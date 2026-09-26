import { useState } from "react";
import { CopyField, Field, SecretInput, useAction } from "../../components/forms";
import { useToast } from "../../components/Toast";
import { InlineState, Loading } from "../../components/ui";
import { dataOf, read } from "../../lib/api";
import { dayIST, dayShort, plural, timeIST } from "../../lib/format";
import {
  addMailSource,
  MAIL_ERROR,
  preTestMail,
  PROVIDERS,
  removeMailSource,
  rotateMailPassword,
  setup,
  testMailSource,
  type MailSource,
  type MailTest,
  type Provider,
} from "../../lib/setup";
import { useStore } from "../../lib/useStore";

const APP_PASSWORD_URL = "https://myaccount.google.com/apppasswords";
const LABEL = "tijori";

export const testText = (t: MailTest, label: string) =>
  t.ok ? `Connected. Tijori can see ${plural(t.message_count ?? 0, "message")} in the ${label} label.` : t.error_code ? MAIL_ERROR[t.error_code] : "The test didn't pass. Try again.";

/**
 * Step 2: an IMAP source with an app password. The server tests only saved sources, so this saves, then tests;
 * a rejected password can be replaced and re-tested in place. The password field is cleared after every submit.
 */
export function MailConnect({ onConnected }: { onConnected?: (s: MailSource) => void }) {
  const toast = useToast();
  const [provider, setProvider] = useState<Provider>("gmail");
  const [host, setHost] = useState("");
  const [port, setPort] = useState(993);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [saved, setSaved] = useState<MailSource | null>(null);
  const [result, setResult] = useState<MailTest | null>(null);
  const act = useAction();
  const pre = useAction();
  const custom = provider === "custom";
  const ready = !!(password.trim() && (saved || (email.trim() && (!custom || (host.trim() && (port === 993 || (port >= 1024 && port <= 65535)))))));
  const pw = () => password.replace(/\s+/g, "");

  const body = () => ({ provider, host: host.trim(), port, email: email.trim(), app_password: pw(), label: LABEL });
  const connect = async () => {
    const r = await act.run(async () => {
      const src = saved ?? (await addMailSource(body()));
      if (saved) await rotateMailPassword(saved.id, pw());
      setSaved(src);
      return { src, test: await testMailSource(src.id) };
    });
    setPassword("");
    if (!r.ok) return;
    setResult(r.value.test);
    // A missing label still means the credentials work; the next step creates it.
    if (r.value.test.ok || r.value.test.error_code === "mailbox_not_found") {
      toast(`Connected ${r.value.src.email}.`);
      onConnected?.(r.value.src);
    }
  };

  return (
    <form
      className="form"
      onSubmit={(e) => {
        e.preventDefault();
        if (ready) void connect();
      }}
    >
      {!saved && (
        <>
          <div className="field">
            <span className="lab-t" id="prov-l">
              Mail provider
            </span>
            <div className="seg prov" role="group" aria-labelledby="prov-l">
              {(Object.keys(PROVIDERS) as Provider[]).map((p) => (
                <button type="button" key={p} className={p === provider ? "on" : ""} aria-pressed={p === provider} onClick={() => setProvider(p)}>
                  {PROVIDERS[p].name}
                </button>
              ))}
            </div>
            {!custom && <div className="hint">Connects to {PROVIDERS[provider].host}:{PROVIDERS[provider].port} over TLS, read-only.</div>}
          </div>
          {custom && (
            <div className="row-2">
              <Field label="IMAP server" hint="A public host name, not an IP address.">
                {(id) => <input id={id} className="inp" value={host} onChange={(e) => setHost(e.target.value)} spellCheck={false} placeholder="imap.example.com" />}
              </Field>
              <Field label="Port" hint="993, or 1024–65535">
                {(id) => <input id={id} className="inp inp-4" inputMode="numeric" value={port} onChange={(e) => setPort(Number(e.target.value.replace(/\D/g, "")) || 0)} />}
              </Field>
            </div>
          )}
          <Field label="Email address">{(id) => <input id={id} className="inp" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@gmail.com" />}</Field>
        </>
      )}
      <Field
        label={saved ? `New app password for ${saved.email}` : "App password"}
        hint="Not your normal password. It's sealed on your household's server and never shown again."
      >
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
          <p>Open your mail account's security settings and create an app password for IMAP (some providers call it an "app-specific password"). Paste it above.</p>
        )}
        <p className="sub">
          An app password can read all your mail, but Tijori only ever opens the <b>{LABEL}</b> label, read-only. Revoke it from your account at any
          time and Tijori simply stops syncing.
        </p>
      </details>
      {result && (
        <div className={`result ${result.ok ? "ok" : "bad"}`} role="status">
          {testText(result, LABEL)}
          {result.ok && !saved && " Nothing is saved yet: Save and connect to keep it."}
        </div>
      )}
      {act.error && (
        <div className="result bad" role="alert">
          {act.error}
        </div>
      )}
      {pre.error && (
        <div className="result bad" role="alert">
          {pre.error}
        </div>
      )}
      <div className="row mt">
        {!saved && (
          <button
            type="button"
            className="btn ghost"
            disabled={!ready || pre.busy || act.busy}
            onClick={async () => {
              setResult(null);
              // Stores nothing. Only a passing password stays in the field, so the member can save it next.
              const r = await pre.run(() => preTestMail(body()));
              if (r.ok) setResult(r.value);
              if (!r.ok || !r.value.ok) setPassword("");
            }}
          >
            {pre.busy ? "Testing…" : "Test connection"}
          </button>
        )}
        <button type="submit" className="btn" disabled={!ready || act.busy}>
          {act.busy ? "Connecting…" : saved ? "Replace and test again" : "Save and connect"}
        </button>
      </div>
    </form>
  );
}

const STATUS_COLOR: Record<MailSource["status"], string> = { ok: "var(--in)", untested: "var(--warn)", error: "var(--bad)" };
const STATUS_TEXT: Record<MailSource["status"], string> = { ok: "Working", untested: "Not tested", error: "Failing" };

/** Settings → Mail sources: status, last test, test again, rotate the app password, remove. */
export function MailSources() {
  useStore();
  const toast = useToast();
  const st = read(setup.mailSources());
  const [adding, setAdding] = useState(false);
  const [rotating, setRotating] = useState<number | null>(null);
  const [confirm, setConfirm] = useState<number | null>(null);
  const [tests, setTests] = useState<Record<number, MailTest>>({});
  const [secret, setSecret] = useState("");
  const act = useAction();
  if (st.status === "loading") return <Loading card={false} />;
  if (st.status === "error") return <InlineState>{st.error.message}</InlineState>;
  if (!st.data) return <InlineState>Mail sources aren't available on the server yet.</InlineState>;
  const test = async (s: MailSource) => {
    const r = await act.run(() => testMailSource(s.id));
    if (r.ok) setTests({ ...tests, [s.id]: r.value });
  };
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
                  {STATUS_TEXT[s.status]}
                </span>
              </div>
              <div className="sub">
                {s.last_tested_at ? `Last tested ${dayShort(dayIST(s.last_tested_at))}, ${timeIST(s.last_tested_at)}` : "Not tested yet"}
                {s.last_error_code && <span className="bad"> · {MAIL_ERROR[s.last_error_code]}</span>}
              </div>
              {tests[s.id] && (
                <div className={`result ${tests[s.id]!.ok ? "ok" : "bad"}`} role="status">
                  {testText(tests[s.id]!, s.label)}
                </div>
              )}
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
                    toast("App password replaced. Test it to confirm.");
                  }}
                >
                  <SecretInput value={secret} onChange={setSecret} placeholder="New app password" label="New app password" />
                  <button type="submit" className="btn" disabled={act.busy || !secret.trim()}>
                    Replace
                  </button>
                  <button
                    type="button"
                    className="btn ghost"
                    onClick={() => {
                      setRotating(null);
                      setSecret("");
                    }}
                  >
                    Cancel
                  </button>
                </form>
              ) : confirm === s.id ? (
                <div className="row">
                  <span className="sub">Stop syncing {s.email} and delete its sealed password?</span>
                  <button
                    type="button"
                    className="btn ghost danger"
                    onClick={async () => {
                      if ((await act.run(() => removeMailSource(s.id))).ok) {
                        setConfirm(null);
                        toast("Mail source removed.");
                      }
                    }}
                  >
                    Remove
                  </button>
                  <button type="button" className="btn ghost" onClick={() => setConfirm(null)}>
                    Keep
                  </button>
                </div>
              ) : (
                <div className="row">
                  <button type="button" className="linkish nm" disabled={act.busy} onClick={() => test(s)}>
                    Test now
                  </button>
                  <button
                    type="button"
                    className="linkish"
                    onClick={() => {
                      setRotating(s.id);
                      setConfirm(null);
                    }}
                  >
                    Rotate password
                  </button>
                  <button
                    type="button"
                    className="linkish"
                    onClick={() => {
                      setConfirm(s.id);
                      setRotating(null);
                    }}
                  >
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
          <MailConnect onConnected={() => setAdding(false)} />
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

/** Step 3: the label (and a Gmail filter feeding it), so the app password only ever sees bank mail. "Check again" re-runs the IMAP test. */
export function LabelSetup({ onContinue }: { onContinue?: () => void }) {
  useStore();
  const sources = dataOf(read(setup.mailSources())) ?? [];
  const onboarding = dataOf(read(setup.onboarding()));
  const [result, setResult] = useState<MailTest | null>(null);
  const act = useAction();
  const src = sources[0] ?? null;
  const label = src?.label ?? LABEL;
  const gmail = !src || src.provider === "gmail";
  const filter = onboarding?.gmail_filter ?? null;
  return (
    <div className="form">
      {gmail ? (
        <ol className="steps-list">
          <li>
            In Gmail, create a label named <b className="mono-t">{label}</b>.
          </li>
          <li>
            Open <b>Settings → Filters and blocked addresses → Create a new filter</b> and describe your banks' alert and statement emails (their sender
            addresses). {filter ? "Or paste this into Has the words:" : ""}
            {filter && <CopyField value={filter} label="Gmail filter query" />}
          </li>
          <li>
            Choose <b>Create filter</b>, tick <b>Apply the label: {label}</b> and <b>Also apply filter to matching conversations</b>.
          </li>
          <li>
            In <b>Settings → Labels</b>, untick <b>Show in IMAP</b> for every label except <b>{label}</b>. The app password can then only see bank mail.
          </li>
        </ol>
      ) : (
        <ol className="steps-list">
          <li>
            Create a folder named <b className="mono-t">{label}</b> in your mailbox.
          </li>
          <li>Add a rule that moves emails from your banks and card issuers into it.</li>
        </ol>
      )}
      {!src && <InlineState>Connect a mail source first; the check signs in with it.</InlineState>}
      {result && (
        <div className={`result ${result.ok ? "ok" : "bad"}`} role="status">
          {testText(result, label)}
        </div>
      )}
      {act.error && (
        <div className="result bad" role="alert">
          {act.error}
        </div>
      )}
      <div className="row mt">
        <button
          type="button"
          className="btn ghost"
          disabled={!src || act.busy}
          onClick={async () => {
            if (!src) return;
            const r = await act.run(() => testMailSource(src.id));
            if (r.ok) setResult(r.value);
          }}
        >
          {act.busy ? "Checking…" : result ? "Check again" : "Check"}
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
