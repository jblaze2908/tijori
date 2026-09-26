// Hand-written SVG/HTML charts following the dataviz method: one y-axis, thin marks (bars ≤ 24px, 2px lines),
// 2px surface gaps, recessive solid hairline grids, a hover/focus tooltip on every chart and a table twin.
// Each chart draws at its host's measured width; the page redraws on resize.
import { applyStyles, color, html, sty, trusted, type Html } from "./lib/dom";
import { compact, dayShort, inr, WEEKDAYS, addDays, weekday } from "./lib/format";
import { heatBin, heatThresholds, type Point } from "./lib/insights";
import { DIV_BAD, DIV_GOOD, DIV_MID, HEAT, HEAT_ZERO, inkOn } from "./lib/colors";
import type { ISODate, Paise, Period } from "./lib/types";

export interface Table {
  head: string[];
  rows: string[][];
}
export interface Chart {
  label: string;
  draw(host: HTMLElement): void;
  table(): Table;
}

const PAD = { l: 52, r: 14, t: 12, b: 26 };
const BAR_MAX = 24;
const GAP = 2;

function nice(v: number): number {
  if (!(v > 0)) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}

/** Round ticks bracketing the values; `zero` keeps 0 on the axis (bars always grow from it). */
function scale(values: number[], zero: boolean, minRange = 1000) {
  let lo = Math.min(...values, zero ? 0 : Infinity);
  let hi = Math.max(...values, zero ? 0 : -Infinity);
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) [lo, hi] = [0, minRange];
  if (hi - lo < minRange) hi = lo + minRange;
  const step = nice((hi - lo) / 4);
  const a = Math.floor(lo / step) * step;
  const b = Math.ceil(hi / step) * step;
  const ticks: number[] = [];
  for (let v = a; v <= b + step / 2; v += step) ticks.push(v);
  return { lo: a, hi: b, ticks };
}

function tipper(host: HTMLElement) {
  const tip = host.querySelector<HTMLElement>(":scope > .tip")!;
  return {
    show(content: Html, x: number, y = 8) {
      tip.innerHTML = content.s;
      applyStyles(tip);
      tip.style.opacity = "1";
      const w = tip.offsetWidth;
      const hw = host.clientWidth;
      const left = x + 14 + w > hw ? x - 14 - w : x + 14;
      tip.style.left = `${Math.max(0, Math.min(hw - w, left))}px`;
      tip.style.top = `${y}px`;
    },
    hide() {
      tip.style.opacity = "0";
    },
  };
}

/** Tooltip row: a short line key in the series colour, the label, and the value as the strong element. */
export const tipRow = (c: string | null, label: string, value: string, strong = true) =>
  html`<div class="r"><span>${c ? html`<i class="lk"${sty(`background:${c}`)}></i>` : ""}${label}</span>${strong ? html`<b class="num">${value}</b>` : html`<span class="num">${value}</span>`}</div>`;
export const tipTitle = (t: string) => html`<div class="tt">${t}</div>`;

const width = (host: HTMLElement, min = 260) => Math.max(min, Math.floor(host.clientWidth));

function yAxis(sc: ReturnType<typeof scale>, y: (v: number) => number, W: number, fmt: (v: number) => string) {
  return sc.ticks.map(
    (v) => html`<line x1="${PAD.l}" x2="${W - PAD.r}" y1="${y(v)}" y2="${y(v)}" stroke="${color(v === 0 ? "var(--line-2)" : "var(--line)")}" stroke-width="1"/>
      <text x="${PAD.l - 8}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="${color("var(--t3)")}">${fmt(v)}</text>`,
  );
}

// ---------- line ----------

export interface LineSeries {
  id: string;
  name: string;
  color: string;
  points: Point[];
  area?: boolean;
  dash?: string;
  /** Stroke width; 2px is the data spec, thinner only for reference lines. */
  width?: number;
}

