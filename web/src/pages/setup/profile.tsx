import { useState } from "react";
import { ChipsInput, Field, useAction } from "../../components/forms";
import { useToast } from "../../components/Toast";
import { InlineState, Loading } from "../../components/ui";
import { api, dataOf, read } from "../../lib/api";
import { plural } from "../../lib/format";
import { CLASS_LABEL, CLASSES, KIND_LABEL, kindLine, KINDS, PRESETS, SUBTYPES } from "../../lib/accounts";
import { addAccount, removeAccount, saveClassifyProfile, saveMonthStart, saveName, setup, updateAccount, type AccountFields, type ClassifyProfile } from "../../lib/setup";
import { useStore } from "../../lib/useStore";
import type { Account, AccountKind, AssetClass } from "../../lib/types";

const DAYS = Array.from({ length: 28 }, (_, i) => i + 1);
const ordinal = (d: number) => `${d}${d % 10 === 1 && d !== 11 ? "st" : d % 10 === 2 && d !== 12 ? "nd" : d % 10 === 3 && d !== 13 ? "rd" : "th"}`;
const EMPTY: ClassifyProfile = { own_names: [], own_vpas: [], own_account_masks: [], investment_account_masks: [], employer_patterns: [] };

// docs/api.md limits, applied as the member types so a PUT never bounces.
const last4 = (s: string) => (/^\d{4}$/.test(s.replace(/\D/g, "").slice(-4)) ? s.replace(/\D/g, "").slice(-4) : null);
const vpa = (s: string) => (/^[\w.-]{2,}@[\w.-]{2,}$/.test(s.toLowerCase()) && s.length <= 120 ? s.toLowerCase() : null);
const bankName = (s: string) => {
  const v = s.toUpperCase().replace(/\s+/g, " ").trim();
  return v.length >= 1 && v.length <= 120 ? v : null;
};
const employer = (s: string) => {
  const v = s.trim();
  return v.length >= 3 && v.length <= 80 ? v.toUpperCase() : null;
};

/** Name (PATCH /api/me) and month start day (PATCH /api/settings): onboarding step 1 and Settings → General. */
export function ProfileForm({ submitLabel, onSaved }: { submitLabel: string; onSaved?: () => void }) {
  useStore();
  const toast = useToast();
  const me = dataOf(read(api.me()));
  const settings = dataOf(read(api.settings()));
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
        const n = nameV.trim();
        if (n.length < 1 || n.length > 120) return act.setError("Add your name (up to 120 characters).");
        const r = await act.run(async () => {
          if (me && n !== me.name) await saveName(n);
          if (settings && dayV !== settings.monthStartDay) await saveMonthStart(dayV);
        });
        if (!r.ok) return;
        setName(null);
        setDay(null);
        toast("Saved.");
        onSaved?.();
      }}
    >
      {me && <p className="sub">Signed in with Google as {me.email}.</p>}
      <Field label="Your name">{(id) => <input id={id} className="inp" value={nameV} maxLength={120} autoComplete="name" onChange={(e) => setName(e.target.value)} />}</Field>
      <Field label="Your month starts on" hint="Pick your salary day so each month runs payday to payday. The 1st means calendar months. Every month in Tijori follows this.">
        {(id) => (
          <select id={id} className="sel" value={dayV} onChange={(e) => setDay(Number(e.target.value))}>
            {DAYS.map((d) => (
              <option key={d} value={d}>
                {d === 1 ? "1st (calendar months)" : `${ordinal(d)} of the month`}
              </option>
            ))}
          </select>
        )}
      </Field>
      {act.error && (
        <div className="result bad" role="alert">
          {act.error}
        </div>
      )}
      <div className="row mt">
        <button type="submit" className="btn" disabled={act.busy}>
          {act.busy ? "Saving…" : submitLabel}
        </button>
      </div>
    </form>
  );
}

/** "Bank · 6 transactions · Cash & savings, liquid", leaving out whatever the name already says. */
function accountLine(a: Account): string {
  const what = kindLine(a.kind, a.subtype, a.label);
  const cls = CLASS_LABEL[a.asset_class];
  const counts = a.in_net_worth ? [cls, a.liquid ? "liquid" : ""].filter((x) => x && !a.label.toLowerCase().includes(x.toLowerCase()) && x !== what).join(", ") : "not in net worth";
  return [what, a.txn_count ? plural(a.txn_count, "transaction") : "", counts].filter(Boolean).join(" · ");
}

