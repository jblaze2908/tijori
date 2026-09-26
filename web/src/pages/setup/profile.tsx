import { useState } from "react";
import { ChipsInput, Field, useAction } from "../../components/forms";
import { useToast } from "../../components/Toast";
import { InlineState, Loading } from "../../components/ui";
import { api, dataOf, read } from "../../lib/api";
import { addAccount, removeAccount, saveClassifyProfile, saveProfile, setup, type ClassifyProfile } from "../../lib/setup";
import { useStore } from "../../lib/useStore";
import type { AccountKind } from "../../lib/types";

const DAYS = Array.from({ length: 28 }, (_, i) => i + 1);

/** Step 1: name and the salary-cycle start day (every month view in the app follows it). */
export function ProfileForm({ submitLabel, onSaved }: { submitLabel: string; onSaved?: () => void }) {
  useStore();
  const toast = useToast();
  const me = dataOf(read(api.me()));
  const settings = dataOf(read(api.settings()));
  // null = untouched, so the server's value shows until the member edits it.
  const [name, setName] = useState<string | null>(null);
  const [day, setDay] = useState<number | null>(null);
  const act = useAction();
  const nameV = name ?? me?.name ?? "";
  const dayV = day ?? settings?.monthStartDay ?? 1;
  return (
    <form
      className="form"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!nameV.trim()) return act.setError("Add your name so the household can tell members apart.");
        if (!(await act.run(() => saveProfile(nameV.trim(), dayV))).ok) return;
        toast("Profile saved.");
        onSaved?.();
      }}
    >
      <Field label="Your name">{(id) => <input id={id} className="inp" value={nameV} maxLength={80} autoComplete="name" onChange={(e) => setName(e.target.value)} />}</Field>
      <Field label="Your month starts on" hint="Pick your salary day so each month runs payday to payday. Day 1 means calendar months.">
        {(id) => (
          <select id={id} className="sel" value={dayV} onChange={(e) => setDay(Number(e.target.value))}>
            {DAYS.map((d) => (
              <option key={d} value={d}>
                {d === 1 ? "1st (calendar months)" : `${d}${d % 10 === 2 && d !== 12 ? "nd" : d % 10 === 3 && d !== 13 ? "rd" : "th"} of the month`}
              </option>
            ))}
          </select>
        )}
      </Field>
      {act.error && <div className="result bad" role="alert">{act.error}</div>}
      <div className="row mt">
        <button type="submit" className="btn" disabled={act.busy}>
          {act.busy ? "Saving…" : submitLabel}
        </button>
      </div>
    </form>
  );
}

const KINDS: [AccountKind, string][] = [
  ["bank", "Bank account"],
  ["card", "Credit card"],
  ["wallet", "Wallet"],
  ["deposit", "Fixed deposit"],
];
const last4 = (s: string) => (/^\d{4}$/.test(s.replace(/\D/g, "").slice(-4)) ? s.replace(/\D/g, "").slice(-4) : null);
const vpa = (s: string) => (/^[\w.-]{2,}@[\w.-]{2,}$/.test(s.toLowerCase()) ? s.toLowerCase() : null);