export function lineChart(o: {
  label: string;
  series: LineSeries[];
  height?: number;
  zero?: boolean;
  formatX: (x: number) => string;
  tipTitle: (x: number) => string;
  tipExtra?: (x: number) => Html | null;
  formatY?: (v: number) => string;
  /** Tooltip/table value format; money by default. */
  formatValue?: (v: number) => string;
}): Chart {
  const fv = o.formatValue ?? inr;
  const h = o.height ?? 210;
  const xs = [...new Set(o.series.flatMap((s) => s.points.map((p) => p[0])))].sort((a, b) => a - b);
  const valueAt = (s: LineSeries, x: number) => s.points.find((p) => p[0] === x)?.[1];
  return {
    label: o.label,
    table: () => ({
      head: ["", ...o.series.map((s) => s.name)],
      rows: xs.map((x) => [o.tipTitle(x), ...o.series.map((s) => (valueAt(s, x) == null ? "—" : fv(valueAt(s, x)!)))]),
    }),
    draw(host) {
      const W = width(host);
      const sc = scale(o.series.flatMap((s) => s.points.map((p) => p[1])), o.zero ?? true);
      const x0 = xs[0] ?? 0;
      const x1 = xs.at(-1) ?? 1;
      const x = (v: number) => PAD.l + ((W - PAD.l - PAD.r) * (v - x0)) / (x1 - x0 || 1);
      const y = (v: number) => PAD.t + (h - PAD.t - PAD.b) * (1 - (v - sc.lo) / (sc.hi - sc.lo || 1));
      const every = Math.max(1, Math.ceil((xs.length * 44) / (W - PAD.l - PAD.r)));
      // Regular ticks that would crowd the always-shown last label are skipped.
      const showX = (i: number) => i === xs.length - 1 || (i % every === 0 && xs.length - 1 - i >= every);
      const gid = `g${Math.random().toString(36).slice(2, 8)}`;
      const paths = [...o.series].reverse().map((s, ri) => {
        const d = s.points.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ");
        const a = s.points[0];
        const z = s.points.at(-1);
        const c = color(s.color);
        const area =
          s.area && a && z
            ? html`<defs><linearGradient id="${gid}${ri}" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="${c}" stop-opacity=".2"/><stop offset="100%" stop-color="${c}" stop-opacity="0"/></linearGradient></defs>
              <path d="${d} L${x(z[0])},${y(Math.max(sc.lo, 0))} L${x(a[0])},${y(Math.max(sc.lo, 0))} Z" fill="url(#${gid}${ri})"/>`
            : "";
        return html`${area}<path d="${d}" fill="none" stroke="${c}" stroke-width="${s.width ?? 2}" stroke-linejoin="round" stroke-linecap="round"${s.dash ? html` stroke-dasharray="${s.dash}"` : ""}/>`;
      });
      const first = o.series[0];
      const last = first?.points.at(-1);
      host.innerHTML = html`<svg viewBox="0 0 ${W} ${h}" width="${W}" height="${h}" role="img" aria-label="${o.label}">
        ${yAxis(sc, y, W, o.formatY ?? compact)}
        ${xs.map((v, i) => (showX(i) ? html`<text x="${x(v)}" y="${h - 6}" text-anchor="middle" font-size="11" fill="${color("var(--t3)")}">${o.formatX(v)}</text>` : ""))}
        ${paths}
        ${first && last ? html`<circle cx="${x(last[0])}" cy="${y(last[1])}" r="4.5" fill="${color(first.color)}" stroke="${color("var(--s1)")}" stroke-width="2"/>` : ""}
        <line class="xh" y1="${PAD.t}" y2="${h - PAD.b}" stroke="${color("var(--t3)")}" stroke-width="1" opacity="0"/>
        ${o.series.map((s) => html`<circle class="hd" r="4.5" fill="${color(s.color)}" stroke="${color("var(--s1)")}" stroke-width="2" opacity="0"/>`)}
        <rect class="hit" x="${PAD.l}" y="0" width="${W - PAD.l - PAD.r}" height="${h}" fill="transparent"/>
      </svg><div class="tip" aria-hidden="true"></div>`.s;
      const svg = host.querySelector("svg")!;
      const cross = svg.querySelector(".xh")!;
      const dots = [...svg.querySelectorAll(".hd")];
      const tip = tipper(host);
      let idx = -1;
      const show = (i: number) => {
        const xv = xs[i];
        if (xv == null) return;
        idx = i;
        const px = x(xv);
        cross.setAttribute("x1", String(px));
        cross.setAttribute("x2", String(px));
        cross.setAttribute("opacity", "1");
        const rows: Html[] = [];
        o.series.forEach((s, si) => {
          const v = valueAt(s, xv);
          const dot = dots[si]!;
          if (v == null) return void dot.setAttribute("opacity", "0");
          dot.setAttribute("cx", String(px));
          dot.setAttribute("cy", String(y(v)));
          dot.setAttribute("opacity", "1");
          rows.push(tipRow(color(s.color), s.name, fv(v)));
        });
        tip.show(html`${tipTitle(o.tipTitle(xv))}${rows}${o.tipExtra?.(xv) ?? ""}`, (px * host.clientWidth) / W);
      };
      const hide = () => {
        idx = -1;
        cross.setAttribute("opacity", "0");
        for (const d of dots) d.setAttribute("opacity", "0");
        tip.hide();
      };
      const nearest = (e: PointerEvent) => {
        const r = svg.getBoundingClientRect();
        const sx = ((e.clientX - r.left) * W) / r.width;
        let best = 0;
        xs.forEach((v, i) => {
          if (Math.abs(x(v) - sx) < Math.abs(x(xs[best]!) - sx)) best = i;
        });
        show(best);
      };
      const hit = svg.querySelector<SVGRectElement>(".hit")!;
      hit.onpointermove = nearest;
      hit.onpointerdown = nearest;
      hit.onpointerleave = hide;
      // Keyboard: the chart is one tab stop; arrows move the crosshair.
      host.tabIndex = 0;
      host.onfocus = () => show(idx < 0 ? xs.length - 1 : idx);
      host.onblur = hide;
      host.onkeydown = (e) => {
        if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
          e.preventDefault();
          show(Math.max(0, Math.min(xs.length - 1, (idx < 0 ? xs.length - 1 : idx) + (e.key === "ArrowLeft" ? -1 : 1))));
        }
      };
    },
  };
}

