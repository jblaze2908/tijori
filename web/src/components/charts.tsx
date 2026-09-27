import { useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from "react";
import { compact, inr } from "../lib/format";
import type { Paise } from "../lib/types";

const AX = "#8a8a8a";
const BLUE = "#3987e5";
const GREEN = "#85c89a";

function niceMax(v: number, steps = 3): number {
  if (v <= 0) return 1;
  const raw = v / steps;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  return step * steps;
}
const pts = (xs: number[], ys: number[]) => xs.map((x, i) => `${i ? "L" : "M"}${x.toFixed(1)} ${ys[i]!.toFixed(1)}`).join("");
const signedK = (v: number) => (v === 0 ? "±₹0" : `${v > 0 ? "+" : "−"}${compact(Math.abs(v))}`);

export interface TipRow {
  color?: string;
  dash?: boolean;
  label: string;
  value: string;
  note?: string;
  strong?: boolean;
}

/** Hover snaps to the nearest of `xs` (viewBox units): pointer, tap, or arrow keys once focused. O(xs) per pointer move. */
function useNearest(xs: number[], W: number) {
  const [i, setI] = useState<number | null>(null);
  const n = xs.length;
  const pick = (e: PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    if (!r.width || !n) return;
    const vx = ((e.clientX - r.left) / r.width) * W;
    let best = 0;
    for (let k = 1; k < n; k++) if (Math.abs(xs[k]! - vx) < Math.abs(xs[best]! - vx)) best = k;
    setI(best);
  };
  const pointer = {
    onPointerMove: pick,
    onPointerDown: pick,
    // A tap has no hover to end, so a touch keeps its tooltip until the next tap or blur.
    onPointerLeave: (e: PointerEvent<SVGSVGElement>) => {
      if (e.pointerType !== "touch") setI(null);
    },
  };
  const keys = {
    tabIndex: 0,
    onFocus: () => setI((v) => v ?? n - 1),
    onBlur: () => setI(null),
    onKeyDown: (e: KeyboardEvent<SVGSVGElement>) => {
      const to = e.key === "ArrowLeft" ? -1 : e.key === "ArrowRight" ? 1 : e.key === "Home" ? -n : e.key === "End" ? n : 0;
      if (e.key === "Escape") setI(null);
      if (!to || !n) return;
      e.preventDefault();
      setI((v) => Math.min(n - 1, Math.max(0, (v ?? n - 1) + to)));
    },
  };
  return { i: i != null && i < n ? i : null, pointer, keys };
}

/** The chart box's CSS width, so a chart can draw in real pixels: text and dots keep their size on a phone.
 *  Re-renders only when the box resizes. */
function useBoxWidth(fallback: number) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(fallback);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.round(e!.contentRect.width) || fallback));
    ro.observe(el);
    return () => ro.disconnect();
  }, [fallback]);
  return [ref, w] as const;
}

/** The readout beside the hovered point; flips left, then clamps, so it never leaves the chart box. */
function Tip({ W, H, x, y, title, rows }: { W: number; H: number; x: number; y: number; title: string; rows: TipRow[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    const box = el?.parentElement;
    if (!el || !box) return;
    const bw = box.clientWidth, w = el.offsetWidth, px = (x / W) * bw;
    let left = px + 14;
    if (left + w > bw) left = px - 14 - w;
    if (left < 0) left = Math.max(0, Math.min(bw - w, px - w / 2));
    el.style.left = `${left}px`;
  });
  return (
    <div ref={ref} className="ctip" style={{ top: `${(y / H) * 100}%` }} aria-live="polite">
      <span className="h">{title}</span>
      {rows.map((r) => (
        <div key={r.label} className={`r${r.strong ? " s" : ""}`}>
          <i className={r.dash ? "d" : undefined} style={r.color ? ({ "--k": r.color } as CSSProperties) : undefined} />
          <b>{r.value}</b>
          <span>{r.label}</span>
          <em>{r.note ?? ""}</em>
        </div>
      ))}
    </div>
  );
}