/**
 * Everything you own or owe, one account each: statements for the same institution and last 4 digits land on a
 * declared account later, and each account's settings decide how it counts in net worth.
 */
function AccountsEditor() {
  useStore();
  const toast = useToast();
  const accounts = read(api.accounts());
  const [draft, setDraft] = useState({ institution: "", name: "", kind: "bank" as AccountKind, subtype: "", mask: "" });
  const [editing, setEditing] = useState<number | null>(null);
  const [confirm, setConfirm] = useState<number | null>(null);
  const act = useAction();
  const list = dataOf(accounts) ?? [];
  const subtypes = SUBTYPES[draft.kind];
  const has = (kind: AccountKind, subtype?: string) => list.some((a) => a.kind === kind && (subtype == null || a.subtype === subtype));
  return (
    <>
      {accounts.status === "loading" ? (
        <Loading card={false} />
      ) : accounts.status === "error" ? (
        <InlineState>{accounts.error.message}</InlineState>
      ) : list.length ? (
        <div className="list">
          {list.map((a) => (
            <div className="li" key={a.id} style={editing === a.id ? { flexDirection: "column", alignItems: "stretch" } : undefined}>
              {editing === a.id ? (
                <AccountForm a={a} done={() => setEditing(null)} />
              ) : (
                <>
                  <div className="mid">
                    <b>{a.label}</b>
                    <small className="sub">
                      {accountLine(a)}
                    </small>
                  </div>
                  {confirm === a.id ? (
                    <span className="row">
                      <button
                        type="button"
                        className="btn ghost danger"
                        onClick={async () => {
                          if ((await act.run(() => removeAccount(a.id))).ok) toast("Account removed.");
                          setConfirm(null);
                        }}
                      >
                        Remove
                      </button>
                      <button type="button" className="btn ghost" onClick={() => setConfirm(null)}>
                        Keep
                      </button>
                    </span>
                  ) : (
                    <span className="row">
                      <button type="button" className="linkish nm" onClick={() => setEditing(a.id)}>
                        Edit
                      </button>
                      <button type="button" className="linkish" onClick={() => setConfirm(a.id)}>
                        Remove
                      </button>
                    </span>
                  )}
                </>
              )}
            </div>
          ))}
        </div>
      ) : (
        <InlineState>No accounts yet. Add the ones whose alerts and statements reach your mail, or let the first statement create them.</InlineState>
      )}
      <div className="chips" aria-label="Quick add">
        <span className="sub" style={{ alignSelf: "center" }}>
          No statement reaches these. Add one, then set its value under Net worth:
        </span>
        {PRESETS.filter((p) => !has(p.kind, p.subtype)).map((p) => (
          <button
            key={p.label}
            type="button"
            className="chip"
            disabled={act.busy}
            onClick={async () => {
              if ((await act.run(() => addAccount({ institution: p.institution, kind: p.kind, subtype: p.subtype ?? null }))).ok) toast(`${p.label} added.`);
            }}
          >
            + {p.label}
          </button>
        ))}
      </div>
      <form
        className="row-form"
        onSubmit={async (e) => {
          e.preventDefault();
          const mask = draft.mask ? last4(draft.mask) : null;
          if (!draft.institution.trim()) return act.setError("Add the bank, issuer or a name for it.");
          if (draft.mask && !mask) return act.setError("The last 4 digits must be exactly 4 digits.");
          const r = await act.run(() =>
            addAccount({ institution: draft.institution.trim(), kind: draft.kind, subtype: draft.subtype || null, name: draft.name.trim() || null, mask }),
          );
          if (r.ok) setDraft({ institution: "", name: "", kind: draft.kind, subtype: "", mask: "" });
        }}
      >
        <input className="inp" placeholder="Bank, issuer or name" aria-label="Bank, issuer or name" value={draft.institution} maxLength={80} onChange={(e) => setDraft({ ...draft, institution: e.target.value })} />
        <select className="sel" aria-label="Account type" value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value as AccountKind, subtype: "" })}>
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {KIND_LABEL[k]}
            </option>
          ))}
        </select>
        {subtypes && (
          <select className="sel" aria-label="Which one" value={draft.subtype} onChange={(e) => setDraft({ ...draft, subtype: e.target.value })}>
            <option value="">Which one?</option>
            {subtypes.map(([k, l]) => (
              <option key={k} value={k}>
                {l}
              </option>
            ))}
          </select>
        )}
        <input className="inp inp-4" placeholder="Last 4" aria-label="Last 4 digits" inputMode="numeric" maxLength={4} value={draft.mask} onChange={(e) => setDraft({ ...draft, mask: e.target.value.replace(/\D/g, "") })} />
        <input className="inp" placeholder="Nickname (optional)" aria-label="Nickname" value={draft.name} maxLength={80} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
        <button type="submit" className="btn ghost" disabled={act.busy}>
          Add
        </button>
      </form>
      {act.error && (
        <div className="result bad" role="alert">
          {act.error}
        </div>
      )}
    </>
  );
}