// ---------- columns: stacked, grouped, 100%, signed ----------

export interface Segment {
  key: string;
  name: string;
  color: string;
  value: Paise;
}
export interface Band {
  key: string;
  label: string;
  short: string;
  segments: Segment[];
}

/** Column path with a 4px rounded data-end and a square base, growing up (h > 0) or down (h < 0) from y0. */
function colPath(x: number, y0: number, w: number, h: number, round: boolean): string {
  const up = h >= 0;
  const hh = Math.abs(h);
  const r = round ? Math.min(4, w / 2, hh) : 0;
  const top = up ? y0 - hh : y0 + hh;
  if (up)
    return `M${x},${y0}V${top + r}${r ? `Q${x},${top} ${x + r},${top}` : ""}H${x + w - r}${r ? `Q${x + w},${top} ${x + w},${top + r}` : ""}V${y0}Z`;
  return `M${x},${y0}V${top - r}${r ? `Q${x},${top} ${x + r},${top}` : ""}H${x + w - r}${r ? `Q${x + w},${top} ${x + w},${top - r}` : ""}V${y0}Z`;
}

export function columnChart(o: {
  label: string;
  bands: Band[];
  mode: "stack" | "group";
  height?: number;
  normalize?: boolean;
  ref?: { value: Paise; label: string } | null;
  tip: (band: number, seg: string | null) => Html;
  onSelect?: (band: number, seg: string | null) => void;
  table?: () => Table;
}): Chart {
  const h = o.height ?? 220;
  const n = o.bands.length;
  const totals = o.bands.map((b) => b.segments.reduce((a, s) => a + Math.max(0, s.value), 0));
  const share = (bi: number, v: number) => (o.normalize ? (totals[bi] ? v / totals[bi]! : 0) : v);
  return {
    label: o.label,
    table:
      o.table ??
      (() => {
        const keys = [...new Map(o.bands.flatMap((b) => b.segments.map((s) => [s.key, s.name] as const))).entries()];
        return {
          head: ["", ...keys.map(([, name]) => name)],
          rows: o.bands.map((b) => [
            b.label,
            ...keys.map(([k]) => {
              const s = b.segments.find((x) => x.key === k);
              if (!s) return "—";
              return o.normalize ? `${Math.round(share(o.bands.indexOf(b), s.value) * 100)}%` : inr(s.value);
            }),
          ]),
        };
      }),
    draw(host) {
      const W = width(host);
      const values: number[] = o.normalize
        ? [0, 1]
        : o.mode === "stack"
          ? [...totals, ...(o.ref ? [o.ref.value] : [])]
          : [...o.bands.flatMap((b) => b.segments.map((s) => s.value)), ...(o.ref ? [o.ref.value] : [])];
      const sc = o.normalize ? { lo: 0, hi: 1, ticks: [0, 0.25, 0.5, 0.75, 1] } : scale(values, true);
      const y = (v: number) => PAD.t + (h - PAD.t - PAD.b) * (1 - (v - sc.lo) / (sc.hi - sc.lo || 1));
      const plotW = W - PAD.l - PAD.r;
      const bw = plotW / Math.max(1, n);
      const segs = o.bands[0]?.segments.length ?? 1;
      const barW = o.mode === "stack" ? Math.min(BAR_MAX, bw * 0.62) : Math.min(BAR_MAX, (bw * 0.78 - GAP * (segs - 1)) / segs);
      const y0 = y(0);
      const extents: { key: string; top: number; bottom: number }[][] = [];
      const marks = o.bands.map((b, bi) => {
        const cx = PAD.l + bw * bi + bw / 2;
        const ext: { key: string; top: number; bottom: number }[] = [];
        extents.push(ext);
        if (o.mode === "stack") {
          let acc = 0;
          const visible = b.segments.filter((s) => s.value > 0);
          return visible.map((s, si) => {
            const v0 = share(bi, acc);
            acc += s.value;
            const v1 = share(bi, acc);
            const top = y(v1);
            const bottom = y(v0) - (si > 0 ? GAP : 0);
            ext.push({ key: s.key, top, bottom });
            return html`<path class="seg" data-b="${bi}" d="${colPath(cx - barW / 2, bottom, barW, Math.max(0, bottom - top), si === visible.length - 1)}" fill="${color(s.color)}"/>`;
          });
        }
        const gw = barW * segs + GAP * (segs - 1);
        return b.segments.map((s, si) => {
          const x = cx - gw / 2 + si * (barW + GAP);
          const hh = y0 - y(s.value);
          ext.push({ key: s.key, top: Math.min(y0, y(s.value)), bottom: Math.max(y0, y(s.value)) });
          return html`<path class="seg" data-b="${bi}" d="${colPath(x, y0, barW, hh, true)}" fill="${color(s.color)}"/>`;
        });
      });
      const every = Math.max(1, Math.ceil(44 / bw));
      const showX = (i: number) => i === n - 1 || (i % every === 0 && n - 1 - i >= every);
      const ref = o.ref && !o.normalize
        ? html`<line x1="${PAD.l}" x2="${W - PAD.r}" y1="${y(o.ref.value)}" y2="${y(o.ref.value)}" stroke="${color("var(--t2)")}" stroke-width="1" stroke-dasharray="4 4"/>
          ${o.ref.label ? html`<text x="${W - PAD.r}" y="${y(o.ref.value) - 5}" text-anchor="end" font-size="11" fill="${color("var(--t2)")}">${o.ref.label}</text>` : ""}`
        : "";
      host.innerHTML = html`<svg viewBox="0 0 ${W} ${h}" width="${W}" height="${h}" role="img" aria-label="${o.label}">
        ${yAxis(sc, y, W, o.normalize ? (v) => `${Math.round(v * 100)}%` : compact)}
        ${o.bands.map((b, bi) => (showX(bi) ? html`<text x="${PAD.l + bw * bi + bw / 2}" y="${h - 6}" text-anchor="middle" font-size="11" fill="${color("var(--t3)")}">${b.short}</text>` : ""))}
        ${marks}${ref}
        ${o.bands.map((b, bi) => html`<rect class="hit${o.onSelect ? " sel" : ""}" data-b="${bi}" x="${PAD.l + bw * bi}" y="0" width="${bw}" height="${h - PAD.b}" fill="transparent" tabindex="0" role="img" aria-label="${b.label}"/>`)}
      </svg><div class="tip" aria-hidden="true"></div>`.s;
      const svg = host.querySelector("svg")!;
      const tip = tipper(host);
      const segEls = [...svg.querySelectorAll<SVGPathElement>(".seg")];
      const focus = (bi: number | null) => {
        for (const p of segEls) p.setAttribute("opacity", bi == null || p.dataset.b === String(bi) ? "1" : ".45");
      };
      const segAt = (bi: number, py: number) => extents[bi]?.find((e) => py >= e.top - 1 && py <= e.bottom + 1)?.key ?? null;
      for (const hit of svg.querySelectorAll<SVGRectElement>(".hit")) {
        const bi = Number(hit.dataset.b);
        const cx = ((PAD.l + bw * bi + bw / 2) * host.clientWidth) / W;
        const at = (e: PointerEvent) => {
          const r = svg.getBoundingClientRect();
          return segAt(bi, ((e.clientY - r.top) * h) / r.height);
        };
        hit.onpointermove = (e) => {
          focus(bi);
          tip.show(o.tip(bi, o.mode === "stack" ? at(e) : null), cx);
        };
        hit.onpointerleave = () => {
          focus(null);
          tip.hide();
        };
        hit.onfocus = () => {
          focus(bi);
          tip.show(o.tip(bi, null), cx);
        };
        hit.onblur = () => {
          focus(null);
          tip.hide();
        };
        if (o.onSelect) {
          hit.onclick = (e) => o.onSelect!(bi, o.mode === "stack" ? at(e) : null);
          hit.onkeydown = (e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              o.onSelect!(bi, null);
            }
          };
        }
      }
    },
  };
}

