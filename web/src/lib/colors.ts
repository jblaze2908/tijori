// Colour follows the entity, never its rank. Categorical slots are the validated --c1..--c8 order
// (dataviz validate_palette, dark, against --s1); anything past eight folds into neutral grey.

/** Names follow the backend's default taxonomy (docs/api.md). Stacks are drawn in slot order so neighbours
 *  are always the validated adjacent pairs. */
export const CATEGORY_SLOT: Record<string, number> = {
  Shopping: 1,
  Groceries: 2,
  "Bills & subscriptions": 3,
  "Eating out": 4,
  "Local shops": 5,
  Entertainment: 6,
  Travel: 7,
  "Card bill payment": 8,
};

export const OTHER = "var(--t3)";

// Money flows share one mapping everywhere: the first three slots validate all-pairs, and income is grey context.
export const FLOW = { spend: "var(--c1)", invest: "var(--c2)", saved: "var(--c3)", income: "var(--t3)", committed: "var(--t3)" } as const;
export const slotColor = (slot: number | undefined) => (slot && slot >= 1 && slot <= 8 ? `var(--c${slot})` : OTHER);
export const categoryColor = (c: string | null) => slotColor(c ? CATEGORY_SLOT[c] : undefined);
/** Item categories are open-ended (the agent names them), so a stable hash picks the slot: same name, same colour
 *  everywhere. Lowercased because the server matches them in any case. */
export function itemCategoryColor(c: string | null): string {
  if (!c) return OTHER;
  let h = 0;
  for (const ch of c.trim().toLowerCase()) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return slotColor((h % 8) + 1);
}
/** --c6 is too dark to read as a 1.5–2px line on the dark surface, so lines take a lifted step of the same green. */
export const lineInk = (c: string) => (c === "var(--c6)" ? "#1fa31f" : c);

/** Net worth by asset class (docs/api.md, accounts): what you own in palette slots, what you owe in the red pole. */
export const ASSET_CLASS_COLOR: Record<string, string> = {
  cash: "var(--c1)",
  deposits: "var(--c2)",
  equity: "var(--c3)",
  retirement: "var(--c4)",
  gold: "var(--c5)",
  property: "var(--c6)",
  other: OTHER,
  credit: "#e66767",
  loans: "#e58d8d",
};

// Sequential single-hue ramp for the spend heatmap (blue, validated --ordinal on the dark surface);
// dark mode runs dark→light so "near zero" recedes toward the surface.
export const HEAT = ["#184f95", "#256abf", "#3987e5", "#6da7ec", "#b7d3f6"];
export const HEAT_ZERO = "var(--s3)";

// Diverging pair: blue ↔ red poles with the neutral gray midpoint (validated, dark). Red = worse for you.
export const DIV_GOOD = "#3987e5";
export const DIV_BAD = "#e66767";
export const DIV_MID = "#383835";

const MONOGRAM = ["#3b5b8c", "#7a4a2c", "#2c6b55", "#6b5a24", "#6d3f5a", "#2d5e2d", "#4f4787", "#7a3b3b"];
export function monogramColor(s: string): string {
  let h = 0;
  for (const ch of s) h += ch.charCodeAt(0);
  return MONOGRAM[h % MONOGRAM.length] ?? "#303030";
}

function luminance(hex: string): number {
  const n = parseInt(hex.replace("#", ""), 16);
  const ch = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * ch[0]! + 0.7152 * ch[1]! + 0.0722 * ch[2]!;
}
/** White or near-black, whichever contrasts more with a fill: for labels set inside a coloured mark. */
export function inkOn(hex: string): string {
  if (!/^#[0-9a-f]{6}$/i.test(hex)) return "#fff";
  const L = luminance(hex);
  return (1.05 / (L + 0.05)) >= ((L + 0.05) / (luminance("#111111") + 0.05)) ? "#fff" : "#111";
}

/** One colour per account, so two banks or two fund lines are told apart: its asset class's colour, the next account
 *  of the same class a step lighter toward the surface (stable by account id, so a line keeps its colour). */
export function accountColors(items: { account_id: number | null; key: string; asset_class: string }[]): Map<string, string> {
  const byClass = new Map<string, { key: string; id: number }[]>();
  for (const c of items) {
    const list = byClass.get(c.asset_class) ?? [];
    list.push({ key: c.key, id: c.account_id ?? Number.MAX_SAFE_INTEGER });
    byClass.set(c.asset_class, list);
  }
  const out = new Map<string, string>();
  for (const [cls, list] of byClass) {
    const base = ASSET_CLASS_COLOR[cls] ?? OTHER;
    list.sort((a, b) => a.id - b.id).forEach((c, i) => out.set(c.key, i ? `color-mix(in srgb, ${base} ${Math.max(100 - i * 22, 34)}%, var(--s1))` : base));
  }
  return out;
}
