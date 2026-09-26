// Hand-drawn SVG charts for the UI v1 screens. Each draws into a fixed viewBox and scales uniformly with its box.
import { compact } from "../lib/format";
import type { Paise } from "../lib/types";

const AX = "#8a8a8a";

function niceMax(v: number, steps = 3): number {
  if (v <= 0) return 1;
  const raw = v / steps;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  return step * steps;
}
const pts = (xs: number[], ys: number[]) => xs.map((x, i) => `${i ? "L" : "M"}${x.toFixed(1)} ${ys[i]!.toFixed(1)}`).join("");

/** Cumulative spend by day: actual to `today`, dashed normal for the whole cycle, dotted projection to the end. */
export function ProgressChart(o: {
  days: number;
  actual: Paise[];
  normal: Paise[] | null;
  projected: Paise | null;
  mark: { day: number; label: string } | null;
  dayLabel: (d: number) => string;
  width?: number;
  height?: number;
}) {
  const W = o.width ?? 740;
  const H = o.height ?? 300;
  const L = 44, R = W - 10, T = 12, B = H - 30;
  const top = niceMax(Math.max(o.projected ?? 0, ...o.actual, ...(o.normal ?? [0])));
  const x = (d: number) => L + ((d - 1) / Math.max(1, o.days - 1)) * (R - L);
  const y = (v: number) => B - (v / top) * (B - T);
  const today = o.actual.length;
  const ax = o.actual.map((_, i) => x(i + 1));
  const ay = o.actual.map(y);
  const ticks = [0, 1, 2, 3].map((i) => (top / 3) * i);
  const last = o.actual.at(-1) ?? 0;
  const xLabels = [1, 8, 15, 22].filter((d) => d < today - 2 || d > today + 2);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Cumulative spend by day">
      {ticks.map((t) => (
        <g key={t}>
          <path d={`M${L} ${y(t)}H${R}`} className="grid" />
          <text x={L - 10} y={y(t) + 3} textAnchor="end" className="ax">
            {t === 0 ? "0" : compact(t).replace("₹", "")}
          </text>
        </g>
      ))}
      {o.normal && <path d={pts(o.normal.map((_, i) => x(i + 1)), o.normal.map(y))} stroke={AX} strokeWidth={1.5} strokeDasharray="4 4" fill="none" />}
      {today > 0 && (
        <>
          <path d={`${pts(ax, ay)}L${ax.at(-1)} ${B}L${ax[0]} ${B}Z`} fill="#3987e5" fillOpacity={0.1} />
          {o.projected != null && today < o.days && (
            <path d={`M${x(today)} ${y(last)}L${x(o.days)} ${y(o.projected)}`} stroke="#3987e5" strokeWidth={2} strokeDasharray="2.5 3.5" strokeLinecap="round" />
          )}
          <path d={pts(ax, ay)} stroke="#3987e5" strokeWidth={2} fill="none" strokeLinejoin="round" strokeLinecap="round" />
          {today < o.days && <path d={`M${x(today)} ${y(last)}V${B}`} stroke="#ededed" strokeOpacity={0.25} strokeDasharray="2 3" />}
          <circle cx={x(today)} cy={y(last)} r={4} fill="var(--s1)" stroke="#3987e5" strokeWidth={2} />
          {o.mark && o.mark.day <= today && (
            <>
              <circle cx={x(o.mark.day)} cy={y(o.actual[o.mark.day - 1]!)} r={3} fill="#9085e9" />
              <text x={x(o.mark.day)} y={y(o.actual[o.mark.day - 1]!) - 16} textAnchor="middle" fontSize={12} fill="#a3a3a3">
                {o.mark.label}
              </text>
            </>
          )}
        </>
      )}
      {o.projected != null && today < o.days && (
        <>
          <circle cx={x(o.days)} cy={y(o.projected)} r={3.5} fill="var(--s1)" stroke="#3987e5" strokeWidth={1.5} />
          <text x={R - 4} y={y(o.projected) - 12} textAnchor="end" className="ax now" fontSize={12}>
            {compact(o.projected)}
          </text>
        </>
      )}
      {o.normal && (
        <text x={R - 4} y={y(o.normal.at(-1)!) + 22} textAnchor="end" className="ax" fontSize={12}>
          {compact(o.normal.at(-1)!)}
        </text>
      )}
      {xLabels.map((d) => (
        <text key={d} x={x(d)} y={H - 8} textAnchor={d === 1 ? "start" : "middle"} className="ax">
          {o.dayLabel(d)}
        </text>
      ))}
      {today < o.days && (
        <text x={x(today)} y={H - 8} textAnchor="middle" className="ax now">
          Today
        </text>
      )}
      <text x={R} y={H - 8} textAnchor="end" className="ax">
        {o.days}
      </text>
    </svg>
  );
}

