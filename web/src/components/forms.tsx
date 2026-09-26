import { useId, useRef, useState, type ReactNode } from "react";

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: (id: string) => ReactNode }) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children(id)}
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}

/**
 * Write-only secret input: never prefilled, never shown back (no reveal toggle), no autofill or spellcheck.
 * The caller clears it after every submit.
 */
export function SecretInput(props: { id?: string; value: string; onChange: (v: string) => void; placeholder?: string; label?: string }) {
  return (
    <input
      id={props.id}
      className="inp"
      type="password"
      autoComplete="new-password"
      spellCheck={false}
      autoCapitalize="off"
      aria-label={props.label}
      placeholder={props.placeholder}
      value={props.value}
      onChange={(e) => props.onChange(e.target.value)}
    />
  );
}

/** A list of short strings (names, UPI handles, last-4 digits) edited as removable chips. */
export function ChipsInput(props: { id?: string; values: string[]; onChange: (v: string[]) => void; placeholder: string; normalize?: (s: string) => string | null }) {
  const [draft, setDraft] = useState("");
  const [bad, setBad] = useState(false);
  const add = () => {
    const raw = draft.trim();
    if (!raw) return;
    const v = props.normalize ? props.normalize(raw) : raw;
    if (v == null) return setBad(true);
    if (!props.values.includes(v)) props.onChange([...props.values, v]);
    setDraft("");
    setBad(false);
  };
  return (
    <div className={`chips-in${bad ? " bad-in" : ""}`}>
      {props.values.map((v) => (
        <span className="chip-v" key={v}>
          {v}
          <button type="button" aria-label={`Remove ${v}`} onClick={() => props.onChange(props.values.filter((x) => x !== v))}>
            ×
          </button>
        </span>
      ))}
      <input
        id={props.id}
        value={draft}
        placeholder={props.placeholder}
        aria-invalid={bad}
        onChange={(e) => {
          setDraft(e.target.value);
          setBad(false);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            add();
          } else if (e.key === "Backspace" && !draft && props.values.length) props.onChange(props.values.slice(0, -1));
        }}
        onBlur={add}
      />
    </div>
  );
}

/** A read-only value with a Copy button; falls back to selecting the text where the clipboard API isn't allowed. */
export function CopyField({ value, label }: { value: string; label: string }) {
  const ref = useRef<HTMLInputElement>(null);
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      ref.current?.select();
    }
  };
  return (
    <div className="copy">
      <input ref={ref} className="inp mono-t" readOnly value={value} aria-label={label} onFocus={(e) => e.currentTarget.select()} />
      <button type="button" className="btn ghost" onClick={copy}>
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

/** Tracks one async action's busy/error state; the message shown is always the client's own, never a server body. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async <T,>(fn: () => Promise<T>): Promise<{ ok: true; value: T } | { ok: false; error: string }> => {
    setBusy(true);
    setError(null);
    try {
      return { ok: true, value: await fn() };
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Something went wrong.";
      setError(msg);
      return { ok: false, error: msg };
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, run, setError };
}