// ---------- diverging bars (HTML: long labels ellipsize cleanly) ----------

export interface DivRow {
  key: string;
  label: string;
  value: Paise;
  valueLabel: string;
  tip: Html;
}

/** Bars grow left or right of a neutral centre. `upIsBad` makes increases red (spending), else blue. */
export function divergingBars(o: { label: string; rows: DivRow[]; upIsBad: boolean; onSelect?: (key: string) => void; table: () => Table }): Chart {
  return {
    label: o.label,
    table: o.table,
    draw(host) {
      const max = Math.max(1, ...o.rows.map((r) => Math.abs(r.value)));
      const tag = o.onSelect ? "button" : "div";
      host.innerHTML = html`<div class="dv" role="list" aria-label="${o.label}">${o.rows.map((r, i) => {
        const w = (Math.abs(r.value) / max) * 50;
        const c = (r.value > 0) === o.upIsBad ? DIV_BAD : DIV_GOOD;
        const pos = r.value >= 0 ? `left:50%;width:${w}%;border-radius:0 4px 4px 0` : `right:50%;width:${w}%;border-radius:4px 0 0 4px`;
        return html`${trusted(`<${tag} class="dv-row" data-i="${i}" role="listitem"${tag === "button" ? ' type="button"' : ' tabindex="0"'}>`)}
          <span class="dv-l">${r.label}</span>
          <span class="dv-t"${sty(`--mid:${DIV_MID}`)}><i${sty(`${pos};background:${c}`)}></i></span>
          <span class="dv-v num">${r.valueLabel}</span>${trusted(`</${tag}>`)}`;
      })}</div><div class="tip" aria-hidden="true"></div>`.s;
      applyStyles(host);
      const tip = tipper(host);
      for (const el of host.querySelectorAll<HTMLElement>(".dv-row")) {
        const r = o.rows[Number(el.dataset.i)]!;
        const show = () => tip.show(r.tip, el.offsetLeft + el.offsetWidth / 2, el.offsetTop + el.offsetHeight);
        el.onpointerenter = show;
        el.onfocus = show;
        el.onpointerleave = () => tip.hide();
        el.onblur = () => tip.hide();
        if (o.onSelect) el.onclick = () => o.onSelect!(r.key);
      }
    },
  };
}