export interface Stack {
  key: string;
  color: string;
  value: Paise;
}
/** One stacked bar per period; `projected` draws a dashed cap above the last bar. */
export function StackedBars(o: { bars: { label: string; sub: string; stacks: Stack[]; projected?: Paise | null; now?: boolean }[]; normal?: Paise | null; height?: number }) {
  const W = 1080;
  const H = o.height ?? 280;
  const L = 48, R = W - 8, T = 16, B = H - 44;
  const totals = o.bars.map((b) => b.stacks.reduce((a, s) => a + s.value, 0));
  const top = niceMax(Math.max(...totals, ...o.bars.map((b) => b.projected ?? 0), o.normal ?? 0));
  const y = (v: number) => B - (v / top) * (B - T);
  const slot = (R - L) / Math.max(1, o.bars.length);
  const bw = Math.min(40, slot * 0.55);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Spend by period">
      {[0, 1, 2, 3].map((i) => (
        <g key={i}>
          <path d={`M${L} ${y((top / 3) * i)}H${R}`} className="grid" />
          <text x={L - 10} y={y((top / 3) * i) + 3} textAnchor="end" className="ax">
            {i === 0 ? "0" : compact((top / 3) * i).replace("₹", "")}
          </text>
        </g>
      ))}
      {o.bars.map((b, i) => {
        const cx = L + slot * i + slot / 2;
        let acc = 0;
        return (
          <g key={b.label}>
            {b.stacks.map((s) => {
              const y0 = y(acc);
              acc += s.value;
              const y1 = y(acc);
              return s.value > 0 ? <rect key={s.key} x={cx - bw / 2} y={y1} width={bw} height={Math.max(0, y0 - y1 - 1)} fill={s.color}><title>{`${b.label} · ${s.key}: ${compact(s.value)}`}</title></rect> : null;
            })}
            {b.projected != null && b.projected > acc && (
              <rect x={cx - bw / 2} y={y(b.projected)} width={bw} height={y(acc) - y(b.projected)} fill="none" stroke="#9bb4ff" strokeDasharray="3 3" />
            )}
            <text x={cx} y={B + 16} textAnchor="middle" className={`ax${b.now ? " now" : ""}`}>
              {b.label}
            </text>
            <text x={cx} y={B + 32} textAnchor="middle" className={`ax${b.now ? " now" : ""}`}>
              {b.sub}
            </text>
          </g>
        );
      })}
      {o.normal != null && o.normal > 0 && <path d={`M${L} ${y(o.normal)}H${R}`} stroke="#ededed" strokeOpacity={0.6} strokeDasharray="4 4" />}
    </svg>
  );
}

