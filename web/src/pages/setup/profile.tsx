import { useState } from "react";
import { ChipsInput, Field, useAction } from "../../components/forms";
import { useToast } from "../../components/Toast";
import { InlineState, Loading } from "../../components/ui";
import { api, dataOf, read } from "../../lib/api";
import { saveClassifyProfile, saveMonthStart, setup, type ClassifyProfile } from "../../lib/setup";
import { useStore } from "../../lib/useStore";

const DAYS = Array.from({ length: 28 }, (_, i) => i + 1);
const ordinal = (d: number) => `${d}${d % 10 === 1 && d !== 11 ? "st" : d % 10 === 2 && d !== 12 ? "nd" : d % 10 === 3 && d !== 13 ? "rd" : "th"}`;
const EMPTY: ClassifyProfile = { own_names: [], own_vpas: [], own_account_masks: [], investment_account_masks: [], employer_patterns: [] };

// docs/api.md limits, applied as the member types so a PUT never bounces.
const last4 = (s: string) => (/^\d{4}$/.test(s.replace(/\D/g, "").slice(-4)) ? s.replace(/\D/g, "").slice(-4) : null);
const vpa = (s: string) => (/^[\w.-]{2,}@[\w.-]{2,}$/.test(s.toLowerCase()) && s.length <= 120 ? s.toLowerCase() : null);
const name = (s: string) => {
  const v = s.toUpperCase().replace(/\s+/g, " ").trim();
  return v.length >= 1 && v.length <= 120 ? v : null;
};
const employer = (s: string) => {
  const v = s.trim();
  return v.length >= 3 && v.length <= 80 ? v.toUpperCase() : null;
};

/** The month start day. The name is Google's and can't be edited here. */
export function MonthStartField({ value, onChange }: { value: number; onChange: (d: number) => void }) {
  return (
    <Field label="Your month starts on" hint="Pick your salary day so each month runs payday to payday. The 1st means calendar months.">
      {(id) => (
        <select id={id} className="sel" value={value} onChange={(e) => onChange(Number(e.target.value))}>
          {DAYS.map((d) => (
            <option key={d} value={d}>
              {d === 1 ? "1st (calendar months)" : `${ordinal(d)} of the month`}
            </option>
          ))}
        </select>
      )}
    </Field>
  );
}

/** Own names, UPI handles and account digits, so money moving between your own accounts isn't counted as spending. */
function ClassifyFields({ p, set }: { p: ClassifyProfile; set: (k: keyof ClassifyProfile, v: string[]) => void }) {
  return (
    <>
      <Field label="Your name as banks print it" hint="As it shows in UPI and NEFT narrations. A long enough prefix is fine, since banks cut names short.">
        {(id) => <ChipsInput id={id} values={p.own_names} onChange={(v) => set("own_names", v.slice(0, 10))} placeholder="Type a name, press Enter" normalize={name} />}
      </Field>
      <Field label="Your UPI handles">{(id) => <ChipsInput id={id} values={p.own_vpas} onChange={(v) => set("own_vpas", v.slice(0, 20))} placeholder="name@bank" normalize={vpa} />}</Field>
      <Field label="Last 4 digits of your own accounts" hint="Only the last 4 digits, never a whole account number.">
        {(id) => <ChipsInput id={id} values={p.own_account_masks} onChange={(v) => set("own_account_masks", v.slice(0, 20))} placeholder="1234" normalize={last4} />}
      </Field>
      <Field label="Last 4 digits of investment accounts (optional)" hint="Money sent to these is filed as investing, not spending.">
        {(id) => <ChipsInput id={id} values={p.investment_account_masks} onChange={(v) => set("investment_account_masks", v.slice(0, 20))} placeholder="5555" normalize={last4} />}
      </Field>
      <Field label="Employer names (optional)" hint="Credits whose narration contains one of these are filed as salary.">
        {(id) => <ChipsInput id={id} values={p.employer_patterns} onChange={(v) => set("employer_patterns", v.slice(0, 10))} placeholder="As it appears on your salary credit" normalize={employer} />}
      </Field>
    </>
  );
}

function useClassifyDraft() {
  const st = read(setup.classifyProfile());
  const [edit, setEdit] = useState<ClassifyProfile | null>(null);
  const server = st.status === "ready" ? st.data : null;
  const p = edit ?? server ?? EMPTY;
  return { st, p, dirty: edit != null, set: (k: keyof ClassifyProfile, v: string[]) => setEdit({ ...p, [k]: v }), reset: () => setEdit(null) };
}