// ---------- calendar heatmap (HTML grid, Monday first) ----------

export function heatmap(o: { label: string; period: Period; values: Map<ISODate, Paise>; through: ISODate }): Chart {
  const days: ISODate[] = [];
  for (let d = o.period.start; d <= o.period.end; d = addDays(d, 1)) days.push(d);
  const th = heatThresholds(days.filter((d) => d <= o.through).map((d) => o.values.get(d) ?? 0), HEAT.length);
  return {
    label: o.label,
    table: () => ({ head: ["Day", "Spent"], rows: days.filter((d) => d <= o.through).map((d) => [dayShort(d), inr(o.values.get(d) ?? 0)]) }),
    draw(host) {
      const lead = weekday(o.period.start);
      const cells: Html[] = [];
      for (let i = 0; i < lead; i++) cells.push(html`<span class="hm-c hm-x"></span>`);
      days.forEach((d, i) => {
        const future = d > o.through;
        const v = o.values.get(d) ?? 0;
        const bin = future ? -2 : heatBin(v, th);
        const bg = bin === -2 ? "transparent" : bin < 0 ? HEAT_ZERO : HEAT[bin]!;
        const ink = bin >= 3 ? "#0d1b2e" : "var(--t2)";
        cells.push(html`<span class="hm-c${future ? " hm-f" : ""}" data-i="${i}"${sty(`background:${bg};color:${ink}`)}>${Number(d.slice(8, 10))}</span>`);
      });
      const legend = [
        html`<span><i${sty(`background:${HEAT_ZERO}`)}></i>₹0</span>`,
        ...HEAT.map((c, i) => html`<span><i${sty(`background:${c}`)}></i>${i === HEAT.length - 1 ? `> ${compact(th.at(-1) ?? 0)}` : `≤ ${compact(th[i] ?? 0)}`}</span>`),
      ];
      host.innerHTML = html`<div class="hm" role="img" aria-label="${o.label}">
        ${WEEKDAYS.map((w) => html`<span class="hm-h">${w.slice(0, 2)}</span>`)}${cells}
      </div><div class="hm-leg">${legend}</div><div class="tip" aria-hidden="true"></div>`.s;
      applyStyles(host);
      const tip = tipper(host);
      for (const el of host.querySelectorAll<HTMLElement>(".hm-c[data-i]")) {
        const d = days[Number(el.dataset.i)]!;
        el.onpointerenter = () =>
          tip.show(html`${tipTitle(dayShort(d))}${d > o.through ? html`<div class="r"><span>Not yet</span></div>` : tipRow(null, "Spent", inr(o.values.get(d) ?? 0))}`, el.offsetLeft + el.offsetWidth / 2, el.offsetTop + el.offsetHeight);
        el.onpointerleave = () => tip.hide();
      }
    },
  };
}