/** Cumulative spend by day: actual to `today`, dashed normal for the whole cycle, dotted projection to the end. */
export function ProgressChart(o: {
  days: number;
  actual: Paise[];
  normal: Paise[] | null;
  projected: Paise | null;
  mark: { day: number; label: string } | null;
  dayLabel: (d: number) => string;
  /** Tooltip heading for day d; defaults to "Day d". */
  tipLabel?: (d: number) => string;
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
  const xs = Array.from({ length: o.days }, (_, i) => x(i + 1));
  const { i: hi, pointer, keys } = useNearest(xs, W);
  const projAt = (d: number) => (o.projected == null || today >= o.days || d <= today ? null : Math.round(last + ((o.projected - last) * (d - today)) / (o.days - today)));
  const hov = hi == null ? null : { d: hi + 1, spent: hi < today ? o.actual[hi]! : null, proj: projAt(hi + 1), normal: o.normal?.[hi] ?? null };
  const rows: TipRow[] = !hov
    ? []
    : [
        ...(hov.spent != null ? [{ color: BLUE, label: "Spent", value: inr(hov.spent) }] : []),
        ...(hov.proj != null ? [{ color: BLUE, dash: true, label: "Projected", value: inr(hov.proj) }] : []),
        ...(hov.normal != null ? [{ color: AX, dash: true, label: "Normal", value: inr(hov.normal), note: hov.spent != null ? signedK(hov.spent - hov.normal) : undefined }] : []),
      ];
  const hy = hov ? y(Math.max(hov.spent ?? 0, hov.proj ?? 0, hov.normal ?? 0)) : 0;
  return (
    <div className="cbox">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Cumulative spend by day" {...pointer} {...keys}>
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
            <path d={`${pts(ax, ay)}L${ax.at(-1)} ${B}L${ax[0]} ${B}Z`} fill={BLUE} fillOpacity={0.1} />
            {o.projected != null && today < o.days && (
              <path d={`M${x(today)} ${y(last)}L${x(o.days)} ${y(o.projected)}`} stroke={BLUE} strokeWidth={2} strokeDasharray="2.5 3.5" strokeLinecap="round" />
            )}
            <path d={pts(ax, ay)} stroke={BLUE} strokeWidth={2} fill="none" strokeLinejoin="round" strokeLinecap="round" />
            {ax.slice(0, -1).map((cx, i) => (
              <circle key={i} cx={cx} cy={ay[i]} r={2.5} fill={BLUE} />
            ))}
            {today < o.days && <path d={`M${x(today)} ${y(last)}V${B}`} stroke="#ededed" strokeOpacity={0.25} strokeDasharray="2 3" />}
            <circle cx={x(today)} cy={y(last)} r={4} fill="var(--s1)" stroke={BLUE} strokeWidth={2} />
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
            <circle cx={x(o.days)} cy={y(o.projected)} r={3.5} fill="var(--s1)" stroke={BLUE} strokeWidth={1.5} />
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
        {hov && (
          <g pointerEvents="none">
            <path d={`M${xs[hi!]} ${T}V${B}`} className="xhair" />
            {hov.normal != null && <circle cx={xs[hi!]} cy={y(hov.normal)} r={4} fill="var(--s1)" stroke={AX} strokeWidth={2} />}
            {hov.proj != null && <circle cx={xs[hi!]} cy={y(hov.proj)} r={4} fill="var(--s1)" stroke={BLUE} strokeWidth={2} />}
            {hov.spent != null && <circle cx={xs[hi!]} cy={y(hov.spent)} r={5} fill={BLUE} stroke="var(--s1)" strokeWidth={2} />}
          </g>
        )}
      </svg>
      {hov && rows.length > 0 && <Tip W={W} H={H} x={xs[hi!]!} y={hy} title={o.tipLabel?.(hov.d) ?? `Day ${hov.d}`} rows={rows} />}
    </div>
  );
}

export interface Stack {
  key: string;
  color: string;
  value: Paise;
}
/** One stacked bar per period; `projected` draws a dashed cap above the last bar. */
export function StackedBars(o: { bars: { label: string; sub: string; stacks: Stack[]; projected?: Paise | null; now?: boolean; tip?: string }[]; normal?: Paise | null; height?: number }) {
  const W = 1080;
  const H = o.height ?? 280;
  const L = 48, R = W - 8, T = 16, B = H - 44;
  const totals = o.bars.map((b) => b.stacks.reduce((a, s) => a + s.value, 0));
  const top = niceMax(Math.max(...totals, ...o.bars.map((b) => b.projected ?? 0), o.normal ?? 0));
  const y = (v: number) => B - (v / top) * (B - T);
  const slot = (R - L) / Math.max(1, o.bars.length);
  const bw = Math.min(40, slot * 0.55);
  const cxs = o.bars.map((_, i) => L + slot * i + slot / 2);
  const { i: hi, pointer, keys } = useNearest(cxs, W);
  const hb = hi == null ? null : o.bars[hi]!;
  const rows: TipRow[] = !hb
    ? []
    : [
        ...hb.stacks.filter((s) => s.value > 0).reverse().map((s) => ({ color: s.color, label: s.key, value: inr(s.value) })),
        { label: hb.now ? "Total so far" : "Total", value: inr(totals[hi!]!), strong: true },
        ...(hb.projected != null && hb.projected > totals[hi!]! ? [{ color: "#9bb4ff", dash: true, label: "Projected", value: inr(hb.projected) }] : []),
        ...(o.normal ? [{ color: "#ededed", dash: true, label: "Normal", value: inr(o.normal) }] : []),
      ];
  return (
    <div className="cbox">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Spend by period" {...pointer} {...keys}>
        {[0, 1, 2, 3].map((i) => (
          <g key={i}>
            <path d={`M${L} ${y((top / 3) * i)}H${R}`} className="grid" />
            <text x={L - 10} y={y((top / 3) * i) + 3} textAnchor="end" className="ax">
              {i === 0 ? "0" : compact((top / 3) * i).replace("₹", "")}
            </text>
          </g>
        ))}
        {hi != null && <rect x={cxs[hi]! - slot / 2 + 2} y={T} width={slot - 4} height={B - T} rx={6} className="xband" />}
        {o.bars.map((b, i) => {
          const cx = cxs[i]!;
          let acc = 0;
          return (
            <g key={b.label} opacity={hi != null && hi !== i ? 0.55 : 1}>
              {b.stacks.map((s) => {
                const y0 = y(acc);
                acc += s.value;
                const y1 = y(acc);
                return s.value > 0 ? <rect key={s.key} x={cx - bw / 2} y={y1} width={bw} height={Math.max(0, y0 - y1 - 1)} fill={s.color} /> : null;
              })}
              {b.projected != null && b.projected > acc && (
                <rect x={cx - bw / 2} y={y(b.projected)} width={bw} height={y(acc) - y(b.projected)} fill="none" stroke="#9bb4ff" strokeDasharray="3 3" />
              )}
              <text x={cx} y={B + 16} textAnchor="middle" className={`ax${b.now || hi === i ? " now" : ""}`}>
                {b.label}
              </text>
              <text x={cx} y={B + 32} textAnchor="middle" className={`ax${b.now || hi === i ? " now" : ""}`}>
                {b.sub}
              </text>
            </g>
          );
        })}
        {o.normal != null && o.normal > 0 && <path d={`M${L} ${y(o.normal)}H${R}`} stroke="#ededed" strokeOpacity={0.6} strokeDasharray="4 4" />}
      </svg>
      {hb && <Tip W={W} H={H} x={cxs[hi!]!} y={y(Math.max(totals[hi!]!, hb.projected ?? 0))} title={hb.tip ?? hb.label} rows={rows} />}
    </div>
  );
}

/** Row-sized trend: a dot per period, hover for its value. `labels` name each period in the tooltip. */
export function Sparkline({ values, color, lastOpen, labels }: { values: Paise[]; color: string; lastOpen?: boolean; labels?: string[] }) {
  const W = 110, H = 28;
  const max = Math.max(1, ...values);
  const x = (i: number) => 4 + (i / Math.max(1, values.length - 1)) * (W - 8);
  const y = (v: number) => H - 4 - (v / max) * (H - 8);
  const xs = values.map((_, i) => x(i));
  const ys = values.map(y);
  const { i: hi, pointer } = useNearest(xs, W);
  return (
    <div className="cbox spark">
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} aria-hidden {...pointer}>
        <path d={pts(xs, ys)} stroke={color} strokeWidth={1.5} fill="none" strokeLinejoin="round" />
        {xs.slice(0, -1).map((cx, i) => (
          <circle key={i} cx={cx} cy={ys[i]} r={1.5} fill={color} />
        ))}
        {values.length > 0 && <circle cx={xs.at(-1)} cy={ys.at(-1)} r={2.5} fill={lastOpen ? "var(--s1)" : color} stroke={color} strokeWidth={1.2} />}
        {hi != null && <circle cx={xs[hi]} cy={ys[hi]} r={3.5} fill={lastOpen && hi === values.length - 1 ? "var(--s1)" : color} stroke={hi === values.length - 1 && lastOpen ? color : "var(--s1)"} strokeWidth={1.5} />}
      </svg>
      {hi != null && (
        <Tip W={W} H={H} x={xs[hi]!} y={ys[hi]!} title={`${labels?.[hi] ?? ""}${lastOpen && hi === values.length - 1 ? " · so far" : ""}`} rows={[{ color, label: "Spent", value: inr(values[hi]!), note: hi > 0 && !(lastOpen && hi === values.length - 1) ? signedK(values[hi]! - values[hi - 1]!) : undefined }]} />
      )}
    </div>
  );
}