/** Onboarding step 1: the month start day plus the self-transfer profile, saved together. */
export function ProfileStep({ onSaved }: { onSaved: () => void }) {
  useStore();
  const toast = useToast();
  const me = dataOf(read(api.me()));
  const settings = dataOf(read(api.settings()));
  const [day, setDay] = useState<number | null>(null);
  const c = useClassifyDraft();
  const act = useAction();
  const dayV = day ?? settings?.monthStartDay ?? 1;
  return (
    <form
      className="form"
      onSubmit={async (e) => {
        e.preventDefault();
        const classify = c.st.status === "ready" && !!c.st.data;
        if (classify && !c.p.own_names.length) return act.setError("Add at least your name as banks print it, so transfers between your own accounts are recognised.");
        const r = await act.run(async () => {
          if (day != null && day !== settings?.monthStartDay) await saveMonthStart(day);
          if (classify && c.dirty) await saveClassifyProfile(c.p);
        });
        if (!r.ok) return;
        c.reset();
        toast("Profile saved.");
        onSaved();
      }}
    >
      {me && (
        <p className="sub">
          Signed in as <b className="t1">{me.name}</b> ({me.email}). Your name comes from your Google account.
        </p>
      )}
      <MonthStartField value={dayV} onChange={setDay} />
      <h4 className="fh">So Tijori can spot your own transfers</h4>
      {c.st.status === "loading" ? <Loading card={false} /> : c.st.status === "ready" && !c.st.data ? <InlineState>This part isn't available on the server yet.</InlineState> : <ClassifyFields p={c.p} set={c.set} />}
      {act.error && (
        <div className="result bad" role="alert">
          {act.error}
        </div>
      )}
      <div className="row mt">
        <button type="submit" className="btn" disabled={act.busy}>
          {act.busy ? "Saving…" : "Save and continue"}
        </button>
      </div>
    </form>
  );
}

/** Settings → General: the month start day on its own. */
export function MonthStartForm() {
  useStore();
  const toast = useToast();
  const settings = dataOf(read(api.settings()));
  const [day, setDay] = useState<number | null>(null);
  const act = useAction();
  const dayV = day ?? settings?.monthStartDay ?? 1;
  return (
    <form
      className="form"
      onSubmit={async (e) => {
        e.preventDefault();
        if ((await act.run(() => saveMonthStart(dayV))).ok) {
          setDay(null);
          toast("Saved.");
        }
      }}
    >
      <MonthStartField value={dayV} onChange={setDay} />
      {act.error && (
        <div className="result bad" role="alert">
          {act.error}
        </div>
      )}
      <div className="row">
        <button type="submit" className="btn" disabled={act.busy || day == null}>
          {act.busy ? "Saving…" : "Save"}
        </button>
      </div>
    </form>
  );
}

/** Settings → Accounts: the accounts Tijori has seen (they come from statements) and the self-transfer profile. */
export function AccountsSettings() {
  useStore();
  const toast = useToast();
  const accounts = read(api.accounts());
  const c = useClassifyDraft();
  const act = useAction();
  const list = dataOf(accounts) ?? [];
  const suggest = list.map((a) => a.mask).filter((m): m is string => !!m && !c.p.own_account_masks.includes(m));
  return (
    <div className="form">
      <h4 className="fh">Accounts</h4>
      {accounts.status === "loading" ? (
        <Loading card={false} />
      ) : accounts.status === "error" ? (
        <InlineState>{accounts.error.message}</InlineState>
      ) : list.length ? (
        <div className="list">
          {list.map((a) => (
            <div className="li" key={a.id}>
              <div className="mid">
                <b>{a.label}</b>
                <small className="cap">
                  {a.kind} · {a.txn_count} transactions{a.has_statement_password ? " · statement password saved" : ""}
                </small>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <InlineState>Accounts appear here after the first statement from each bank is uploaded.</InlineState>
      )}
      <h4 className="fh">So Tijori can spot your own transfers</h4>
      {c.st.status === "loading" ? (
        <Loading card={false} />
      ) : c.st.status === "ready" && !c.st.data ? (
        <InlineState>This part isn't available on the server yet.</InlineState>
      ) : (
        <>
          <ClassifyFields p={c.p} set={c.set} />
          {suggest.length > 0 && (
            <button type="button" className="linkish nm" onClick={() => c.set("own_account_masks", [...c.p.own_account_masks, ...suggest].slice(0, 20))}>
              Add {suggest.map((m) => `••${m}`).join(", ")} from your accounts
            </button>
          )}
          {act.error && (
            <div className="result bad" role="alert">
              {act.error}
            </div>
          )}
          <div className="row">
            <button
              type="button"
              className="btn"
              disabled={act.busy || !c.dirty}
              onClick={async () => {
                if ((await act.run(() => saveClassifyProfile(c.p))).ok) {
                  c.reset();
                  toast("Saved. It applies to statements uploaded from now on.");
                }
              }}
            >
              {act.busy ? "Saving…" : "Save"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