// ---------- donut (part-to-whole at a glance; the table beside it carries exact values) ----------

export function donut(o: { label: string; slices: { key: string; label: string; value: Paise; color: string }[]; center: [string, string] }): Chart {
  const total = o.slices.reduce((a, s) => a + Math.max(0, s.value), 0);
  return {
    label: o.label,
    table: () => ({ head: ["Slice", "Value", "Share"], rows: o.slices.map((s) => [s.label, inr(s.value), total ? `${((s.value / total) * 100).toFixed(1)}%` : "—"]) }),
    draw(host) {
      const size = Math.min(220, width(host, 160));
      const R = size / 2 - 4;
      const r = R - 30;
      const c = size / 2;
      let a0 = -Math.PI / 2;
      const xy = (a: number, rad: number): [number, number] => [c + rad * Math.cos(a), c + rad * Math.sin(a)];
      const pt = (a: number, rad: number) => xy(a, rad).map((v) => v.toFixed(2)).join(",");
      const visible = o.slices.filter((s) => s.value > 0);
      const arcs = visible.map((s) => {
          const a1 = a0 + (s.value / total) * Math.PI * 2;
          const large = a1 - a0 > Math.PI ? 1 : 0;
          const d =
            visible.length === 1
              ? `M${pt(-Math.PI / 2, R)}A${R},${R} 0 1 1 ${pt(Math.PI * 1.5 - 0.0001, R)}L${pt(Math.PI * 1.5 - 0.0001, r)}A${r},${r} 0 1 0 ${pt(-Math.PI / 2, r)}Z`
              : `M${pt(a0, R)}A${R},${R} 0 ${large} 1 ${pt(a1, R)}L${pt(a1, r)}A${r},${r} 0 ${large} 0 ${pt(a0, r)}Z`;
          const mid = (a0 + a1) / 2;
          const share = s.value / total;
          a0 = a1;
          // % goes inside the ring only where it fits; smaller slices keep their value in the list and tooltip.
          const [lx, ly] = xy(mid, (R + r) / 2);
          const lbl = share >= 0.07 ? html`<text x="${lx.toFixed(1)}" y="${(ly + 4).toFixed(1)}" text-anchor="middle" font-size="11" font-weight="600" fill="${inkOn(color(s.color))}">${Math.round(share * 100)}%</text>` : "";
          return html`<g class="sl" data-k="${s.key}" tabindex="0"><path d="${d}" fill="${color(s.color)}" stroke="${color("var(--s1)")}" stroke-width="${GAP}"/>${lbl}</g>`;
        });
      host.innerHTML = html`<svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" role="img" aria-label="${o.label}">
        ${arcs}
        <text x="${c}" y="${c - 2}" text-anchor="middle" font-size="16" font-weight="600" fill="${color("var(--t1)")}">${o.center[0]}</text>
        <text x="${c}" y="${c + 15}" text-anchor="middle" font-size="11" fill="${color("var(--t3)")}">${o.center[1]}</text>
      </svg><div class="tip" aria-hidden="true"></div>`.s;
      const tip = tipper(host);
      for (const g of host.querySelectorAll<SVGGElement>(".sl")) {
        const s = o.slices.find((x) => x.key === g.dataset.k)!;
        const show = () => tip.show(html`${tipTitle(s.label)}${tipRow(color(s.color), "Share", `${((s.value / total) * 100).toFixed(1)}%`)}${tipRow(null, "Value", inr(s.value), false)}`, size / 2, 8);
        g.onpointerenter = show;
        g.onfocus = show;
        g.onpointerleave = () => tip.hide();
        g.onblur = () => tip.hide();
      }
    },
  };
}