/** One account's settings. Liquid and asset class start at the kind's default; "Default" puts them back. */
function AccountForm({ a, done }: { a: Account; done: () => void }) {
  const toast = useToast();
  const act = useAction();
  const [f, setF] = useState({
    name: a.name ?? "",
    mask: a.mask ?? "",
    kind: a.kind,
    subtype: a.subtype ?? "",
    in_net_worth: a.in_net_worth,
    liquid: a.liquid === a.liquid_default ? "" : String(a.liquid),
    asset_class: a.asset_class === a.asset_class_default ? "" : a.asset_class,
    opened_at: a.opened_at ?? "",
  });
  const subtypes = SUBTYPES[f.kind];
  const kindChanged = f.kind !== a.kind;
  const save = async () => {
    const mask = f.mask ? last4(f.mask) : null;
    if (f.mask && !mask) return act.setError("The last 4 digits must be exactly 4 digits.");
    const fields: AccountFields = {
      name: f.name.trim() || null,
      mask,
      kind: f.kind,
      subtype: f.subtype || null,
      in_net_worth: f.in_net_worth,
      liquid: f.liquid === "" ? null : f.liquid === "true",
      asset_class: (f.asset_class || null) as AssetClass | null,
      opened_at: f.opened_at || null,
    };
    if ((await act.run(() => updateAccount(a.id, fields))).ok) {
      toast(`${f.name.trim() || a.institution} saved.`);
      done();
    }
  };
  return (
    <form
      className="form"
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      <b>{a.label}</b>
      <div className="row-form">
        <input className="inp" aria-label="Nickname" placeholder="Nickname" value={f.name} maxLength={120} onChange={(e) => setF({ ...f, name: e.target.value })} />
        <input className="inp inp-4" aria-label="Last 4 digits" placeholder="Last 4" inputMode="numeric" maxLength={4} value={f.mask} onChange={(e) => setF({ ...f, mask: e.target.value.replace(/\D/g, "") })} />
        <select className="sel" aria-label="Account type" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value as AccountKind, subtype: "" })}>
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {KIND_LABEL[k]}
            </option>
          ))}
        </select>
        {subtypes && (
          <select className="sel" aria-label="Which one" value={f.subtype} onChange={(e) => setF({ ...f, subtype: e.target.value })}>
            <option value="">Which one?</option>
            {subtypes.map(([k, l]) => (
              <option key={k} value={k}>
                {l}
              </option>
            ))}
          </select>
        )}
      </div>
      <div className="row-form" style={{ alignItems: "center" }}>
        <label className="row" style={{ gap: 6 }}>
          <input type="checkbox" checked={f.in_net_worth} onChange={(e) => setF({ ...f, in_net_worth: e.target.checked })} />
          Counts in net worth
        </label>
        <select className="sel" aria-label="Liquid" value={f.liquid} disabled={!f.in_net_worth} onChange={(e) => setF({ ...f, liquid: e.target.value })}>
          <option value="">Liquid: default{kindChanged ? "" : a.liquid_default ? " (yes)" : " (no)"}</option>
          <option value="true">Liquid: yes</option>
          <option value="false">Liquid: no</option>
        </select>
        <select className="sel" aria-label="Asset class" value={f.asset_class} disabled={!f.in_net_worth} onChange={(e) => setF({ ...f, asset_class: e.target.value })}>
          <option value="">Asset class: default{kindChanged ? "" : ` (${CLASS_LABEL[a.asset_class_default]})`}</option>
          {CLASSES.map((c) => (
            <option key={c} value={c}>
              {CLASS_LABEL[c]}
            </option>
          ))}
        </select>
        <label className="row" style={{ gap: 6 }} title="Net worth history doesn't wait for this account's first value: before it, the account held nothing">
          Opened
          <input className="inp" type="date" aria-label="Opened on" value={f.opened_at} onChange={(e) => setF({ ...f, opened_at: e.target.value })} />
        </label>
      </div>
      <div className="row">
        <button type="submit" className="btn" disabled={act.busy}>
          {act.busy ? "Saving…" : "Save"}
        </button>
        <button type="button" className="btn ghost" onClick={done}>
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