export interface Line {
  key: string;
  color: string;
  values: Paise[];
}
/** One line per series over shared periods, a dot on every point; `openLast` marks the last period as still running
 *  (hollow dot, dashed last segment, no change figure: a part-month against a full one isn't a change). */
export function LineChart(o: { labels: string[]; tips?: string[]; series: Line[]; openLast?: boolean; height?: number; label?: string }) {
  const [box, W] = useBoxWidth(1080);
  const H = o.height ?? (W < 600 ? 220 : 280);
  const L = 44, R = W - 12, T = 16, B = H - 30;
  const n = o.labels.length;
  const top = niceMax(Math.max(0, ...o.series.flatMap((s) => s.values)));
  const x = (i: number) => (n <= 1 ? (L + R) / 2 : L + (i / (n - 1)) * (R - L));
  const y = (v: number) => B - (v / top) * (B - T);
  const xs = o.labels.map((_, i) => x(i));
  const { i: hi, pointer, keys } = useNearest(xs, W);
  // A month label ("Jan '26", 11px mono) needs ~72px, the first one start-anchored, before neighbours collide.
  const step = Math.max(1, Math.ceil(n / Math.max(2, Math.floor((R - L) / 72))));
  const ticks = [0, 1, 2, 3].map((i) => (top / 3) * i);
  const openAt = o.openLast ? n - 1 : -1;
  const rows: TipRow[] =
    hi == null
      ? []
      : [...o.series]
          .sort((a, b) => b.values[hi]! - a.values[hi]!)
          .map((s) => ({ color: s.color, label: s.key, value: inr(s.values[hi]!), note: hi > 0 && hi !== openAt ? signedK(s.values[hi]! - s.values[hi - 1]!) : undefined }));
  const hy = hi == null ? 0 : y(Math.max(0, ...o.series.map((s) => s.values[hi]!)));
  return (
    <div className="cbox" ref={box}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={o.label ?? "Spend by month"} {...pointer} {...keys}>
        {ticks.map((t) => (
          <g key={t}>
            <path d={`M${L} ${y(t)}H${R}`} className="grid" />
            <text x={L - 10} y={y(t) + 3} textAnchor="end" className="ax">
              {t === 0 ? "0" : compact(t).replace("₹", "")}
            </text>
          </g>
        ))}
        {hi != null && <path d={`M${xs[hi]} ${T}V${B}`} className="xhair" />}
        {o.series.map((s) => {
          const ys = s.values.map(y);
          const solid = openAt > 0 ? n - 1 : n;
          return (
            <g key={s.key}>
              <path d={pts(xs.slice(0, solid), ys.slice(0, solid))} stroke={s.color} strokeWidth={2} fill="none" strokeLinejoin="round" strokeLinecap="round" />
              {openAt > 0 && <path d={`M${xs[n - 2]} ${ys[n - 2]}L${xs[n - 1]} ${ys[n - 1]}`} stroke={s.color} strokeWidth={2} strokeDasharray="3 4" strokeLinecap="round" />}
              {ys.map((cy, i) =>
                i === openAt ? (
                  <circle key={i} cx={xs[i]} cy={cy} r={hi === i ? 5 : 4} fill="var(--s1)" stroke={s.color} strokeWidth={2} />
                ) : (
                  <circle key={i} cx={xs[i]} cy={cy} r={hi === i ? 5.5 : 4} fill={s.color} stroke="var(--s1)" strokeWidth={2} />
                ),
              )}
            </g>
          );
        })}
        {o.labels.map((l, i) =>
          i === n - 1 || (i % step === 0 && n - 1 - i >= step) ? (
            <text key={i} x={xs[i]} y={H - 8} textAnchor={n > 1 && i === 0 ? "start" : n > 1 && i === n - 1 ? "end" : "middle"} className={`ax${i === openAt || i === hi ? " now" : ""}`}>
              {l}
            </text>
          ) : null,
        )}
      </svg>
      {hi != null && rows.length > 0 && <Tip W={W} H={H} x={xs[hi]!} y={hy} title={`${o.tips?.[hi] ?? o.labels[hi]}${hi === openAt ? " · so far" : ""}`} rows={rows} />}
    </div>
  );
}

