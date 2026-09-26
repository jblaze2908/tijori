// Markup helpers. Every ${} in html`` is escaped unless it is already Html, so API text (merchant names,
// UPI remarks, narrations) can never become markup. Dynamic styles go through CSSOM (see mount) so the
// page works under a strict CSP without inline style attributes.

export class Html {
  constructor(readonly s: string) {}
  toString() {
    return this.s;
  }
}

const ESC: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (v: unknown) => String(v).replace(/[&<>"']/g, (c) => ESC[c] ?? c);

/** Trusted markup, for output that is itself built with html`` or from constants. */
export const trusted = (s: string) => new Html(s);

function part(v: unknown): string {
  if (v instanceof Html) return v.s;
  if (Array.isArray(v)) return v.map(part).join("");
  if (v == null || v === false) return "";
  return esc(v);
}

export function html(strings: TemplateStringsArray, ...vals: unknown[]): Html {
  let out = strings[0] ?? "";
  for (let i = 0; i < vals.length; i++) out += part(vals[i]) + (strings[i + 1] ?? "");
  return new Html(out);
}

/** A data-style attribute; mount() applies it via CSSOM. Values are escaped like any other attribute. */
export const sty = (css: string) => html` data-style="${css}"`;

export function mount(root: Element, markup: Html) {
  root.innerHTML = markup.s;
  applyStyles(root);
}

export function applyStyles(root: ParentNode) {
  for (const el of root.querySelectorAll<HTMLElement | SVGElement>("[data-style]")) {
    el.style.cssText = el.dataset.style ?? "";
    el.removeAttribute("data-style");
  }
}

export const $ = <T extends Element = HTMLElement>(sel: string, root: ParentNode = document) => root.querySelector<T>(sel);
export const $$ = <T extends Element = HTMLElement>(sel: string, root: ParentNode = document) => [...root.querySelectorAll<T>(sel)];

/** Resolved design tokens, for SVG attributes (presentation attributes don't reliably resolve var()). */
const tokenCache = new Map<string, string>();
export function token(name: string): string {
  let v = tokenCache.get(name);
  if (v === undefined) {
    v = getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
    tokenCache.set(name, v);
  }
  return v;
}
/** Accepts "var(--x)" or a literal colour and returns something an SVG attribute can use. */
export const color = (c: string) => {
  const m = /^var\((--[\w-]+)\)$/.exec(c);
  return m?.[1] ? token(m[1]) : c;
};