/** Own names, UPI handles and account digits, so money moving between your own accounts isn't counted as spending. */
function ClassifyFields({ p, set }: { p: ClassifyProfile; set: (k: keyof ClassifyProfile, v: string[]) => void }) {
  return (
    <>
      <Field label="Your name as banks print it" hint="As it shows in UPI and NEFT narrations. A long enough prefix is fine, since banks cut names short.">
        {(id) => <ChipsInput id={id} values={p.own_names} onChange={(v) => set("own_names", v.slice(0, 10))} placeholder="Type a name, press Enter" normalize={bankName} />}
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

/**
 * Accounts plus the self-transfer profile (PUT /api/profile/classify replaces all five lists). Onboarding step 2
 * (`onContinue` set, own names required) and Settings → Accounts.
 */
export function AccountsSettings({ onContinue }: { onContinue?: () => void }) {
  useStore();
  const toast = useToast();
  const st = read(setup.classifyProfile());
  const accounts = dataOf(read(api.accounts())) ?? [];
  const [edit, setEdit] = useState<ClassifyProfile | null>(null);
  const act = useAction();
  const server = st.status === "ready" ? st.data : null;
  const p = edit ?? server ?? EMPTY;
  const set = (k: keyof ClassifyProfile, v: string[]) => setEdit({ ...p, [k]: v });
  const suggest = accounts.map((a) => a.mask).filter((m): m is string => !!m && !p.own_account_masks.includes(m));
  const save = async () => {
    if (onContinue && server && !p.own_names.length) return act.setError("Add at least your name as banks print it, so transfers between your own accounts are recognised.");
    if (server && edit) {
      const r = await act.run(() => saveClassifyProfile(edit));
      if (!r.ok) return;
      setEdit(null);
      toast("Saved. It applies to statements uploaded from now on.");
    }
    onContinue?.();
  };
  return (
    <div className="form">
      <h4 className="fh">Your accounts</h4>
      <p className="sub">Everything you own or owe, one account each. Its type decides how it counts in net worth; edit an account to change that.</p>
      <AccountsEditor />
      <h4 className="fh">So Tijori can spot your own transfers</h4>
      <p className="sub">Money moving between your own accounts isn't spending. Tijori matches these against bank narrations.</p>
      {st.status === "loading" ? (
        <Loading card={false} />
      ) : !server ? (
        <InlineState>{st.status === "error" ? st.error.message : "This part isn't available on the server yet."}</InlineState>
      ) : (
        <>
          <ClassifyFields p={p} set={set} />
          {suggest.length > 0 && (
            <button type="button" className="linkish nm" onClick={() => set("own_account_masks", [...p.own_account_masks, ...suggest].slice(0, 20))}>
              Add {suggest.map((m) => `••${m}`).join(", ")} from your accounts
            </button>
          )}
        </>
      )}
      {act.error && (
        <div className="result bad" role="alert">
          {act.error}
        </div>
      )}
      <div className="row mt">
        <button type="button" className="btn" disabled={act.busy || (!onContinue && !edit)} onClick={save}>
          {act.busy ? "Saving…" : onContinue ? "Save and continue" : "Save"}
        </button>
      </div>
    </div>
  );
}