/** Net worth history (solid) and projection points (dotted), a dot on every point, labels on the key points. */
export function NetWorthChart(o: { history: { date: string; value: Paise }[]; projection: { date: string; value: Paise }[]; label: (d: string) => string; height?: number; width?: number }) {
  const W = o.width ?? 1100;
  const H = o.height ?? 260;
  const L = 48, R = W - 16, T = 24, B = H - 30;
  const all = [...o.history, ...o.projection];
  const t0 = all.length ? Date.parse(all[0]!.date) : 0, t1 = all.length ? Date.parse(all.at(-1)!.date) : 0;
  const x = (d: string) => L + ((Date.parse(d) - t0) / Math.max(1, t1 - t0)) * (R - L);
  const { i: hi, pointer, keys } = useNearest(all.map((p) => x(p.date)), W);
  if (!all.length) return null;
  const vals = all.map((p) => p.value);
  const lo = Math.min(...vals), hi2 = Math.max(...vals);
  const pad = (hi2 - lo) * 0.15 || hi2 * 0.05 || 1;
  const min = Math.max(0, lo - pad), max = hi2 + pad;
  const y = (v: number) => B - ((v - min) / (max - min)) * (B - T);
  const hx = o.history.map((p) => x(p.date));
  const hy = o.history.map((p) => y(p.value));
  const now = o.history.at(-1);
  const proj = now ? [now, ...o.projection] : o.projection;
  const ticks = [0, 1, 2, 3].map((i) => min + ((max - min) / 3) * i);
  const hp = hi == null ? null : all[hi]!;
  const projected = hi != null && hi >= o.history.length;
  return (
    <div className="cbox">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Net worth over time" {...pointer} {...keys}>
        {ticks.map((t) => (
          <g key={t}>
            <path d={`M${L} ${y(t)}H${R}`} className="grid" />
            <text x={L - 10} y={y(t) + 3} textAnchor="end" className="ax">
              {compact(t).replace("₹", "")}
            </text>
          </g>
        ))}
        {o.projection.length > 0 && now && <rect x={x(now.date)} y={T} width={R - x(now.date)} height={B - T} fill="#ffffff" fillOpacity={0.02} />}
        {hx.length > 1 && <path d={`${pts(hx, hy)}L${hx.at(-1)} ${B}L${hx[0]} ${B}Z`} fill={GREEN} fillOpacity={0.08} />}
        {hp && <path d={`M${x(hp.date)} ${T}V${B}`} className="xhair" />}
        <path d={pts(hx, hy)} stroke={GREEN} strokeWidth={2} fill="none" strokeLinejoin="round" />
        {hx.slice(0, -1).map((cx, i) => (
          <circle key={i} cx={cx} cy={hy[i]} r={2.5} fill={GREEN} />
        ))}
        {proj.length > 1 && <path d={pts(proj.map((p) => x(p.date)), proj.map((p) => y(p.value)))} stroke={GREEN} strokeWidth={2} strokeDasharray="2.5 3.5" fill="none" />}
        {proj.map((p, i) => (
          <g key={p.date}>
            <circle cx={x(p.date)} cy={y(p.value)} r={i === 0 ? 4 : 3.5} fill="var(--s1)" stroke={GREEN} strokeWidth={i === 0 ? 2 : 1.5} />
            <text x={x(p.date)} y={y(p.value) - 12} textAnchor={i === proj.length - 1 ? "end" : "middle"} className="ax now" fontSize={12}>
              {compact(p.value)}
            </text>
          </g>
        ))}
        {hp && <circle cx={x(hp.date)} cy={y(hp.value)} r={5.5} fill={projected ? "var(--s1)" : GREEN} stroke={projected ? GREEN : "var(--s1)"} strokeWidth={2} pointerEvents="none" />}
        {[o.history[0], now, o.projection.at(-1)].filter((p, i, a) => p && a.findIndex((q) => q?.date === p.date) === i).map((p, i, a) => (
          <text key={p!.date} x={x(p!.date)} y={H - 8} textAnchor={i === 0 ? "start" : i === a.length - 1 ? "end" : "middle"} className={`ax${p === now ? " now" : ""}`}>
            {p === now ? "Now" : o.label(p!.date)}
          </text>
        ))}
      </svg>
      {hp && (
        <Tip
          W={W}
          H={H}
          x={x(hp.date)}
          y={y(hp.value)}
          title={o.label(hp.date)}
          rows={[{ color: GREEN, dash: projected, label: projected ? "Projected" : "Net worth", value: inr(hp.value), note: hi! > 0 ? signedK(hp.value - all[hi! - 1]!.value) : undefined }]}
        />
      )}
    </div>
  );
}