// ---------- sparkline ----------

export function sparkline(o: { label: string; points: Point[]; color: string; formatTip: (x: number) => string; height?: number }): Chart {
  const h = o.height ?? 40;
  return {
    label: o.label,
    table: () => ({ head: ["Period", "Spent"], rows: o.points.map((p) => [o.formatTip(p[0]), inr(p[1])]) }),
    draw(host) {
      const W = width(host, 120);
      const max = Math.max(1, ...o.points.map((p) => p[1]));
      const n = o.points.length;
      const x = (i: number) => 3 + ((W - 6) * i) / Math.max(1, n - 1);
      const y = (v: number) => 4 + (h - 8) * (1 - v / max);
      const d = o.points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ");
      const c = color(o.color);
      const last = o.points.at(-1);
      host.innerHTML = html`<svg viewBox="0 0 ${W} ${h}" width="${W}" height="${h}" role="img" aria-label="${o.label}">
        <path d="${d} L${x(n - 1)},${h - 2} L${x(0)},${h - 2} Z" fill="${c}" fill-opacity=".1"/>
        <path d="${d}" fill="none" stroke="${c}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
        ${last ? html`<circle cx="${x(n - 1)}" cy="${y(last[1])}" r="4" fill="${c}" stroke="${color("var(--s1)")}" stroke-width="2"/>` : ""}
        <line class="xh" y1="0" y2="${h}" stroke="${color("var(--t3)")}" stroke-width="1" opacity="0"/>
        <rect class="hit" x="0" y="0" width="${W}" height="${h}" fill="transparent"/>
      </svg><div class="tip" aria-hidden="true"></div>`.s;
      const svg = host.querySelector("svg")!;
      const cross = svg.querySelector(".xh")!;
      const tip = tipper(host);
      const hit = svg.querySelector<SVGRectElement>(".hit")!;
      hit.onpointermove = (e) => {
        const r = svg.getBoundingClientRect();
        const i = Math.max(0, Math.min(n - 1, Math.round((((e.clientX - r.left) * W) / r.width - 3) / ((W - 6) / Math.max(1, n - 1)))));
        const p = o.points[i];
        if (!p) return;
        cross.setAttribute("x1", String(x(i)));
        cross.setAttribute("x2", String(x(i)));
        cross.setAttribute("opacity", "1");
        tip.show(html`${tipTitle(o.formatTip(p[0]))}${tipRow(c, "Spent", inr(p[1]))}`, (x(i) * host.clientWidth) / W, h);
      };
      hit.onpointerleave = () => {
        cross.setAttribute("opacity", "0");
        tip.hide();
      };
    },
  };
}