export function Sparkline({ values, color, lastOpen }: { values: Paise[]; color: string; lastOpen?: boolean }) {
  const W = 110, H = 28;
  const max = Math.max(1, ...values);
  const x = (i: number) => 3 + (i / Math.max(1, values.length - 1)) * (W - 6);
  const y = (v: number) => H - 4 - (v / max) * (H - 8);
  const xs = values.map((_, i) => x(i));
  const ys = values.map(y);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} aria-hidden>
      <path d={pts(xs, ys)} stroke={color} strokeWidth={1.5} fill="none" strokeLinejoin="round" />
      {values.length > 0 && <circle cx={xs.at(-1)} cy={ys.at(-1)} r={2.5} fill={lastOpen ? "var(--s1)" : color} stroke={color} strokeWidth={1.2} />}
    </svg>
  );
}

/** Net worth history (solid) and projection points (dotted), with labels on the key points. */
export function NetWorthChart(o: { history: { date: string; value: Paise }[]; projection: { date: string; value: Paise }[]; label: (d: string) => string; height?: number; width?: number }) {
  const W = o.width ?? 1100;
  const H = o.height ?? 260;
  const L = 48, R = W - 16, T = 24, B = H - 30;
  const all = [...o.history, ...o.projection];
  if (!all.length) return null;
  const t0 = Date.parse(all[0]!.date), t1 = Date.parse(all.at(-1)!.date);
  const x = (d: string) => L + ((Date.parse(d) - t0) / Math.max(1, t1 - t0)) * (R - L);
  const vals = all.map((p) => p.value);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo) * 0.15 || hi * 0.05 || 1;
  const min = Math.max(0, lo - pad), max = hi + pad;
  const y = (v: number) => B - ((v - min) / (max - min)) * (B - T);
  const hx = o.history.map((p) => x(p.date));
  const hy = o.history.map((p) => y(p.value));
  const now = o.history.at(-1);
  const proj = now ? [now, ...o.projection] : o.projection;
  const ticks = [0, 1, 2, 3].map((i) => min + ((max - min) / 3) * i);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Net worth over time">
      {ticks.map((t) => (
        <g key={t}>
          <path d={`M${L} ${y(t)}H${R}`} className="grid" />
          <text x={L - 10} y={y(t) + 3} textAnchor="end" className="ax">
            {compact(t).replace("₹", "")}
          </text>
        </g>
      ))}
      {o.projection.length > 0 && now && <rect x={x(now.date)} y={T} width={R - x(now.date)} height={B - T} fill="#ffffff" fillOpacity={0.02} />}
      {hx.length > 1 && <path d={`${pts(hx, hy)}L${hx.at(-1)} ${B}L${hx[0]} ${B}Z`} fill="#85c89a" fillOpacity={0.08} />}
      <path d={pts(hx, hy)} stroke="#85c89a" strokeWidth={2} fill="none" strokeLinejoin="round" />
      {proj.length > 1 && <path d={pts(proj.map((p) => x(p.date)), proj.map((p) => y(p.value)))} stroke="#85c89a" strokeWidth={2} strokeDasharray="2.5 3.5" fill="none" />}
      {proj.map((p, i) => (
        <g key={p.date}>
          <circle cx={x(p.date)} cy={y(p.value)} r={i === 0 ? 4 : 3.5} fill="var(--s1)" stroke="#85c89a" strokeWidth={i === 0 ? 2 : 1.5} />
          <text x={i === proj.length - 1 ? x(p.date) : x(p.date)} y={y(p.value) - 12} textAnchor={i === proj.length - 1 ? "end" : "middle"} className="ax now" fontSize={12}>
            {compact(p.value)}
          </text>
        </g>
      ))}
      {[o.history[0], now, o.projection.at(-1)].filter((p, i, a) => p && a.findIndex((q) => q?.date === p.date) === i).map((p, i, a) => (
        <text key={p!.date} x={x(p!.date)} y={H - 8} textAnchor={i === 0 ? "start" : i === a.length - 1 ? "end" : "middle"} className={`ax${p === now ? " now" : ""}`}>
          {p === now ? "Now" : o.label(p!.date)}
        </text>
      ))}
    </svg>
  );
}