/** Step 2: banks and cards, plus the facts that let Tijori recognise money moving between your own accounts. */
export function AccountsForm({ onContinue }: { onContinue?: () => void }) {
  useStore();
  const toast = useToast();
  const accounts = read(api.accounts());
  const profileState = read(setup.classifyProfile());
  const [draft, setDraft] = useState({ institution: "", name: "", kind: "bank" as AccountKind, mask: "" });
  const [edit, setEdit] = useState<ClassifyProfile | null>(null);
  const [confirm, setConfirm] = useState<number | null>(null);
  const add = useAction();
  const save = useAction();
  const list = dataOf(accounts) ?? [];
  const profile = edit ?? (profileState.status === "ready" ? profileState.data : null);
  const empty: ClassifyProfile = { own_names: [], own_vpas: [], own_account_masks: [], investment_account_masks: [], employer_patterns: [] };
  const p = profile ?? empty;
  const set = (k: keyof ClassifyProfile, v: string[]) => setEdit({ ...p, [k]: v });
  const masksFromAccounts = list.map((a) => a.mask).filter((m): m is string => !!m && !p.own_account_masks.includes(m));

  return (
    <div className="form">
      <h4 className="fh">Banks and cards</h4>
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
                <small className="cap">{a.kind}</small>
              </div>
              {confirm === a.id ? (
                <span className="row">
                  <button type="button" className="btn ghost danger" onClick={() => add.run(() => removeAccount(a.id)).then(() => setConfirm(null))}>
                    Remove
                  </button>
                  <button type="button" className="btn ghost" onClick={() => setConfirm(null)}>
                    Keep
                  </button>
                </span>
              ) : (
                <button type="button" className="linkish" onClick={() => setConfirm(a.id)}>
                  Remove
                </button>
              )}
            </div>
          ))}
        </div>
      ) : (
        <InlineState>No accounts yet. Add the ones whose alerts and statements reach your mail.</InlineState>
      )}
      <form
        className="row-form"
        onSubmit={async (e) => {
          e.preventDefault();
          const mask = last4(draft.mask);
          if (!draft.institution.trim() || !mask) return add.setError("Add the bank's name and the last 4 digits.");
          const r = await add.run(() => addAccount({ institution: draft.institution.trim(), name: draft.name.trim() || null, kind: draft.kind, mask }));
          if (r.ok) setDraft({ institution: "", name: "", kind: draft.kind, mask: "" });
        }}
      >
        <input className="inp" placeholder="Bank or card issuer" aria-label="Bank or card issuer" value={draft.institution} maxLength={80} onChange={(e) => setDraft({ ...draft, institution: e.target.value })} />
        <select className="sel" aria-label="Account type" value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value as AccountKind })}>
          {KINDS.map(([k, l]) => (
            <option key={k} value={k}>
              {l}
            </option>
          ))}
        </select>
        <input className="inp inp-4" placeholder="Last 4 digits" aria-label="Last 4 digits" inputMode="numeric" maxLength={4} value={draft.mask} onChange={(e) => setDraft({ ...draft, mask: e.target.value.replace(/\D/g, "") })} />
        <input className="inp" placeholder="Nickname (optional)" aria-label="Nickname" value={draft.name} maxLength={80} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
        <button type="submit" className="btn ghost" disabled={add.busy}>
          Add
        </button>
      </form>
      {add.error && <div className="result bad" role="alert">{add.error}</div>}

      <h4 className="fh">So Tijori can spot your own transfers</h4>
      <p className="sub">Money moving between your own accounts isn't spending. Tijori matches these against bank narrations. Only the last 4 digits of an account number, never the whole number.</p>
      {profileState.status === "ready" && !profileState.data ? (
        <InlineState>This part isn't available on the server yet.</InlineState>
      ) : (
        <>
          <Field label="Your name as banks print it" hint="For example the name on your account, as it shows in UPI and NEFT narrations.">
            {(id) => <ChipsInput id={id} values={p.own_names} onChange={(v) => set("own_names", v)} placeholder="Type a name, press Enter" normalize={(s) => s.toUpperCase().replace(/\s+/g, " ")} />}
          </Field>
          <Field label="Your UPI handles">{(id) => <ChipsInput id={id} values={p.own_vpas} onChange={(v) => set("own_vpas", v)} placeholder="name@bank" normalize={vpa} />}</Field>
          <Field
            label="Last 4 digits of your own accounts"
            hint={
              masksFromAccounts.length ? (
                <button type="button" className="linkish nm" onClick={() => set("own_account_masks", [...p.own_account_masks, ...masksFromAccounts])}>
                  Add {masksFromAccounts.map((m) => `••${m}`).join(", ")} from your accounts
                </button>
              ) : undefined
            }
          >
            {(id) => <ChipsInput id={id} values={p.own_account_masks} onChange={(v) => set("own_account_masks", v)} placeholder="1234" normalize={last4} />}
          </Field>
          <Field label="Employer names (optional)" hint="Credits from these are filed as salary.">
            {(id) => <ChipsInput id={id} values={p.employer_patterns} onChange={(v) => set("employer_patterns", v)} placeholder="As it appears on your salary credit" normalize={(s) => s.toUpperCase()} />}
          </Field>
          {save.error && <div className="result bad" role="alert">{save.error}</div>}
        </>
      )}
      <div className="row mt">
        {profileState.status === "ready" && profileState.data && (
          <button
            type="button"
            className="btn"
            disabled={save.busy || !edit}
            onClick={async () => {
              if (!edit) return;
              if (!(await save.run(() => saveClassifyProfile(edit))).ok) return;
              setEdit(null);
              toast("Saved.");
            }}
          >
            {save.busy ? "Saving…" : "Save"}
          </button>
        )}
        {onContinue && (
          <button type="button" className={edit ? "btn ghost" : "btn"} onClick={onContinue}>
            Continue
          </button>
        )}
      </div>
    </div>
  );
}
