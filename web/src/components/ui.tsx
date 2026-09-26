import type { CSSProperties, ReactNode } from "react";
import type { ApiError } from "../lib/api";
import { categoryColor, monogramColor } from "../lib/colors";

export function Monogram({ name }: { name: string }) {
  const letter = name.replace(/[^A-Za-z0-9]/g, "").slice(0, 1).toUpperCase() || "•";
  return (
    <span className="mono" style={{ background: monogramColor(name) }} aria-hidden>
      {letter}
    </span>
  );
}

export const Dot = ({ color, style }: { color: string; style?: CSSProperties }) => (
  <i className="dot" style={{ background: color, ...style }} aria-hidden />
);

export function CategoryPill({ category }: { category: string | null }) {
  return (
    <span className="pill">
      <Dot color={categoryColor(category)} style={{ margin: 0 }} />
      {category ?? "Uncategorized"}
    </span>
  );
}

export function Switch({ on, onChange, label }: { on: boolean; onChange: (on: boolean) => void; label: ReactNode }) {
  return (
    <label className="toggle">
      <button type="button" role="switch" aria-checked={on} className={`sw${on ? "" : " off"}`} onClick={() => onChange(!on)} />
      {label}
    </label>
  );
}

export function Loading({ label = "Loading…", card = true }: { label?: string; card?: boolean }) {
  return (
    <div className={`${card ? "card " : ""}empty loading`} aria-busy="true" role="status">
      <span className="spin" aria-hidden />
      {label}
    </div>
  );
}

export function ErrorState({ error, onRetry, title = "Couldn't load this" }: { error: ApiError; onRetry: () => void; title?: string }) {
  return (
    <div className="card empty" role="alert">
      <b>{title}</b>
      {error.message}
      <div style={{ marginTop: 14 }}>
        <button type="button" className="btn ghost" onClick={onRetry}>
          Try again
        </button>
      </div>
    </div>
  );
}

export function Empty({ title, children, card = true }: { title: string; children?: ReactNode; card?: boolean }) {
  return (
    <div className={`${card ? "card " : ""}empty`}>
      <b>{title}</b>
      {children}
    </div>
  );
}

/** One-line inline state for a secondary card section, so one failing endpoint doesn't blank the page. */
export function InlineState({ children, onRetry }: { children: ReactNode; onRetry?: () => void }) {
  return (
    <div className="sub" style={{ marginTop: 12 }}>
      {children}
      {onRetry && (
        <button type="button" className="linkish" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

/**
 * Change vs a named comparison period. Colour = direction × whether up is good for this figure, with an arrow
 * and words, so it never relies on colour alone. Compact chips keep the comparison in a tooltip and for screen readers.
 */
export function Delta({ cur, prev, vs, upIsGood, points, compact }: { cur: number; prev: number | null; vs: string; upIsGood: boolean; points?: boolean; compact?: boolean }) {
  if (prev == null) return null;
  const chip = (cls: string, text: string) => (
    <span className={`delta ${cls}`} title={`${text} vs ${vs}`}>
      {text}
      {compact ? <span className="sr-only"> vs {vs}</span> : <span className="vs"> vs {vs}</span>}
    </span>
  );
  if (points) {
    const d = Math.round((cur - prev) * 100);
    return chip(d === 0 ? "flat" : d > 0 === upIsGood ? "good" : "bad", `${d > 0 ? "↑" : d < 0 ? "↓" : "→"} ${Math.abs(d)} pts`);
  }
  if (prev <= 0) return cur > 0 ? chip("flat", "new") : null;
  const ratio = cur / prev;
  const p = Math.round((ratio - 1) * 100);
  const cls = p === 0 ? "flat" : p > 0 === upIsGood ? "good" : "bad";
  // Past 3× a percentage stops meaning much ("8317%"); a multiplier reads at a glance.
  return chip(cls, ratio >= 3 ? `↑ ${ratio >= 10 ? Math.round(ratio) : ratio.toFixed(1)}×` : `${p > 0 ? "↑" : p < 0 ? "↓" : "→"} ${Math.abs(p)}%`);
}

export const CardHead = ({ title, x }: { title: ReactNode; x?: ReactNode }) => (
  <h3>
    {title}
    {x ? <span className="x">{x}</span> : null}
  </h3>
);
